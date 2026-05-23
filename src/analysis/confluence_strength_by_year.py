"""Per-FY break-strength AND volatility-regime table (read-only).

Operator (2026-05-23, revisiting the strength angle): compare sign strength vs
volatility across years — was FY2021 full of weak fires?

The original confluence_strength_probe.py answered Q1 (strength terciles flat =
uninformative) and gave a brief Q2.  This expands the per-FY view and separates two
things that get conflated:

  A. BREAK DECISIVENESS  = (edge − level) / ATR14   — how decisively the break
     cleared its level relative to volatility (the "strength" of the signal).
     NOTE: the kijun probe found this is NEGATIVELY related to fwd return (a more
     decisive break = more EXTENDED entry), so "stronger years" are not "better years".
  B. VOLATILITY REGIME   = ATR14 / close            — how volatile/wide the bars
     were that year (low = quiet/choppy market).
  C. OUTCOME            = 10-bar fwd return of ALL fires + DR — the regime tell.

4 level-breakout signs (clean break distance): brk_tenkan_hi/brk_kumo_hi/brk_sma/brk_bol.

If FY2021's weakness were a SIGNAL-quality problem, A would be low in FY2021.
If it's a REGIME problem, A is ~normal but C (fwd return) is flat — and B shows the
volatility backdrop.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_strength_by_year
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.analysis.confluence_strength_probe import _SIGNS, _H, _fy_of, _indicators, _strength
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.simulator.cache import DataCache

_FY_ORDER = [f"FY{y}" for y in range(2018, 2026)]


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
    logger.info("{} stocks, {} fires", len(codes), sum(len(v) for v in fires.values()))

    # per-FY accumulators
    strg = defaultdict(list)   # break decisiveness (edge-level)/ATR
    vol = defaultdict(list)    # ATR/close at fire
    fwd = defaultdict(list)    # 10-bar fwd return
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
            sg = _strength(sign, i, L, C, atr, tk, kt, sma20, bb_up)
            if sg is None:
                continue
            a = atr[i]
            strg[fy].append(sg)
            vol[fy].append(a / C[i] if C[i] else np.nan)
            fwd[fy].append(C[i + _H] / C[i] - 1.0)
        if (ci + 1) % 50 == 0:
            logger.info("  {}/{} stocks", ci + 1, len(codes))

    # global weak threshold (bottom tercile of break decisiveness)
    alls = np.concatenate([np.array(strg[fy]) for fy in _FY_ORDER if strg[fy]])
    q33 = np.percentile(alls, 33.33)

    print("\n" + "=" * 92)
    print(f"PER-FY STRENGTH vs VOLATILITY — 4 level signs, {len(alls)} fires, outcome={_H}-bar fwd")
    print("=" * 92)
    print(f"\n  {'FY':<9}{'fires':>7}   | A. break decisiveness (disp/ATR)  | B. vol  | C. outcome")
    print(f"  {'':<9}{'':>7}   |  mean   median  %weak(<{q33:+.2f}) | ATR/px | DR    mean fwd")
    print("  " + "-" * 84)
    for fy in _FY_ORDER:
        if not strg[fy]:
            continue
        S = np.array(strg[fy]); V = np.array(vol[fy]); F = np.array(fwd[fy])
        note = " <- worst" if fy == "FY2021" else (" OOS" if fy == "FY2025" else "")
        print(f"  {fy:<9}{len(S):>7}   | {S.mean():>+5.2f}  {np.median(S):>+6.2f}  "
              f"{100*(S<=q33).mean():>9.1f}% | {100*np.nanmean(V):>5.2f}% | "
              f"{100*(F>0).mean():>4.0f}% {F.mean()*100:>+7.2f}%{note}")
    print("  " + "-" * 84)
    print("\n  Read: if FY2021 'weak fires' were a SIGNAL problem -> col A low in FY2021.")
    print("        if a REGIME problem -> col A ~normal but col C (fwd return) flat; col B = backdrop.")
    print("  (Recall kijun probe: HIGHER decisiveness in A => LOWER fwd return = extension, "
          "so A and C are not expected to move together.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
