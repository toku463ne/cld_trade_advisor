"""div_gap — Opening Gap Divergence sign detector. See docs/signs/div_gap.md."""

from __future__ import annotations

import bisect
import datetime

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_STOCK_GAP_MIN = 0.005   # stock gap up > +0.5 %
_N225_GAP_MAX  = -0.005  # N225 gap down < -0.5 %
_SCORE_CAP     = 0.02    # gap magnitude at which score saturates to 1.0

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["div_gap"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "div_gap": (
        "**Opening Gap Divergence** — "
        "stock gaps up at the open on a day when N225 gaps down. "
        "Strong independent buying interest."
    ),
}


class DivGapDetector:
    """Initialise once per (stock, N225) hourly cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache: DataCache,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        stock_bars       = stock_cache.bars
        n225_bars        = n225_cache.bars
        self._dts        = [b.dt for b in stock_bars]

        # Previous-session close and session-open per date — N225
        n225_by_date: dict[datetime.date, list] = {}
        for b in n225_bars:
            n225_by_date.setdefault(b.dt.date(), []).append(b)
        n225_dates_sorted = sorted(n225_by_date)
        n225_prev_close: dict[datetime.date, float] = {}
        n225_sess_open:  dict[datetime.date, float] = {}
        for i, d in enumerate(n225_dates_sorted):
            n225_sess_open[d] = n225_by_date[d][0].open
            if i > 0:
                n225_prev_close[d] = n225_by_date[n225_dates_sorted[i - 1]][-1].close

        # Previous-session close per date — stock
        stock_by_date: dict[datetime.date, list] = {}
        for b in stock_bars:
            stock_by_date.setdefault(b.dt.date(), []).append(b)
        stock_dates_sorted = sorted(stock_by_date)
        stock_prev_close: dict[datetime.date, float] = {}
        for i, d in enumerate(stock_dates_sorted):
            if i > 0:
                stock_prev_close[d] = stock_by_date[stock_dates_sorted[i - 1]][-1].close

        # Scan: fire on first bar of each session meeting gap conditions
        self._fire_events: list[tuple[int, float]] = []
        for i, b in enumerate(stock_bars):
            d = b.dt.date()
            if i > 0 and stock_bars[i - 1].dt.date() == d:
                continue  # not first bar of session

            spc = stock_prev_close.get(d)
            npc = n225_prev_close.get(d)
            nso = n225_sess_open.get(d)
            if spc is None or npc is None or nso is None or spc == 0 or npc == 0:
                continue

            stock_gap = b.open / spc - 1.0
            n225_gap  = nso / npc - 1.0

            if stock_gap > _STOCK_GAP_MIN and n225_gap < _N225_GAP_MAX:
                score = (
                    min(stock_gap / _SCORE_CAP, 1.0)
                    * min(abs(n225_gap) / _SCORE_CAP, 1.0)
                )
                self._fire_events.append((i, score))

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        for fi, score in reversed(self._fire_events):
            if fi > idx:
                continue
            if idx - fi > valid_bars:
                break
            valid_until_idx = min(fi + valid_bars, len(self._dts) - 1)
            return SignResult(
                sign_type="div_gap",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
