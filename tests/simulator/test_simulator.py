"""Unit tests for TradeSimulator.

Fill model:
  - Condition checked against typical_price = (H+L+C)/3 of the current bar.
  - Actual fill price = open of the NEXT bar.
  - Market orders skip condition checking and go straight to TRIGGERED,
    so they also fill at the next bar's open.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from src.simulator.bar import BarData
from src.simulator.cache import DataCache
from src.simulator.order import OrderSide, OrderStatus, OrderType, Position
from src.simulator.simulator import TradeSimulator


UTC = datetime.timezone.utc


def _bar(
    dt_str: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    vol: int = 1000,
) -> BarData:
    return BarData(
        dt=datetime.datetime.fromisoformat(dt_str).replace(tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=vol,
    )


def _make_sim(capital: float = 1_000_000.0) -> TradeSimulator:
    cache = MagicMock(spec=DataCache)
    return TradeSimulator(cache, initial_capital=capital)


def _advance(sim: TradeSimulator, bar: BarData) -> BarData:
    """Drive the simulator through one bar without going through DataCache."""
    sim._current_bar = bar
    sim._process_orders(bar)
    return bar


class TestTypicalPrice:
    def test_formula(self) -> None:
        bar = _bar("2024-01-04", 100, 110, 90, 105)
        assert bar.typical_price == pytest.approx((110 + 90 + 105) / 3)


class TestMarketOrder:
    def test_market_order_fills_at_next_bar_open(self) -> None:
        bar1 = _bar("2024-01-03", 100, 110, 90, 100)
        bar2 = _bar("2024-01-04", 105, 115, 95, 110)

        sim = _make_sim()
        _advance(sim, bar1)
        sim.buy(10, OrderType.MARKET)

        # Still pending fill after bar1 — order is TRIGGERED, not yet filled
        assert sim.position.is_flat
        assert len(sim.triggered_orders) == 1

        _advance(sim, bar2)
        # Fills at bar2's open = 105
        assert sim.position.quantity == pytest.approx(10)
        assert sim.position.entry_price == pytest.approx(105.0)
        assert sim.cash == pytest.approx(1_000_000 - 105 * 10)

    def test_market_sell_fills_at_next_bar_open(self) -> None:
        bar1 = _bar("2024-01-03", 100, 110, 90, 100)
        bar2 = _bar("2024-01-04", 95, 105, 90, 100)

        sim = _make_sim()
        _advance(sim, bar1)
        sim.sell(5, OrderType.MARKET)

        _advance(sim, bar2)
        assert sim.position.quantity == pytest.approx(-5)
        assert sim.position.entry_price == pytest.approx(95.0)  # bar2 open

    def test_short_covered_by_buy_realizes_pnl(self) -> None:
        bar1 = _bar("2024-01-03", 100, 110, 90, 100)
        bar2 = _bar("2024-01-04", 98, 105, 90, 95)   # fills short at open=98
        bar3 = _bar("2024-01-05", 90, 95, 85, 90)    # fills cover at open=90

        sim = _make_sim()
        _advance(sim, bar1)
        sim.sell(10, OrderType.MARKET)
        _advance(sim, bar2)  # short filled at 98
        sim.buy(10, OrderType.MARKET)
        _advance(sim, bar3)  # cover filled at 90

        # Profit = (98 - 90) * 10 = 80
        assert sim.realized_pnl == pytest.approx((98 - 90) * 10)
        assert sim.position.is_flat


class TestLimitOrder:
    def test_limit_buy_triggers_when_tp_at_or_below_limit(self) -> None:
        # tp = (110+90+95)/3 ≈ 98.33; limit = 100 → condition met
        bar1 = _bar("2024-01-03", 100, 110, 90, 95)   # tp ≈ 98.3 → triggers
        bar2 = _bar("2024-01-04", 102, 112, 95, 108)  # fills at open=102

        sim = _make_sim()
        # Place the order before tick (no current bar context yet)
        sim._current_bar = MagicMock()
        sim._current_bar.dt = datetime.datetime(2024, 1, 2, tzinfo=UTC)
        oid = sim.buy(10, OrderType.LIMIT, price=100.0)

        _advance(sim, bar1)  # condition checked → triggered
        assert sim.position.is_flat
        assert len(sim.triggered_orders) == 1

        _advance(sim, bar2)  # fills at bar2 open = 102
        assert sim.position.quantity == pytest.approx(10)
        assert sim.position.entry_price == pytest.approx(102.0)

    def test_limit_buy_does_not_trigger_when_tp_above_limit(self) -> None:
        # tp = (120+100+115)/3 ≈ 111.7; limit = 100 → condition NOT met
        bar1 = _bar("2024-01-03", 110, 120, 100, 115)

        sim = _make_sim()
        sim._current_bar = MagicMock()
        sim._current_bar.dt = datetime.datetime(2024, 1, 2, tzinfo=UTC)
        sim.buy(10, OrderType.LIMIT, price=100.0)

        _advance(sim, bar1)
        assert sim.position.is_flat
        assert len(sim.pending_orders) == 1
        assert len(sim.triggered_orders) == 0

    def test_limit_sell_triggers_when_tp_at_or_above_limit(self) -> None:
        # First open a long position
        bar0 = _bar("2024-01-02", 100, 110, 90, 100)
        bar1 = _bar("2024-01-03", 98, 100, 90, 98)    # bar0 market buy fills here
        # tp bar1 = (100+90+98)/3 = 96; limit sell at 95 → tp >= 95 → NOT triggered
        bar2 = _bar("2024-01-04", 115, 125, 110, 120) # tp = (125+110+120)/3 ≈ 118.3 → triggered
        bar3 = _bar("2024-01-05", 118, 128, 112, 122) # fills at open=118

        sim = _make_sim()
        _advance(sim, bar0)
        sim.buy(10, OrderType.MARKET)    # triggered
        _advance(sim, bar1)              # long filled at bar1.open=98

        sim._current_bar = bar1
        sim.sell(10, OrderType.LIMIT, price=110.0)

        _advance(sim, bar2)  # tp ≈ 118.3 >= 110 → triggered
        assert not sim.position.is_flat  # not filled yet

        _advance(sim, bar3)  # fills at open=118
        assert sim.position.is_flat
        assert sim.realized_pnl == pytest.approx((118 - 98) * 10)


class TestStopOrder:
    def test_stop_sell_triggers_when_tp_at_or_below_stop(self) -> None:
        # Open long, then place stop-loss
        bar0 = _bar("2024-01-02", 100, 110, 90, 100)
        bar1 = _bar("2024-01-03", 102, 108, 96, 102)  # long fills at open=102
        # tp bar1 = (108+96+102)/3 = 102 — above stop 95, no trigger
        bar2 = _bar("2024-01-04", 88, 90, 80, 84)     # tp=(90+80+84)/3=84.7 → triggers
        bar3 = _bar("2024-01-05", 82, 88, 78, 83)     # fills at open=82

        sim = _make_sim()
        _advance(sim, bar0)
        sim.sell(10, OrderType.MARKET)    # short open triggered; we actually want long
        # Redo: use buy
        sim.reset()
        _advance(sim, bar0)
        sim.buy(10, OrderType.MARKET)
        _advance(sim, bar1)               # long filled at 102

        sim._current_bar = bar1
        sim.sell(10, OrderType.STOP, price=95.0)

        _advance(sim, bar2)  # tp ≈ 84.7 <= 95 → triggered
        assert not sim.position.is_flat

        _advance(sim, bar3)  # fills at open=82
        assert sim.position.is_flat
        assert sim.realized_pnl == pytest.approx((82 - 102) * 10)

    def test_stop_buy_triggers_when_tp_at_or_above_stop(self) -> None:
        bar0 = _bar("2024-01-02", 100, 110, 90, 100)
        bar1 = _bar("2024-01-04", 108, 120, 105, 115)  # tp=(120+105+115)/3=113.3 >= 110 → triggered
        bar2 = _bar("2024-01-05", 112, 122, 108, 118)  # fills at open=112

        sim = _make_sim()
        sim._current_bar = bar0
        sim.buy(10, OrderType.STOP, price=110.0)

        _advance(sim, bar1)  # triggered
        assert sim.position.is_flat

        _advance(sim, bar2)  # fills at 112
        assert sim.position.entry_price == pytest.approx(112.0)


class TestOrderCancel:
    def test_cancel_pending_order(self) -> None:
        sim = _make_sim()
        sim._current_bar = MagicMock()
        sim._current_bar.dt = datetime.datetime(2024, 1, 1, tzinfo=UTC)
        oid = sim.buy(10, OrderType.LIMIT, price=50.0)
        assert len(sim.pending_orders) == 1

        assert sim.cancel(oid) is True
        assert len(sim.pending_orders) == 0

    def test_cancel_triggered_order_not_possible(self) -> None:
        """Once triggered, an order is committed and cancel returns False."""
        bar = _bar("2024-01-03", 100, 110, 90, 100)
        sim = _make_sim()
        _advance(sim, bar)
        oid = sim.buy(10, OrderType.MARKET)  # immediately triggered

        assert sim.cancel(oid) is False
        assert len(sim.triggered_orders) == 1

    def test_cancel_nonexistent_returns_false(self) -> None:
        sim = _make_sim()
        assert sim.cancel(999) is False


class TestPositionNetting:
    def test_add_to_long_uses_weighted_average_entry(self) -> None:
        bar0 = _bar("2024-01-02", 100, 110, 90, 100)
        bar1 = _bar("2024-01-03", 100, 108, 92, 100)  # first buy fills at open=100
        bar2 = _bar("2024-01-04", 110, 118, 102, 110)  # second buy triggered bar1, fills at open=110
        bar3 = _bar("2024-01-05", 115, 120, 108, 115)  # just to read state

        sim = _make_sim()
        _advance(sim, bar0)
        sim.buy(10, OrderType.MARKET)
        _advance(sim, bar1)                 # fills 10 @ open=100
        sim.buy(10, OrderType.MARKET)
        _advance(sim, bar2)                 # fills 10 @ open=110

        expected_avg = (100 * 10 + 110 * 10) / 20
        assert sim.position.quantity == pytest.approx(20)
        assert sim.position.entry_price == pytest.approx(expected_avg)


class TestReset:
    def test_reset_clears_all_state(self) -> None:
        bar0 = _bar("2024-01-02", 100, 110, 90, 100)
        bar1 = _bar("2024-01-03", 105, 112, 98, 108)

        sim = _make_sim()
        _advance(sim, bar0)
        sim.buy(10, OrderType.MARKET)
        _advance(sim, bar1)

        sim.reset()
        assert sim.position.is_flat
        assert sim.cash == pytest.approx(1_000_000)
        assert sim.pending_orders == []
        assert sim.triggered_orders == []
        assert sim.trade_history == []
        assert sim.realized_pnl == pytest.approx(0)

    def test_reset_does_not_reload_cache(self) -> None:
        cache = MagicMock(spec=DataCache)
        sim = TradeSimulator(cache)
        sim.reset()
        cache.load.assert_not_called()


class TestEquity:
    def test_equity_includes_unrealized_pnl(self) -> None:
        bar0 = _bar("2024-01-02", 100, 110, 90, 100)
        bar1 = _bar("2024-01-03", 104, 112, 98, 108)  # long fills at 104
        bar2 = _bar("2024-01-04", 110, 118, 104, 114)  # price rises

        sim = _make_sim()
        _advance(sim, bar0)
        sim.buy(10, OrderType.MARKET)
        _advance(sim, bar1)   # filled at 104
        _advance(sim, bar2)   # current bar for equity calculation

        # equity = cash + unrealized P&L
        # cash = 1_000_000 - 104*10 = 998_960
        # unrealized = (bar2.typical_price - 104) * 10
        expected = (1_000_000 - 104 * 10) + (bar2.typical_price - 104) * 10
        assert sim.equity == pytest.approx(expected)
