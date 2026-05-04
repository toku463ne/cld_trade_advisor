"""BarData — the price snapshot returned by tick()."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass(slots=True)
class BarData:
    """OHLCV bar at a single point in time, with optional indicator values.

    typical_price — the price used for all order acceptance decisions:
        (high + low + close) / 3
    indicators    — keyed by name e.g. {"SMA20": 1234.5, "EMA50": 1200.0}
    """

    dt: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    indicators: dict[str, float] = field(default_factory=dict)

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0
