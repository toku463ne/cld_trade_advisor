"""ConfluenceSignStrategy — entry trigger: ≥N bullish signs valid for stock today.

Sister of RegimeSignStrategy, validated by `src/analysis/
confluence_strategy_backtest.py` (commit bc758d0):

    Strategy              trades  Sharpe  mean_r   win%
    regime_sign baseline   171   +1.33   +0.77%   varies
    N ≥ 3 confluence       165   +3.80   +1.97%   59%

Bullish set (brk_wall excluded per dilution finding):

    str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo

Each sign's fire is "valid" for `valid_bars[sign]` trading days after
firing (per the detector defaults — str_hold/brk_bol=3, others=5).
On any trade day, the confluence count for (stock) = number of distinct
bullish signs currently valid.  Strategy fires if count ≥ N.

Same public interface as RegimeSignStrategy (`propose()`,
`propose_range()`), so the Daily tab can run both in shadow mode and
the operator picks per-row.

Long-only.  corr_mode classification uses the same 20-bar rolling
Pearson convention as RegimeSign.
"""
from __future__ import annotations

import datetime
import math
from collections import defaultdict
from typing import Any, NamedTuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import (
    N225RegimeSnapshot,
    StockClusterMember,
    StockClusterRun,
)
from src.data.db import get_session
from src.data.models import Stock
from src.signs import (
    BrkBolDetector,
    BrkSmaDetector,
    RevNloDetector,
    RevPeakDetector,
    StrHoldDetector,
    StrLagDetector,
    StrLeadDetector,
)
from src.simulator.cache import DataCache
from src.strategy.base import ProposalStrategy
from src.strategy.proposal import SignalProposal
from src.strategy.regime_sign import _rolling_corr   # reuse 20-bar corr helper

_N225 = "^N225"
_GRAN = "1d"

_HIGH_CORR_THRESHOLD = 0.6
_LOW_CORR_THRESHOLD  = 0.3

# 7-sign bullish set (brk_wall excluded — confluence-dilution finding)
_BULLISH_SIGNS: tuple[str, ...] = (
    "str_hold", "str_lead", "str_lag",
    "brk_sma",  "brk_bol",
    "rev_lo",   "rev_nlo",
)

# Per-sign valid_bars used to extend a fire's "still active" window.
# Matches detector defaults (so confluence here is identical to what
# bullish_confluence_v2_probe measured).
_VALID_BARS: dict[str, int] = {
    "str_hold": 3,
    "str_lead": 5,
    "str_lag":  5,
    "brk_sma":  5,
    "brk_bol":  3,
    "rev_lo":   5,
    "rev_nlo":  5,
}


class _SnapData(NamedTuple):
    kumo_state: int | None
    adx:        float | None
    adx_pos:    float | None
    adx_neg:    float | None


def _build_detector(
    sign:        str,
    cache:       DataCache,
    n225_cache:  DataCache,
    window:      int,
) -> Any | None:
    """Build the appropriate detector for *sign* on *cache*."""
    if sign == "str_hold":
        return StrHoldDetector(cache, n225_cache)
    if sign == "str_lead":
        return StrLeadDetector(cache, n225_cache)
    if sign == "str_lag":
        return StrLagDetector(cache, n225_cache)
    if sign == "brk_sma":
        return BrkSmaDetector(cache, window=window)
    if sign == "brk_bol":
        return BrkBolDetector(cache, window=window)
    if sign == "rev_lo":
        return RevPeakDetector(cache, side="lo")
    if sign == "rev_nlo":
        return RevNloDetector(cache, n225_cache)
    return None


class ConfluenceSignStrategy(ProposalStrategy):
    """Daily multi-stock scanner: fire when ≥N bullish signs are valid today.

    Args:
        session:    DB session for loading cluster members + snapshots.
        stock_set:  Cluster-set name (e.g. "classified2024").
        start:      Start of data-loading range (UTC-aware).
        end:        End of data-loading range (inclusive).
        n_gate:     Minimum number of distinct valid bullish signs to fire
                    (default 3 — recommended per backtest, Sharpe +3.80).
        window:     Detector indicator window (default 20, same as RegimeSign).
        gran:       OHLCV granularity (default "1d").
    """

    def __init__(
        self,
        session:    Session,
        stock_set:  str,
        start:      datetime.datetime,
        end:        datetime.datetime,
        n_gate:     int = 3,
        window:     int = 20,
        gran:       str = _GRAN,
    ) -> None:
        if n_gate < 1:
            raise ValueError(f"n_gate must be ≥1, got {n_gate}")
        if n_gate > len(_BULLISH_SIGNS):
            raise ValueError(f"n_gate={n_gate} exceeds bullish-set size {len(_BULLISH_SIGNS)}")
        self._n_gate = n_gate
        self._window = window

        # ── Universe ──────────────────────────────────────────────────────────
        cluster_run = session.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == stock_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise ValueError(f"No StockClusterRun found for stock_set={stock_set!r}")

        all_members = session.execute(
            select(StockClusterMember)
            .where(StockClusterMember.run_id == cluster_run.id)
        ).scalars().all()
        stock_codes = [m.stock_code for m in all_members if m.is_representative]
        logger.info("ConfluenceSign universe: {} representative stocks from {}",
                    len(stock_codes), stock_set)

        # ── Load caches ───────────────────────────────────────────────────────
        load_end = end + datetime.timedelta(days=1)
        logger.info("Loading ^N225 cache …")
        self._n225_cache = DataCache(_N225, gran)
        self._n225_cache.load(session, start, load_end)

        logger.info("Loading {} stock caches …", len(stock_codes))
        self._stock_caches: dict[str, DataCache] = {}
        for code in stock_codes:
            c = DataCache(code, gran)
            c.load(session, start, load_end)
            if len(c) > 0:
                self._stock_caches[code] = c
        logger.info("Loaded {} non-empty stock caches", len(self._stock_caches))

        # ── Build bullish-set detectors ───────────────────────────────────────
        self._detectors: dict[tuple[str, str], Any] = {}
        for sign in _BULLISH_SIGNS:
            built = 0
            for code, cache in self._stock_caches.items():
                det = _build_detector(sign, cache, self._n225_cache, window)
                if det is not None:
                    self._detectors[(sign, code)] = det
                    built += 1
            logger.info("  {} — {} detectors", sign, built)

        # ── Rolling N225 correlations (for corr_mode classification) ──────────
        self._corr_map: dict[str, dict[datetime.date, float]] = {
            code: _rolling_corr(cache, self._n225_cache)
            for code, cache in self._stock_caches.items()
        }

        # ── N225 regime snapshots (for kumo / adx context) ────────────────────
        snaps = session.execute(
            select(N225RegimeSnapshot)
            .where(N225RegimeSnapshot.date >= start.date(),
                   N225RegimeSnapshot.date <= end.date())
        ).scalars().all()
        self._snap_map: dict[datetime.date, _SnapData] = {
            s.date: _SnapData(s.kumo_state, s.adx, s.adx_pos, s.adx_neg)
            for s in snaps
        }
        logger.info("N225 regime snapshots: {} dates", len(self._snap_map))

        # ── Sector labels (passthrough for downstream filtering) ──────────────
        self._sector_map: dict[str, str] = {
            code: sector
            for code, sector in session.execute(
                select(Stock.code, Stock.sector17)
            ).all()
            if sector
        }

    # ── Public interface ──────────────────────────────────────────────────────

    def _corr_mode(self, code: str, as_of_date: datetime.date) -> tuple[str, float]:
        corr_val = self._corr_map.get(code, {}).get(as_of_date, float("nan"))
        abs_c = abs(corr_val) if not math.isnan(corr_val) else float("nan")
        if math.isnan(abs_c):
            return "mid", corr_val
        if abs_c >= _HIGH_CORR_THRESHOLD:
            return "high", corr_val
        if abs_c <= _LOW_CORR_THRESHOLD:
            return "low", corr_val
        return "mid", corr_val

    def propose(
        self,
        as_of: datetime.datetime,
        mode:  str | None = None,
    ) -> list[SignalProposal]:
        """Return confluence-gated proposals for *as_of*."""
        as_of_date = as_of.date() if hasattr(as_of, "date") else as_of

        # N225 regime context (for kumo / adx fields — not gating)
        snap = self._snap_map.get(as_of_date)
        kumo_state = snap.kumo_state if (snap and snap.kumo_state is not None) else 0
        adx  = snap.adx     if (snap and snap.adx     is not None) else float("nan")
        adxp = snap.adx_pos if (snap and snap.adx_pos is not None) else float("nan")
        adxn = snap.adx_neg if (snap and snap.adx_neg is not None) else float("nan")

        proposals: list[SignalProposal] = []
        for code, cache in self._stock_caches.items():
            # Count valid signs + collect the (sign, score, fired_at, valid_until) tuples
            active: list[tuple[str, float, datetime.datetime, datetime.datetime]] = []
            for sign in _BULLISH_SIGNS:
                det = self._detectors.get((sign, code))
                if det is None:
                    continue
                vb = _VALID_BARS.get(sign, 5)
                result = det.detect(as_of, valid_bars=vb)
                if result is None:
                    continue
                active.append((sign, result.score, result.fired_at, result.valid_until))

            if len(active) < self._n_gate:
                continue

            # Lead sign = highest sign_score among constituents (display label)
            active.sort(key=lambda t: -t[1])
            lead_sign, lead_score, lead_fired, lead_valid = active[0]
            constituents = ",".join(sorted(s for s, *_ in active))

            cm, corr_val = self._corr_mode(code, as_of_date)

            # sign_type embeds confluence count + constituents for display
            label = f"conf{len(active)}:{constituents}"
            proposals.append(SignalProposal(
                sign_type        = label,
                stock_code       = code,
                sign_score       = float(len(active)),   # confluence count IS the score
                fired_at         = lead_fired,
                valid_until      = lead_valid,
                corr_mode        = cm,
                corr_n225        = corr_val,
                kumo_state       = kumo_state,
                adx              = adx,
                adx_pos          = adxp,
                adx_neg          = adxn,
                # No regime-cell stats — populate with placeholders so downstream
                # consumers expecting these fields don't crash.
                regime_bench_flw = 0.0,
                regime_ev        = 0.0,
                regime_dr        = 0.0,
                regime_n         = 0,
            ))

        # Sort by confluence count (descending), then by stock code (stable)
        proposals.sort(key=lambda p: (-p.sign_score, p.stock_code))
        return proposals

    def propose_range(
        self,
        start: datetime.datetime,
        end:   datetime.datetime,
    ) -> dict[datetime.date, list[SignalProposal]]:
        """Run propose() for every trading date in [start, end]."""
        dates = sorted({b.dt.date() for b in self._n225_cache.bars
                        if start.date() <= b.dt.date() <= end.date()})
        out: dict[datetime.date, list[SignalProposal]] = {}
        for d in dates:
            dt = datetime.datetime.combine(d, datetime.time(15, 0),
                                           tzinfo=datetime.timezone.utc)
            proposals = self.propose(dt)
            if proposals:
                out[d] = proposals
        return out

    @staticmethod
    def from_config(
        stock_set: str,
        start:     datetime.datetime,
        end:       datetime.datetime,
        n_gate:    int = 3,
        **kwargs:  Any,
    ) -> "ConfluenceSignStrategy":
        """Convenience factory: opens its own DB session."""
        with get_session() as session:
            return ConfluenceSignStrategy(
                session   = session,
                stock_set = stock_set,
                start     = start,
                end       = end,
                n_gate    = n_gate,
                **kwargs,
            )
