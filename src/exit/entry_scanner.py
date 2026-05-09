"""Early-LOW peak scanner for the exit-rule benchmark study.

Scans a DataCache for every *early LOW* zigzag trough (direction == -1),
computes 20-bar rolling correlation vs ^N225 at that bar, and emits an
:class:`EntryCandidate` for each.  These candidates are then fed to the
exit-rule simulator; entries are never skipped here — the portfolio
constraint (≤1 high-corr, ≤3 low-corr) is enforced by the simulator.
"""

from __future__ import annotations

import datetime
import statistics
from typing import NamedTuple

from src.exit.base import EntryCandidate
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache

_CORR_WINDOW  = 20
_ZS_LOOKBACK  = 16  # max legs kept in zs_history (EWA naturally down-weights old ones)


class _DayBar(NamedTuple):
    date:  datetime.date
    open:  float
    high:  float
    low:   float
    close: float


def _daily_bars(cache: DataCache) -> list[_DayBar]:
    """Collapse intraday bars to one _DayBar per calendar date."""
    groups: dict[datetime.date, list] = {}
    for b in cache.bars:
        groups.setdefault(b.dt.date(), []).append(b)
    result: list[_DayBar] = []
    for d in sorted(groups):
        day = groups[d]
        result.append(_DayBar(
            date=d,
            open=day[0].open,
            high=max(b.high  for b in day),
            low=min(b.low   for b in day),
            close=day[-1].close,
        ))
    return result


def _rolling_corr(
    stock_closes: list[float],
    ref_closes:   list[float],
    window: int = _CORR_WINDOW,
) -> list[float | None]:
    """Returns-based Pearson correlation for each bar (aligned by position)."""
    n = len(stock_closes)
    result: list[float | None] = [None] * n
    min_periods = max(5, window // 2)
    for i in range(1, n):
        start = max(1, i - window + 1)
        if i - start + 1 < min_periods:
            continue
        s_rets = [
            (stock_closes[j] - stock_closes[j - 1]) / stock_closes[j - 1]
            for j in range(start, i + 1)
        ]
        r_rets = [
            (ref_closes[j] - ref_closes[j - 1]) / ref_closes[j - 1]
            for j in range(start, i + 1)
        ]
        try:
            result[i] = statistics.correlation(s_rets, r_rets)
        except statistics.StatisticsError:
            result[i] = None
    return result


def _corr_mode(corr: float | None) -> str:
    if corr is None:
        return "mid"
    a = abs(corr)
    if a >= 0.6:
        return "high"
    if a <= 0.3:
        return "low"
    return "mid"


def scan_entries(
    stock_cache: DataCache,
    n225_cache:  DataCache,
    start: datetime.date,
    end:   datetime.date,
    zz_size:       int = 5,
    zz_middle:     int = 2,
) -> list[EntryCandidate]:
    """Return EntryCandidate list for all early LOW peaks in [start, end].

    Args:
        stock_cache:  DataCache for the target stock.
        n225_cache:   DataCache for ^N225 (for corr computation).
        start, end:   Inclusive date range to return candidates from.
        zz_size:      Zigzag size parameter (confirmed peak half-width).
        zz_middle:    Zigzag middle_size (early peak right-side bars).
    """
    stock_days = _daily_bars(stock_cache)
    n225_days  = _daily_bars(n225_cache)

    # align on shared dates
    n225_by_date = {d.date: d.close for d in n225_days}
    shared_dates = [d.date for d in stock_days if d.date in n225_by_date]
    stock_by_date = {d.date: d for d in stock_days}

    dates  = shared_dates
    highs  = [stock_by_date[d].high  for d in dates]
    lows   = [stock_by_date[d].low   for d in dates]
    closes = [stock_by_date[d].close for d in dates]
    n225cl = [n225_by_date[d]        for d in dates]

    corrs = _rolling_corr(closes, n225cl)

    peaks = detect_peaks(highs, lows, size=zz_size, middle_size=zz_middle)
    # index peaks by bar_index for fast lookup
    peak_by_idx = {p.bar_index: p for p in peaks}

    # collect zigzag leg sizes up to each bar (for zs_history)
    # a leg = |price_at_peak - price_at_previous_peak|
    leg_sizes: list[float] = []
    prev_price: float | None = None
    leg_size_at: dict[int, list[float]] = {}  # bar_index → sizes so far
    all_peak_idxs = sorted(p.bar_index for p in peaks)
    for idx in all_peak_idxs:
        p = peak_by_idx[idx]
        if prev_price is not None:
            leg_sizes.append(abs(p.price - prev_price))
        leg_size_at[idx] = list(leg_sizes)
        prev_price = p.price

    candidates: list[EntryCandidate] = []
    for p in peaks:
        if p.direction != -1:
            continue  # only early LOW troughs
        d = dates[p.bar_index]
        if d < start or d > end:
            continue
        corr = corrs[p.bar_index]
        cm   = _corr_mode(corr)
        zs_hist = leg_size_at.get(p.bar_index, [])
        recent  = zs_hist[-_ZS_LOOKBACK:] if zs_hist else []
        candidates.append(EntryCandidate(
            stock_code=stock_cache.stock_code,
            entry_date=d,
            entry_price=p.price,   # trough low — conservative long entry
            corr_mode=cm,
            corr_n225=corr if corr is not None else 0.0,
            zs_history=tuple(recent),
        ))
    return candidates


def scan_confirmed_entries(
    stock_cache: DataCache,
    n225_cache:  DataCache,
    start: datetime.date,
    end:   datetime.date,
    zz_size:   int = 5,
    zz_middle: int = 2,
) -> list[EntryCandidate]:
    """Oracle-entry variant: only troughs that later confirmed as direction == −2.

    Entry date  = dates[trough_idx + middle_size]  — the day the early LOW
                  signal would have fired (no price lookahead).
    Entry price = lows[trough_idx]                 — the actual trough low.

    Because we use the full bar history to filter out false troughs, this
    benchmark isolates exit-rule performance from entry noise.  Scores are
    an upper bound on live results.
    """
    stock_days = _daily_bars(stock_cache)
    n225_days  = _daily_bars(n225_cache)

    n225_by_date = {d.date: d.close for d in n225_days}
    shared_dates = [d.date for d in stock_days if d.date in n225_by_date]
    stock_by_date = {d.date: d for d in stock_days}

    dates  = shared_dates
    highs  = [stock_by_date[d].high  for d in dates]
    lows   = [stock_by_date[d].low   for d in dates]
    closes = [stock_by_date[d].close for d in dates]
    n225cl = [n225_by_date[d]        for d in dates]
    n      = len(dates)

    corrs = _rolling_corr(closes, n225cl)

    peaks = detect_peaks(highs, lows, size=zz_size, middle_size=zz_middle)
    peak_by_idx = {p.bar_index: p for p in peaks}

    # Zigzag leg sizes accumulated chronologically
    leg_sizes: list[float] = []
    prev_price: float | None = None
    leg_size_at: dict[int, list[float]] = {}
    for idx in sorted(peak_by_idx):
        p = peak_by_idx[idx]
        if prev_price is not None:
            leg_sizes.append(abs(p.price - prev_price))
        leg_size_at[idx] = list(leg_sizes)
        prev_price = p.price

    candidates: list[EntryCandidate] = []
    for p in peaks:
        if p.direction != -2:
            continue  # only *confirmed* LOW troughs
        trough_idx    = p.bar_index
        detection_idx = trough_idx + zz_middle   # day the early signal fires
        if detection_idx >= n:
            continue
        trough_date = dates[trough_idx]
        entry_date  = dates[detection_idx]
        # trough itself must be within the FY; detection just slightly after
        if trough_date < start or entry_date > end:
            continue
        corr = corrs[detection_idx]
        cm   = _corr_mode(corr)
        zs_hist = leg_size_at.get(trough_idx, [])
        recent  = zs_hist[-_ZS_LOOKBACK:] if zs_hist else []
        candidates.append(EntryCandidate(
            stock_code=stock_cache.stock_code,
            entry_date=entry_date,
            entry_price=p.price,      # actual trough low (no price lookahead)
            corr_mode=cm,
            corr_n225=corr if corr is not None else 0.0,
            zs_history=tuple(recent),
        ))
    return candidates
