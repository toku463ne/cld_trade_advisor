"""Compute performance metrics from a BacktestResult."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean

import numpy as np

from src.backtest.runner import BacktestResult
from src.simulator.order import OrderSide

# Approximate bars per calendar year for each granularity
_BARS_PER_YEAR: dict[str, float] = {
    "1m":  252 * 390,
    "5m":  252 * 78,
    "15m": 252 * 26,
    "30m": 252 * 13,
    "1h":  252 * 6.5,
    "1d":  252.0,
    "1wk": 52.0,
}


@dataclass
class BacktestMetrics:
    """Performance summary for one backtest run."""

    total_return_pct: float       # (final_equity / initial - 1) * 100
    annualized_return_pct: float  # CAGR
    sharpe_ratio: float           # annualized, using bar-level returns
    max_drawdown_pct: float       # peak-to-trough (negative value)
    win_rate_pct: float           # % of closed trades that were profitable
    profit_factor: float          # gross_profit / gross_loss
    total_trades: int             # number of completed round-trips
    avg_holding_days: float       # mean calendar days per trade
    score: float                  # composite ranking score (higher = better)


def compute_metrics(result: BacktestResult, granularity: str = "1d") -> BacktestMetrics:
    """Derive all metrics from *result*."""
    equity = np.asarray(result.equity_curve, dtype=np.float64)
    n = len(equity)

    if n < 2 or result.initial_capital <= 0:
        return _zero_metrics()

    bars_per_year = _BARS_PER_YEAR.get(granularity, 252.0)

    # ------------------------------------------------------------------ returns
    total_return = (result.final_equity / result.initial_capital - 1.0) * 100.0
    years = n / bars_per_year
    # Clamp ratio to 0 before fractional exponent: negative base → complex in Python.
    ratio = max(result.final_equity / result.initial_capital, 0.0)
    annualized = (
        (ratio ** (1.0 / years) - 1.0) * 100.0
        if years > 0 else 0.0
    )

    # ----------------------------------------------------------------- Sharpe
    bar_returns = np.diff(equity) / equity[:-1]
    std = float(bar_returns.std())
    sharpe = (
        float(bar_returns.mean()) / std * math.sqrt(bars_per_year)
        if std > 1e-12 else 0.0
    )

    # --------------------------------------------------------------- drawdown
    running_peak = np.maximum.accumulate(equity)
    drawdowns = (equity - running_peak) / running_peak
    max_dd = float(drawdowns.min()) * 100.0

    # ----------------------------------------------------------- trade stats
    buy_trades  = [t for t in result.trades if t.side == OrderSide.BUY]
    sell_trades = [t for t in result.trades if t.side == OrderSide.SELL]
    n_closed = min(len(buy_trades), len(sell_trades))  # completed round-trips

    if n_closed > 0:
        closed_sells = sell_trades[:n_closed]
        wins = [t for t in closed_sells if t.realized_pnl > 0]
        win_rate = len(wins) / n_closed * 100.0

        gross_profit = sum(t.realized_pnl for t in wins)
        gross_loss   = abs(sum(t.realized_pnl for t in closed_sells if t.realized_pnl < 0))
        profit_factor = (
            gross_profit / gross_loss if gross_loss > 1e-10
            else (float("inf") if gross_profit > 0 else 0.0)
        )

        pairs = list(zip(buy_trades[:n_closed], closed_sells))
        avg_holding = mean([(s.dt - b.dt).days for b, s in pairs])
    else:
        win_rate = 0.0
        profit_factor = 0.0
        avg_holding = 0.0

    # ------------------------------------------------------------------ score
    # Calmar-like: annualised return / |max drawdown|.
    # Requires at least 3 completed trades to be meaningful.
    abs_dd = max(abs(max_dd), 0.1)
    score = annualized / abs_dd if n_closed >= 3 else -999.0

    return BacktestMetrics(
        total_return_pct=round(total_return, 4),
        annualized_return_pct=round(annualized, 4),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown_pct=round(max_dd, 4),
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(profit_factor, 4),
        total_trades=n_closed,
        avg_holding_days=round(avg_holding, 1),
        score=round(score, 4),
    )


def _zero_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        total_return_pct=0.0,
        annualized_return_pct=0.0,
        sharpe_ratio=0.0,
        max_drawdown_pct=0.0,
        win_rate_pct=0.0,
        profit_factor=0.0,
        total_trades=0,
        avg_holding_days=0.0,
        score=-999.0,
    )
