"""Exit rule ABC and shared data types for the exit-rule benchmark study."""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True)
class EntryCandidate:
    """One long-entry candidate derived from an early LOW zigzag peak.

    Attributes:
        stock_code:  Ticker.
        entry_date:  Date of the early LOW (bar where condition first satisfied).
        entry_price: Low price at that bar (trough entry, conservative fill).
        corr_mode:   "high" | "mid" | "low" vs ^N225 at entry time.
        corr_n225:   Actual 20-bar rolling correlation at entry_date.
        zs_history:  Sorted list of recent zigzag leg sizes (absolute |high-low|)
                     available AT entry_date — used by ZS-based exit rules.
    """

    stock_code:  str
    entry_date:  datetime.date
    entry_price: float
    corr_mode:   str
    corr_n225:   float
    zs_history:  tuple[float, ...]   # recent ZS magnitudes, oldest first


@dataclass
class ExitContext:
    """Per-bar state fed to ExitRule.should_exit().

    Attributes:
        bar_index:    Bars since entry (0 = entry bar itself).
        entry_price:  Fill price (copied from EntryCandidate).
        high:         Current bar's high.
        low:          Current bar's low.
        close:        Current bar's close.
        adx:          Stock's ADX(14) at this bar.
        adx_pos:      Stock's +DI at this bar.
        adx_neg:      Stock's −DI at this bar.
        peak_adx:     Maximum ADX seen since entry (for ADX trailing stop).
        zs_history:   Same tuple as EntryCandidate (rule may use it for context).
    """

    bar_index:   int
    entry_price: float
    high:        float
    low:         float
    close:       float
    adx:         float
    adx_pos:     float
    adx_neg:     float
    peak_adx:    float
    zs_history:  tuple[float, ...]


class ExitResult(NamedTuple):
    """Outcome of a single trade."""

    stock_code:  str
    entry_date:  datetime.date
    exit_date:   datetime.date
    entry_price: float
    exit_price:  float
    hold_bars:   int
    exit_reason: str   # "tp" | "sl" | "time" | "adx_trail" | "end_of_data"
    corr_mode:   str

    @property
    def return_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price


class ExitRule(ABC):
    """Abstract base class for exit rules.

    Implement :meth:`name` and :meth:`should_exit`.  The simulator calls
    :meth:`reset` before each new trade so stateful rules can clear
    per-trade accumulators.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in benchmark reports (e.g. "time_stop_20")."""
        ...

    def reset(self) -> None:
        """Called before each new trade.  Override for stateful rules."""

    @abstractmethod
    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        """Decide whether to exit on this bar.

        Returns:
            (exit_now, reason) where reason is a short label such as
            "tp", "sl", "time", "adx_trail".  When exit_now is False,
            reason is ignored.
        """
        ...
