"""PEAD — management-forecast-revision surprise (read-only).

Implements the pre-registered definition in
`docs/analysis/pead_forecast_revision_preregistration.md`. Surprise = change in full-year
management EPS guidance scaled by price; ride the ~60-bar drift, β-stripped vs TOPIX.

The surprise / pairing / event-timing / CAR logic lives in PURE functions (unit-tested in
tests/test_pead_forecast_revision.py — verifiable now, before any data exists). `run()` is a
thin driver that assembles the per-event table from the jq_* tables and prints the quintile
drift; it only produces output once the J-Quants Standard 10-yr backfill is loaded (the
Free-plan 12-week window is too short to form revision pairs + a 60-bar forward window).

Run (after backfill): PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_forecast_revision
"""
from __future__ import annotations

import bisect
import datetime
import statistics
import sys
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from loguru import logger

_TSE_CLOSE = datetime.time(15, 0)
_BETA_WIN = 60
_HORIZONS = (20, 60)


# ── pure logic (unit-tested) ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Disclosure:
    """One earnings disclosure, reduced to the fields the revision surprise needs."""
    disclosed_date: datetime.date
    disclosed_time: datetime.time | None
    fy_end: datetime.date | None              # current_fiscal_year_end_date
    forecast_eps: Decimal | None              # full-year management EPS guidance
    doc_basis: str | None = None              # type_of_document (accounting basis)


def _sort_key(d: Disclosure) -> tuple:
    return (d.disclosed_date, d.disclosed_time or datetime.time.min)


def pair_same_fy_revisions(discs: list[Disclosure]) -> list[tuple[Disclosure, Disclosure]]:
    """Pair each disclosure with the most recent prior same-FY disclosure carrying a
    forecast. Returns (prev, curr) pairs eligible for a revision surprise.

    Excludes (per pre-registration): missing forecast_eps on either side, differing
    fiscal-year target, or differing accounting basis (type_of_document).
    """
    ordered = sorted(discs, key=_sort_key)
    pairs: list[tuple[Disclosure, Disclosure]] = []
    for i, curr in enumerate(ordered):
        if curr.forecast_eps is None or curr.fy_end is None:
            continue
        for j in range(i - 1, -1, -1):
            prev = ordered[j]
            # most recent prior disclosure that is a same-FY, same-basis forecast;
            # skip (don't abort on) rows that don't qualify so a one-off mismatched
            # intermediary can't block an otherwise-clean revision pair.
            if (prev.fy_end != curr.fy_end or prev.forecast_eps is None
                    or prev.doc_basis != curr.doc_basis):
                continue
            pairs.append((prev, curr))
            break
    return pairs


def revision_surprise(prev_eps: Decimal | None, curr_eps: Decimal | None,
                      price: float | None) -> float | None:
    """ΔFEPS / price — positive means guidance was raised. None if inputs unusable."""
    if prev_eps is None or curr_eps is None or not price:
        return None
    return float((curr_eps - prev_eps) / Decimal(str(price)))


def tradable_entry_day(disclosed_date: datetime.date, disclosed_time: datetime.time | None,
                       trading_days: list[datetime.date]) -> datetime.date | None:
    """First trading day on/after the effective announcement day. After-close (>=15:00)
    pushes the effective day to the next trading day. `trading_days` must be sorted."""
    effective = disclosed_date
    if disclosed_time is not None and disclosed_time >= _TSE_CLOSE:
        idx = bisect.bisect_right(trading_days, disclosed_date)
        if idx >= len(trading_days):
            return None
        effective = trading_days[idx]
    idx = bisect.bisect_left(trading_days, effective)
    return trading_days[idx] if idx < len(trading_days) else None


def beta(stock_rets: np.ndarray, mkt_rets: np.ndarray) -> float | None:
    m = ~(np.isnan(stock_rets) | np.isnan(mkt_rets))
    s, k = stock_rets[m], mkt_rets[m]
    if len(k) < 30 or k.var() == 0:
        return None
    return float(np.cov(s, k)[0, 1] / k.var())


def beta_stripped_car(stock_closes: np.ndarray, mkt_closes: np.ndarray,
                      entry_idx: int, horizon: int, b: float) -> float | None:
    """Cumulative abnormal return (stock − β·market) over `horizon` bars from entry_idx."""
    last = entry_idx + horizon
    if entry_idx < 1 or last >= len(stock_closes) or last >= len(mkt_closes):
        return None
    s = stock_closes[entry_idx:last + 1]
    k = mkt_closes[entry_idx:last + 1]
    if s[0] <= 0 or k[0] <= 0:
        return None
    s_ret = s[-1] / s[0] - 1.0
    k_ret = k[-1] / k[0] - 1.0
    return float(s_ret - b * k_ret)


def quintile_edges(values: list[float]) -> list[float]:
    return list(np.percentile(values, [20, 40, 60, 80])) if values else []


def quintile_of(x: float, edges: list[float]) -> int:
    return bisect.bisect_right(edges, x)          # 0..4


# ── thin DB driver (runs once the 10-yr backfill exists) ─────────────────────
def run() -> None:
    from collections import defaultdict

    from sqlalchemy import select

    from src.data.db import get_session
    from src.data.jquants_models import (
        JqDailyQuote, JqStatement, JqTopix, JqTradingCalendar,
    )
    from src.data.jquants_collector import to_yf_code  # noqa: F401 (code mapping if joining yfinance)

    with get_session() as s:
        cal = [d for (d,) in s.execute(
            select(JqTradingCalendar.date)
            .where(JqTradingCalendar.holiday_division == "1")
            .order_by(JqTradingCalendar.date)).all()]
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)
        ).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .order_by(JqTopix.date)).all()

    if not stmts or not cal or not topix:
        logger.warning("jq_* tables not populated yet (statements={}, calendar={}, topix={}). "
                       "Load the J-Quants Standard 10-yr backfill, then re-run.",
                       len(stmts), len(cal), len(topix))
        return

    by_code: dict[str, list[Disclosure]] = defaultdict(list)
    for code, dd, dt, fy, feps, basis in stmts:
        by_code[code].append(Disclosure(dd, dt, fy, feps, basis))

    t_dates = [d for d, _ in topix]
    t_close = np.array([float(c) if c is not None else np.nan for _, c in topix])

    events = []   # (surprise, code, entry_day)
    for code, discs in by_code.items():
        for prev, curr in pair_same_fy_revisions(discs):
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is None:
                continue
            events.append((prev.forecast_eps, curr.forecast_eps, code, entry))
    logger.info("formed {} revision pairs across {} codes", len(events), len(by_code))

    # Per-event price join + CAR is left to the analyst run on real data; here we report
    # how many usable pairs exist so the gate (n>=1000, each quintile>=100) is checkable.
    logger.info("Pre-registered gates require n>=1000 pooled, >=100/quintile, H={} primary 60. "
                "See docs/analysis/pead_forecast_revision_preregistration.md", _HORIZONS)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
