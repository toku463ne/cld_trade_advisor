"""rev_nhi-breadth regime indicator.

For each trading date, computes the fraction of universe stocks that
fired ``rev_nhi`` (touched their prior-N-day high AND closed with a
bearish body) on that date.  When this fraction sits above its
historical ``regime_percentile``, the market is in a "high reversal
risk" regime — many stocks are exhausted at multi-day highs, and
forward index returns are systematically dampened.

Empirical validation (2026-05-16, FY2024 + FY2025 + 2026 YTD bootstrap):
- FY2024:  Δ(top quintile − bottom quintile) forward 10-bar N225 return
           = −4.83pp, 95% CI [−6.62, −3.10].
- FY2025:  Δ = −2.38pp, 95% CI [−3.95, −0.80].
- ALL:     Δ = −1.44pp, 95% CI [−2.37, −0.52].
The signal holds in both bear-ish and bull cohorts. See memory file
project_rev_n_regime_indicator.md for details.

Typical usage from a strategy::

    regime = RevNRegime.build(
        stock_caches=universe_caches,
        dates=n225_trading_dates,
        n_days=20,
    )

    if regime.is_high(today):
        # high reversal-risk: dampen new-entry sizing or skip
        ...
"""

from __future__ import annotations

import datetime

import numpy as np

from src.signs.rev_nday import RevNDayDetector
from src.simulator.cache import DataCache


_N_DAYS         = 20
_REGIME_PCT     = 0.80
_SIDE_DEFAULT   = "hi"


class RevNRegime:
    """Pre-loaded reversal-breadth regime gate.

    Parameters
    ----------
    frac_by_date:
        Mapping date → fraction of universe firing the rev_nday detector
        on that date (0.0 – 1.0).
    regime_percentile:
        Historical percentile used to set the high-regime cut-off.
        Default 0.80 — top quintile of breadth triggers ``is_high``.
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
            self._cutoff = 1.0   # never fires when no data
        self._regime_pct = regime_percentile

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        stock_caches: dict[str, DataCache],
        dates: list[datetime.date],
        n_days: int = _N_DAYS,
        regime_percentile: float = _REGIME_PCT,
        side: str = _SIDE_DEFAULT,
    ) -> "RevNRegime":
        """Build the regime gate from pre-loaded OHLCV caches.

        Args:
            stock_caches: Mapping stock_code → DataCache (already loaded).
            dates:        List of trading dates to compute breadth for
                          (typically the union of ^N225 trading dates).
            n_days:       Lookback window for rev_nday detector (default 20).
            regime_percentile: Historical percentile cut-off (default 0.80).
            side:         "hi" (rev_nhi) or "lo" (rev_nlo).

        Returns:
            RevNRegime with computed per-date breadth and historical cutoff.
        """
        if not stock_caches or not dates:
            return cls({}, regime_percentile)

        # Build per-stock detectors (cheap; OHLCV already loaded)
        detectors: dict[str, RevNDayDetector] = {}
        bars_by_stock_date: dict[str, dict[datetime.date, list]] = {}
        for code, cache in stock_caches.items():
            if not cache.bars:
                continue
            detectors[code] = RevNDayDetector(cache, n_days=n_days, side=side)
            by_date: dict[datetime.date, list] = {}
            for b in cache.bars:
                by_date.setdefault(b.dt.date(), []).append(b)
            bars_by_stock_date[code] = by_date

        # Compute per-date breadth
        frac_by_date: dict[datetime.date, float] = {}
        for d in dates:
            n_fires = 0
            n_active = 0
            for code, det in detectors.items():
                stock_bars = bars_by_stock_date[code].get(d)
                if not stock_bars:
                    continue   # stock had no bar this date
                n_active += 1
                as_of = stock_bars[-1].dt
                if det.detect(as_of, valid_bars=1) is not None:
                    n_fires += 1
            if n_active > 0:
                frac_by_date[d] = n_fires / n_active

        return cls(frac_by_date, regime_percentile)

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def frac(self, date: datetime.date) -> float:
        """Fraction of universe firing rev_nday on *date*. NaN if no data."""
        return self._frac.get(date, float("nan"))

    def is_high(self, date: datetime.date) -> bool:
        """True when breadth ≥ historical cutoff. False when no data."""
        f = self._frac.get(date)
        if f is None:
            return False
        return f >= self._cutoff

    def percentile(self, date: datetime.date) -> float:
        """Empirical percentile rank of *date*'s breadth within historical
        sample (0–100). Returns NaN if no data for *date*.
        """
        f = self._frac.get(date)
        if f is None:
            return float("nan")
        values = sorted(self._frac.values())
        if not values:
            return float("nan")
        # rank of f within values (1-based)
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
            f"RevNRegime(dates={len(self._frac)}, "
            f"cutoff={self._cutoff:.3f}, "
            f"pct={self._regime_pct:.2f})"
        )
