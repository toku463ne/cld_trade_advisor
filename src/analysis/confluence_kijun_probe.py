"""Tenkan-vs-Kijun break-level probe (read-only).

Operator (2026-05-23): brk_tenkan_hi is in ~90% of confluence fires — the 9-period
tenkan is so fast it's nearly always being tagged, so "broke above tenkan" barely
discriminates ("ambiguous").  Proposal: use the slower 26-period KIJUN base line as
the break level instead.

Probe-first, BEFORE implementing a brk_kijun_hi sign or rebenching.  For every
existing brk_tenkan_hi fire, compute price position vs BOTH lines and split:
  - above_kijun  : low >= kijun26  (the subset a kijun requirement would KEEP)
  - below_kijun  : tenkan < low < kijun  (tenkan-only fires kijun would DROP)
Compare 10-bar forward returns + DR.

Logic mirrors confluence_strength_probe.py (which REFUTED displacement-past-tenkan):
this tests a DIFFERENT axis — not "how far past tenkan" but "a different, slower
LEVEL."  The strength probe even noted tenkan is "deliberately fast/noisy", so the
kijun level is the natural untested follow-up.

Q1 INFORMATIVE? above-kijun vs below-kijun forward DR / mean return.
Q2 CADENCE: what fraction of tenkan fires survive a kijun requirement.
Q3 FY2021: does the above-kijun subset rescue the worst confluence year, or is it
   flat (regime, not level) like the strength probe found?

ABORT GATE: if above/below-kijun DR are within ~1.5pp (no separation), STOP — a
kijun sign is a no-op like the strength gate; don't build it or rebench.  If a
clear lift, the NEXT step (operator-authorized) is implement brk_kijun_hi + a
confluence A/B + paired fill-order null + FY2025 OOS.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_kijun_probe
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.simulator.cache import DataCache

_SIGN = "brk_tenkan_hi"
_H = 10
_FY_BOUNDS = [(f"FY{y}", datetime.date(y, 4, 1), datetime.date(y + 1, 3, 31))
              for y in range(2018, 2026)]


def _fy_of(d):
    for lbl, a, b in _FY_BOUNDS:
        if a <= d <= b:
            return lbl
    return None


def _indicators(cache):
    seen, dts, H, L, C = set(), [], [], [], []
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
    prevC = np.concatenate([[C[0]], C[:-1]])
    tr = np.maximum(H - L, np.maximum(np.abs(H - prevC), np.abs(L - prevC)))
    atr = pd.Series(tr).rolling(14, min_periods=7).mean().to_numpy()
    ichi = calc_ichimoku(list(H), list(L), list(C), tenkan_period=9, kijun_period=26)
    tk = np.asarray(ichi["tenkan"], dtype=float)
    kj = np.asarray(ichi["kijun"], dtype=float)
    return {d: i for i, d in enumerate(dts)}, L, C, atr, tk, kj


def run() -> None:
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkEvent.stock_code, SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type == _SIGN)).all()
    fires = defaultdict(list)
    for st, fa in rows:
        fires[st].append(fa.date() if hasattr(fa, "date") else fa)
    codes = sorted(fires)
    logger.info("{} stocks, {} {} fires", len(codes), sum(len(v) for v in fires.values()), _SIGN)

    # recs: (fy, above_kijun, tk_disp_atr, kj_disp_atr, fwd)
    recs = []
    ss = datetime.datetime(2017, 4, 1, tzinfo=datetime.timezone.utc)
    se = datetime.datetime(2026, 5, 23, tzinfo=datetime.timezone.utc)
    for ci, code in enumerate(codes):
        with get_session() as s:
            c = DataCache(code, "1d"); c.load(s, ss, se)
        if not c.bars:
            continue
        idx, L, C, atr, tk, kj = _indicators(c)
        n = len(C)
        for d in fires[code]:
            fy = _fy_of(d)
            if fy is None or d not in idx:
                continue
            i = idx[d]
            if i + _H >= n:
                continue
            a = atr[i]
            if not (a and a == a and a > 0) or not (tk[i] == tk[i]) or not (kj[i] == kj[i]):
                continue
            fwd = C[i + _H] / C[i] - 1.0
            recs.append((fy, L[i] >= kj[i], (L[i] - tk[i]) / a, (L[i] - kj[i]) / a, fwd))
        if (ci + 1) % 50 == 0:
            logger.info("  {}/{} stocks", ci + 1, len(codes))

    fy_arr = [r[0] for r in recs]
    ak = np.array([r[1] for r in recs])
    fwd = np.array([r[4] for r in recs])
    print("\n" + "=" * 84)
    print(f"KIJUN PROBE — {len(recs)} {_SIGN} fires, outcome={_H}-bar fwd return, "
          "split by whether price is also above kijun26")
    print("=" * 84)

    # Q1 + Q2
    print(f"\nQ1/Q2 — split brk_tenkan_hi fires by kijun position:")
    print(f"  {'subset':<26}{'n':>8}{'share':>8}{'DR(fwd>0)':>11}{'mean fwd':>10}")
    for lab, m in [("above kijun (KEEP)", ak), ("below kijun (tenkan-only, DROP)", ~ak)]:
        if m.any():
            print(f"  {lab:<26}{m.sum():>8}{100*m.mean():>7.1f}%"
                  f"{100*(fwd[m]>0).mean():>10.1f}%{fwd[m].mean()*100:>9.2f}%")
    sep = 100*(fwd[ak] > 0).mean() - 100*(fwd[~ak] > 0).mean()
    print(f"\n  DR separation (above − below): {sep:+.1f}pp   "
          f"(abort if |sep| < ~1.5pp → kijun = no-op)")

    # Q1b: kijun displacement terciles (is the kijun LEVEL informative by magnitude?)
    kjd = np.array([r[3] for r in recs])
    q1, q2 = np.percentile(kjd, [33.33, 66.67])
    print(f"\nQ1b — kijun-displacement terciles ((low−kijun)/ATR), cut {q1:+.2f}/{q2:+.2f}:")
    print(f"  {'bucket':<14}{'n':>8}{'avg disp':>10}{'DR':>8}{'mean fwd':>10}")
    for lab, lo, hi in [("below kijun", -1e9, 0.0), ("0..mid", 0.0, q2), ("strong>kijun", q2, 1e9)]:
        m = (kjd > lo) & (kjd <= hi)
        if m.any():
            print(f"  {lab:<14}{m.sum():>8}{kjd[m].mean():>10.2f}"
                  f"{100*(fwd[m]>0).mean():>7.1f}%{fwd[m].mean()*100:>9.2f}%")

    # Q3 per FY
    print(f"\nQ3 — per-FY: does above-kijun rescue FY2021? (DR | mean fwd)")
    print(f"  {'FY':<9}{'n':>7}{'%above kj':>11}   above-kijun        below-kijun")
    for lbl, _, _ in _FY_BOUNDS:
        idxs = [k for k, f in enumerate(fy_arr) if f == lbl]
        if not idxs:
            continue
        a_m = np.array([recs[k][1] for k in idxs])
        f_m = np.array([recs[k][4] for k in idxs])
        note = "  <-- worst" if lbl == "FY2021" else ("  OOS" if lbl == "FY2025" else "")
        above = f"{100*(f_m[a_m]>0).mean():.0f}% {f_m[a_m].mean()*100:+.2f}%" if a_m.any() else "—"
        below = f"{100*(f_m[~a_m]>0).mean():.0f}% {f_m[~a_m].mean()*100:+.2f}%" if (~a_m).any() else "—"
        print(f"  {lbl:<9}{len(idxs):>7}{100*a_m.mean():>10.1f}%   {above:<18} {below}{note}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
