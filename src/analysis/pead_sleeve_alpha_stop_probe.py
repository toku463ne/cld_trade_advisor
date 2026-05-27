"""PEAD sleeve exit probe — does a catastrophic idiosyncratic (alpha) stop beat a pure
60-bar time-stop on cohort up-revision longs? (read-only)

Question (operator, 2026-05-27, design-proposal §3a): the sleeve's default exit is a pure
60-bar time-stop (no TP/SL), because a *price-level* stop on a beta≈1 name fires mostly on
MARKET moves = the pro-cyclical timing confound the §4 gate strips out. The only coherent
risk stop is a CATASTROPHIC, IDIOSYNCRATIC one: exit when the stock underperforms β·index
(its β-stripped / alpha CAR) by a large pre-set margin — the real PEAD failure mode where a
later profit-warning reverses the up-revision. That stop is a NEW pre-committed parameter,
so it must earn its place with data, not intuition.

This probe reconstructs, for every N225-cohort UP-revision event (ΔFEPS>0), the RUNNING
β-stripped CAR path (alpha vs TOPIX) across its 60-bar window, then for a sweep of stop
thresholds θ reports:
  - hit rate: fraction of events whose alpha path ever crosses ≤ θ inside the window
  - of the hits: HELPED (final 60-bar alpha < the stopped value → stop avoided worse) vs
    WHIPSAW (final alpha > stopped value → name recovered, stop was premature)
  - net effect on MEAN realized alpha: book with the alpha-stop − pure-time-stop book
  - left-tail effect: mean of the worst-decile final alphas, stopped vs unstopped
Decision rule (pre-stated): pure time-stop stays the default UNLESS a stop materially lifts
mean alpha AND the worst-decile tail without an excessive whipsaw rate. Diagnostic only.

Reuses the unit-tested pure logic in pead_forecast_revision; same loading as
pead_updrift_vs_n225. β-strip vs TOPIX (= signal 1's alpha), close-to-close adj (no
open-fill), entry = the signal's tradable entry day.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_sleeve_alpha_stop_probe
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict

import numpy as np
from loguru import logger

from src.analysis.pead_forecast_revision import (
    Disclosure, beta, doc_basis, pair_same_fy_revisions, revision_surprise,
    tradable_entry_day,
)

_BETA_WIN = 60
_H = 60
_THRESHOLDS = (-0.08, -0.10, -0.12, -0.15, -0.20)   # catastrophic alpha-drawdown stops


def _alpha_path(srow: np.ndarray, mrow: np.ndarray, ei: int, b: float,
                h: int) -> np.ndarray | None:
    """Running β-stripped CAR (stock − β·market) for steps 1..h from entry day ei.

    Base = close at ei (entry day). Returns an array of length h (step k = bars-after-entry)
    with np.nan where a bar is missing/unusable. None if the base or endpoint is unusable.
    """
    last = ei + h
    if ei < 0 or last >= len(srow) or last >= len(mrow):
        return None
    s0, m0 = srow[ei], mrow[ei]
    if not (s0 > 0) or not (m0 > 0):
        return None
    out = np.full(h, np.nan, dtype=np.float64)
    for k in range(1, h + 1):
        sk, mk = srow[ei + k], mrow[ei + k]
        if sk > 0 and mk > 0:
            out[k - 1] = (sk / s0 - 1.0) - b * (mk / m0 - 1.0)
    return out


def _stopped_value(path: np.ndarray, theta: float) -> tuple[float, bool]:
    """Realized alpha under a catastrophic stop at θ: the alpha at the FIRST finite bar that
    crosses ≤ θ (≈θ, executed at that bar's close); else the final-bar alpha (time-stop).
    Returns (realized_alpha, hit?)."""
    for k in range(len(path)):
        v = path[k]
        if np.isfinite(v) and v <= theta:
            return float(v), True
    # no hit → time-stop at the last finite bar
    finite = path[np.isfinite(path)]
    return (float(finite[-1]) if finite.size else float("nan")), False


def run() -> None:  # noqa: C901 — single linear assembly + reporting
    from sqlalchemy import select

    from src.data.db import get_session
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqStatement, JqTopix
    from src.data.models import Ohlcv1d

    with get_session() as s:
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)
        ).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        cohort = {c for (c,) in s.execute(select(Ohlcv1d.stock_code).distinct())}
        codes = [c for (c,) in s.execute(select(JqDailyQuote.code).distinct()
                                         .order_by(JqDailyQuote.code))]
        cal = [d for d, _ in topix]
        col_of = {d: i for i, d in enumerate(cal)}
        row_of = {c: i for i, c in enumerate(codes)}
        topix_arr = np.array([float(c) for _, c in topix], dtype=np.float64)
        arr = np.full((len(codes), len(cal)), np.nan, dtype=np.float32)
        stream = s.connection().execution_options(stream_results=True, yield_per=200_000)
        for code, d, ac in stream.execute(
                select(JqDailyQuote.code, JqDailyQuote.date, JqDailyQuote.adj_close)):
            ci = col_of.get(d)
            ri = row_of.get(code)
            if ci is not None and ri is not None and ac is not None:
                arr[ri, ci] = float(ac)

    if not stmts or not topix:
        logger.warning("jq_* not populated (statements={}, topix={}).", len(stmts), len(topix))
        return
    logger.info("loaded {} statements, {} cal days, {} priced codes",
                len(stmts), len(cal), len(codes))

    raw: dict[str, list[tuple]] = defaultdict(list)
    for code, dd, dt, fy, feps, tod in stmts:
        raw[code].append((dd, dt, fy, feps, tod))
    by_code: dict[str, list[Disclosure]] = {}
    for code, rows in raw.items():
        fin = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = fin.most_common(1)[0][0] if fin else None
        by_code[code] = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                         for dd, dt, fy, feps, tod in rows]

    # cohort up-revision events → final alpha, min alpha, stopped value per θ
    finals: list[float] = []
    mins: list[float] = []
    stopped: dict[float, list[float]] = {th: [] for th in _THRESHOLDS}
    hit: dict[float, list[bool]] = {th: [] for th in _THRESHOLDS}
    for code, discs in by_code.items():
        ri = row_of.get(code)
        if ri is None or to_yf_code(code) not in cohort:
            continue
        srow = arr[ri]
        for prev, curr in pair_same_fy_revisions(discs):
            if curr.fy_end is None:
                continue
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is None:
                continue
            ei = col_of[entry]
            if ei < _BETA_WIN + 1 or ei + _H >= len(cal):
                continue
            price = srow[ei - 1]
            if not (price > 0):
                continue
            sp = revision_surprise(prev.forecast_eps, curr.forecast_eps, float(price))
            if sp is None or sp <= 0:                  # UP-revisions only
                continue
            sw, mw = srow[ei - _BETA_WIN - 1:ei], topix_arr[ei - _BETA_WIN - 1:ei]
            b = beta(sw[1:] / sw[:-1] - 1.0, mw[1:] / mw[:-1] - 1.0)
            if b is None:
                continue
            path = _alpha_path(srow, topix_arr, ei, b, _H)
            if path is None:
                continue
            fin_vals = path[np.isfinite(path)]
            if fin_vals.size < _H // 2:                # need a usable window
                continue
            final = float(fin_vals[-1])
            if math.isnan(final):
                continue
            finals.append(final)
            mins.append(float(np.nanmin(path)))
            for th in _THRESHOLDS:
                sv, h = _stopped_value(path, th)
                stopped[th].append(sv)
                hit[th].append(h)

    n = len(finals)
    if n < 100:
        logger.warning("too few cohort up-events ({}) — cannot report", n)
        return
    fa = np.array(finals)
    ma = np.array(mins)
    dec = np.percentile(fa, 10)                         # worst-decile cutoff
    worst_mask = fa <= dec

    print("\n" + "=" * 98)
    print("PEAD SLEEVE EXIT — CATASTROPHIC ALPHA-STOP vs PURE 60-bar TIME-STOP (N225 cohort UP)")
    print("=" * 98)
    print(f"cohort up-revision events: n={n}  (β-stripped vs TOPIX, H={_H}, close-to-close)")
    print(f"\nPure time-stop (no stop) baseline:")
    print(f"  mean final alpha   = {fa.mean()*100:+.2f}%   median = {np.median(fa)*100:+.2f}%")
    print(f"  worst-decile final = {fa[worst_mask].mean()*100:+.2f}%  (cutoff {dec*100:+.2f}%)")
    print(f"  min-alpha (deepest intra-window) distribution:")
    for q, lab in [(50, "median"), (25, "p25"), (10, "p10"), (5, "p5"), (1, "p1")]:
        print(f"      {lab:<7} {np.percentile(ma, q)*100:+.2f}%")
    frac_le = lambda t: float((ma <= t).mean())         # noqa: E731
    print("  fraction of events whose alpha path EVER reaches ≤ θ (the addressable tail):")
    for th in _THRESHOLDS:
        print(f"      θ={th*100:>5.0f}%  {frac_le(th)*100:5.1f}%")

    print("\nALPHA-STOP SWEEP (does stopping at θ beat holding to bar 60?):")
    print(f"  {'θ':>6}{'nHit':>7}{'hit%':>7}{'helped':>8}{'whipsaw':>9}{'whip%':>7}"
          f"{'meanα_stop':>12}{'Δmeanα':>9}{'wdec_stop':>11}{'Δwdec':>8}")
    for th in _THRESHOLDS:
        sv = np.array(stopped[th])
        hh = np.array(hit[th])
        nhit = int(hh.sum())
        # among hits: did the stop avoid a worse final, or whipsaw a recovery?
        if nhit:
            fh = fa[hh]
            svh = sv[hh]
            helped = int((fh < svh - 1e-12).sum())      # final worse than stopped → stop helped
            whip = int((fh > svh + 1e-12).sum())        # final better than stopped → whipsaw
            whip_pct = whip / nhit * 100
        else:
            helped = whip = 0
            whip_pct = float("nan")
        d_mean = (sv.mean() - fa.mean())
        wdec_stop = sv[worst_mask].mean()
        d_wdec = wdec_stop - fa[worst_mask].mean()
        print(f"  {th*100:>5.0f}%{nhit:>7}{nhit/n*100:>6.1f}%{helped:>8}{whip:>9}"
              f"{whip_pct:>6.1f}%{sv.mean()*100:>11.2f}%{d_mean*100:>8.2f}%"
              f"{wdec_stop*100:>10.2f}%{d_wdec*100:>7.2f}%")

    print("\n" + "-" * 98)
    print("READ: Δmeanα = alpha-stop book − pure-time-stop book (mean realized 60-bar alpha).")
    print("  A stop EARNS its parameter only if Δmeanα > 0 AND Δwdec > 0 (lifts the worst")
    print("  decile) with whip% not dominating. If every θ shows Δmeanα ≤ 0 or whip% high,")
    print("  the up-revision drift recovers through transient alpha dips → PURE TIME-STOP,")
    print("  no SL (the §3a default). Stops here are idiosyncratic (β-stripped), so they are")
    print("  NOT the market-timing confound — a negative result is a clean 'no stop needed'.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
