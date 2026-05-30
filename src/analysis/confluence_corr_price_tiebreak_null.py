"""Price tie-break WITHIN similar-correlation candidates — paired null (budget book).

Operator (2026-05-30, clarified): keep correlation/diversification as the PRIMARY slot
pick. Only when candidates have SIMILAR correlation-to-holdings, break the tie by price
(deployed capital = lots×100×price). Test BOTH directions — prefer expensive vs prefer
cheapest — against a random tie-break.

All three arms share the SAME primary: corr-greedy (least max-|corr|-to-held, the
confluence_slot_order day_selector). Candidates are binned into corr bands of width
W_BAND; WITHIN a band the order is:
    RANDOM : seeded shuffle           (control)
    EXP    : highest deployed capital first
    CHEAP  : lowest  deployed capital first
Budget ¥2M book (deployed-capital weights), 6 slots. Paired by seed: same corr-greedy
primary + same base randomness; arms differ only in the within-band tie-break.

REAL only if a direction's Δ Sharpe vs RANDOM has P(Δ>0) >= 0.95 AND 95% CI excludes 0.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_corr_price_tiebreak_null
"""
from __future__ import annotations

import datetime
import math
import random
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _BUDGET, _FYS, _SLOTS
from src.analysis.confluence_capacity_null import _closes, _metrics, _pos_daily, _sharpe
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_notional, position_weight, recommended_lots
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_CORR_WIN = 20
_W_BAND = 0.15      # corr-to-holdings band width that counts as "similar"


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


def _stock_returns(caches):
    returns, didx = {}, {}
    for code, c in caches.items():
        dts, cmap = _closes(c)
        arr = np.array([cmap[d] for d in dts])
        r = np.zeros(len(arr))
        r[1:] = arr[1:] / arr[:-1] - 1.0
        returns[code] = r
        didx[code] = {d: i for i, d in enumerate(dts)}
    return returns, didx


def _make_selector(returns, didx, mode, rng, depl, corr_cache):
    """corr_cache: shared {(today, a, b): |corr|} memo across all seeds/arms of an FY."""
    def trailing(code, today):
        di = didx.get(code)
        if di is None:
            return None
        i = di.get(today)
        if i is None or i < _CORR_WIN:
            return None
        return returns[code][i - _CORR_WIN + 1:i + 1]

    def pair_corr(a, b, today):
        key = (today, a, b) if a < b else (today, b, a)
        v = corr_cache.get(key)
        if v is not None:
            return v
        ra, rb = trailing(a, today), trailing(b, today)
        if ra is None or rb is None:
            cc = 0.0
        else:
            cc = np.corrcoef(ra, rb)[0, 1]
            cc = 0.0 if math.isnan(cc) else abs(cc)
        corr_cache[key] = cc
        return cc

    def selector(today, cands, open_pos):
        held = list({p.candidate.stock_code for p in open_pos})
        if not held:
            scored = []
            for c in cands:
                tb = ((-depl[id(c)], c.stock_code) if mode == "exp"
                      else (depl[id(c)], c.stock_code) if mode == "cheap" else (rng.random(),))
                scored.append((0,) + tb + (c,))
            scored.sort(key=lambda t: t[:-1])
            return [t[-1] for t in scored]

        scored = []
        for c in cands:
            best = max((pair_corr(c.stock_code, h, today) for h in held), default=0.0)
            band = round(best / _W_BAND)               # similar-corr bin
            if mode == "exp":
                tb = (-depl[id(c)], c.stock_code)
            elif mode == "cheap":
                tb = (depl[id(c)], c.stock_code)
            else:
                tb = (rng.random(),)
            scored.append((band,) + tb + (c,))
        scored.sort(key=lambda t: t[:-1])
        return [t[-1] for t in scored]
    return selector


def _budget_returns(pool, caches, cfg, stock_dts, cal, selector):
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end, day_selector=selector)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        lots = recommended_lots(_BUDGET, float(p.entry_price), _SLOTS)
        w = position_weight(lots, float(p.entry_price), _BUDGET)
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r * w
    return [day.get(d, 0.0) for d in cal]


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    fires = _fires(_WINDOWED)
    exsim._MAX_LOW_CORR = _SLOTS - 1
    # RAND varies by seed (K shuffles); EXP/CHEAP are deterministic price tie-breaks
    # (no rng) → one book each, like the deployed-capital null.
    st_rand = [[] for _ in range(_K)]
    st_exp: list[float] = []
    st_cheap: list[float] = []

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
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
        returns, didx = _stock_returns(caches)
        n_dts, _ = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        pool = []
        for code in caches:
            pool += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE)
        depl, pool_aff = {}, []
        for c in pool:
            lots = recommended_lots(_BUDGET, float(c.entry_price), _SLOTS)
            if lots <= 0:
                continue
            depl[id(c)] = position_notional(lots, float(c.entry_price))
            pool_aff.append(c)

        corr_cache: dict = {}     # shared memo across all seeds/arms of this FY
        # EXP / CHEAP: deterministic → run once
        sel_exp = _make_selector(returns, didx, "exp", random.Random(0), depl, corr_cache)
        st_exp += _budget_returns(pool_aff, caches, cfg, stock_dts, cal, sel_exp)[1:]
        sel_cheap = _make_selector(returns, didx, "cheap", random.Random(0), depl, corr_cache)
        st_cheap += _budget_returns(pool_aff, caches, cfg, stock_dts, cal, sel_cheap)[1:]
        # RAND control: K seeded shuffles
        for k in range(_K):
            sel = _make_selector(returns, didx, "rand", random.Random(k), depl, corr_cache)
            st_rand[k] += _budget_returns(pool_aff, caches, cfg, stock_dts, cal, sel)[1:]
        logger.info("  {} done ({} affordable cands, {} cached corr pairs)",
                    cfg.label, len(pool_aff), len(corr_cache))

    sh_rand = np.array([_sharpe(st_rand[k]) for k in range(_K)])
    rt_rand = np.array([_metrics(st_rand[k])[0] for k in range(_K)])
    dd_rand = np.array([_metrics(st_rand[k])[2] for k in range(_K)])
    sh_exp, rt_exp, dd_exp = _sharpe(st_exp), _metrics(st_exp)[0], _metrics(st_exp)[2]
    sh_cheap, rt_cheap, dd_cheap = _sharpe(st_cheap), _metrics(st_cheap)[0], _metrics(st_cheap)[2]

    print("\n" + "=" * 86)
    print(f"CORR-PRIMARY + PRICE TIE-BREAK — {_K}-shuffle RAND control, ¥{_BUDGET:,} budget "
          f"book, corr band {_W_BAND}")
    print("=" * 86)
    print(f"\n{'arm (tie-break)':<26}{'Sharpe':>9}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret':>8}{'DD':>7}")
    print(f"{'random (control)':<26}{sh_rand.mean():>9.2f}{sh_rand.std():>7.2f}"
          f"{np.percentile(sh_rand,5):>8.2f}{np.percentile(sh_rand,50):>8.2f}"
          f"{np.percentile(sh_rand,95):>8.2f}{rt_rand.mean()*100:>7.0f}%{dd_rand.mean()*100:>6.0f}%")
    print(f"{'prefer EXPENSIVE (det.)':<26}{sh_exp:>9.2f}{'—':>7}{'—':>8}{'—':>8}{'—':>8}"
          f"{rt_exp*100:>7.0f}%{dd_exp*100:>6.0f}%")
    print(f"{'prefer CHEAPEST (det.)':<26}{sh_cheap:>9.2f}{'—':>7}{'—':>8}{'—':>8}{'—':>8}"
          f"{rt_cheap*100:>7.0f}%{dd_cheap*100:>6.0f}%")

    for lbl, sh_x, rt_x in [("EXPENSIVE", sh_exp, rt_exp), ("CHEAPEST", sh_cheap, rt_cheap)]:
        pctl = (sh_rand < sh_x).mean()    # P(Δ>0) = P(rand below the det. book)
        print(f"\n[{lbl} det. book vs random-tie-break distribution]")
        print(f"  Sharpe {sh_x:+.3f} sits at percentile {pctl*100:.0f} of the random null "
              f"(P(Δ>0)={pctl:.3f}) | Δ ret {(rt_x-rt_rand.mean())*100:+.0f}pp")
        sep = pctl >= 0.95
        print(f"  -> {'REAL (clears p95)' if sep else 'NOT separated'}")

    print("\n  VERDICT: a price tie-break direction is real only if its deterministic book "
          "clears the p95 of the random-tie-break null. Else, among similar-corr candidates, "
          "expensive vs cheap doesn't matter.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
