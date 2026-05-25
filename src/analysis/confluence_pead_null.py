"""pead_up inclusion vs its own fill-order null — capital-aware 6-slot book, paired shuffles.

Binding ship gate for the pead_up confluence sign. The inclusion A/B
(confluence_pead_inclusion_ab) was PROMISING at N=3 on per-trade Sharpe
(+3.01→+3.71, OOS FY2025 +1.43, diversifying corr +0.34, tail-hedge +10.3pp) but
per-trade Sharpe is not the portfolio metric and per-FY was 4/6 (FY2024 −1.63).
This certifies — or rejects — the inclusion against fill-order luck on the
capital-aware 6-slot book, the same bar that gated capacity and rejected every
selection rule (project_confluence_fill_order_null).

Method: K paired shuffles. Arm A = baseline 10-sign pool; arm B = baseline +
pead_up (vb=60) pool — DIFFERENT pools (the extra vote pushes more stock-days over
N≥3). For each seed k, shuffle each pool with Random(k) and run both at the
production 6-slot cap (1 high + 5 low), marking capital-aware r/6. Seed-pairing
feeds both arms the same realization of fill-order randomness, so Δ = Sharpe(B) −
Sharpe(A) per seed is the inclusion effect net of order luck. Ship only if the Δ
band reliably excludes 0 AND no per-FY sign-flip kills it (FY2024 is the watch).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_pead_null
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_pead_inclusion_ab import (
    _build_pead_up_fires, _load_pead_statements,
)
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_PEAD_VB = {**_BULLISH, "pead_up": 60}
_N_GATE = 3
_K = 200
_LOW = 5   # production 6-slot book = 1 high + 5 low
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


def _closes(cache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), sh, float((eq / runmax - 1.0).min())


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(pool, caches, cfg, stock_dts, cal, n_slots):
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / n_slots
    return [day_contrib.get(d, 0.0) for d in cal]


def _build_cands(fires, caches, corr_maps, zs_maps, cfg):
    out = []
    for code in caches:
        out.extend(cbt._candidates_for_stock(
            code, fires.get(code, []), caches[code],
            corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
    return out


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0   # DB holds only fresh post-rebuild runs (match confluence_benchmark)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    base_fires: defaultdict[str, list] = defaultdict(list)
    for sg, stk, fa in rows:
        base_fires[stk].append((sg, fa.date() if hasattr(fa, "date") else fa))
    stmts_by_yf = _load_pead_statements()

    st = {"A": [[] for _ in range(_K)], "B": [[] for _ in range(_K)]}   # stitched daily
    fy_sh: dict[tuple[str, str], np.ndarray] = {}                        # per-FY Sharpe arrays

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 180)
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
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        pead_fires = _build_pead_up_fires(caches, stmts_by_yf)

        cbt._VALID_BARS = dict(_BULLISH)
        cands_a = _build_cands(base_fires, caches, corr_maps, zs_maps, cfg)
        cbt._VALID_BARS = dict(_PEAD_VB)
        merged: defaultdict[str, list] = defaultdict(list)
        for src in (base_fires, pead_fires):
            for code, fs in src.items():
                merged[code].extend(fs)
        cands_b = _build_cands(merged, caches, corr_maps, zs_maps, cfg)

        exsim._MAX_LOW_CORR = _LOW
        rets_a: list[list[float]] = []
        rets_b: list[list[float]] = []
        for k in range(_K):
            pa = cands_a[:]; random.Random(k).shuffle(pa)
            ra = _fy_returns(pa, caches, cfg, stock_dts, cal, 1 + _LOW)
            pb = cands_b[:]; random.Random(k).shuffle(pb)
            rb = _fy_returns(pb, caches, cfg, stock_dts, cal, 1 + _LOW)
            rets_a.append(ra); rets_b.append(rb)
            st["A"][k] += ra[1:]
            st["B"][k] += rb[1:]
        exsim._MAX_LOW_CORR = 5
        fy_sh[(cfg.label, "A")] = np.array([_sharpe(r) for r in rets_a])
        fy_sh[(cfg.label, "B")] = np.array([_sharpe(r) for r in rets_b])
        logger.info("  {} done (A={} / B={} cands, {} pead fires, {} shuffles)",
                    cfg.label, len(cands_a), len(cands_b),
                    sum(len(v) for v in pead_fires.values()), _K)

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in ("A", "B")}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in ("A", "B")}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in ("A", "B")}

    print("\n" + "=" * 84)
    print(f"PEAD_UP INCLUSION vs FILL-ORDER NULL — {_K} paired shuffles, capital-aware 6-slot")
    print("=" * 84)
    print(f"\n{'arm':<14}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a, name in [("A", "baseline"), ("B", "+pead_up")]:
        s_ = sh[a]
        print(f"{name:<14}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.0f}%")

    d = sh["B"] - sh["A"]    # paired Δ Sharpe (B − A), same fill order per seed
    print("\n[paired Δ Sharpe = +pead_up − baseline, same fill order each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}]")
    print(f"  P(Δ > 0) = {(d > 0).mean():.3f}  ({int((d>0).sum())}/{_K} shuffles)")
    dr = rt["B"] - rt["A"]
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")
    ddd = dd["B"] - dd["A"]
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.1f}pp (positive = +pead_up shallower DD)")

    print("\nPER-FY paired Δ Sharpe (+pead_up − baseline) — FY2024 is the watch:")
    flip = []
    for cfg in _FYS:
        ka, kb = (cfg.label, "A"), (cfg.label, "B")
        if ka not in fy_sh:
            continue
        dfy = fy_sh[kb] - fy_sh[ka]
        dfy = dfy[~np.isnan(dfy)]
        if len(dfy) == 0:
            continue
        tag = "  ← watch" if cfg.label == "FY2024" else ""
        if dfy.mean() < 0:
            flip.append(cfg.label)
        print(f"  {cfg.label}  Δ mean {dfy.mean():+.3f} | P(Δ>0)={(dfy>0).mean():.2f}{tag}")

    sep = (sh["B"].mean() - sh["A"].mean()) / math.sqrt(sh["B"].std()**2 + sh["A"].std()**2)
    print(f"\n  distribution separation (Δmean / pooled sd) = {sep:.2f}")
    certified = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
    if certified and not flip:
        verdict = "SHIP — Δ band excludes 0 net of fill-order luck, no per-FY sign-flip"
    elif certified:
        verdict = f"MIXED — Δ band clears null but per-FY negative in {flip}"
    else:
        verdict = "REJECT — pead_up inclusion within fill-order noise (same fate as selection rules)"
    print(f"  VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
