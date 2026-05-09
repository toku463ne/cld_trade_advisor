"""Tests for RegimeSignStrategy.propose().

The strategy's __init__ is heavy (loads ~150 stock caches from DB). These
tests bypass it entirely via object.__new__ and directly wire the attributes
that propose() reads, so no DB access is needed.
"""

from __future__ import annotations

import datetime
import math
import numpy as np
from typing import Any

import pytest

from src.analysis.regime_ranking import RankEntry
from src.signs.base import SignResult
from src.simulator.bar import BarData
from src.simulator.cache import DataCache
from src.strategy.proposal import SignalProposal
from src.strategy.regime_sign import RegimeSignStrategy, _SnapData


UTC = datetime.timezone.utc
_TODAY = datetime.date(2024, 6, 1)
_DT    = datetime.datetime(2024, 6, 1, 15, 0, tzinfo=UTC)


# ── Helpers ────────────────────────────────────────────────────────────────────

class _MockDetector:
    """Minimal sign-detector stub.  Returns a fixed SignResult or None."""

    def __init__(self, result: SignResult | None) -> None:
        self._result = result

    def detect(self, as_of: Any, valid_bars: int = 5) -> SignResult | None:
        return self._result


def _sign_result(sign: str, code: str, score: float = 1.0) -> SignResult:
    return SignResult(
        sign_type=sign,
        stock_code=code,
        score=score,
        fired_at=_DT,
        valid_until=_DT,
    )


def _rank_entry(sign: str, kumo: int, bench_flw: float = 0.05) -> RankEntry:
    dr = 0.6
    return RankEntry(sign_type=sign, kumo_state=kumo, n=40, dr=dr,
                     mag_flw=bench_flw / dr, bench_flw=bench_flw)


def _empty_cache(code: str) -> DataCache:
    """Return a DataCache with no bars — value type correct for _stock_caches."""
    return DataCache(code, "1d")


def _make_strategy(
    snap: _SnapData = _SnapData(kumo_state=1, adx=15.0, adx_pos=8.0, adx_neg=6.0),
    ranking: dict | None = None,
    detectors: dict | None = None,
    corr_map: dict | None = None,
    stock_kumo: dict | None = None,
    mode: str = "backtest",
) -> RegimeSignStrategy:
    """Construct a RegimeSignStrategy with pre-wired attributes, no DB needed.

    _stock_caches is built from corr_map keys so propose()'s iteration loop
    has something to iterate over.  The cache values are empty but the values
    are never used inside propose() — only the keys (stock codes) matter.
    """
    cm = corr_map or {}
    strat: RegimeSignStrategy = object.__new__(RegimeSignStrategy)
    strat._mode         = mode
    strat._window       = 20
    strat._valid_bars   = 5
    strat._ranking      = ranking or {}
    strat._snap_map     = {_TODAY: snap}
    strat._stock_caches = {code: _empty_cache(code) for code in cm}
    strat._detectors    = detectors or {}
    strat._corr_map     = cm
    strat._stock_kumo   = stock_kumo or {}
    return strat


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestProposeReturnsEmptyWhenNoData:
    def test_no_snapshot_returns_empty(self) -> None:
        """propose() returns [] when the date is absent from snap_map."""
        strat = _make_strategy()
        strat._snap_map = {}  # no snapshot for any date
        assert strat.propose(_DT) == []

    def test_snapshot_kumo_none_returns_empty(self) -> None:
        snap_no_kumo = _SnapData(kumo_state=None, adx=15.0, adx_pos=8.0, adx_neg=6.0)
        strat = _make_strategy(snap=snap_no_kumo)
        assert strat.propose(_DT) == []

    def test_no_ranking_returns_empty(self) -> None:
        strat = _make_strategy(ranking={})
        assert strat.propose(_DT) == []

    def test_no_detector_for_sign_code_returns_empty(self) -> None:
        ranking = {("str_hold", 1): _rank_entry("str_hold", 1)}
        # High-corr stock but no detector registered
        corr_map = {"A.T": {_TODAY: 0.8}}
        strat = _make_strategy(ranking=ranking, corr_map=corr_map, detectors={})
        assert strat.propose(_DT) == []


class TestHighCorrProposal:
    def test_high_corr_stock_uses_n225_kumo(self) -> None:
        """A high-corr stock with a firing detector produces a proposal."""
        ranking = {("str_hold", 1): _rank_entry("str_hold", 1, bench_flw=0.05)}
        corr_map = {"A.T": {_TODAY: 0.75}}   # |corr| >= 0.6 → high
        detectors = {("str_hold", "A.T"): _MockDetector(_sign_result("str_hold", "A.T"))}
        strat = _make_strategy(
            snap=_SnapData(kumo_state=1, adx=15.0, adx_pos=8.0, adx_neg=6.0),
            ranking=ranking, corr_map=corr_map, detectors=detectors,
        )
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.corr_mode == "high"
        assert p.sign_type == "str_hold"
        assert p.stock_code == "A.T"
        assert p.kumo_state == 1

    def test_only_best_high_corr_returned(self) -> None:
        """When two high-corr stocks both fire, only the top-ranked is returned."""
        ranking = {
            ("str_hold", 1): _rank_entry("str_hold", 1, bench_flw=0.07),
            ("div_gap",   1): _rank_entry("div_gap",  1, bench_flw=0.04),
        }
        corr_map = {
            "A.T": {_TODAY: 0.8},
            "B.T": {_TODAY: 0.7},
        }
        detectors = {
            ("str_hold", "A.T"): _MockDetector(_sign_result("str_hold", "A.T", score=1.0)),
            ("div_gap",  "B.T"): _MockDetector(_sign_result("div_gap",  "B.T", score=1.0)),
        }
        strat = _make_strategy(
            snap=_SnapData(kumo_state=1, adx=15.0, adx_pos=8.0, adx_neg=6.0),
            ranking=ranking, corr_map=corr_map, detectors=detectors,
        )
        proposals = strat.propose(_DT)
        high_props = [p for p in proposals if p.corr_mode == "high"]
        assert len(high_props) == 1
        assert high_props[0].sign_type == "str_hold"  # higher bench_flw wins

    def test_high_corr_proposal_carries_regime_metrics(self) -> None:
        """Proposal's regime fields match the ranking entry values."""
        rank = _rank_entry("str_hold", 1, bench_flw=0.06)
        ranking = {("str_hold", 1): rank}
        corr_map = {"A.T": {_TODAY: 0.8}}
        detectors = {("str_hold", "A.T"): _MockDetector(_sign_result("str_hold", "A.T"))}
        strat = _make_strategy(ranking=ranking, corr_map=corr_map, detectors=detectors)
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.regime_bench_flw == pytest.approx(rank.bench_flw)
        assert p.regime_dr == pytest.approx(rank.dr)
        assert p.regime_n == rank.n


class TestLowCorrProposal:
    def test_low_corr_stock_uses_own_kumo(self) -> None:
        """A low-corr stock is gated by its own kumo_state, not N225."""
        ranking = {("brk_sma", 1): _rank_entry("brk_sma", 1, bench_flw=0.04)}
        corr_map = {"B.T": {_TODAY: 0.1}}   # |corr| <= 0.3 → low
        stock_kumo = {"B.T": {_TODAY: 1}}
        detectors = {("brk_sma", "B.T"): _MockDetector(_sign_result("brk_sma", "B.T"))}
        strat = _make_strategy(
            snap=_SnapData(kumo_state=-1, adx=15.0, adx_pos=8.0, adx_neg=6.0),
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo,
        )
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.corr_mode == "low"
        assert p.kumo_state == 1  # stock's own kumo, not N225

    def test_low_corr_no_stock_kumo_skipped(self) -> None:
        """Low-corr stock with no kumo data for the day is skipped."""
        ranking = {("brk_sma", 1): _rank_entry("brk_sma", 1)}
        corr_map = {"B.T": {_TODAY: 0.1}}
        stock_kumo: dict = {}   # empty → no data
        detectors = {("brk_sma", "B.T"): _MockDetector(_sign_result("brk_sma", "B.T"))}
        strat = _make_strategy(
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo,
        )
        assert strat.propose(_DT) == []

    def test_low_corr_ranking_not_found_for_kumo_skipped(self) -> None:
        """Low-corr stock fires a sign but its (sign, kumo) cell isn't in ranking."""
        # ranking has kumo=1 but stock's own kumo is -1
        ranking = {("brk_sma", 1): _rank_entry("brk_sma", 1)}
        corr_map = {"B.T": {_TODAY: 0.1}}
        stock_kumo = {"B.T": {_TODAY: -1}}   # doesn't match ranking key (brk_sma, -1)
        detectors = {("brk_sma", "B.T"): _MockDetector(_sign_result("brk_sma", "B.T"))}
        strat = _make_strategy(
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo,
        )
        assert strat.propose(_DT) == []


class TestModeBacktestVsTrade:
    def _setup_two_low(self) -> RegimeSignStrategy:
        ranking = {
            ("brk_sma",  1): _rank_entry("brk_sma", 1, bench_flw=0.05),
            ("str_hold", 1): _rank_entry("str_hold", 1, bench_flw=0.04),
        }
        corr_map = {"A.T": {_TODAY: 0.1}, "B.T": {_TODAY: 0.1}}
        stock_kumo = {"A.T": {_TODAY: 1}, "B.T": {_TODAY: 1}}
        detectors = {
            ("brk_sma",  "A.T"): _MockDetector(_sign_result("brk_sma",  "A.T", score=1.0)),
            ("str_hold", "B.T"): _MockDetector(_sign_result("str_hold", "B.T", score=1.0)),
        }
        return ranking, corr_map, stock_kumo, detectors

    def test_backtest_mode_returns_best_low_only(self) -> None:
        ranking, corr_map, stock_kumo, detectors = self._setup_two_low()
        strat = _make_strategy(
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo, mode="backtest",
        )
        proposals = strat.propose(_DT)
        low_props = [p for p in proposals if p.corr_mode == "low"]
        assert len(low_props) == 1

    def test_trade_mode_returns_all_low(self) -> None:
        ranking, corr_map, stock_kumo, detectors = self._setup_two_low()
        strat = _make_strategy(
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo, mode="trade",
        )
        proposals = strat.propose(_DT)
        low_props = [p for p in proposals if p.corr_mode == "low"]
        assert len(low_props) == 2

    def test_mode_override_in_propose(self) -> None:
        """propose(mode='trade') overrides the instance's 'backtest' mode."""
        ranking, corr_map, stock_kumo, detectors = self._setup_two_low()
        strat = _make_strategy(
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo, mode="backtest",
        )
        proposals = strat.propose(_DT, mode="trade")
        low_props = [p for p in proposals if p.corr_mode == "low"]
        assert len(low_props) == 2


class TestAdxVetoInPropose:
    def test_str_lead_excluded_in_choppy_regime(self) -> None:
        """str_lead requires ADX bear state; choppy ADX (< 20) vetoes it."""
        ranking = {
            ("str_lead", 1): _rank_entry("str_lead", 1, bench_flw=0.08),
            ("div_gap",  1): _rank_entry("div_gap",  1, bench_flw=0.04),
        }
        corr_map = {"A.T": {_TODAY: 0.8}}
        detectors = {
            ("str_lead", "A.T"): _MockDetector(_sign_result("str_lead", "A.T", 2.0)),
            ("div_gap",  "A.T"): _MockDetector(_sign_result("div_gap",  "A.T", 1.0)),
        }
        snap_choppy = _SnapData(kumo_state=1, adx=10.0, adx_pos=8.0, adx_neg=6.0)
        strat = _make_strategy(
            snap=snap_choppy, ranking=ranking, corr_map=corr_map, detectors=detectors,
        )
        proposals = strat.propose(_DT)
        sign_types = {p.sign_type for p in proposals}
        assert "str_lead" not in sign_types
        assert "div_gap" in sign_types

    def test_str_lead_included_in_adx_bear(self) -> None:
        """str_lead is included when ADX >= 20 and −DI > +DI."""
        ranking = {("str_lead", 1): _rank_entry("str_lead", 1, bench_flw=0.08)}
        corr_map = {"A.T": {_TODAY: 0.8}}
        detectors = {("str_lead", "A.T"): _MockDetector(_sign_result("str_lead", "A.T"))}
        snap_bear = _SnapData(kumo_state=1, adx=25.0, adx_pos=10.0, adx_neg=18.0)
        strat = _make_strategy(
            snap=snap_bear, ranking=ranking, corr_map=corr_map, detectors=detectors,
        )
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        assert proposals[0].sign_type == "str_lead"

    def test_nan_adx_treated_as_choppy(self) -> None:
        """NaN ADX classifies as choppy — str_lead is vetoed."""
        ranking = {("str_lead", 1): _rank_entry("str_lead", 1, bench_flw=0.08)}
        corr_map = {"A.T": {_TODAY: 0.8}}
        detectors = {("str_lead", "A.T"): _MockDetector(_sign_result("str_lead", "A.T"))}
        snap_nan = _SnapData(kumo_state=1, adx=None, adx_pos=None, adx_neg=None)
        strat = _make_strategy(
            snap=snap_nan, ranking=ranking, corr_map=corr_map, detectors=detectors,
        )
        assert strat.propose(_DT) == []


class TestDetectorFiringBehavior:
    def test_detector_returning_none_excluded(self) -> None:
        """A detector whose detect() returns None contributes no proposal."""
        ranking = {("str_hold", 1): _rank_entry("str_hold", 1)}
        corr_map = {"A.T": {_TODAY: 0.8}}
        detectors = {("str_hold", "A.T"): _MockDetector(None)}
        strat = _make_strategy(ranking=ranking, corr_map=corr_map, detectors=detectors)
        assert strat.propose(_DT) == []

    def test_mid_corr_uses_n225_kumo_path(self) -> None:
        """A mid-corr stock (0.3 < |corr| < 0.6) follows the high-corr N225 path."""
        ranking = {("str_hold", 1): _rank_entry("str_hold", 1)}
        corr_map = {"M.T": {_TODAY: 0.45}}   # mid
        detectors = {("str_hold", "M.T"): _MockDetector(_sign_result("str_hold", "M.T"))}
        strat = _make_strategy(ranking=ranking, corr_map=corr_map, detectors=detectors,
                               mode="trade")
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        assert proposals[0].corr_mode == "mid"

    def test_proposals_sorted_by_bench_flw(self) -> None:
        """Low-corr proposals in trade mode are sorted by bench_flw descending."""
        ranking = {
            ("brk_sma",  1): _rank_entry("brk_sma",  1, bench_flw=0.03),
            ("str_hold", 1): _rank_entry("str_hold", 1, bench_flw=0.07),
            ("div_gap",  1): _rank_entry("div_gap",  1, bench_flw=0.05),
        }
        corr_map = {c: {_TODAY: 0.1} for c in ["A.T", "B.T", "C.T"]}
        stock_kumo = {c: {_TODAY: 1} for c in ["A.T", "B.T", "C.T"]}
        detectors = {
            ("brk_sma",  "A.T"): _MockDetector(_sign_result("brk_sma",  "A.T")),
            ("str_hold", "B.T"): _MockDetector(_sign_result("str_hold", "B.T")),
            ("div_gap",  "C.T"): _MockDetector(_sign_result("div_gap",  "C.T")),
        }
        strat = _make_strategy(
            ranking=ranking, corr_map=corr_map, detectors=detectors,
            stock_kumo=stock_kumo, mode="trade",
        )
        proposals = strat.propose(_DT)
        bench_fws = [p.regime_bench_flw for p in proposals]
        assert bench_fws == sorted(bench_fws, reverse=True)
