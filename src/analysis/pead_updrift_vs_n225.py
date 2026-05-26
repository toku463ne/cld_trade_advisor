"""PEAD probe — absolute (β-inclusive) 60-bar return of N225-cohort UP-revision longs
vs the N225 index, per fiscal year, with an alpha/beta decomposition (read-only).

Question this answers (operator, 2026-05-26): in confluence's *worst* years (down N225),
were PEAD up-revision names LESS hurt, EQUALLY hurt, or MORE hurt than the broader N225?

Every prior PEAD analysis measured a cross-sectional *spread* (up − down) or a *β-stripped*
CAR — both net out the market move, so none of them report the absolute, beta-inclusive
return of an up-revision long in a down year, which is exactly the mitigation claim. This
probe reports, per FY, for the N225 deployment cohort, group=up (ΔFEPS>0):
  - mean absolute 60-bar stock return (adj-close ei→ei+60, the same entry the signal uses)
  - mean N225 index return over each event's *own* 60-bar window (event-matched benchmark)
  - abs − N225  ........... the "less / equally / more hurt" answer (>0 = cushioned)
  - mean alpha (β-stripped vs TOPIX, = pipeline c60) and mean β (trailing 60)
The cohort down-group is shown alongside for the within-year relative-cushion contrast.

NOT a gate / not a decision — a diagnostic to set the sleeve thesis's downside threshold
against a real distribution. Reuses the unit-tested pure logic in pead_forecast_revision.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_updrift_vs_n225
"""
from __future__ import annotations

import datetime
import math
import sys
from collections import Counter, defaultdict

import numpy as np
from loguru import logger

from src.analysis.pead_forecast_revision import (
    Disclosure, beta, beta_stripped_car, doc_basis, pair_same_fy_revisions,
    revision_surprise, tradable_entry_day,
)

_BETA_WIN = 60
_H = 60


def _abs_ret(closes: np.ndarray, ei: int, h: int) -> float | None:
    """Plain (β-inclusive) close-to-close return over `h` bars from ei. None if unusable."""
    last = ei + h
    if ei < 0 or last >= len(closes):
        return None
    a, b = closes[ei], closes[last]
    if not (a > 0) or not (b > 0):
        return None
    return float(b / a - 1.0)


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
        # N225 index, aligned to the TOPIX (TSE) calendar by date.
        n225_rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.close_price).where(Ohlcv1d.stock_code == "^N225")
        ).all()

        cal = [d for d, _ in topix]
        col_of = {d: i for i, d in enumerate(cal)}
        row_of = {c: i for i, c in enumerate(codes)}
        topix_arr = np.array([float(c) for _, c in topix], dtype=np.float64)
        n225_arr = np.full(len(cal), np.nan, dtype=np.float64)
        for ts, close in n225_rows:
            ci = col_of.get(ts.date())
            if ci is not None and close is not None:
                n225_arr[ci] = float(close)
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
    logger.info("loaded {} statements, {} cal days, {} priced codes, N225 pts {}",
                len(stmts), len(cal), len(codes), int(np.isfinite(n225_arr).sum()))

    raw: dict[str, list[tuple]] = defaultdict(list)
    for code, dd, dt, fy, feps, tod in stmts:
        raw[code].append((dd, dt, fy, feps, tod))
    by_code: dict[str, list[Disclosure]] = {}
    for code, rows in raw.items():
        fin = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = fin.most_common(1)[0][0] if fin else None
        by_code[code] = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                         for dd, dt, fy, feps, tod in rows]

    # per-event records (cohort only — this is the deployment question)
    rec: list[tuple] = []   # (fy_year, grp, abs_stock, n225_ret, alpha_topix, beta)
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
            if sp is None:
                continue
            sw, mw = srow[ei - _BETA_WIN - 1:ei], topix_arr[ei - _BETA_WIN - 1:ei]
            b = beta(sw[1:] / sw[:-1] - 1.0, mw[1:] / mw[:-1] - 1.0)
            if b is None:
                continue
            abs_stock = _abs_ret(srow, ei, _H)
            alpha = beta_stripped_car(srow, topix_arr, ei, _H, b)
            n225_ret = _abs_ret(n225_arr, ei, _H)
            if abs_stock is None or alpha is None or n225_ret is None or math.isnan(alpha):
                continue
            grp = 0 if sp < 0 else (2 if sp > 0 else 1)
            rec.append((curr.fy_end.year, grp, abs_stock, n225_ret, alpha, b))

    if len(rec) < 100:
        logger.warning("too few cohort events ({}) — cannot report", len(rec))
        return
    fy_a = np.array([r[0] for r in rec])
    grp_a = np.array([r[1] for r in rec])
    abs_a = np.array([r[2] for r in rec])
    n225_a = np.array([r[3] for r in rec])
    alpha_a = np.array([r[4] for r in rec])
    beta_a = np.array([r[5] for r in rec])

    def _row(mask: np.ndarray) -> tuple[int, float, float, float, float]:
        n = int(mask.sum())
        if n == 0:
            return 0, float("nan"), float("nan"), float("nan"), float("nan")
        return (n, float(abs_a[mask].mean()), float(n225_a[mask].mean()),
                float(alpha_a[mask].mean()), float(beta_a[mask].mean()))

    print("\n" + "=" * 100)
    print("PEAD UP-REVISION LONGS — ABSOLUTE 60-bar RETURN vs N225, PER FY (N225 cohort)")
    print("=" * 100)
    print(f"cohort up/down events with N225 window: n={len(rec)}  (H={_H} bars, adj-close→close)")
    print("\nUP-REVISION group (ΔFEPS>0) — the deployable long:")
    print(f"  {'FY':<7}{'n_up':>6}{'absRet':>10}{'N225Ret':>10}{'abs−N225':>10}"
          f"{'alpha':>9}{'beta':>7}   note")
    yrs = sorted(set(int(x) for x in fy_a))
    down_years = []
    for yr in yrs:
        mup = (fy_a == yr) & (grp_a == 2)
        n, ar, nr, al, be = _row(mup)
        if n == 0:
            continue
        diff = ar - nr
        flag = ""
        if nr < 0:
            down_years.append(yr)
            flag = "← N225 DOWN year"
        print(f"  FY{yr:<5}{n:>6}{ar*100:>9.2f}%{nr*100:>9.2f}%{diff*100:>9.2f}%"
              f"{al*100:>8.2f}%{be:>7.2f}   {flag}")

    print("\nDOWN-REVISION group (ΔFEPS<0) — within-year contrast (same cohort):")
    print(f"  {'FY':<7}{'n_dn':>6}{'absRet':>10}{'N225Ret':>10}{'abs−N225':>10}{'beta':>7}")
    for yr in yrs:
        mdn = (fy_a == yr) & (grp_a == 0)
        n, ar, nr, al, be = _row(mdn)
        if n == 0:
            continue
        print(f"  FY{yr:<5}{n:>6}{ar*100:>9.2f}%{nr*100:>9.2f}%{(ar-nr)*100:>9.2f}%{be:>7.2f}")

    print("\n" + "-" * 100)
    print("MITIGATION READ — down N225 years only (the confluence-pain regime):")
    if not down_years:
        print("  (no FY had a negative event-matched mean N225 return)")
    for yr in down_years:
        mup = (fy_a == yr) & (grp_a == 2)
        n, ar, nr, al, be = _row(mup)
        verdict = ("LESS hurt (cushioned)" if ar > nr else
                   "MORE hurt" if ar < nr else "equally hurt")
        print(f"  FY{yr}: up-longs {ar*100:+.2f}% vs N225 {nr*100:+.2f}%  → {verdict} "
              f"by {(ar-nr)*100:+.2f}pp  (alpha {al*100:+.2f}%, beta {be:.2f}, n={n})")
    # pooled across down years
    if down_years:
        mdy = np.isin(fy_a, down_years) & (grp_a == 2)
        n, ar, nr, al, be = _row(mdy)
        print(f"\n  POOLED down-years: up-longs {ar*100:+.2f}% vs N225 {nr*100:+.2f}%  "
              f"→ abs−N225 {(ar-nr)*100:+.2f}pp, alpha {al*100:+.2f}%, beta {be:.2f}, n={n}")
        print("  Decomposition: absRet ≈ alpha(vs TOPIX) + beta·marketRet. A cushion that is")
        print("  mostly POSITIVE ALPHA is signal-mitigation; one that is mostly BETA<1 is just")
        print("  a lower-risk long (still down a lot in absolute terms when the market falls).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
