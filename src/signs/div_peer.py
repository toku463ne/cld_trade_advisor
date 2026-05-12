"""div_peer — Intra-cluster Divergence sign detector.

Fires on the first hourly bar of a trading day when:
  - Stock daily return > +STOCK_RET_MIN   (+0.5 %)
  - ≥ PEER_DOWN_FRAC (60 %) of the cluster peers have daily return < PEER_DOWN_MIN (−0.3 %)

Daily returns are derived from hourly caches (last close of each date).

Score = min(stock_ret / 0.02, 1.0) × peer_down_fraction
  A stock rising strongly (+2 %) while all peers fall scores 1.0.
  A marginal rise (+0.5 %) while 60 % of peers are down scores 0.3 × 0.6 = 0.18.

Valid for up to ``valid_bars`` *trading days* after firing (default 1 — the
underlying signal is a single-day close-to-close peer return, so a longer
validity window would let stale fires linger past the period actually measured).
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# uv run --env-file devenv python -m src.analysis.sign_benchmark \
#     --sign div_peer --cluster-set classified2023 \
#     --start 2023-04-01 --end 2025-03-31 --gran 1d
# run_id=33  n=474  direction_rate=57.4%  p≈0.001
# bench_flw=0.048  bench_rev=0.031  mean_bars=12.4  (mag_flw=0.084  mag_rev=0.072)
# → RECOMMEND (FLW) — significant; intra-cluster divergence is a reliable follow-through signal
# Low-corr only (run_id=40, --corr-mode low):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign div_peer --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
#   n=97  direction_rate=47.8%  p≈0.665  bench_flw=0.035
#   → Note: reverses on low-corr stocks (p not significant, below 50%); use on all corr regimes

from __future__ import annotations

import bisect
import datetime

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_STOCK_RET_MIN  =  0.005   # stock daily return > +0.5 %
_PEER_DOWN_MIN  = -0.003   # peer "down" threshold: daily return < −0.3 %
_PEER_DOWN_FRAC =  0.60    # at least 60 % of peers must be down
_SCORE_RET_CAP  =  0.02    # stock return at which score saturates to 1.0

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["div_peer"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "div_peer": (
        "**Intra-cluster Divergence** — "
        "stock outperforms its correlation-cluster peers while the peers are declining. "
        "Idiosyncratic buying not explained by sector moves."
    ),
}


class DivPeerDetector:
    """Initialise once per stock cache + list of cluster-peer caches."""

    def __init__(
        self,
        stock_cache: DataCache,
        peer_caches: list[DataCache],
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # Derive daily close for stock
        stock_close: dict[datetime.date, float] = {}
        for b in stock_cache.bars:
            stock_close[b.dt.date()] = b.close

        # Derive daily close for each peer
        peer_closes: list[dict[datetime.date, float]] = []
        for pc in peer_caches:
            d_close: dict[datetime.date, float] = {}
            for b in pc.bars:
                d_close[b.dt.date()] = b.close
            peer_closes.append(d_close)

        # Hourly-bar lookup helpers
        self._trading_dates: list[datetime.date] = sorted({dt.date() for dt in self._dts})
        date_to_first: dict[datetime.date, int] = {}
        date_to_last:  dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i
            date_to_last[d] = i
        self._date_to_last = date_to_last

        stock_dates = sorted(stock_close)

        self._fire_events: list[tuple[int, datetime.date, float]] = []
        for i in range(1, len(stock_dates)):
            d      = stock_dates[i]
            prev_d = stock_dates[i - 1]

            sc  = stock_close.get(d)
            psc = stock_close.get(prev_d)
            if sc is None or psc is None or psc == 0:
                continue

            stock_ret = sc / psc - 1.0
            if stock_ret <= _STOCK_RET_MIN:
                continue

            n_down, n_total = 0, 0
            for by_date in peer_closes:
                cc = by_date.get(d)
                pc = by_date.get(prev_d)
                if cc is None or pc is None or pc == 0:
                    continue
                n_total += 1
                if cc / pc - 1.0 < _PEER_DOWN_MIN:
                    n_down += 1

            if n_total == 0 or n_down / n_total < _PEER_DOWN_FRAC:
                continue

            if d not in date_to_first:
                continue

            peer_down_frac = n_down / n_total
            score = min(stock_ret / _SCORE_RET_CAP, 1.0) * peer_down_frac
            self._fire_events.append((date_to_first[d], d, score))

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 1,
    ) -> SignResult | None:
        """Return the most recent valid div_peer sign at *as_of*, or None.

        valid_bars counts *trading days*. Default is 1 because the underlying
        peer-divergence test is a single-day close-to-close measurement.
        """
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        as_of_date     = as_of.date()
        as_of_date_pos = bisect.bisect_right(self._trading_dates, as_of_date) - 1

        for fi, fire_date, score in reversed(self._fire_events):
            if fi > idx:
                continue
            fire_date_pos        = bisect.bisect_left(self._trading_dates, fire_date)
            trading_days_elapsed = as_of_date_pos - fire_date_pos
            if trading_days_elapsed > valid_bars:
                break

            valid_date_pos  = min(fire_date_pos + valid_bars, len(self._trading_dates) - 1)
            valid_date      = self._trading_dates[valid_date_pos]
            valid_until_idx = self._date_to_last.get(valid_date, idx)

            return SignResult(
                sign_type="div_peer",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
