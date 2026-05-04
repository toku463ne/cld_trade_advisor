"""DataCache — loads OHLCV + indicators into memory for fast tick() access.

Designed for repeated use across ML/GA iterations: load once, reset the
simulator many times without re-querying the database.
"""

from __future__ import annotations

import bisect
import datetime
from typing import Callable, Sequence

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from src.data.models import OHLCV_MODEL_MAP
from src.simulator.bar import BarData

# Type for a custom indicator function: receives close-price array, returns values array
IndicatorFn = Callable[[np.ndarray], np.ndarray]


class DataCache:
    """In-memory OHLCV store for a single stock + granularity.

    Usage::

        cache = DataCache("7203.T", "1d")
        cache.load(session, start, end)
        cache.add_sma(20)          # adds "SMA20" indicator
        cache.add_sma(50)          # adds "SMA50"
        cache.add_indicator("RSI", fn)  # custom indicator

        bar = cache.tick(some_datetime)  # O(log n) lookup

        # Iterate all bars in order (preferred for backtests):
        for bar in cache.bars:
            ...
    """

    def __init__(self, stock_code: str, gran: str) -> None:
        if gran not in OHLCV_MODEL_MAP:
            raise ValueError(f"Unknown granularity {gran!r}")
        self.stock_code = stock_code
        self.gran = gran
        self._bars: list[BarData] = []
        self._dts: list[datetime.datetime] = []   # parallel to _bars, for bisect
        self._closes: np.ndarray = np.empty(0)    # kept for indicator computation
        self._indicator_fns: dict[str, tuple[IndicatorFn, str]] = {}  # name -> (fn, label)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(
        self,
        session: Session,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> None:
        """Load OHLCV rows from the DB and (re-)compute all registered indicators."""
        from sqlalchemy import select

        model = OHLCV_MODEL_MAP[self.gran]
        stmt = (
            select(model)
            .where(model.stock_code == self.stock_code)
            .where(model.ts >= start)
            .where(model.ts < end)
            .order_by(model.ts)
        )
        rows = session.execute(stmt).scalars().all()

        self._bars = [
            BarData(
                dt=row.ts,
                open=float(row.open_price),
                high=float(row.high_price),
                low=float(row.low_price),
                close=float(row.close_price),
                volume=int(row.volume),
                indicators={},
            )
            for row in rows
        ]
        self._dts = [b.dt for b in self._bars]
        self._closes = np.array([b.close for b in self._bars], dtype=np.float64)

        # Re-compute any previously registered indicators
        for name, values in self._compute_all_indicators():
            for i, bar in enumerate(self._bars):
                bar.indicators[name] = float(values[i]) if not np.isnan(values[i]) else 0.0

    # ------------------------------------------------------------------
    # Indicator registration
    # ------------------------------------------------------------------

    def add_sma(self, period: int) -> "DataCache":
        """Add a Simple Moving Average indicator named 'SMA{period}'.

        Idempotent: no-op if already registered.  Safe to call after
        :meth:`load` — the indicator is immediately injected into all bars.
        """
        name = f"SMA{period}"
        if name in self._indicator_fns:
            return self

        def _sma(closes: np.ndarray) -> np.ndarray:
            return pd.Series(closes).rolling(period, min_periods=period).mean().to_numpy()

        self._add_fn(name, _sma)
        return self

    def add_ema(self, period: int) -> "DataCache":
        """Add an Exponential Moving Average indicator named 'EMA{period}'.

        Idempotent: no-op if already registered.
        """
        name = f"EMA{period}"
        if name in self._indicator_fns:
            return self

        def _ema(closes: np.ndarray) -> np.ndarray:
            return pd.Series(closes).ewm(span=period, adjust=False).mean().to_numpy()

        self._add_fn(name, _ema)
        return self

    def add_rolling_std(self, period: int) -> "DataCache":
        """Add a rolling standard-deviation indicator named 'RSTD{period}'.

        Idempotent: no-op if already registered.  Safe to call after
        :meth:`load` — the indicator is immediately injected into all bars.
        """
        name = f"RSTD{period}"
        if name in self._indicator_fns:
            return self

        def _rstd(closes: np.ndarray) -> np.ndarray:
            return pd.Series(closes).rolling(period, min_periods=period).std().to_numpy()

        self._add_fn(name, _rstd)
        return self

    def add_indicator(self, name: str, fn: IndicatorFn) -> "DataCache":
        """Register a custom indicator.

        *fn* receives the full close-price array and must return an array of
        the same length (NaN for bars where the indicator is undefined).
        """
        self._add_fn(name, fn)
        return self

    # ------------------------------------------------------------------
    # Tick access
    # ------------------------------------------------------------------

    def tick(self, dt: datetime.datetime) -> BarData | None:
        """Return the bar at or immediately before *dt*.  O(log n)."""
        if not self._bars:
            return None
        idx = bisect.bisect_right(self._dts, dt) - 1
        if idx < 0:
            return None
        return self._bars[idx]

    @property
    def bars(self) -> list[BarData]:
        """All bars in chronological order (for sequential backtest iteration)."""
        return self._bars

    @property
    def datetimes(self) -> list[datetime.datetime]:
        return self._dts

    def __len__(self) -> int:
        return len(self._bars)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _add_fn(self, name: str, fn: IndicatorFn) -> None:
        self._indicator_fns[name] = fn
        if self._closes.size > 0:
            values = fn(self._closes)
            for i, bar in enumerate(self._bars):
                bar.indicators[name] = float(values[i]) if not np.isnan(values[i]) else 0.0

    def _compute_all_indicators(self) -> list[tuple[str, np.ndarray]]:
        results = []
        for name, fn in self._indicator_fns.items():
            results.append((name, fn(self._closes)))
        return results
