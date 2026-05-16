"""SMA-breadth regime indicator.

For each trading date, computes the fraction of universe stocks whose
close exceeds their own N-day simple moving average.  When this
breadth fraction sits above its historical 80th percentile, the market
is in a "high trend-extension" regime — most stocks are above trend,
and forward index returns are systematically dampened.

Empirical validation (2026-05-16 cohort bootstrap, FY2024 + FY2025 +
2026 YTD, 10 k iterations):

- FY2024:   Δ(top quintile − bottom) forward 10-bar N225 = −8.73 pp,
            95 % CI [−10.68, −6.84].
- FY2025:   Δ = −1.76 pp, 95 % CI [−3.52, −0.02].
- 2026 YTD: Δ = −6.32 pp, 95 % CI [−9.43, −3.20].
- ALL:      Δ = −2.64 pp, 95 % CI [−3.57, −1.69].

Passes in all 3 individual cohorts plus aggregate; strongest standalone
signal in the breadth-indicator family (see ``docs/analysis/breadth_indicators.md``).

Companion of :class:`~src.indicators.rev_n_regime.RevNRegime` — the
two are moderately correlated (r ≈ 0.49) but rev_nhi adds material
information when SMA(50) is HIGH (joint AND-cell forward N225 ≈ 0 %),
so they ship together as a complementary pair.

Typical usage::

    regime = SMARegime.build(
        stock_caches=universe_caches,
        dates=n225_trading_dates,
        sma_n=50,
    )

    if regime.is_high(today):
        # high trend-extension: dampen new-entry sizing or skip
        ...
"""

from __future__ import annotations

import datetime

import numpy as np

from src.simulator.cache import DataCache


_SMA_N        = 50
_REGIME_PCT   = 0.80


class SMARegime:
    """Pre-loaded SMA-breadth regime gate.

    Parameters
    ----------
    frac_by_date:
        Mapping date → fraction of universe with close above own SMA.
    regime_percentile:
        Historical percentile cut-off (default 0.80 — top quintile).
    """

    def __init__(
        self,
        frac_by_date: dict[datetime.date, float],
        regime_percentile: float = _REGIME_PCT,
    ) -> None:
        self._frac = frac_by_date
        values = list(frac_by_date.values())
        if values:
            self._cutoff: float = float(
                np.nanpercentile(values, regime_percentile * 100)
            )
        else:
            self._cutoff = 1.0
        self._regime_pct = regime_percentile

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        stock_caches: dict[str, DataCache],
        dates: list[datetime.date],
        sma_n: int = _SMA_N,
        regime_percentile: float = _REGIME_PCT,
        min_active: int = 50,
    ) -> "SMARegime":
        """Build the regime gate from pre-loaded OHLCV caches.

        Args:
            stock_caches: Mapping stock_code → DataCache (already loaded).
            dates:        Trading dates to compute breadth for.
            sma_n:        SMA lookback in trading days (default 50).
            regime_percentile: Historical percentile cut-off (default 0.80).
            min_active:   Minimum number of stocks with computed SMA on a
                          date before that date's breadth is recorded
                          (default 50 — avoid noisy early dates with
                          sparse SMA coverage).
        """
        if not stock_caches or not dates:
            return cls({}, regime_percentile)

        # Pre-compute per-stock daily close + per-date SMA value
        stock_close_sma: dict[str, dict[datetime.date, tuple[float, float]]] = {}
        for code, cache in stock_caches.items():
            if not cache.bars:
                continue
            bars_by_date: dict[datetime.date, list] = {}
            for b in cache.bars:
                bars_by_date.setdefault(b.dt.date(), []).append(b)
            sorted_dates = sorted(bars_by_date)
            closes = [bars_by_date[d][-1].close for d in sorted_dates]
            cs_map: dict[datetime.date, tuple[float, float]] = {}
            for i in range(sma_n, len(sorted_dates)):
                sma = sum(closes[i - sma_n : i]) / sma_n
                cs_map[sorted_dates[i]] = (float(closes[i]), float(sma))
            stock_close_sma[code] = cs_map

        frac_by_date: dict[datetime.date, float] = {}
        for d in dates:
            n_above = 0
            n_active = 0
            for code, cs in stock_close_sma.items():
                v = cs.get(d)
                if v is None:
                    continue
                n_active += 1
                if v[0] > v[1]:
                    n_above += 1
            if n_active >= min_active:
                frac_by_date[d] = n_above / n_active

        return cls(frac_by_date, regime_percentile)

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def frac(self, date: datetime.date) -> float:
        """Fraction of universe above own SMA on *date*. NaN if no data."""
        return self._frac.get(date, float("nan"))

    def is_high(self, date: datetime.date) -> bool:
        """True when breadth ≥ historical cutoff. False when no data."""
        f = self._frac.get(date)
        if f is None:
            return False
        return f >= self._cutoff

    def percentile(self, date: datetime.date) -> float:
        """Empirical percentile rank (0-100) of *date*'s breadth."""
        f = self._frac.get(date)
        if f is None:
            return float("nan")
        values = sorted(self._frac.values())
        if not values:
            return float("nan")
        rank = sum(1 for v in values if v <= f)
        return 100.0 * rank / len(values)

    @property
    def cutoff(self) -> float:
        """High-regime breadth threshold (the regime_percentile cut-off)."""
        return self._cutoff

    @property
    def regime_percentile(self) -> float:
        """The percentile used to define the high-regime cut-off (e.g. 0.80)."""
        return self._regime_pct

    def __repr__(self) -> str:
        return (
            f"SMARegime(dates={len(self._frac)}, "
            f"cutoff={self._cutoff:.3f}, "
            f"pct={self._regime_pct:.2f})"
        )
