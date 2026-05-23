"""Strength probe (read-only): is "break magnitude / volatility" informative, and
does it explain why FY2021 is the worst confluence year?

Operator (2026-05-23): brk_tenkan_hi/brk_sma fires look WEAK — tiny move vs
volatility — yet count toward confluence. Hypothesis: a strength score
= displacement past the broken level / ATR could separate decisive breaks from
marginal whipsaws, and FY2021 (choppiest year) may be full of weak fires.

This DESCRIPTIVE probe (no gate) answers two questions on the 4 level-breakout
bullish signs (the ones with a clean break distance):
  brk_tenkan_hi: (low − tenkan9)/ATR14
  brk_kumo_hi  : (low − kumo_top)/ATR14         kumo_top=max(senkouA[-26],senkouB[-26])
  brk_sma      : (low − SMA20)/ATR14
  brk_bol      : (close − upper_bb(20,2))/ATR14
(str_*/rev_*/chiko_hi excluded — no clean price-vs-level break distance.)

Q1 INFORMATIVE? bin fires by strength tercile → forward DR / mean H-bar return.
Q2 EXPLAINS FY2021? per-FY mean strength + low-strength fraction; is FY2021 weaker?

NOT a ship test — a per-fire descriptive probe. A strength gate would still need a
strategy A/B + paired null + OOS, and must help via a GENERAL mechanism (helps
choppy FYs, neutral in bull) — not just curve-fit FY2021.

OUTCOME (2026-05-23, 46,360 fires of the 4 level signs, FY2018-2025): hypothesis
REFUTED, do NOT build the gate. Q1 strength NOT informative — DR flat across
terciles (weak 53.9% / mid 53.2% / strong 53.9%), mean 10-bar fwd +0.68/+0.67/
+0.76% (within noise). A barely-cleared break (0.09 ATR) does as well as a decisive
one (0.83 ATR). Q2 does NOT explain FY2021 — FY2021 fires only marginally weaker
(mean strength 0.38 vs bull ~0.43; %low-strength 36% vs 33%), but the mean FWD
return of ALL fires was ~flat in FY2021 (+0.16%) vs ~+2% in bull FYs (FY2020 +1.97,
FY2025 +2.02). So FY2021's badness is REGIME/BETA (the market was choppy/down — every
fire's forward return was low), NOT weak-strength fires. A strength gate would be a
no-op (like trend_score floor) and any FY2021 'fix' from it = overfit. Probe-first
abort gate (Q1 uninformative) → STOP, no A/B. See project_confluence_strength_probe.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_strength_probe
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.simulator.cache import DataCache

_SIGNS = ("brk_tenkan_hi", "brk_kumo_hi", "brk_sma", "brk_bol")
_H = 10   # forward horizon (bars) for the outcome proxy
_FY_BOUNDS = [(f"FY{y}", datetime.date(y, 4, 1), datetime.date(y + 1, 3, 31))
              for y in range(2018, 2026)]


def _fy_of(d):
    for lbl, a, b in _FY_BOUNDS:
        if a <= d <= b:
            return lbl
    return None


def _indicators(cache):
    """Per-date arrays: idx, low, close, ATR14, tenkan, kumo_top, sma20, bb_upper."""
    seen, dts, H, L, C, V = set(), [], [], [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); H.append(b.high); L.append(b.low); C.append(b.close)
    order = np.argsort(dts)
    dts = [dts[i] for i in order]
    H = np.array([H[i] for i in order]); L = np.array([L[i] for i in order])
    C = np.array([C[i] for i in order])
    n = len(dts)
    # ATR14 (Wilder-ish: simple rolling mean of TR)
    prevC = np.concatenate([[C[0]], C[:-1]])
    tr = np.maximum(H - L, np.maximum(np.abs(H - prevC), np.abs(L - prevC)))
    atr = pd.Series(tr).rolling(14, min_periods=7).mean().to_numpy()
    # ichimoku
    ichi = calc_ichimoku(list(H), list(L), list(C), tenkan_period=9)
    tk = np.asarray(ichi["tenkan"], dtype=float)
    sa = np.asarray(ichi["senkou_a"], dtype=float); sb = np.asarray(ichi["senkou_b"], dtype=float)
    disp = ichi["displacement"]
    kt = np.full(n, np.nan)
    for i in range(disp, n):
        kt[i] = max(sa[i - disp], sb[i - disp])
    cs = pd.Series(C)
    sma20 = cs.rolling(20, min_periods=10).mean().to_numpy()
    std20 = cs.rolling(20, min_periods=10).std().to_numpy()
    bb_up = sma20 + 2.0 * std20
    return {d: i for i, d in enumerate(dts)}, L, C, atr, tk, kt, sma20, bb_up


def _strength(sign, i, L, C, atr, tk, kt, sma20, bb_up):
    a = atr[i]
    if not (a and a == a and a > 0):
        return None
    if sign == "brk_tenkan_hi":
        lvl, edge = tk[i], L[i]
    elif sign == "brk_kumo_hi":
        lvl, edge = kt[i], L[i]
    elif sign == "brk_sma":
        lvl, edge = sma20[i], L[i]
    elif sign == "brk_bol":
        lvl, edge = bb_up[i], C[i]
    else:
        return None
    if not (lvl == lvl):
        return None
    return (edge - lvl) / a


def run() -> None:
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_SIGNS)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    codes = sorted(fires)
    logger.info("{} stocks with {} fires", len(codes), sum(len(v) for v in fires.values()))

    recs = []   # (sign, fy, strength, fwd_ret)
    ss = datetime.datetime(2017, 4, 1, tzinfo=datetime.timezone.utc)
    se = datetime.datetime(2026, 5, 23, tzinfo=datetime.timezone.utc)
    for ci, code in enumerate(codes):
        with get_session() as s:
            c = DataCache(code, "1d"); c.load(s, ss, se)
        if not c.bars:
            continue
        idx, L, C, atr, tk, kt, sma20, bb_up = _indicators(c)
        n = len(C)
        for sign, d in fires[code]:
            fy = _fy_of(d)
            if fy is None or d not in idx:
                continue
            i = idx[d]
            if i + _H >= n:
                continue
            strg = _strength(sign, i, L, C, atr, tk, kt, sma20, bb_up)
            if strg is None:
                continue
            fwd = C[i + _H] / C[i] - 1.0
            recs.append((sign, fy, strg, fwd))
        if (ci + 1) % 50 == 0:
            logger.info("  {}/{} stocks", ci + 1, len(codes))

    arr_s = np.array([r[2] for r in recs])
    arr_f = np.array([r[3] for r in recs])
    print("\n" + "=" * 78)
    print(f"STRENGTH PROBE — 4 level-breakout signs, {len(recs)} fires, "
          f"strength=(edge−level)/ATR14, outcome={_H}-bar fwd return")
    print("=" * 78)

    # Q1: informative? global strength terciles
    q1, q2 = np.percentile(arr_s, [33.33, 66.67])
    print(f"\nQ1 INFORMATIVE? strength terciles cut at {q1:+.2f} / {q2:+.2f} ATR")
    print(f"  {'bucket':<16}{'n':>7}{'avg strength':>14}{'DR(fwd>0)':>11}{'mean fwd':>10}")
    for lab, lo, hi in [("weak (T1)", -1e9, q1), ("mid (T2)", q1, q2), ("strong (T3)", q2, 1e9)]:
        m = (arr_s > lo) & (arr_s <= hi)
        if not m.any():
            continue
        print(f"  {lab:<16}{m.sum():>7}{arr_s[m].mean():>14.2f}"
              f"{100*(arr_f[m]>0).mean():>10.1f}%{arr_f[m].mean()*100:>9.2f}%")

    # per-sign mean strength
    print(f"\n  per-sign mean strength (ATR units):")
    for sg in _SIGNS:
        sv = np.array([r[2] for r in recs if r[0] == sg])
        if sv.size:
            print(f"    {sg:<16} n={sv.size:>6}  mean {sv.mean():+.2f}  median {np.median(sv):+.2f}")

    # Q2: FY2021 weaker? per-FY
    print(f"\nQ2 EXPLAINS FY2021? per-FY strength (low-strength = bottom global tercile, ≤{q1:+.2f})")
    print(f"  {'FY':<9}{'fires':>7}{'mean strength':>15}{'% low-strength':>16}{'mean fwd':>10}")
    for lbl, _, _ in _FY_BOUNDS:
        sv = np.array([r[2] for r in recs if r[1] == lbl])
        fv = np.array([r[3] for r in recs if r[1] == lbl])
        if not sv.size:
            continue
        note = "  <-- worst confluence FY" if lbl == "FY2021" else ("  OOS" if lbl == "FY2025" else "")
        print(f"  {lbl:<9}{sv.size:>7}{sv.mean():>15.2f}{100*(sv<=q1).mean():>15.1f}%"
              f"{fv.mean()*100:>9.2f}%{note}")
    print("\n  (Q1 yes if strong tercile DR/fwd > weak tercile by a clear margin. "
          "Q2 yes if FY2021 mean strength is lower / %low-strength higher than bull FYs.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
