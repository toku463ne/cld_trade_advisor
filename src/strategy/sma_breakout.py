"""N-days-under-SMA breakout strategy.

Entry rule
----------
While watching: count consecutive bars where ``bar.high < SMA``.
When that count reaches N **and** the current bar's ``bar.low > SMA``
(price broke back above the SMA from below), submit a market buy.

Exit rules (whichever fires first)
-----------------------------------
* **Take profit**: close when typical_price >= entry_price * (1 + A)
* **Stop loss**:   close when typical_price <= entry_price * (1 - B)
* **Time stop**:   close after M bars in position

All condition checks use typical_price = (H+L+C)/3.
Actual fill price is the open of the next bar.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Sequence

from src.simulator.bar import BarData
from src.simulator.order import OrderType
from src.simulator.simulator import TradeSimulator
from src.strategy.base import ParamSpec, Strategy, StrategyPlugin


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SMABreakoutParams:
    sma_period: int    # moving-average window (e.g. 20)
    n_days: int        # minimum consecutive bars with high < SMA
    m_days: int        # maximum bars to hold position before forced exit
    tp: float          # take profit fraction (e.g. 0.05 = 5%)
    sl: float          # stop loss fraction  (e.g. 0.02 = 2%)
    units: int = 100   # shares per trade

    @property
    def indicator_name(self) -> str:
        return f"SMA{self.sma_period}"

    def label(self) -> str:
        return (
            f"sma{self.sma_period}"
            f"_N{self.n_days}"
            f"_M{self.m_days}"
            f"_TP{self.tp:.0%}"
            f"_SL{self.sl:.0%}"
        )


def make_param_grid(
    sma_periods:  Sequence[int]   = (20, 50),
    n_days_values: Sequence[int]  = (3, 5, 7),
    m_days_values: Sequence[int]  = (5, 10, 20),
    tp_values:    Sequence[float] = (0.02, 0.05, 0.10),
    sl_values:    Sequence[float] = (0.01, 0.02, 0.05),
    units: int = 100,
) -> list[SMABreakoutParams]:
    """Return every combination of the supplied parameter sequences."""
    return [
        SMABreakoutParams(
            sma_period=sma, n_days=n, m_days=m, tp=a, sl=b, units=units,
        )
        for sma, n, m, a, b in itertools.product(
            sma_periods, n_days_values, m_days_values, tp_values, sl_values,
        )
    ]


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class _State(IntEnum):
    WATCHING = 0      # counting consecutive days under SMA
    WAITING_FILL = 1  # buy order placed, awaiting next-bar open fill
    IN_POSITION = 2   # long position open, monitoring exit conditions
    CLOSING = 3       # sell order placed, awaiting next-bar open fill


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class SMABreakoutStrategy(Strategy):
    """Long-only N-days-under-SMA breakout with triple exit."""

    def __init__(self, params: SMABreakoutParams) -> None:
        self.params = params
        self._state: _State = _State.WATCHING
        self._under_count: int = 0
        self._bars_in_position: int = 0

    @property
    def name(self) -> str:
        p = self.params
        return (
            f"SMABreakout("
            f"sma={p.sma_period}, N={p.n_days}, M={p.m_days}, "
            f"TP={p.tp:.0%}, SL={p.sl:.0%})"
        )

    def reset(self) -> None:
        self._state = _State.WATCHING
        self._under_count = 0
        self._bars_in_position = 0

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    def on_bar(self, bar: BarData, sim: TradeSimulator) -> None:
        sma = bar.indicators.get(self.params.indicator_name, 0.0)
        if sma <= 0.0:
            return  # SMA not yet warmed up

        if self._state == _State.WATCHING:
            self._on_watching(bar, sim, sma)

        elif self._state == _State.WAITING_FILL:
            self._on_waiting_fill(bar, sim)

        elif self._state == _State.IN_POSITION:
            self._bars_in_position += 1
            self._on_in_position(bar, sim)

        elif self._state == _State.CLOSING:
            if sim.position.is_flat:
                self._state = _State.WATCHING
                self._under_count = 0
                self._bars_in_position = 0

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _on_watching(self, bar: BarData, sim: TradeSimulator, sma: float) -> None:
        if bar.high < sma:
            self._under_count += 1
        else:
            # Price has recovered above SMA on this bar
            if self._under_count >= self.params.n_days and bar.low > sma:
                # N+ consecutive highs under SMA, then today's low cleared it
                sim.buy(self.params.units, OrderType.MARKET)
                self._state = _State.WAITING_FILL
            self._under_count = 0

    def _on_waiting_fill(self, bar: BarData, sim: TradeSimulator) -> None:
        if sim.position.quantity > 0:
            # Buy was filled at this bar's open (handled by simulator)
            self._state = _State.IN_POSITION
            self._bars_in_position = 1
            # Check exit conditions on the fill bar too
            self._on_in_position(bar, sim)

    def _on_in_position(self, bar: BarData, sim: TradeSimulator) -> None:
        pos = sim.position
        if pos.entry_price <= 0:
            return

        pnl_pct = (bar.typical_price - pos.entry_price) / pos.entry_price
        should_close = (
            pnl_pct >= self.params.tp
            or pnl_pct <= -self.params.sl
            or self._bars_in_position >= self.params.m_days
        )
        if should_close:
            sim.sell(int(abs(pos.quantity)), OrderType.MARKET)
            self._state = _State.CLOSING


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class SMABreakoutPlugin(StrategyPlugin["SMABreakoutParams"]):
    @property
    def name(self) -> str:
        return "SMABreakout"

    @property
    def cli_name(self) -> str:
        return "sma_breakout"

    def setup_cache(self, cache: Any, _units: int) -> None:
        for period in (20, 50):
            cache.add_sma(period)

    def make_grid(self, units: int) -> list[SMABreakoutParams]:
        return make_param_grid(units=units)

    def make_strategy(self, params: SMABreakoutParams) -> SMABreakoutStrategy:
        return SMABreakoutStrategy(params)

    def param_labels(self) -> list[tuple[str, str, str]]:
        return [
            ("sma_period", "SMA",  "Moving average window"),
            ("n_days",     "N",    "Consecutive bars with high < SMA before entry"),
            ("m_days",     "M",    "Forced exit after this many bars"),
            ("tp",         "TP%",  "Close when gain ≥ TP"),
            ("sl",         "SL%",  "Close when loss ≥ SL"),
        ]

    def entry_exit_lines(self) -> list[str]:
        return [
            "- **Entry**: buy when `high < SMA` for ≥ N consecutive bars, "
            "then `low > SMA` on the next bar.",
            "- **Take profit**: close when typical_price ≥ entry_price × (1 + TP).",
            "- **Stop loss**: close when typical_price ≤ entry_price × (1 − SL).",
            "- **Time stop**: close after M bars in position (whichever comes first).",
            "- All orders fill at the **open of the next bar** after the condition fires.",
        ]

    def param_space(self) -> list[ParamSpec]:
        return [
            ParamSpec("sma_period", low=5,     high=200,  dtype=int),
            ParamSpec("n_days",     low=1,     high=30,   dtype=int),
            ParamSpec("m_days",     low=3,     high=60,   dtype=int),
            ParamSpec("tp",         low=0.01,  high=0.30, dtype=float),
            ParamSpec("sl",         low=0.005, high=0.15, dtype=float),
        ]

    def setup_cache_for_params(self, cache: Any, params: SMABreakoutParams) -> None:
        cache.add_sma(params.sma_period)

    def default_seeds(self) -> list[SMABreakoutParams]:
        return [
            SMABreakoutParams(sma_period=20, n_days=3, m_days=20, tp=0.10, sl=0.01),
            SMABreakoutParams(sma_period=20, n_days=3, m_days=10, tp=0.10, sl=0.01),
            SMABreakoutParams(sma_period=20, n_days=5, m_days=10, tp=0.10, sl=0.05),
        ]


from src.strategy.registry import register  # noqa: E402
register(SMABreakoutPlugin())
