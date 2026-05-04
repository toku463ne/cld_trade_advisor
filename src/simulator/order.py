"""Order types, position, and trade records for the trade simulator."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import IntEnum


class OrderSide(IntEnum):
    BUY = 1
    SELL = -1


class OrderType(IntEnum):
    MARKET = 1
    LIMIT = 2
    STOP = 3


class OrderStatus(IntEnum):
    PENDING = 0
    FILLED = 1
    CANCELLED = 2
    REJECTED = 3
    TRIGGERED = 4   # condition met, waiting for next bar's open price


@dataclass(slots=True)
class Order:
    """A pending or completed order."""

    id: int
    side: OrderSide
    type: OrderType
    quantity: float           # always positive; side determines direction
    created_at: datetime.datetime
    limit_price: float | None = None   # LIMIT orders
    stop_price: float | None = None    # STOP orders
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    filled_at: datetime.datetime | None = None
    realized_pnl: float = 0.0


@dataclass(slots=True)
class Position:
    """Current net position.

    quantity > 0  →  long
    quantity < 0  →  short
    quantity == 0 →  flat
    """

    quantity: float = 0.0
    entry_price: float = 0.0
    entry_dt: datetime.datetime | None = None

    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "long"
        elif self.quantity < 0:
            return "short"
        return "flat"

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0.0

    def unrealized_pnl(self, current_price: float) -> float:
        """P&L if the position were closed at *current_price*."""
        return (current_price - self.entry_price) * self.quantity


@dataclass(slots=True)
class Trade:
    """Record of a filled order (for history and DB persistence)."""

    order_id: int
    side: OrderSide
    quantity: float
    price: float
    dt: datetime.datetime
    realized_pnl: float = 0.0
