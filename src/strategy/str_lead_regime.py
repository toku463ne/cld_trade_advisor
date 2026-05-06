"""StrLeadRegime — Relative-strength long after confirmed N225 trough.

Entry conditions (all must hold on the same hourly bar):
  1. StrLeadDetector fires at this exact bar (N225 just confirmed a LOW
     and this stock's drawdown was < 50 % of N225's over the same window).
  2. Regime gate: N225 daily close > 20-day SMA (bull market only).

Exit — first of:
  Time stop : max_hold_days trading days after entry.
  Hard stop : entry_price − atr_stop_mult × ATR14_daily (daily ATR14,
              derived from hourly bars).
  Zigzag    : first confirmed hourly HIGH zigzag peak after entry.

Fill model : two-bar — condition fires on bar N, order fills at bar N+1 open.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from src.indicators.corr_regime import CorrRegime
from src.indicators.zigzag import detect_peaks
from src.signs import StrLeadDetector
from src.simulator.bar import BarData
from src.simulator.cache import DataCache
from src.simulator.order import OrderType
from src.simulator.simulator import TradeSimulator
from src.strategy.base import Strategy

_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 2


@dataclass(frozen=True)
class StrLeadRegimeParams:
    max_hold_days:       int   = 20     # trading-day time stop
    atr_stop_mult:       float = 1.5    # hard stop = entry − mult × ATR14_daily
    min_hold_bars:       int   = 8      # min hourly bars before zigzag exit triggers
    daily_zz_exit:       bool  = False  # True = use daily zigzag exit; False = hourly
    use_regime_gate:     bool  = False  # False = skip N225>SMA20 filter (det handles it)
    capital_pct:         float = 0.10   # fraction of equity to allocate per trade
    units:               int   = 0      # fixed share count (0 = use capital_pct)
    min_score:           float = 0.0    # minimum sign score to enter (0 = no filter)


class _State(IntEnum):
    WATCHING     = 0
    WAITING_FILL = 1
    IN_POSITION  = 2
    CLOSING      = 3


class StrLeadRegimeStrategy(Strategy):
    """Initialise once with caches; call reset() before each backtest run."""

    def __init__(
        self,
        stock_cache:   DataCache,
        n225_cache:    DataCache,
        params:        StrLeadRegimeParams,
        corr_n225_1h:  dict[datetime.date, float] | None = None,
        allowed_dates: set[datetime.date] | None = None,
        corr_regime:   CorrRegime | None = None,
    ) -> None:
        self.params         = params
        self._allowed_dates = allowed_dates   # None = all dates allowed
        self._corr_regime   = corr_regime
        bars                = stock_cache.bars
        self._dts           = [b.dt for b in bars]

        # ── Sign detector ─────────────────────────────────────────────────
        self._detector = StrLeadDetector(stock_cache, n225_cache, corr_n225_1h)

        # ── Regime: N225 daily close > SMA-20 ────────────────────────────
        n225_day: dict[datetime.date, float] = {}
        for b in n225_cache.bars:
            n225_day[b.dt.date()] = b.close
        dates  = sorted(n225_day)
        n225_c = [n225_day[d] for d in dates]
        regime: dict[datetime.date, bool] = {}
        for i, d in enumerate(dates):
            if i < 20:
                regime[d] = True          # insufficient history → allow entry
            else:
                sma = sum(n225_c[i - 20 : i]) / 20
                regime[d] = n225_c[i] > sma
        self._regime = regime

        # ── Daily ATR14 (derived from hourly bars) ────────────────────────
        day_highs: dict[datetime.date, float] = {}
        day_lows:  dict[datetime.date, float] = {}
        day_close: dict[datetime.date, float] = {}
        for b in bars:
            d = b.dt.date()
            day_highs[d] = max(day_highs.get(d, -1e18), b.high)
            day_lows[d]  = min(day_lows.get(d,  1e18), b.low)
            day_close[d] = b.close
        day_dates   = sorted(day_highs)
        atr14_daily = _compute_atr14_daily(day_dates, day_highs, day_lows, day_close)
        self._atr_daily: dict[datetime.date, float] = dict(zip(day_dates, atr14_daily))

        # ── Confirmed hourly HIGH zigzag peak timestamps ──────────────────
        highs = [b.high for b in bars]
        lows  = [b.low  for b in bars]
        peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE)
        self._hi_confs: set[datetime.datetime] = {
            bars[p.bar_index + _ZZ_SIZE].dt
            for p in peaks
            if p.direction == 2 and p.bar_index + _ZZ_SIZE < len(bars)
        }

        # ── Confirmed DAILY HIGH zigzag peak dates ────────────────────────
        # Built from daily aggregated highs/lows; confirmation = peak_date + ZZ_SIZE days
        day_hi_list = [day_highs[d] for d in day_dates]
        day_lo_list = [day_lows[d]  for d in day_dates]
        daily_peaks = detect_peaks(day_hi_list, day_lo_list, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE)
        self._daily_hi_confs: set[datetime.date] = {
            day_dates[p.bar_index + _ZZ_SIZE]
            for p in daily_peaks
            if p.direction == 2 and p.bar_index + _ZZ_SIZE < len(day_dates)
        }

        self.reset()

    # ── Property / abstract impl ──────────────────────────────────────────

    @property
    def name(self) -> str:
        p = self.params
        return (
            f"StrLeadRegime("
            f"hold={p.max_hold_days}d, "
            f"atr={p.atr_stop_mult}×, "
            f"units={p.units})"
        )

    def reset(self) -> None:
        self._state:         _State               = _State.WATCHING
        self._entry_atr:     float                = 0.0
        self._entry_dt:      datetime.datetime | None = None
        self._entry_bar_idx: int                  = -1
        self._trade_dates:   set[datetime.date]   = set()
        self._bars_held:     int                  = 0

    # ── Bar dispatch ──────────────────────────────────────────────────────

    def on_bar(self, bar: BarData, sim: TradeSimulator) -> None:
        if self._state == _State.WATCHING:
            self._on_watching(bar, sim)
        elif self._state == _State.WAITING_FILL:
            self._on_waiting_fill(bar, sim)
        elif self._state == _State.IN_POSITION:
            self._trade_dates.add(bar.dt.date())
            self._on_in_position(bar, sim)
        elif self._state == _State.CLOSING:
            if sim.position.is_flat:
                self._state = _State.WATCHING

    # ── State handlers ────────────────────────────────────────────────────

    def _on_watching(self, bar: BarData, sim: TradeSimulator) -> None:
        if self.params.use_regime_gate and not self._regime.get(bar.dt.date(), True):
            return
        if self._corr_regime is not None and self._corr_regime.is_high(bar.dt.date()):
            return
        sign = self._detector.detect(bar.dt, valid_bars=0)
        if (sign is not None
                and sign.fired_at == bar.dt
                and sign.score >= self.params.min_score
                and (self._allowed_dates is None
                     or bar.dt.date() in self._allowed_dates)):
            if self.params.units > 0:
                qty = self.params.units
            else:
                # Capital-fraction sizing: use signal bar close as fill price proxy
                qty = max(1, int(sim.equity * self.params.capital_pct / bar.close))
            sim.buy(qty, OrderType.MARKET)
            self._state = _State.WAITING_FILL

    def _on_waiting_fill(self, bar: BarData, sim: TradeSimulator) -> None:
        if sim.position.quantity > 0:
            self._entry_dt   = bar.dt
            self._entry_atr  = self._atr_daily.get(bar.dt.date(), 0.0)
            self._trade_dates = {bar.dt.date()}
            self._bars_held  = 0
            self._state      = _State.IN_POSITION
            self._on_in_position(bar, sim)

    def _on_in_position(self, bar: BarData, sim: TradeSimulator) -> None:
        pos = sim.position
        if pos.entry_price <= 0 or pos.is_flat:
            return

        self._bars_held += 1
        stop = pos.entry_price - self.params.atr_stop_mult * self._entry_atr

        time_hit   = len(self._trade_dates) >= self.params.max_hold_days
        atr_hit    = bar.low <= stop
        min_passed = self._bars_held >= self.params.min_hold_bars
        if self.params.daily_zz_exit:
            zigzag_hit = (
                min_passed
                and bar.dt.date() in self._daily_hi_confs
                and self._entry_dt is not None
                and bar.dt > self._entry_dt
            )
        else:
            zigzag_hit = (
                min_passed
                and bar.dt in self._hi_confs
                and self._entry_dt is not None
                and bar.dt > self._entry_dt
            )

        if time_hit or atr_hit or zigzag_hit:
            sim.sell(int(abs(pos.quantity)), OrderType.MARKET)
            self._state = _State.CLOSING


# ── Helper ────────────────────────────────────────────────────────────────────

def _compute_atr14_daily(
    dates:  list[datetime.date],
    highs:  dict[datetime.date, float],
    lows:   dict[datetime.date, float],
    closes: dict[datetime.date, float],
) -> list[float]:
    """Wilder's ATR-14 on daily OHLC data."""
    atr: list[float] = []
    for i, d in enumerate(dates):
        h, l = highs[d], lows[d]
        if i == 0:
            tr = h - l
        else:
            pc = closes[dates[i - 1]]
            tr = max(h - l, abs(h - pc), abs(l - pc))
        if not atr:
            atr.append(tr)
        elif len(atr) < 14:
            atr.append((sum(atr) + tr) / (len(atr) + 1))
        else:
            atr.append((atr[-1] * 13 + tr) / 14)
    return atr
