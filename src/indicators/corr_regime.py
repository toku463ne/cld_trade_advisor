"""Cross-sectional N225-correlation regime indicator.

For each trading date computes the fraction of universe stocks whose
20-bar daily rolling correlation to ^N225 exceeds *corr_threshold*.
When this fraction sits above its historical *regime_percentile*, the
market is in a "high-corr regime" — most stocks move in lockstep with
the index, so stock-selection signals carry little edge.

Typical usage from a strategy::

    with get_session() as session:
        regime = CorrRegime.build(
            session,
            stock_codes=universe,
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2026, 3, 31, tzinfo=UTC),
        )

    # Inside on_bar:
    if regime.is_high(bar.dt.date()):
        return   # skip entry
"""

from __future__ import annotations

import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import MovingCorr


_INDICATOR  = "^N225"
_GRAN       = "1d"
_WINDOW     = 20
_CORR_THRESH   = 0.70
_REGIME_PCT    = 0.80


class CorrRegime:
    """Pre-loaded regime gate; no DB calls after construction.

    Parameters
    ----------
    frac_by_date:
        Mapping date → fraction of stocks with corr > corr_threshold.
    regime_percentile:
        Historical percentile used to set the high-regime cut-off.
    """

    def __init__(
        self,
        frac_by_date: dict[datetime.date, float],
        regime_percentile: float = _REGIME_PCT,
    ) -> None:
        self._frac = frac_by_date
        values = list(frac_by_date.values())
        if values:
            self._cutoff: float = float(np.nanpercentile(values, regime_percentile * 100))
        else:
            self._cutoff = 1.0  # never fires when no data

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        session: Session,
        stock_codes: list[str],
        start: datetime.datetime,
        end: datetime.datetime,
        corr_threshold: float = _CORR_THRESH,
        regime_percentile: float = _REGIME_PCT,
        window_bars: int = _WINDOW,
        indicator: str = _INDICATOR,
        gran: str = _GRAN,
    ) -> "CorrRegime":
        """Load moving-corr rows from DB and build the regime gate.

        Requires that `src.analysis.moving_corr.compute_and_save` has
        already been run for this stock set, period, and window.
        """
        if not stock_codes:
            return cls({}, regime_percentile)

        rows = session.execute(
            select(MovingCorr.ts, MovingCorr.stock_code, MovingCorr.corr_value)
            .where(
                MovingCorr.stock_code.in_(stock_codes),
                MovingCorr.indicator   == indicator,
                MovingCorr.granularity == gran,
                MovingCorr.window_bars == window_bars,
                MovingCorr.ts          >= start,
                MovingCorr.ts          <  end,
                MovingCorr.corr_value.is_not(None),
            )
            .order_by(MovingCorr.ts)
        ).all()

        # Aggregate: per date, count stocks with corr > threshold
        above: dict[datetime.date, int]  = {}
        total: dict[datetime.date, int]  = {}
        for row in rows:
            d = row.ts.date()
            total[d] = total.get(d, 0) + 1
            if row.corr_value is not None and row.corr_value > corr_threshold:
                above[d] = above.get(d, 0) + 1

        frac_by_date: dict[datetime.date, float] = {}
        for d, n in total.items():
            frac_by_date[d] = above.get(d, 0) / n if n > 0 else 0.0

        return cls(frac_by_date, regime_percentile)

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def frac(self, date: datetime.date) -> float:
        """Fraction of stocks with corr > threshold on *date*. NaN if no data."""
        return self._frac.get(date, float("nan"))

    def is_high(self, date: datetime.date) -> bool:
        """True when the corr regime fraction exceeds the historical cutoff.

        Returns False (i.e. permit entry) when there is no data for *date*.
        """
        f = self._frac.get(date)
        if f is None:
            return False
        return f >= self._cutoff

    @property
    def cutoff(self) -> float:
        """The high-regime fraction threshold (historical regime_percentile)."""
        return self._cutoff

    def __repr__(self) -> str:
        return (
            f"CorrRegime(dates={len(self._frac)}, cutoff={self._cutoff:.3f})"
        )
