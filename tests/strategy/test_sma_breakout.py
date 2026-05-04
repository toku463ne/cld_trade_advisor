"""Unit tests for SMABreakoutStrategy — no DB or real DataCache required."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from src.simulator.bar import BarData
from src.simulator.cache import DataCache
from src.simulator.order import OrderSide, OrderType
from src.simulator.simulator import TradeSimulator
from src.strategy.sma_breakout import SMABreakoutParams, SMABreakoutStrategy, _State

UTC = datetime.timezone.utc
SMA = 2000.0
DEFAULT_PARAMS = SMABreakoutParams(
    sma_period=20, n_days=3, m_days=10,
    tp=0.05, sl=0.02, units=100
)


def _bar(
    dt_str: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    sma: float = SMA,
) -> BarData:
    return BarData(
        dt=datetime.datetime.fromisoformat(dt_str).replace(tzinfo=UTC),
        open=open_, high=high, low=low, close=close, volume=10000,
        indicators={"SMA20": sma},
    )


def _make_sim() -> TradeSimulator:
    cache = MagicMock(spec=DataCache)
    return TradeSimulator(cache, initial_capital=1_000_000)


def _advance(sim: TradeSimulator, bar: BarData) -> None:
    sim._current_bar = bar
    sim._process_orders(bar)


class TestWatchingState:
    def test_counts_consecutive_bars_under_sma(self) -> None:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)
        sim = _make_sim()

        for day in range(1, 4):
            bar = _bar(f"2024-01-0{day}", 1900, 1950, 1800, 1900)
            _advance(sim, bar)
            strat.on_bar(bar, sim)

        assert strat._under_count == 3
        assert strat._state == _State.WATCHING

    def test_resets_count_on_high_above_sma(self) -> None:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)
        sim = _make_sim()

        for day in range(1, 3):
            bar = _bar(f"2024-01-0{day}", 1900, 1950, 1800, 1900)
            _advance(sim, bar)
            strat.on_bar(bar, sim)
        assert strat._under_count == 2

        # high > SMA, low <= SMA → no signal, count resets
        bar = _bar("2024-01-03", 1900, 2050, 1950, 2000)
        _advance(sim, bar)
        strat.on_bar(bar, sim)
        assert strat._under_count == 0

    def test_no_signal_when_count_below_n(self) -> None:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)  # n_days=3
        sim = _make_sim()

        # Only 2 under-SMA days, then breakout
        for day in range(1, 3):
            bar = _bar(f"2024-01-0{day}", 1900, 1950, 1800, 1900)
            _advance(sim, bar)
            strat.on_bar(bar, sim)

        bar = _bar("2024-01-03", 1900, 2100, 2020, 2050)  # low > SMA but count=2 < 3
        _advance(sim, bar)
        strat.on_bar(bar, sim)

        assert strat._state == _State.WATCHING
        assert len(sim.pending_orders) == 0
        assert len(sim.triggered_orders) == 0

    def test_buy_order_placed_when_signal_fires(self) -> None:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)  # n_days=3
        sim = _make_sim()

        for day in range(1, 4):
            bar = _bar(f"2024-01-0{day}", 1900, 1950, 1800, 1900)
            _advance(sim, bar)
            strat.on_bar(bar, sim)

        # Signal bar: high > SMA, low > SMA, count >= 3
        signal_bar = _bar("2024-01-04", 1900, 2100, 2020, 2060)
        _advance(sim, signal_bar)
        strat.on_bar(signal_bar, sim)

        assert strat._state == _State.WAITING_FILL
        assert len(sim.triggered_orders) == 1
        order = sim.triggered_orders[0]
        assert order.side == OrderSide.BUY
        assert order.type == OrderType.MARKET
        assert order.quantity == 100


class TestWaitingFillState:
    def _setup_waiting(self) -> tuple[SMABreakoutStrategy, TradeSimulator]:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)
        sim = _make_sim()
        for day in range(1, 4):
            bar = _bar(f"2024-01-0{day}", 1900, 1950, 1800, 1900)
            _advance(sim, bar)
            strat.on_bar(bar, sim)
        signal_bar = _bar("2024-01-04", 1900, 2100, 2020, 2060)
        _advance(sim, signal_bar)
        strat.on_bar(signal_bar, sim)
        assert strat._state == _State.WAITING_FILL
        return strat, sim

    def test_transitions_to_in_position_when_filled(self) -> None:
        strat, sim = self._setup_waiting()
        # Fill bar: triggered order fills at its open
        fill_bar = _bar("2024-01-05", 2030, 2100, 2010, 2050)
        _advance(sim, fill_bar)  # fills at 2030
        strat.on_bar(fill_bar, sim)
        assert strat._state == _State.IN_POSITION
        assert sim.position.quantity == pytest.approx(100)
        assert sim.position.entry_price == pytest.approx(2030.0)  # fill bar open


class TestExitConditions:
    def _setup_in_position(
        self, entry_price: float = 2030.0
    ) -> tuple[SMABreakoutStrategy, TradeSimulator]:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)
        sim = _make_sim()
        for day in range(1, 4):
            bar = _bar(f"2024-01-0{day}", 1900, 1950, 1800, 1900)
            _advance(sim, bar)
            strat.on_bar(bar, sim)
        signal_bar = _bar("2024-01-04", 1900, 2100, 2020, 2060)
        _advance(sim, signal_bar)
        strat.on_bar(signal_bar, sim)
        fill_bar = _bar("2024-01-05", entry_price, entry_price + 50, entry_price - 10, entry_price + 20)
        _advance(sim, fill_bar)
        strat.on_bar(fill_bar, sim)
        assert strat._state == _State.IN_POSITION
        return strat, sim

    def test_take_profit_triggers_sell(self) -> None:
        strat, sim = self._setup_in_position(entry_price=2000.0)
        # take_profit=5%; tp needs to be >= 2000 * 1.05 = 2100
        # typical_price = (H+L+C)/3; set bar so tp = 2110
        # H=2200, L=2050, C=2080 → tp = (2200+2050+2080)/3 = 2110
        bar = _bar("2024-01-06", 2100, 2200, 2050, 2080)
        _advance(sim, bar)
        strat.on_bar(bar, sim)
        assert strat._state == _State.CLOSING
        assert len(sim.triggered_orders) == 1
        assert sim.triggered_orders[0].side == OrderSide.SELL

    def test_stop_loss_triggers_sell(self) -> None:
        strat, sim = self._setup_in_position(entry_price=2000.0)
        # stop_loss=2%; tp needs to be <= 2000 * 0.98 = 1960
        # H=1970, L=1920, C=1940 → tp = (1970+1920+1940)/3 = 1943.3
        bar = _bar("2024-01-06", 2000, 1970, 1920, 1940)
        _advance(sim, bar)
        strat.on_bar(bar, sim)
        assert strat._state == _State.CLOSING

    def test_time_stop_triggers_sell(self) -> None:
        strat, sim = self._setup_in_position(entry_price=2000.0)
        # m_days=10; after 10 bars, force close
        for i in range(6, 15):  # days 6 to 14 = 9 more bars; with fill day = 10 total
            bar = _bar(f"2024-01-{i:02d}", 2000, 2020, 1990, 2010)
            _advance(sim, bar)
            strat.on_bar(bar, sim)
            if strat._state == _State.CLOSING:
                break

        assert strat._state == _State.CLOSING

    def test_transitions_back_to_watching_after_close(self) -> None:
        strat, sim = self._setup_in_position(entry_price=2000.0)
        # Trigger stop loss
        stop_bar = _bar("2024-01-06", 2000, 1970, 1920, 1940)
        _advance(sim, stop_bar)
        strat.on_bar(stop_bar, sim)
        assert strat._state == _State.CLOSING

        # Close fills on next bar
        close_bar = _bar("2024-01-07", 1950, 1980, 1940, 1960)
        _advance(sim, close_bar)  # fills sell at 1950
        strat.on_bar(close_bar, sim)
        assert strat._state == _State.WATCHING
        assert sim.position.is_flat


class TestReset:
    def test_reset_clears_all_state(self) -> None:
        strat = SMABreakoutStrategy(DEFAULT_PARAMS)
        strat._under_count = 5
        strat._state = _State.IN_POSITION
        strat._bars_in_position = 3
        strat.reset()
        assert strat._state == _State.WATCHING
        assert strat._under_count == 0
        assert strat._bars_in_position == 0
