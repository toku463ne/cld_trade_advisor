"""TradeSimulator — lightweight single-stock trade simulator.

Designed to be reset and re-run thousands of times for ML/GA training.
No DB calls happen during simulation; data is saved explicitly at the end.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Sequence

from src.simulator.bar import BarData
from src.simulator.cache import DataCache
from src.simulator.order import Order, OrderSide, OrderStatus, OrderType, Position, Trade


@dataclass
class SimResult:
    """Summary of a completed simulation run."""

    initial_capital: float
    final_equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_trades: int
    trade_history: list[Trade]
    orders: list[Order]
    position: Position

    @property
    def total_return(self) -> float:
        return (self.final_equity - self.initial_capital) / self.initial_capital


class TradeSimulator:
    """Single-stock trade simulator driven by a DataCache.

    Typical usage::

        cache = DataCache("7203.T", "1d")
        cache.load(session, start, end)
        cache.add_sma(20)

        sim = TradeSimulator(cache, initial_capital=1_000_000)

        for bar in cache.bars:
            sim.tick(bar.dt)
            if bar.indicators["SMA20"] > bar.close:
                sim.sell(100, OrderType.MARKET)
            ...

        result = sim.result()
        sim.reset()   # ready for next GA/ML iteration

    Order execution rules
    ---------------------
    All order conditions are evaluated against typical_price = (H+L+C)/3.
    Fill price equals typical_price for market orders; limit/stop price for
    limit and stop orders (when the condition is met).

    Position netting
    ----------------
    A single net position is tracked (positive=long, negative=short).
    Buying while short covers the short first; selling while long closes
    the long first. Realized P&L is accumulated on each close.
    """

    def __init__(self, cache: DataCache, initial_capital: float = 1_000_000.0) -> None:
        self._cache = cache
        self._initial_capital = initial_capital
        self._next_id = 1
        self.reset()

    # ------------------------------------------------------------------
    # Tick — advance simulation to a datetime
    # ------------------------------------------------------------------

    def tick(self, dt: datetime.datetime) -> BarData | None:
        """Advance to *dt*, process all pending orders, return the bar."""
        bar = self._cache.tick(dt)
        if bar is None:
            return None
        self._current_bar = bar
        self._process_orders(bar)
        return bar

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def buy(
        self,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
    ) -> int:
        """Submit a buy order. Returns the order id."""
        return self._submit(OrderSide.BUY, quantity, order_type, price)

    def sell(
        self,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
    ) -> int:
        """Submit a sell order. Returns the order id."""
        return self._submit(OrderSide.SELL, quantity, order_type, price)

    def cancel(self, order_id: int) -> bool:
        """Cancel a pending order. Returns True if found and cancelled.

        Only PENDING orders can be cancelled. TRIGGERED orders are already
        committed to fill at the next bar's open and cannot be recalled.
        """
        order = self._pending_orders.get(order_id)
        if order is None:
            return False
        order.status = OrderStatus.CANCELLED
        del self._pending_orders[order_id]
        self._all_orders.append(order)
        return True

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def position(self) -> Position:
        return self._position

    @property
    def equity(self) -> float:
        """Cash + unrealized P&L of the open position."""
        if self._current_bar is None or self._position.is_flat:
            return self._cash
        return self._cash + self._position.unrealized_pnl(self._current_bar.typical_price)

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def trade_history(self) -> list[Trade]:
        return list(self._trade_history)

    @property
    def pending_orders(self) -> list[Order]:
        """Orders waiting for their condition to be met (PENDING status)."""
        return list(self._pending_orders.values())

    @property
    def triggered_orders(self) -> list[Order]:
        """Orders whose condition was met and are waiting for the next bar's open."""
        return list(self._triggered_orders.values())

    # ------------------------------------------------------------------
    # Result and reset
    # ------------------------------------------------------------------

    def result(self) -> SimResult:
        """Return a snapshot of the simulation result."""
        unrealized = 0.0
        if self._current_bar and not self._position.is_flat:
            unrealized = self._position.unrealized_pnl(self._current_bar.typical_price)

        all_orders = (
            list(self._all_orders)
            + list(self._pending_orders.values())
            + list(self._triggered_orders.values())
        )
        return SimResult(
            initial_capital=self._initial_capital,
            final_equity=self.equity,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=unrealized,
            total_trades=len(self._trade_history),
            trade_history=list(self._trade_history),
            orders=all_orders,
            position=Position(
                quantity=self._position.quantity,
                entry_price=self._position.entry_price,
                entry_dt=self._position.entry_dt,
            ),
        )

    def reset(self) -> None:
        """Reset all state to the initial conditions.

        The DataCache is NOT reloaded — this is intentionally cheap so that
        genetic algorithm / ML loops can reset between iterations without
        touching the database.
        """
        self._cash: float = self._initial_capital
        self._position: Position = Position()
        self._realized_pnl: float = 0.0
        self._pending_orders: dict[int, Order] = {}
        self._triggered_orders: dict[int, Order] = {}
        self._all_orders: list[Order] = []
        self._trade_history: list[Trade] = []
        self._current_bar: BarData | None = None
        self._next_id: int = 1

    # ------------------------------------------------------------------
    # Private — order processing
    # ------------------------------------------------------------------

    def _submit(
        self,
        side: OrderSide,
        quantity: float,
        order_type: OrderType,
        price: float | None,
    ) -> int:
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")

        limit_price: float | None = None
        stop_price: float | None = None
        if order_type == OrderType.LIMIT:
            if price is None:
                raise ValueError("LIMIT order requires a price")
            limit_price = price
        elif order_type == OrderType.STOP:
            if price is None:
                raise ValueError("STOP order requires a price")
            stop_price = price

        order_id = self._next_id
        self._next_id += 1
        dt = self._current_bar.dt if self._current_bar else datetime.datetime.min

        order = Order(
            id=order_id,
            side=side,
            type=order_type,
            quantity=quantity,
            created_at=dt,
            limit_price=limit_price,
            stop_price=stop_price,
        )

        if order_type == OrderType.MARKET:
            # Market orders skip condition checking and go straight to triggered,
            # filling at the open of the next bar.
            self._trigger(order)
        else:
            self._pending_orders[order_id] = order

        return order_id

    def _process_orders(self, bar: BarData) -> None:
        """Two-phase order processing on each bar.

        Phase 1 — fill triggered orders at this bar's open price.
            These were triggered on the previous bar's typical_price.

        Phase 2 — check pending orders against this bar's typical_price.
            Ones whose condition is met become TRIGGERED and will fill
            at the next bar's open.
        """
        # Phase 1: fill at open
        for order in list(self._triggered_orders.values()):
            self._fill(order, bar.open, bar.dt)

        # Phase 2: check conditions against typical_price
        tp = bar.typical_price
        for order in list(self._pending_orders.values()):
            if self._condition_met(order, tp):
                self._trigger(order)

    def _condition_met(self, order: Order, tp: float) -> bool:
        """Return True if the order's trigger condition is satisfied by *tp*."""
        if order.type == OrderType.LIMIT:
            assert order.limit_price is not None
            if order.side == OrderSide.BUY:
                return tp <= order.limit_price
            return tp >= order.limit_price

        if order.type == OrderType.STOP:
            assert order.stop_price is not None
            # Stop BUY: triggers when price rises to/above stop (breakout or short cover)
            if order.side == OrderSide.BUY:
                return tp >= order.stop_price
            # Stop SELL: triggers when price falls to/below stop (stop-loss on long or short entry)
            return tp <= order.stop_price

        return False  # market orders are triggered immediately in _submit

    def _trigger(self, order: Order) -> None:
        """Move an order from pending to triggered (will fill at next bar's open)."""
        order.status = OrderStatus.TRIGGERED
        self._pending_orders.pop(order.id, None)
        self._triggered_orders[order.id] = order

    def _fill(self, order: Order, fill_price: float, dt: datetime.datetime) -> None:
        """Execute a triggered order at *fill_price* (the current bar's open)."""
        qty = order.quantity  # always positive
        side_sign = float(order.side)  # +1 for BUY, -1 for SELL

        realized = self._apply_to_position(order.side, qty, fill_price, dt)

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_at = dt
        order.realized_pnl = realized

        self._realized_pnl += realized
        self._cash -= side_sign * qty * fill_price

        self._trade_history.append(Trade(
            order_id=order.id,
            side=order.side,
            quantity=qty,
            price=fill_price,
            dt=dt,
            realized_pnl=realized,
        ))

        del self._triggered_orders[order.id]
        self._all_orders.append(order)

    def _apply_to_position(
        self,
        side: OrderSide,
        qty: float,
        fill_price: float,
        dt: datetime.datetime,
    ) -> float:
        """Update the position and return the realized P&L from this trade."""
        pos = self._position
        trade_qty = qty * float(side)  # positive for buy, negative for sell

        if pos.is_flat:
            pos.quantity = trade_qty
            pos.entry_price = fill_price
            pos.entry_dt = dt
            return 0.0

        if (pos.quantity > 0 and trade_qty > 0) or (pos.quantity < 0 and trade_qty < 0):
            # Adding to existing position — weighted-average entry price
            total_qty = pos.quantity + trade_qty
            pos.entry_price = (
                (pos.entry_price * abs(pos.quantity) + fill_price * qty)
                / abs(total_qty)
            )
            pos.quantity = total_qty
            return 0.0

        # Opposing trade — close fully or partially
        closing_qty = min(abs(pos.quantity), qty)
        realized = (fill_price - pos.entry_price) * pos.quantity / abs(pos.quantity) * closing_qty

        remaining_position = pos.quantity + trade_qty
        if abs(remaining_position) < 1e-10:
            # Fully closed
            pos.quantity = 0.0
            pos.entry_price = 0.0
            pos.entry_dt = None
        elif (remaining_position > 0) == (pos.quantity > 0):
            # Partially closed, same side remains
            pos.quantity = remaining_position
        else:
            # Flip: closed existing and opened opposite side
            flipped_qty = abs(remaining_position)
            pos.quantity = remaining_position
            pos.entry_price = fill_price
            pos.entry_dt = dt

        return realized
