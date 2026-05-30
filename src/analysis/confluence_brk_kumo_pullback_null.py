"""brk_kumo_hi pullback-continuation GATE vs fill-order null: paired shuffles (read-only).

Q4/Q6 (docs/analysis/confluence_strategy.md) found brk_kumo_hi is a PULLBACK-CONTINUATION
signal, not a fresh-breakout signal: fires where price was already established above the
cloud (high frac of prior 60d over kumo) outperform the fresh-from-below ones (the only
negative-EV bucket). This TRIES the positive gate — keep brk_kumo_hi only when it is a
genuine continuation (frac of prior 60d with close>cloud_top >= threshold) — and tests it
at the BINDING level: the confluence 6-slot book, paired fill-order null.

Sign-logic changes are judged by the confluence A/B (brk_sma precedent: per-fire worse but
confluence won). Arms share all 10 bullish signs; only brk_kumo_hi's fire list differs:
  BASE : all brk_kumo_hi fires (production)
  G20  : drop brk_kumo_hi fires with frac_over_60 < 0.20  (drop the negative fresh-from-below bucket)
  G40  : drop brk_kumo_hi fires with frac_over_60 < 0.40  (keep only the strongest buckets)
Paired by shared seed (different candidate pools → pair via rng(k)). 6-slot book.

REAL if a gate's Δ Sharpe vs BASE has P(Δ>0) >= 0.95 AND 95% CI excludes 0.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_brk_kumo_pullback_null
"""
from __future__ import annotations

import datetime
import random
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.brk_kumo_days_under_stage0 import _stock_levels
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import _closes, _fy_returns, _metrics, _sharpe
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_LOW = 60          # context window
_THRESHOLDS = {"G20": 0.20, "G40": 0.40}


def _fires(signs):
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(signs)))).all()
    f = defaultdict(list)
    for sg, st, fa in rows:
        f[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    return f


def _frac_over_map(cache) -> dict:
    """{date: frac of prior 60 trading days with close > cloud_top}."""
    lv = _stock_levels(cache)
    if lv is None:
        return {}
    dts, o, hi, lo, top, bot = lv
    cmap = {b.dt.date(): b.close for b in cache.bars}
    cl = np.array([cmap[d] for d in dts])
    valid = (~np.isnan(top)) & (top > 0)
    over = np.where(valid, (cl > top).astype(float), 0.0)
    cnt = np.where(valid, 1.0, 0.0)
    co = np.concatenate([[0.0], np.cumsum(over)])
    cc = np.concatenate([[0.0], np.cumsum(cnt)])
    out = {}
    for i in range(len(dts)):
        a = max(0, i - _LOW)
        denom = cc[i] - cc[a]
        if denom >= _LOW // 2:
            out[dts[i]] = (co[i] - co[a]) / denom
    return out


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    fires = _fires(_WINDOWED)
    exsim._MAX_LOW_CORR = _SLOTS - 1

    arms = ["BASE"] + list(_THRESHOLDS)
    st = {a: [[] for _ in range(_K)] for a in arms}
    kept = defaultdict(int); dropped = defaultdict(int)

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=260)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        frac_maps = {code: _frac_over_map(c) for code, c in caches.items()}

        def _fires_for(code, thr):
            """brk_kumo_hi fires gated by frac>=thr (other signs untouched)."""
            out = []
            fm = frac_maps.get(code, {})
            for sg, d in fires.get(code, []):
                if sg != "brk_kumo_hi" or thr is None:
                    out.append((sg, d)); continue
                fr = fm.get(d)
                if fr is None or fr >= thr:          # keep if uncomputable or passes gate
                    out.append((sg, d)); kept[thr] += 1
                else:
                    dropped[thr] += 1
            return out

        def _pool(thr):
            out = []
            for code in caches:
                out += cbt._candidates_for_stock(
                    code, _fires_for(code, thr), caches[code],
                    corr_maps.get(code, {}), zs_maps.get(code, {}),
                    cfg.start, cfg.end, _N_GATE)
            return out

        pools = {"BASE": _pool(None)}
        for a, thr in _THRESHOLDS.items():
            pools[a] = _pool(thr)

        for k in range(_K):
            for a in arms:
                p = pools[a][:]
                random.Random(k).shuffle(p)
                st[a][k] += _fy_returns(p, caches, cfg, stock_dts, cal, _SLOTS)[1:]
        logger.info("  {} done (pools: {})", cfg.label,
                    {a: len(pools[a]) for a in arms})

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in arms}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in arms}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in arms}

    print("\n" + "=" * 84)
    print(f"brk_kumo_hi PULLBACK-CONTINUATION GATE vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot")
    print("=" * 84)
    for a in _THRESHOLDS:
        thr = _THRESHOLDS[a]
        print(f"  {a}: drop brk_kumo_hi frac_over_60 < {thr:.0%} "
              f"→ dropped {dropped[thr]} / kept {kept[thr]} brk_kumo_hi fires")
    print(f"\n{'arm':<18}{'Sharpe mean':>12}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}{'ret':>8}{'DD':>7}")
    for a in arms:
        s_ = sh[a]
        print(f"{a:<18}{s_.mean():>12.2f}{s_.std():>7.2f}{np.percentile(s_,5):>8.2f}"
              f"{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>7.0f}%{dd[a].mean()*100:>6.0f}%")

    for a in _THRESHOLDS:
        d = sh[a] - sh["BASE"]; dr = rt[a] - rt["BASE"]
        print(f"\n[{a} − BASE, paired]")
        print(f"  Δ Sharpe mean {d.mean():+.3f} | 95% CI [{np.percentile(d,2.5):+.3f}, "
              f"{np.percentile(d,97.5):+.3f}] | P(Δ>0)={(d>0).mean():.3f} | Δ ret {dr.mean()*100:+.0f}pp")
        sep = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
        print(f"  -> {'REAL' if sep else 'NOT separated'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
