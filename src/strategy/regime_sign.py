"""RegimeSignStrategy — daily multi-stock signal scanner with ADX + Kumo regime gates.

This strategy scans the stock universe each day, applies regime filters, then
proposes the best entry candidates.

Regime logic
------------
- **High-corr stocks** (|corr vs N225| ≥ 0.6): effectively index proxies.
  Sign ranking and regime gate use **N225 Ichimoku Kumo** state (+1/0/−1).
  ADX veto applied for signs that require a trending N225 environment.

- **Low-corr stocks** (|corr vs N225| ≤ 0.3): independent alpha.
  Sign ranking and regime gate use the **stock's own Ichimoku Kumo** state.
  This avoids applying an N225-centric regime filter to idiosyncratic names.

Two modes
---------
- ``backtest``: 1 high-corr entry + 1 best low-corr entry per day.
- ``trade``:    1 high-corr entry + all qualifying low-corr entries.

Signs supported
---------------
Standard (N225 cache only):
  div_gap, corr_flip, str_hold, str_lead, str_lag, brk_sma, brk_bol,
  rev_lo, rev_hi, rev_nhi, rev_nlo

Extended (require extra inputs — built automatically when data is available):
  corr_shift  — needs GSPC cache loaded alongside N225
  div_peer    — needs peer caches; loads all cluster members (including non-
                representatives) to build intra-cluster peer lists, matching
                the sign_benchmark implementation exactly

Not supported (omitted by design):
  corr_peak   — requires PeakCorrRun table; add via subclass
  div_bar/div_vol — special treatment; add via subclass

Usage
-----
    from src.strategy.regime_sign import RegimeSignStrategy

    strategy = RegimeSignStrategy.from_config(
        stock_set = "classified2024",
        run_ids   = list(range(47, 151)),
        start     = datetime.datetime(2025, 4, 1, tzinfo=datetime.timezone.utc),
        end       = datetime.datetime(2026, 3, 31, tzinfo=datetime.timezone.utc),
        mode      = "trade",
    )
    proposals = strategy.propose(datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc))
"""

from __future__ import annotations

import datetime
import math
from collections import defaultdict
from typing import Any, NamedTuple

import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import N225RegimeSnapshot, StockClusterMember, StockClusterRun
from src.analysis.regime_ranking import ADX_VETO, RankEntry, build_regime_ranking, rank_for_regime
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.signs.brk_bol import BrkBolDetector
from src.signs.brk_sma import BrkSmaDetector
from src.signs.corr_flip import CorrFlipDetector
from src.signs.corr_shift import CorrShiftDetector
from src.signs.div_gap import DivGapDetector
from src.signs.div_peer import DivPeerDetector
from src.signs.rev_nday import RevNDayDetector
from src.signs.rev_nhold import RevNholdDetector
from src.signs.rev_nlo import RevNloDetector
from src.signs.rev_peak import RevPeakDetector
from src.signs.str_hold import StrHoldDetector
from src.signs.str_lag import StrLagDetector
from src.signs.str_lead import StrLeadDetector
from src.strategy.base import ProposalStrategy
from src.strategy.proposal import SignalProposal

_N225 = "^N225"
_GSPC = "^GSPC"
_GRAN = "1d"

_CORR_WINDOW         = 20
_HIGH_CORR_THRESHOLD = 0.6
_LOW_CORR_THRESHOLD  = 0.3

# Ichimoku periods
_TENKAN_P = 9
_KIJUN_P  = 26
_SENKOU_B_P = 52
_CLOUD_SHIFT = 26


class _SnapData(NamedTuple):
    """Detached-safe snapshot of N225 regime values for one date."""
    kumo_state: int | None
    adx:        float | None
    adx_pos:    float | None
    adx_neg:    float | None


# ── Module-level helpers ──────────────────────────────────────────────────────

def _rolling_corr(
    stock_cache: DataCache,
    ref_cache:   DataCache,
    window: int = _CORR_WINDOW,
) -> dict[datetime.date, float]:
    """Return {date: rolling_corr_of_returns} for the stock vs ref.

    Delegates to _rolling_corr_series — both must use daily-return
    correlation (raw-close correlation is upward-biased in trending tape).
    """
    series = _rolling_corr_series(stock_cache, ref_cache, window)
    return {d: float(v) for d, v in series.items() if not math.isnan(v)}


def _rolling_corr_series(
    stock_cache: DataCache,
    ref_cache:   DataCache,
    window: int = _CORR_WINDOW,
) -> pd.Series:
    """Rolling-window Pearson corr of daily returns — matches sign_benchmark._daily_corr_series.

    Uses pct_change() returns (not raw close levels) with a date-based index so that
    CorrShiftDetector fires on the same dates as the benchmark runs.
    """
    stock_close = pd.Series(
        {b.dt.date(): b.close for b in stock_cache.bars}, dtype=float
    )
    ref_close = pd.Series(
        {b.dt.date(): b.close for b in ref_cache.bars}, dtype=float
    )
    aligned = pd.concat([stock_close.rename("s"), ref_close.rename("r")], axis=1).dropna()
    if len(aligned) < window:
        return pd.Series(dtype=float)
    s_ret = aligned["s"].pct_change()
    r_ret = aligned["r"].pct_change()
    return s_ret.rolling(window, min_periods=max(5, window // 2)).corr(r_ret)


def _stock_kumo_series(cache: DataCache) -> dict[datetime.date, int]:
    """Return {date: kumo_state} (+1 above / 0 inside / −1 below) for a stock's own Kumo.

    Uses the same Ichimoku parameters as N225RegimeSnapshot:
    Tenkan=9, Kijun=26, Senkou B=52, cloud shift=26.
    Requires at least Kijun + cloud_shift = 52 bars to produce any output.
    """
    if len(cache.bars) < _KIJUN_P + _CLOUD_SHIFT:
        return {}
    highs  = pd.Series([b.high  for b in cache.bars])
    lows   = pd.Series([b.low   for b in cache.bars])
    closes = pd.Series([b.close for b in cache.bars])
    dates  = [b.dt.date() for b in cache.bars]

    tenkan   = (highs.rolling(_TENKAN_P).max()    + lows.rolling(_TENKAN_P).min())   / 2
    kijun    = (highs.rolling(_KIJUN_P).max()     + lows.rolling(_KIJUN_P).min())    / 2
    senkou_a = ((tenkan + kijun) / 2).shift(_CLOUD_SHIFT)
    senkou_b = ((highs.rolling(_SENKOU_B_P).max() + lows.rolling(_SENKOU_B_P).min()) / 2).shift(_CLOUD_SHIFT)
    kumo_top    = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    kumo_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

    result: dict[datetime.date, int] = {}
    for i, d in enumerate(dates):
        ct = kumo_top.iloc[i]
        cb = kumo_bottom.iloc[i]
        cl = closes.iloc[i]
        if pd.isna(ct) or pd.isna(cb):
            continue
        result[d] = 1 if cl > ct else (-1 if cl < cb else 0)
    return result


def _build_detector(
    sign_type:   str,
    stock_cache: DataCache,
    n225_cache:  DataCache,
    window: int = 20,
) -> Any | None:
    """Instantiate the sign detector for *sign_type* (standard signs only).

    Returns None for unsupported or extended signs (corr_shift, div_peer)
    which require extra inputs and are built separately in RegimeSignStrategy.__init__.
    """
    if sign_type == "div_gap":
        return DivGapDetector(stock_cache, n225_cache)
    if sign_type == "corr_flip":
        return CorrFlipDetector(stock_cache, n225_cache, window=window)
    if sign_type == "str_hold":
        return StrHoldDetector(stock_cache, n225_cache)
    if sign_type == "str_lead":
        return StrLeadDetector(stock_cache, n225_cache)
    if sign_type == "str_lag":
        return StrLagDetector(stock_cache, n225_cache)
    if sign_type == "brk_sma":
        return BrkSmaDetector(stock_cache, window=window)
    if sign_type == "brk_bol":
        return BrkBolDetector(stock_cache, window=window)
    if sign_type == "rev_lo":
        return RevPeakDetector(stock_cache, side="lo")
    if sign_type == "rev_hi":
        return RevPeakDetector(stock_cache, side="hi")
    if sign_type == "rev_nhi":
        return RevNDayDetector(stock_cache, n_days=window, side="hi")
    if sign_type == "rev_nlo":
        return RevNloDetector(stock_cache, n225_cache)
    if sign_type == "rev_nhold":
        return RevNholdDetector(stock_cache, n225_cache)
    return None  # corr_shift, div_peer built separately; corr_peak / div_bar need subclass


# ── Strategy ──────────────────────────────────────────────────────────────────

class RegimeSignStrategy(ProposalStrategy):
    """Daily multi-stock scanner: Kumo regime gate + ADX veto + sign ranking.

    Args:
        session:    DB session used for loading cluster members, snapshots, etc.
        stock_set:  Cluster-set name (e.g. "classified2024").
        run_ids:    SignBenchmarkRun IDs used to build the (sign, kumo) ranking.
        start:      Start of the date range for data loading (UTC-aware).
        end:        End of the date range (inclusive).
        mode:       "backtest" or "trade" (default "backtest").
        window:     Detector indicator window (default 20).
        valid_bars: Sign validity in trading days (default 3 — tight enough for
                    a swing-trading window; per-sign overrides in
                    ``_PER_SIGN_VALID_BARS`` shorten this further for single-bar
                    signals).
        gran:       OHLCV granularity (default "1d").
    """

    # Signs whose underlying signal only measures one trading day's behaviour
    # use a 1-day validity regardless of the strategy-level default.
    # The strategy default (3 days) covers signs whose measurement spans
    # multiple days (str_hold, str_lead, str_lag, rev_nlo, rev_peak,
    # corr_flip, corr_shift, rev_nday).
    _PER_SIGN_VALID_BARS: dict[str, int] = {
        "div_peer":  1,   # single-day close-to-close peer divergence
        "div_bar":   1,   # single-bar price/volume divergence
        "div_gap":   1,   # single opening-gap event
        "div_vol":   1,   # single-bar volatility divergence
        "brk_sma":   1,   # single-bar SMA crossover
        "brk_bol":   1,   # single-bar Bollinger Band crossover
        "corr_peak": 1,   # confirmed on a single zigzag-low day
    }

    def __init__(
        self,
        session:    Session,
        stock_set:  str,
        run_ids:    list[int],
        start:      datetime.datetime,
        end:        datetime.datetime,
        mode:       str   = "backtest",
        window:     int   = 20,
        valid_bars: int   = 3,
        gran:       str   = _GRAN,
        min_dr:     float = 0.0,
    ) -> None:
        self._mode       = mode
        self._window     = window
        self._valid_bars = valid_bars

        # ── Regime ranking ────────────────────────────────────────────────────
        logger.info("Building regime ranking from {} run_ids …", len(run_ids))
        self._ranking = build_regime_ranking(session, run_ids, min_dr=min_dr)
        logger.info("Ranking: {} (sign, kumo) cells", len(self._ranking))

        # ── Stock universe ────────────────────────────────────────────────────
        cluster_run = session.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == stock_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise ValueError(f"No StockClusterRun found for stock_set={stock_set!r}")

        all_members = session.execute(
            select(StockClusterMember)
            .where(StockClusterMember.run_id == cluster_run.id)
        ).scalars().all()

        stock_codes: list[str] = [m.stock_code for m in all_members if m.is_representative]
        # cluster structure needed for div_peer peer resolution
        _cluster_groups: dict[int, list[str]] = defaultdict(list)
        _stock_to_cluster: dict[str, int] = {}
        for m in all_members:
            _cluster_groups[m.cluster_id].append(m.stock_code)
            _stock_to_cluster[m.stock_code] = m.cluster_id
        logger.info("Universe: {} representative stocks from {} ({} total cluster members)",
                    len(stock_codes), stock_set, len(list(all_members)))

        # ── Load caches ───────────────────────────────────────────────────────
        load_end = end + datetime.timedelta(days=1)

        logger.info("Loading ^N225 cache …")
        self._n225_cache = DataCache(_N225, gran)
        self._n225_cache.load(session, start, load_end)

        logger.info("Loading ^GSPC cache …")
        self._gspc_cache = DataCache(_GSPC, gran)
        self._gspc_cache.load(session, start, load_end)
        _have_gspc = len(self._gspc_cache) > 0
        if not _have_gspc:
            logger.warning("^GSPC cache empty — corr_shift will be unavailable")

        logger.info("Loading {} stock caches …", len(stock_codes))
        self._stock_caches: dict[str, DataCache] = {}
        for code in stock_codes:
            c = DataCache(code, gran)
            c.load(session, start, load_end)
            if len(c) > 0:
                self._stock_caches[code] = c
        logger.info("Loaded {} non-empty stock caches", len(self._stock_caches))

        # ── Load non-rep member caches (for div_peer intra-cluster peers) ─────
        needed_signs_early = {sign for (sign, _) in self._ranking}
        _member_caches: dict[str, DataCache] = dict(self._stock_caches)  # reps already loaded
        if "div_peer" in needed_signs_early:
            non_rep_codes = [m.stock_code for m in all_members if not m.is_representative]
            logger.info("Loading {} non-rep member caches for div_peer peers …",
                        len(non_rep_codes))
            loaded_peers = 0
            for code in non_rep_codes:
                if code in _member_caches:
                    continue
                c = DataCache(code, gran)
                c.load(session, start, load_end)
                if len(c) > 0:
                    _member_caches[code] = c
                    loaded_peers += 1
            logger.info("Non-rep caches loaded: {}", loaded_peers)

        # ── Build standard detectors ──────────────────────────────────────────
        needed_signs = {sign for (sign, _) in self._ranking}
        standard_signs = needed_signs - {"corr_shift", "div_peer"}
        logger.info("Building standard detectors for: {}", sorted(standard_signs))

        self._detectors: dict[tuple[str, str], Any] = {}
        for sign in standard_signs:
            built = 0
            for code, cache in self._stock_caches.items():
                det = _build_detector(sign, cache, self._n225_cache, window)
                if det is not None:
                    self._detectors[(sign, code)] = det
                    built += 1
            logger.info("  {} — {} detectors", sign, built)

        # ── Build corr_shift detectors ────────────────────────────────────────
        if "corr_shift" in needed_signs and _have_gspc:
            logger.info("Building corr_shift detectors …")
            built = 0
            for code, cache in self._stock_caches.items():
                n225_s = _rolling_corr_series(cache, self._n225_cache, window)
                gspc_s = _rolling_corr_series(cache, self._gspc_cache, window)
                if n225_s.empty or gspc_s.empty:
                    continue
                self._detectors[("corr_shift", code)] = CorrShiftDetector(
                    cache, n225_s, gspc_s
                )
                built += 1
            logger.info("  corr_shift — {} detectors", built)
        elif "corr_shift" in needed_signs:
            logger.warning("corr_shift in ranking but ^GSPC unavailable — skipped")

        # ── Build div_peer detectors ──────────────────────────────────────────
        # Use intra-cluster peers (all cluster members, not just representatives).
        if "div_peer" in needed_signs:
            logger.info("Building div_peer detectors (intra-cluster peers) …")
            built = 0
            skipped = 0
            for code, cache in self._stock_caches.items():
                cid = _stock_to_cluster.get(code)
                if cid is None:
                    skipped += 1
                    continue
                peers = [
                    _member_caches[c]
                    for c in _cluster_groups[cid]
                    if c != code and c in _member_caches
                ]
                if not peers:
                    skipped += 1
                    continue
                self._detectors[("div_peer", code)] = DivPeerDetector(cache, peers)
                built += 1
            logger.info("  div_peer — {} detectors ({} skipped, no peers)", built, skipped)

        # ── Rolling N225 correlations (for corr classification) ───────────────
        self._corr_map: dict[str, dict[datetime.date, float]] = {
            code: _rolling_corr(cache, self._n225_cache)
            for code, cache in self._stock_caches.items()
        }

        # ── Per-stock Kumo series (for low-corr regime gating) ────────────────
        logger.info("Computing per-stock Kumo series …")
        self._stock_kumo: dict[str, dict[datetime.date, int]] = {
            code: _stock_kumo_series(cache)
            for code, cache in self._stock_caches.items()
        }

        # ── Regime snapshots (N225) ───────────────────────────────────────────
        snaps = session.execute(
            select(N225RegimeSnapshot)
            .where(N225RegimeSnapshot.date >= start.date(),
                   N225RegimeSnapshot.date <= end.date())
        ).scalars().all()
        self._snap_map: dict[datetime.date, _SnapData] = {
            s.date: _SnapData(s.kumo_state, s.adx, s.adx_pos, s.adx_neg)
            for s in snaps
        }
        logger.info("Regime snapshots loaded: {} dates", len(self._snap_map))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _valid_bars_for(self, sign: str) -> int:
        """Return the validity window in trading days for *sign*.

        Per-sign overrides in ``_PER_SIGN_VALID_BARS`` apply (e.g. signs whose
        underlying signal only measures one day fall back to 1); otherwise the
        strategy-level ``valid_bars`` is used.
        """
        return self._PER_SIGN_VALID_BARS.get(sign, self._valid_bars)

    # ── Public interface ──────────────────────────────────────────────────────

    def propose(
        self,
        as_of: datetime.datetime,
        mode:  str | None = None,
    ) -> list[SignalProposal]:
        """Return signal proposals for *as_of*.

        High-corr candidates are selected using the N225 Kumo regime ranking.
        Low-corr candidates are selected using each stock's own Kumo regime,
        avoiding the application of an N225-centric filter to idiosyncratic names.
        """
        effective_mode = mode or self._mode
        as_of_date = as_of.date() if hasattr(as_of, "date") else as_of

        # ── N225 regime ───────────────────────────────────────────────────────
        snap = self._snap_map.get(as_of_date)
        if snap is None or snap.kumo_state is None:
            return []
        n225_kumo = snap.kumo_state
        adx  = snap.adx     if snap.adx     is not None else float("nan")
        adxp = snap.adx_pos if snap.adx_pos is not None else float("nan")
        adxn = snap.adx_neg if snap.adx_neg is not None else float("nan")

        # ── Signs ranked for current N225 regime (high-corr / mid use) ────────
        n225_ranked: list[RankEntry] = rank_for_regime(self._ranking, n225_kumo, adx, adxp, adxn)
        n225_sign_rank: dict[str, int] = {e.sign_type: i for i, e in enumerate(n225_ranked)}
        n225_entry_map: dict[str, RankEntry] = {e.sign_type: e for e in n225_ranked}

        # All sign types present in ranking (any kumo state) for low-corr scan
        all_signs_in_ranking: set[str] = {sign for (sign, _) in self._ranking}

        # ADX state (reused for low-corr ADX veto)
        _adx_bear = (not math.isnan(adx)) and adx >= 20.0 and adxn > adxp
        _adx_bull = (not math.isnan(adx)) and adx >= 20.0 and adxp > adxn

        def _adx_veto_ok(sign: str) -> bool:
            req = ADX_VETO.get(sign)
            if req is None:
                return True
            if req == "bear":
                return _adx_bear
            if req == "bull":
                return _adx_bull
            return False

        # ── Corr classification helper ────────────────────────────────────────
        def _corr_mode(code: str) -> tuple[str, float]:
            corr_val = self._corr_map.get(code, {}).get(as_of_date, float("nan"))
            abs_c = abs(corr_val) if not math.isnan(corr_val) else float("nan")
            if math.isnan(abs_c):
                return "mid", corr_val
            if abs_c >= _HIGH_CORR_THRESHOLD:
                return "high", corr_val
            if abs_c <= _LOW_CORR_THRESHOLD:
                return "low", corr_val
            return "mid", corr_val

        high_proposals: list[SignalProposal] = []
        mid_proposals:  list[SignalProposal] = []
        low_proposals:  list[SignalProposal] = []

        # ── High-corr / mid scan: use N225 Kumo ranked signs ─────────────────
        for sign in (e.sign_type for e in n225_ranked):
            rank_entry = n225_entry_map[sign]
            for code, cache in self._stock_caches.items():
                cm, corr_val = _corr_mode(code)
                if cm == "low":
                    continue  # handled separately below

                det = self._detectors.get((sign, code))
                if det is None:
                    continue
                result = det.detect(as_of, valid_bars=self._valid_bars_for(sign))
                if result is None:
                    continue

                proposal = SignalProposal(
                    sign_type        = sign,
                    stock_code       = code,
                    sign_score       = result.score,
                    fired_at         = result.fired_at,
                    valid_until      = result.valid_until,
                    corr_mode        = cm,
                    corr_n225        = corr_val,
                    kumo_state       = n225_kumo,
                    adx              = adx,
                    adx_pos          = adxp,
                    adx_neg          = adxn,
                    regime_bench_flw = rank_entry.bench_flw,
                    regime_ev        = rank_entry.ev,
                    regime_dr        = rank_entry.dr,
                    regime_n         = rank_entry.n,
                )
                if cm == "high":
                    high_proposals.append(proposal)
                else:
                    mid_proposals.append(proposal)

        # ── Low-corr scan: use each stock's own Kumo ─────────────────────────
        for code, cache in self._stock_caches.items():
            cm, corr_val = _corr_mode(code)
            if cm != "low":
                continue

            stock_kumo = self._stock_kumo.get(code, {}).get(as_of_date)
            if stock_kumo is None:
                continue  # no kumo data for this stock yet

            # Collect all (sign, entry) pairs ranked for this stock's kumo state
            for sign in all_signs_in_ranking:
                rank_entry = self._ranking.get((sign, stock_kumo))
                if rank_entry is None:
                    continue  # sign not ranked for this stock's regime
                if not _adx_veto_ok(sign):
                    continue

                det = self._detectors.get((sign, code))
                if det is None:
                    continue
                result = det.detect(as_of, valid_bars=self._valid_bars_for(sign))
                if result is None:
                    continue

                low_proposals.append(SignalProposal(
                    sign_type        = sign,
                    stock_code       = code,
                    sign_score       = result.score,
                    fired_at         = result.fired_at,
                    valid_until      = result.valid_until,
                    corr_mode        = "low",
                    corr_n225        = corr_val,
                    kumo_state       = stock_kumo,   # stock's own regime
                    adx              = adx,
                    adx_pos          = adxp,
                    adx_neg          = adxn,
                    regime_bench_flw = rank_entry.bench_flw,
                    regime_ev        = rank_entry.ev,
                    regime_dr        = rank_entry.dr,
                    regime_n         = rank_entry.n,
                ))

        # ── Sort and select ───────────────────────────────────────────────────
        # Primary tiebreak after the N225-rank position is now expected value
        # (DR×mag_flw − (1−DR)×mag_rev), so negative-EV cells (which are filtered
        # out at ranking-build time anyway) never compete for the top slot.
        def _sort_n225(p: SignalProposal) -> tuple[int, float, float]:
            return (n225_sign_rank.get(p.sign_type, 999), -p.regime_ev, -p.sign_score)

        def _sort_stock(p: SignalProposal) -> tuple[float, float]:
            return (-p.regime_ev, -p.sign_score)

        high_proposals.sort(key=_sort_n225)
        mid_proposals.sort(key=_sort_n225)
        low_proposals.sort(key=_sort_stock)

        result_list: list[SignalProposal] = []
        if high_proposals:
            result_list.append(high_proposals[0])

        if effective_mode == "backtest":
            if low_proposals:
                result_list.append(low_proposals[0])
        else:  # trade: all low + all mid as extra context
            result_list.extend(low_proposals)
            result_list.extend(mid_proposals)

        return result_list

    # ── Convenience ──────────────────────────────────────────────────────────

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
        stock_set:  str,
        run_ids:    list[int],
        start:      datetime.datetime,
        end:        datetime.datetime,
        mode:       str   = "backtest",
        min_dr:     float = 0.0,
        **kwargs:   Any,
    ) -> "RegimeSignStrategy":
        """Convenience factory: opens its own DB session."""
        with get_session() as session:
            return RegimeSignStrategy(
                session   = session,
                stock_set = stock_set,
                run_ids   = run_ids,
                start     = start,
                end       = end,
                mode      = mode,
                min_dr    = min_dr,
                **kwargs,
            )
