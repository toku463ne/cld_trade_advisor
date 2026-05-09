"""Portfolio-constrained exit-rule simulator.

Given a list of EntryCandidate objects (sorted by entry_date) and one
ExitRule, simulates the portfolio subject to:

    ≤ 1 high-corr position at a time
    ≤ 3 low-corr positions at a time   (mid counts as low-corr)

Entries are accepted in chronological order.  If a candidate arrives on
a day when the relevant slot is full, it is skipped (not queued).

Fill model: two-bar rule — signal detected on entry_date, filled at
*next available bar's open*.  Exit is similarly filled at the open of
the bar following the trigger bar.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import NamedTuple

from src.exit.base import EntryCandidate, ExitContext, ExitResult, ExitRule
from src.simulator.cache import DataCache

_MAX_HIGH_CORR = 1
_MAX_LOW_CORR  = 3   # mid-corr is treated as low


class _DayBar(NamedTuple):
    date:  datetime.date
    open:  float
    high:  float
    low:   float
    close: float
    adx:   float
    adx_p: float
    adx_n: float


def _daily_bars_with_adx(cache: DataCache) -> list[_DayBar]:
    """Return one _DayBar per date, including ADX indicators if loaded."""
    groups: dict[datetime.date, list] = {}
    for b in cache.bars:
        groups.setdefault(b.dt.date(), []).append(b)
    result: list[_DayBar] = []
    for d in sorted(groups):
        day = groups[d]
        last = day[-1]
        result.append(_DayBar(
            date=d,
            open=day[0].open,
            high=max(b.high  for b in day),
            low=min(b.low   for b in day),
            close=last.close,
            adx=last.indicators.get("ADX14", float("nan")),
            adx_p=last.indicators.get("ADX14_POS", float("nan")),
            adx_n=last.indicators.get("ADX14_NEG", float("nan")),
        ))
    return result


@dataclass
class _OpenPosition:
    candidate:  EntryCandidate
    fill_price: float
    fill_date:  datetime.date
    bars:       list[_DayBar]   # bars from fill onward (accumulated)
    peak_adx:   float


def run_simulation(
    candidates:   list[EntryCandidate],
    rule:         ExitRule,
    stock_caches: dict[str, DataCache],
    end_date:     datetime.date,
) -> list[ExitResult]:
    """Simulate all candidates under *rule* with portfolio constraints.

    Args:
        candidates:   EntryCandidate list, any order (sorted internally).
        rule:         ExitRule instance; reset() called per trade.
        stock_caches: Mapping stock_code → DataCache (must cover [start, end]).
        end_date:     Simulation ends at this date; open positions force-closed.

    Returns:
        List of ExitResult, one per completed trade.
    """
    sorted_cands = sorted(candidates, key=lambda c: c.entry_date)

    # Pre-build daily bar index per stock
    bar_index: dict[str, list[_DayBar]] = {
        code: _daily_bars_with_adx(cache)
        for code, cache in stock_caches.items()
    }
    # date → bar position mapping per stock
    date_to_idx: dict[str, dict[datetime.date, int]] = {
        code: {b.date: i for i, b in enumerate(bars)}
        for code, bars in bar_index.items()
    }

    results:  list[ExitResult]    = []
    open_pos: list[_OpenPosition] = []

    # Candidate pointer
    cand_idx = 0
    n_cands  = len(sorted_cands)

    # Collect all relevant dates in sorted order
    all_dates: set[datetime.date] = set()
    for bars in bar_index.values():
        all_dates.update(b.date for b in bars)
    sorted_dates = sorted(all_dates)

    for today in sorted_dates:
        if today > end_date:
            break

        # ── 1. Advance open positions with today's bar ─────────────────
        still_open: list[_OpenPosition] = []
        for pos in open_pos:
            code = pos.candidate.stock_code
            idx_map = date_to_idx.get(code, {})
            bar_i   = idx_map.get(today)
            if bar_i is None:
                still_open.append(pos)
                continue
            bar = bar_index[code][bar_i]
            pos.bars.append(bar)
            pos.peak_adx = max(pos.peak_adx, bar.adx if not _isnan(bar.adx) else pos.peak_adx)
            still_open.append(pos)
        open_pos = still_open

        # Check exits AFTER updating bars (so we can use today's prices)
        closed: list[_OpenPosition] = []
        remaining: list[_OpenPosition] = []
        for pos in open_pos:
            code  = pos.candidate.stock_code
            if not pos.bars:
                remaining.append(pos)
                continue
            bar   = pos.bars[-1]
            bar_n = len(pos.bars) - 1
            ctx = ExitContext(
                bar_index=bar_n,
                entry_price=pos.fill_price,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adx=bar.adx if not _isnan(bar.adx) else 0.0,
                adx_pos=bar.adx_p if not _isnan(bar.adx_p) else 0.0,
                adx_neg=bar.adx_n if not _isnan(bar.adx_n) else 0.0,
                peak_adx=pos.peak_adx,
                zs_history=pos.candidate.zs_history,
            )
            exit_now, reason = pos._rule.should_exit(ctx)  # type: ignore[attr-defined]
            force = today >= end_date
            if exit_now or force:
                reason = reason if (exit_now and not force) else "end_of_data"
                # Fill at next bar open (two-bar model) — approximate with close
                # (we don't have tomorrow's open; use close as proxy)
                exit_price = bar.close
                results.append(ExitResult(
                    stock_code=code,
                    entry_date=pos.candidate.entry_date,
                    exit_date=today,
                    entry_price=pos.fill_price,
                    exit_price=exit_price,
                    hold_bars=bar_n,
                    exit_reason=reason,
                    corr_mode=pos.candidate.corr_mode,
                ))
                closed.append(pos)
            else:
                remaining.append(pos)
        open_pos = remaining

        # ── 2. Accept new candidates whose entry_date == today ─────────
        while cand_idx < n_cands and sorted_cands[cand_idx].entry_date <= today:
            cand = sorted_cands[cand_idx]
            cand_idx += 1
            if cand.entry_date != today:
                continue   # past date — skip (already processed)

            # Count current open positions by corr_mode
            high_open = sum(1 for p in open_pos if p.candidate.corr_mode == "high")
            low_open  = sum(1 for p in open_pos if p.candidate.corr_mode != "high")

            if cand.corr_mode == "high" and high_open >= _MAX_HIGH_CORR:
                continue
            if cand.corr_mode != "high" and low_open >= _MAX_LOW_CORR:
                continue

            # Two-bar fill: find next bar's open
            code    = cand.stock_code
            idx_map = date_to_idx.get(code, {})
            bar_i   = idx_map.get(today)
            if bar_i is None or bar_i + 1 >= len(bar_index.get(code, [])):
                continue   # no next bar available
            fill_bar = bar_index[code][bar_i + 1]

            pos_rule = _clone_rule(rule)
            pos_rule.reset()

            pos = _OpenPosition(
                candidate=cand,
                fill_price=fill_bar.open,
                fill_date=fill_bar.date,
                bars=[],
                peak_adx=0.0,
            )
            pos._rule = pos_rule  # type: ignore[attr-defined]
            open_pos.append(pos)

    # Force-close anything still open after end_date
    for pos in open_pos:
        code = pos.candidate.stock_code
        if not pos.bars:
            continue
        bar = pos.bars[-1]
        results.append(ExitResult(
            stock_code=code,
            entry_date=pos.candidate.entry_date,
            exit_date=bar.date,
            entry_price=pos.fill_price,
            exit_price=bar.close,
            hold_bars=len(pos.bars) - 1,
            exit_reason="end_of_data",
            corr_mode=pos.candidate.corr_mode,
        ))

    return results


def _isnan(x: float) -> bool:
    return x != x


def _clone_rule(rule: ExitRule) -> ExitRule:
    """Deep-clone a rule so each position gets fully independent state."""
    import copy
    return copy.deepcopy(rule)
