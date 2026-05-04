"""Single backtest run: drives the simulator bar-by-bar through a DataCache."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from src.simulator.cache import DataCache
from src.simulator.order import Trade
from src.simulator.simulator import TradeSimulator
from src.strategy.base import Strategy


@dataclass
class BacktestResult:
    """Everything produced by one simulation run."""

    initial_capital: float
    final_equity: float
    equity_curve: list[float]        # equity at the close of every bar
    bar_dts: list[datetime.datetime] # parallel datetimes
    trades: list[Trade]              # all filled trades (buys and sells)
    open_position_pnl: float         # unrealized P&L at end (0 if flat)


def run_backtest(
    strategy: Strategy,
    sim: TradeSimulator,
    cache: DataCache,
) -> BacktestResult:
    """Run *strategy* on *cache* using *sim*, returning full results.

    *sim* is reset at the start so the same instance can be reused across
    thousands of training iterations without re-allocation.
    """
    sim.reset()
    strategy.reset()

    equity_curve: list[float] = []

    for bar in cache.bars:
        sim.tick(bar.dt)
        strategy.on_bar(bar, sim)
        equity_curve.append(sim.equity)

    result = sim.result()
    return BacktestResult(
        initial_capital=result.initial_capital,
        final_equity=result.final_equity,
        equity_curve=equity_curve,
        bar_dts=list(cache.datetimes),
        trades=list(sim.trade_history),
        open_position_pnl=result.unrealized_pnl,
    )
