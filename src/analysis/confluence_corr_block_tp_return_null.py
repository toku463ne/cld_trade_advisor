"""Corr-BLOCK primary + max-yen-at-TP tie-break — paired fill-order null (budget book).

Operator (2026-06-12): re-examine confluence slot selection.  Order candidates by
correlation-to-holdings into BLOCKS (lowest-corr block first), and WITHIN the chosen
block pick the candidate with the largest *gross yen gain if it hits TP*:

    yen_at_tp(c) = (preview_TP(c) - entry_price) * lots * 100

Blocks (operator's, overlapping): B1=[0,0.4], B2=[0.3,0.6], B3=[0.5,1.0], always take
the LOWEST non-empty block.  Under "lowest non-empty block" the overlap collapses to
non-overlapping cuts at 0.4 / 0.6 (a name at 0.35 is always taken via B1 first), so
block(c) = 0 if corr<=0.4, 1 if corr<=0.6, else 2.

NOTE on the key: with the equal-budget book, lots*100*price ~= slot_budget, so
yen_at_tp ~= slot_budget * (TP-entry)/entry = slot_budget * pct-distance-to-TP.  The
unit count washes out; this is mechanically "max %-distance-to-TP within the block"
(the volatility/leg-size axis) scaled by a fixed budget.  Prior: strength probe found
realized fwd return FLAT in target size; price/exposure tie-break was a Sharpe wash
(p70, never separated).  This arm tests it CONFINED to a within-diversification-block
tie-break, which has not been run before.

All arms share the SAME primary (block 0/1/2 ascending).  WITHIN a block the order is:
    RANDOM : seeded shuffle              (control, K shuffles)
    MAXTP  : highest yen_at_tp first     (operator's rule; deterministic)
    MINTP  : lowest  yen_at_tp first     (opposite direction; deterministic)
Budget ¥2M book, 6 slots.  REAL only if MAXTP's deterministic Sharpe clears the p95 of
the random-tie-break null (i.e. P(Δ>0) >= 0.95).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_corr_block_tp_return_null
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
from src.portfolio.sizing import LOT_SHARES, position_weight, recommended_lots
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_CORR_WIN = 20


def _block(corr: float) -> int:
    """Lowest non-empty block index for corr-to-holdings (B1<=0.4, B2<=0.6, B3 else)."""
    if corr <= 0.4:
        return 0
    if corr <= 0.6:
        return 1
    return 2


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


def _make_selector(returns, didx, mode, rng, tp_yen, corr_cache):
    """corr_cache: shared {(today, a, b): |corr|} memo across all seeds/arms of an FY.

    mode: 'maxtp' (largest yen_at_tp first), 'mintp' (smallest first), 'rand' (shuffle).
    """
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

    def tie(c):
        if mode == "maxtp":
            return (-tp_yen[id(c)], c.stock_code)
        if mode == "mintp":
            return (tp_yen[id(c)], c.stock_code)
        return (rng.random(),)

    def selector(today, cands, open_pos):
        held = list({p.candidate.stock_code for p in open_pos})
        if not held:
            scored = [(0,) + tie(c) + (c,) for c in cands]
            scored.sort(key=lambda t: t[:-1])
            return [t[-1] for t in scored]

        scored = []
        for c in cands:
            best = max((pair_corr(c.stock_code, h, today) for h in held), default=0.0)
            scored.append((_block(best),) + tie(c) + (c,))
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
    st_rand = [[] for _ in range(_K)]
    st_max: list[float] = []
    st_min: list[float] = []
    fy_rows: list[tuple] = []   # per-FY: (label, n225_ret, randSh[], randRet[], randDD[],
                                #          maxSh, maxRet, maxDD, minSh, minRet)

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
        tp_yen, pool_aff = {}, []
        for c in pool:
            lots = recommended_lots(_BUDGET, float(c.entry_price), _SLOTS)
            if lots <= 0:
                continue
            tp, _sl = cbt._EXIT_RULE.preview_levels(float(c.entry_price), c.zs_history)
            tp_yen[id(c)] = (tp - float(c.entry_price)) * lots * LOT_SHARES
            pool_aff.append(c)

        # N225 FY return (bull/bear tag), from the FY calendar close-to-close
        _ndts, _ncmap = _closes(n225)
        n225_ret = (_ncmap[cal[-1]] / _ncmap[cal[0]] - 1.0) if len(cal) > 1 else 0.0

        corr_cache: dict = {}
        # MAXTP / MINTP deterministic → run once
        sel_max = _make_selector(returns, didx, "maxtp", random.Random(0), tp_yen, corr_cache)
        fy_max = _budget_returns(pool_aff, caches, cfg, stock_dts, cal, sel_max)[1:]
        st_max += fy_max
        sel_min = _make_selector(returns, didx, "mintp", random.Random(0), tp_yen, corr_cache)
        fy_min = _budget_returns(pool_aff, caches, cfg, stock_dts, cal, sel_min)[1:]
        st_min += fy_min
        # RAND control: K seeded shuffles
        fy_rand_sh, fy_rand_rt, fy_rand_dd = [], [], []
        for k in range(_K):
            sel = _make_selector(returns, didx, "rand", random.Random(k), tp_yen, corr_cache)
            fy_r = _budget_returns(pool_aff, caches, cfg, stock_dts, cal, sel)[1:]
            st_rand[k] += fy_r
            fy_rand_sh.append(_sharpe(fy_r))
            fy_rand_rt.append(_metrics(fy_r)[0])
            fy_rand_dd.append(_metrics(fy_r)[2])
        fy_rows.append((
            cfg.label, n225_ret,
            np.array(fy_rand_sh), np.array(fy_rand_rt), np.array(fy_rand_dd),
            _sharpe(fy_max), _metrics(fy_max)[0], _metrics(fy_max)[2],
            _sharpe(fy_min), _metrics(fy_min)[0],
        ))
        logger.info("  {} done ({} affordable cands, {} cached corr pairs)",
                    cfg.label, len(pool_aff), len(corr_cache))

    sh_rand = np.array([_sharpe(st_rand[k]) for k in range(_K)])
    rt_rand = np.array([_metrics(st_rand[k])[0] for k in range(_K)])
    dd_rand = np.array([_metrics(st_rand[k])[2] for k in range(_K)])
    sh_max, rt_max, dd_max = _sharpe(st_max), _metrics(st_max)[0], _metrics(st_max)[2]
    sh_min, rt_min, dd_min = _sharpe(st_min), _metrics(st_min)[0], _metrics(st_min)[2]

    print("\n" + "=" * 86)
    print(f"CORR-BLOCK PRIMARY + MAX-YEN-AT-TP TIE-BREAK — {_K}-shuffle RAND control, "
          f"¥{_BUDGET:,} book, blocks 0.4/0.6")
    print("=" * 86)
    print(f"\n{'arm (tie-break)':<26}{'Sharpe':>9}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret':>8}{'DD':>7}")
    print(f"{'random (control)':<26}{sh_rand.mean():>9.2f}{sh_rand.std():>7.2f}"
          f"{np.percentile(sh_rand,5):>8.2f}{np.percentile(sh_rand,50):>8.2f}"
          f"{np.percentile(sh_rand,95):>8.2f}{rt_rand.mean()*100:>7.0f}%{dd_rand.mean()*100:>6.0f}%")
    print(f"{'MAX yen-at-TP (det.)':<26}{sh_max:>9.2f}{'—':>7}{'—':>8}{'—':>8}{'—':>8}"
          f"{rt_max*100:>7.0f}%{dd_max*100:>6.0f}%")
    print(f"{'MIN yen-at-TP (det.)':<26}{sh_min:>9.2f}{'—':>7}{'—':>8}{'—':>8}{'—':>8}"
          f"{rt_min*100:>7.0f}%{dd_min*100:>6.0f}%")

    # ── per-FY breakdown: is the MAXTP edge consistent or bull-loaded? ──
    print("\n" + "-" * 98)
    print("PER-FY: MAXTP positioned in EACH FY's own random null  "
          "(pctile = P(Δret>0) within FY; tag by N225 FY return)")
    print("-" * 98)
    print(f"{'FY':<9}{'N225':>7}{'tag':>6}{'randSh':>8}{'maxSh':>7}{'minSh':>7}"
          f"{'randRet':>9}{'maxRet':>9}{'Δret':>8}{'pctile':>8}{'randDD':>8}{'maxDD':>8}")
    bull_d, bear_d = [], []
    for (lbl, n2r, rsh, rrt, rdd, msh, mrt, mdd, nsh, nrt) in fy_rows:
        tag = "bull" if n2r > 0 else "bear"
        dret = (mrt - rrt.mean()) * 100
        pctl = (rrt < mrt).mean() * 100        # MAXTP return vs FY random-return null
        (bull_d if n2r > 0 else bear_d).append(dret)
        print(f"{lbl:<9}{n2r*100:>6.0f}%{tag:>6}{rsh.mean():>8.2f}{msh:>7.2f}{nsh:>7.2f}"
              f"{rrt.mean()*100:>8.0f}%{mrt*100:>8.0f}%{dret:>+7.0f}{pctl:>7.0f}"
              f"{rdd.mean()*100:>7.0f}%{mdd*100:>7.0f}%")
    if bull_d:
        print(f"\n  bull-FY mean Δret (MAXTP − randmean): {np.mean(bull_d):+.0f}pp  "
              f"(n={len(bull_d)}, each: {[round(x) for x in bull_d]})")
    if bear_d:
        print(f"  bear-FY mean Δret (MAXTP − randmean): {np.mean(bear_d):+.0f}pp  "
              f"(n={len(bear_d)}, each: {[round(x) for x in bear_d]})")
    print("  -> beta-loaded if Δret concentrated in bull FYs (bear Δret ≈ 0 or negative); "
          "robust if positive in both.")

    print()
    for lbl, sh_x, rt_x in [("MAX-TP", sh_max, rt_max), ("MIN-TP", sh_min, rt_min)]:
        pctl = (sh_rand < sh_x).mean()
        print(f"\n[{lbl} det. book vs random-tie-break distribution]")
        print(f"  Sharpe {sh_x:+.3f} sits at percentile {pctl*100:.0f} of the random null "
              f"(P(Δ>0)={pctl:.3f}) | Δ ret {(rt_x-rt_rand.mean())*100:+.0f}pp")
        print(f"  -> {'REAL (clears p95)' if pctl >= 0.95 else 'NOT separated'}")

    print("\n  VERDICT: max-yen-at-TP within the lowest corr block is real only if its "
          "deterministic book clears the p95 of the random-tie-break null.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
