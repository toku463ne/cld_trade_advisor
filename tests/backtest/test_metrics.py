"""Unit tests for backtest metrics computation."""

from __future__ import annotations

import datetime
import math

import pytest

from src.backtest.metrics import BacktestMetrics, compute_metrics
from src.backtest.runner import BacktestResult
from src.simulator.order import OrderSide, Trade

UTC = datetime.timezone.utc


def _make_trade(
    order_id: int,
    side: OrderSide,
    qty: float,
    price: float,
    dt_str: str,
    pnl: float,
) -> Trade:
    return Trade(
        order_id=order_id,
        side=side,
        quantity=qty,
        price=price,
        dt=datetime.datetime.fromisoformat(dt_str).replace(tzinfo=UTC),
        realized_pnl=pnl,
    )


def _result(
    equity: list[float],
    trades: list[Trade] | None = None,
    initial: float = 1_000_000,
) -> BacktestResult:
    n = len(equity)
    dts = [
        datetime.datetime(2024, 1, 1, tzinfo=UTC) + datetime.timedelta(days=i)
        for i in range(n)
    ]
    return BacktestResult(
        initial_capital=initial,
        final_equity=equity[-1] if equity else initial,
        equity_curve=equity,
        bar_dts=dts,
        trades=trades or [],
        open_position_pnl=0.0,
    )


class TestTotalReturn:
    def test_positive_return(self) -> None:
        r = _result([1_000_000, 1_100_000])
        m = compute_metrics(r)
        assert m.total_return_pct == pytest.approx(10.0)

    def test_negative_return(self) -> None:
        r = _result([1_000_000, 900_000])
        m = compute_metrics(r)
        assert m.total_return_pct == pytest.approx(-10.0)

    def test_flat_return(self) -> None:
        r = _result([1_000_000, 1_000_000])
        m = compute_metrics(r)
        assert m.total_return_pct == pytest.approx(0.0)


class TestMaxDrawdown:
    def test_simple_drawdown(self) -> None:
        # Peak at 1.2M, trough at 0.9M → DD = (0.9-1.2)/1.2 = -25%
        r = _result([1_000_000, 1_200_000, 900_000, 1_100_000])
        m = compute_metrics(r)
        assert m.max_drawdown_pct == pytest.approx(-25.0, rel=1e-3)

    def test_no_drawdown(self) -> None:
        r = _result([1_000_000, 1_100_000, 1_200_000])
        m = compute_metrics(r)
        assert m.max_drawdown_pct == pytest.approx(0.0, abs=1e-6)


class TestWinRate:
    def test_win_rate_two_wins_one_loss(self) -> None:
        trades = [
            _make_trade(1, OrderSide.BUY,  100, 1000.0, "2024-01-03", 0.0),
            _make_trade(1, OrderSide.SELL, 100, 1050.0, "2024-01-05", 5000.0),
            _make_trade(2, OrderSide.BUY,  100, 1050.0, "2024-01-06", 0.0),
            _make_trade(2, OrderSide.SELL, 100, 1020.0, "2024-01-08", -3000.0),
            _make_trade(3, OrderSide.BUY,  100, 1020.0, "2024-01-09", 0.0),
            _make_trade(3, OrderSide.SELL, 100, 1080.0, "2024-01-12", 6000.0),
        ]
        r = _result([1_000_000] * 15, trades=trades)
        m = compute_metrics(r)
        assert m.win_rate_pct == pytest.approx(100 * 2 / 3, rel=1e-3)
        assert m.total_trades == 3

    def test_zero_trades(self) -> None:
        r = _result([1_000_000] * 5)
        m = compute_metrics(r)
        assert m.win_rate_pct == pytest.approx(0.0)
        assert m.total_trades == 0


class TestProfitFactor:
    def test_profitable_strategy(self) -> None:
        trades = [
            _make_trade(1, OrderSide.BUY,  100, 1000.0, "2024-01-03", 0.0),
            _make_trade(1, OrderSide.SELL, 100, 1100.0, "2024-01-07", 10_000.0),
            _make_trade(2, OrderSide.BUY,  100, 1100.0, "2024-01-08", 0.0),
            _make_trade(2, OrderSide.SELL, 100, 1050.0, "2024-01-10", -5_000.0),
        ]
        r = _result([1_000_000] * 12, trades=trades)
        m = compute_metrics(r)
        assert m.profit_factor == pytest.approx(2.0)

    def test_all_wins_gives_inf(self) -> None:
        trades = [
            _make_trade(1, OrderSide.BUY,  100, 1000.0, "2024-01-03", 0.0),
            _make_trade(1, OrderSide.SELL, 100, 1100.0, "2024-01-07", 10_000.0),
        ]
        r = _result([1_000_000] * 8, trades=trades)
        m = compute_metrics(r)
        assert math.isinf(m.profit_factor)


class TestSharpe:
    def test_positive_sharpe_for_steadily_rising_equity(self) -> None:
        equity = [1_000_000 + i * 1000 for i in range(252)]
        r = _result(equity)
        m = compute_metrics(r)
        assert m.sharpe_ratio > 0

    def test_zero_sharpe_for_flat_equity(self) -> None:
        r = _result([1_000_000] * 252)
        m = compute_metrics(r)
        assert m.sharpe_ratio == pytest.approx(0.0)


class TestScore:
    def test_score_requires_minimum_3_trades(self) -> None:
        trades = [
            _make_trade(1, OrderSide.BUY,  100, 1000.0, "2024-01-03", 0.0),
            _make_trade(1, OrderSide.SELL, 100, 1100.0, "2024-01-07", 10_000.0),
        ]
        r = _result([1_000_000, 1_010_000, 1_020_000], trades=trades)
        m = compute_metrics(r)
        assert m.score == pytest.approx(-999.0)
