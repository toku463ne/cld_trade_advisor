"""Bollinger Band breakout strategy.

Entry logic
-----------
Consolidation phase (N consecutive bars):
  - high < SMA_P + sigma * RSTD_P   (candle stays below upper band)
  - close > SMA_P                    (price above middle band)

Signal bar:
  - close >= SMA_P + sigma * RSTD_P  (close breaks above upper band)
→ Market buy order; fills at next bar's open.

Exit logic (whichever fires first)
-----------------------------------
- Take profit : typical_price >= entry_price * (1 + tp)
- Stop loss   : typical_price <= entry_price * (1 − sl)
- Time stop   : bars_in_position >= m_days
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


@dataclass(frozen=True)
class BollingerBreakoutParams:
    sma_period: int    # P: Bollinger Band SMA / std window
    sigma_mult: float  # A: upper-band multiplier (e.g. 2.0 → 2σ)
    n_days: int        # N: consecutive consolidation bars required
    m_days: int        # M: time stop (bars in position)
    tp: float          # take profit fraction (e.g. 0.05 = 5%)
    sl: float          # stop loss fraction  (e.g. 0.02 = 2%)
    units: int = 100   # shares per trade

    @property
    def sma_key(self) -> str:
        return f"SMA{self.sma_period}"

    @property
    def rstd_key(self) -> str:
        return f"RSTD{self.sma_period}"

    def label(self) -> str:
        return (
            f"bb{self.sma_period}_s{self.sigma_mult}"
            f"_N{self.n_days}_M{self.m_days}"
            f"_TP{self.tp:.0%}_SL{self.sl:.0%}"
        )


def make_param_grid(
    sma_periods:   Sequence[int]   = (20, 50),
    sigma_values:  Sequence[float] = (1.5, 2.0, 2.5),
    n_days_values: Sequence[int]   = (3, 5, 7),
    m_days_values: Sequence[int]   = (10, 20),
    tp_values:     Sequence[float] = (0.05, 0.10),
    sl_values:     Sequence[float] = (0.02, 0.03),
    units: int = 100,
) -> list[BollingerBreakoutParams]:
    """Return every combination of the supplied parameter sequences."""
    return [
        BollingerBreakoutParams(
            sma_period=p, sigma_mult=s, n_days=n,
            m_days=m, tp=tp, sl=sl, units=units,
        )
        for p, s, n, m, tp, sl in itertools.product(
            sma_periods, sigma_values, n_days_values,
            m_days_values, tp_values, sl_values,
        )
    ]


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class _State(IntEnum):
    WATCHING     = 0
    WAITING_FILL = 1
    IN_POSITION  = 2
    CLOSING      = 3


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class BollingerBreakoutStrategy(Strategy):
    """Long-only Bollinger Band breakout with triple exit."""

    def __init__(self, params: BollingerBreakoutParams) -> None:
        self._params = params
        self._state: _State = _State.WATCHING
        self._under_count: int = 0
        self._bars_in_position: int = 0

    @property
    def name(self) -> str:
        p = self._params
        return (
            f"BollingerBreakout("
            f"sma={p.sma_period}, σ={p.sigma_mult}, N={p.n_days}, M={p.m_days}, "
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
        if self._state == _State.WATCHING:
            self._on_watching(bar, sim)
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

    def _on_watching(self, bar: BarData, sim: TradeSimulator) -> None:
        sma = bar.indicators.get(self._params.sma_key, 0.0)
        std = bar.indicators.get(self._params.rstd_key, 0.0)
        if std == 0.0:
            return  # indicator not yet warmed up

        upper = sma + self._params.sigma_mult * std

        # Signal: enough consolidation AND close breaks above upper band
        if self._under_count >= self._params.n_days and bar.close >= upper:
            sim.buy(self._params.units, OrderType.MARKET)
            self._state = _State.WAITING_FILL
            self._under_count = 0
            return

        # Update consolidation counter
        if bar.high < upper and bar.close > sma:
            self._under_count += 1
        else:
            self._under_count = 0

    def _on_waiting_fill(self, bar: BarData, sim: TradeSimulator) -> None:
        if sim.position.quantity > 0:
            self._state = _State.IN_POSITION
            self._bars_in_position = 1
            self._on_in_position(bar, sim)

    def _on_in_position(self, bar: BarData, sim: TradeSimulator) -> None:
        pos = sim.position
        if pos.entry_price <= 0:
            return

        entry = pos.entry_price
        tp_price = entry * (1.0 + self._params.tp)
        sl_price = entry * (1.0 - self._params.sl)

        if (
            bar.typical_price >= tp_price
            or bar.typical_price <= sl_price
            or self._bars_in_position >= self._params.m_days
        ):
            sim.sell(int(abs(pos.quantity)), OrderType.MARKET)
            self._state = _State.CLOSING


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class BollingerBreakoutPlugin(StrategyPlugin["BollingerBreakoutParams"]):
    @property
    def name(self) -> str:
        return "BollingerBreakout"

    @property
    def cli_name(self) -> str:
        return "bollinger_breakout"

    def setup_cache(self, cache: Any, _units: int) -> None:
        for period in (20, 50):
            cache.add_sma(period)
            cache.add_rolling_std(period)

    def make_grid(self, units: int) -> list[BollingerBreakoutParams]:
        return make_param_grid(units=units)

    def make_strategy(self, params: BollingerBreakoutParams) -> BollingerBreakoutStrategy:
        return BollingerBreakoutStrategy(params)

    def param_labels(self) -> list[tuple[str, str, str]]:
        return [
            ("sma_period",  "SMA", "Bollinger Band SMA / std window"),
            ("sigma_mult",  "σ",   "Upper band multiplier (upper = SMA + σ × std)"),
            ("n_days",      "N",   "Consecutive consolidation bars required"),
            ("m_days",      "M",   "Forced exit after this many bars"),
            ("tp",          "TP%", "Close when gain ≥ TP"),
            ("sl",          "SL%", "Close when loss ≥ SL"),
        ]

    def entry_exit_lines(self) -> list[str]:
        return [
            "- **Entry**: consolidate N bars where `high < SMA + σ×std` AND `close > SMA`, "
            "then `close ≥ SMA + σ×std`.",
            "- **Take profit**: close when typical_price ≥ entry_price × (1 + TP).",
            "- **Stop loss**: close when typical_price ≤ entry_price × (1 − SL).",
            "- **Time stop**: close after M bars in position (whichever fires first).",
            "- All orders fill at the **open of the next bar** after the condition fires.",
        ]

    def param_space(self) -> list[ParamSpec]:
        return [
            ParamSpec("sma_period", low=5,     high=200,  dtype=int),
            ParamSpec("sigma_mult", low=0.5,   high=4.0,  dtype=float),
            ParamSpec("n_days",     low=1,     high=30,   dtype=int),
            ParamSpec("m_days",     low=3,     high=60,   dtype=int),
            ParamSpec("tp",         low=0.01,  high=0.30, dtype=float),
            ParamSpec("sl",         low=0.005, high=0.15, dtype=float),
        ]

    def setup_cache_for_params(self, cache: Any, params: BollingerBreakoutParams) -> None:
        cache.add_sma(params.sma_period)
        cache.add_rolling_std(params.sma_period)

    def default_seeds(self) -> list[BollingerBreakoutParams]:
        return [
            BollingerBreakoutParams(sma_period=20, sigma_mult=2.0, n_days=3, m_days=20, tp=0.10, sl=0.02),
            BollingerBreakoutParams(sma_period=20, sigma_mult=1.5, n_days=5, m_days=10, tp=0.05, sl=0.03),
            BollingerBreakoutParams(sma_period=50, sigma_mult=2.0, n_days=5, m_days=20, tp=0.10, sl=0.03),
        ]


from src.strategy.registry import register  # noqa: E402
register(BollingerBreakoutPlugin())
