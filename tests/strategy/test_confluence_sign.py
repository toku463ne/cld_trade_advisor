"""Tests for ConfluenceSignStrategy.propose().

The strategy's __init__ is heavy (loads cluster members + ~200 stock caches +
builds detectors from the DB).  Like test_regime_sign.py, these tests bypass it
entirely via object.__new__ and wire the attributes propose() reads, so no DB
access is needed.  propose() reads only:  _n_gate, _snap_map, _stock_caches
(iterated for codes), _detectors {(sign, code): detector}, _corr_map.

Key behaviours under test:
  * the ≥N-bullish-signs confluence gate,
  * the "conf{N}:{sorted constituents}" label + sign_score == count,
  * lead-sign (highest score) supplies fired_at / valid_until,
  * corr_mode classification (high / low / mid / nan→mid),
  * only signs in _BULLISH_SIGNS are counted,
  * proposals carry N225 context (kumo/adx) but are NOT gated on it — unlike
    RegimeSign, a missing snapshot still yields proposals (kumo 0, adx NaN),
  * proposal ordering (confluence count desc, then stock code),
  * __init__ n_gate validation (runs before any DB access).
"""

from __future__ import annotations

import datetime
import math
from typing import Any

import pytest

from src.signs.base import SignResult
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import (
    _BULLISH_SIGNS,
    ConfluenceSignStrategy,
    _SnapData,
)

UTC = datetime.timezone.utc
_TODAY = datetime.date(2024, 6, 3)
_DT = datetime.datetime(2024, 6, 3, 15, 0, tzinfo=UTC)
_SNAP = _SnapData(kumo_state=1, adx=22.0, adx_pos=14.0, adx_neg=9.0)


# ── Helpers ──────────────────────────────────────────────────────────────────

class _MockDetector:
    """Minimal sign-detector stub: returns a fixed SignResult or None."""

    def __init__(self, result: SignResult | None) -> None:
        self._result = result

    def detect(self, as_of: Any, valid_bars: int = 5) -> SignResult | None:
        return self._result


def _sign_result(
    sign: str,
    code: str,
    score: float = 1.0,
    fired_at: datetime.datetime = _DT,
    valid_until: datetime.datetime = _DT,
) -> SignResult:
    return SignResult(
        sign_type=sign,
        stock_code=code,
        score=score,
        fired_at=fired_at,
        valid_until=valid_until,
    )


def _detectors_for(code: str, *signs: str) -> dict:
    """One firing detector per sign for a single stock (score 1.0 each)."""
    return {(s, code): _MockDetector(_sign_result(s, code)) for s in signs}


def _make_strategy(
    n_gate: int = 3,
    snap: _SnapData | None = _SNAP,
    detectors: dict | None = None,
    corr_map: dict | None = None,
) -> ConfluenceSignStrategy:
    """Construct a ConfluenceSignStrategy with pre-wired attributes, no DB.

    _stock_caches keys = union of corr_map codes and detector codes, so the
    propose() iteration always visits any stock that has a firing detector.
    The DataCache values are empty (never read inside propose()).
    """
    cm = corr_map or {}
    dets = detectors or {}
    codes = set(cm) | {code for (_sign, code) in dets}
    strat: ConfluenceSignStrategy = object.__new__(ConfluenceSignStrategy)
    strat._n_gate = n_gate
    strat._window = 20
    strat._snap_map = {_TODAY: snap} if snap is not None else {}
    strat._stock_caches = {c: DataCache(c, "1d") for c in codes}
    strat._detectors = dets
    strat._corr_map = cm
    return strat


# ── The confluence gate ───────────────────────────────────────────────────────

class TestConfluenceGate:
    def test_below_gate_returns_empty(self) -> None:
        """2 valid signs with n_gate=3 → no proposal."""
        dets = _detectors_for("A.T", "str_hold", "brk_sma")
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        assert strat.propose(_DT) == []

    def test_exactly_at_gate_fires(self) -> None:
        """3 valid signs with n_gate=3 → one proposal."""
        dets = _detectors_for("A.T", "str_hold", "brk_sma", "rev_lo")
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        assert proposals[0].stock_code == "A.T"
        assert proposals[0].sign_score == 3.0

    def test_above_gate_counts_all_valid(self) -> None:
        """4 valid signs → sign_score == 4 (confluence count IS the score)."""
        dets = _detectors_for("A.T", "str_hold", "brk_sma", "rev_lo", "brk_bol")
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        assert proposals[0].sign_score == 4.0

    def test_n_gate_one_fires_on_single_sign(self) -> None:
        dets = _detectors_for("A.T", "str_hold")
        strat = _make_strategy(n_gate=1, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        assert len(strat.propose(_DT)) == 1

    def test_detector_returning_none_does_not_count(self) -> None:
        """A None-returning detector is not a valid sign, so it drops the count."""
        dets = _detectors_for("A.T", "str_hold", "brk_sma")
        dets[("rev_lo", "A.T")] = _MockDetector(None)  # present but inactive
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        assert strat.propose(_DT) == []   # only 2 truly valid → below gate

    def test_only_bullish_signs_are_counted(self) -> None:
        """Detectors for non-bullish signs are never consulted (loop is over
        _BULLISH_SIGNS), so they cannot contribute to the count."""
        non_bullish = "div_gap"
        assert non_bullish not in _BULLISH_SIGNS
        dets = _detectors_for("A.T", "str_hold", "brk_sma")            # 2 bullish
        dets[(non_bullish, "A.T")] = _MockDetector(_sign_result(non_bullish, "A.T"))
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        assert strat.propose(_DT) == []   # non-bullish doesn't lift 2 → 3


# ── Label, score, lead sign ────────────────────────────────────────────────────

class TestLabelAndLeadSign:
    def test_label_format_and_sorted_constituents(self) -> None:
        dets = _detectors_for("A.T", "rev_lo", "brk_sma", "str_hold")
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        p = strat.propose(_DT)[0]
        # constituents are alphabetically sorted inside the label
        assert p.sign_type == "conf3:brk_sma,rev_lo,str_hold"

    def test_lead_sign_supplies_fired_at_and_valid_until(self) -> None:
        """fired_at / valid_until come from the highest-score constituent."""
        early = datetime.datetime(2024, 5, 30, 15, 0, tzinfo=UTC)
        late = datetime.datetime(2024, 6, 2, 15, 0, tzinfo=UTC)
        valid_hi = datetime.datetime(2024, 6, 9, 15, 0, tzinfo=UTC)
        dets = {
            ("str_hold", "A.T"): _MockDetector(
                _sign_result("str_hold", "A.T", score=5.0,
                             fired_at=late, valid_until=valid_hi)),
            ("brk_sma", "A.T"): _MockDetector(
                _sign_result("brk_sma", "A.T", score=1.0, fired_at=early)),
            ("rev_lo", "A.T"): _MockDetector(
                _sign_result("rev_lo", "A.T", score=2.0, fired_at=early)),
        }
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        p = strat.propose(_DT)[0]
        assert p.fired_at == late          # str_hold has the top score
        assert p.valid_until == valid_hi

    def test_sign_score_equals_count_not_lead_score(self) -> None:
        """sign_score is the confluence COUNT, never the lead detector's score."""
        dets = {
            ("str_hold", "A.T"): _MockDetector(_sign_result("str_hold", "A.T", score=9.0)),
            ("brk_sma", "A.T"): _MockDetector(_sign_result("brk_sma", "A.T", score=1.0)),
            ("rev_lo", "A.T"): _MockDetector(_sign_result("rev_lo", "A.T", score=1.0)),
        }
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        assert strat.propose(_DT)[0].sign_score == 3.0


# ── corr_mode classification ────────────────────────────────────────────────────

class TestCorrMode:
    def _propose_with_corr(self, corr: float | None):
        dets = _detectors_for("A.T", "str_hold", "brk_sma", "rev_lo")
        corr_map = {"A.T": {}} if corr is None else {"A.T": {_TODAY: corr}}
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map=corr_map)
        return strat.propose(_DT)[0]

    def test_high_corr(self) -> None:
        p = self._propose_with_corr(0.75)
        assert p.corr_mode == "high"
        assert p.corr_n225 == pytest.approx(0.75)

    def test_high_corr_negative_uses_abs(self) -> None:
        assert self._propose_with_corr(-0.8).corr_mode == "high"

    def test_low_corr(self) -> None:
        assert self._propose_with_corr(0.2).corr_mode == "low"

    def test_mid_corr(self) -> None:
        assert self._propose_with_corr(0.45).corr_mode == "mid"

    def test_missing_corr_is_mid(self) -> None:
        p = self._propose_with_corr(None)
        assert p.corr_mode == "mid"
        assert math.isnan(p.corr_n225)


# ── N225 context is informational, NOT a gate ──────────────────────────────────

class TestContextNotGated:
    def test_snapshot_populates_kumo_and_adx(self) -> None:
        dets = _detectors_for("A.T", "str_hold", "brk_sma", "rev_lo")
        strat = _make_strategy(n_gate=3, snap=_SNAP, detectors=dets,
                               corr_map={"A.T": {_TODAY: 0.1}})
        p = strat.propose(_DT)[0]
        assert p.kumo_state == 1
        assert p.adx == pytest.approx(22.0)
        assert p.adx_pos == pytest.approx(14.0)
        assert p.adx_neg == pytest.approx(9.0)

    def test_missing_snapshot_still_proposes(self) -> None:
        """Unlike RegimeSign, a missing snapshot does NOT veto — it just
        defaults kumo_state to 0 and adx to NaN."""
        dets = _detectors_for("A.T", "str_hold", "brk_sma", "rev_lo")
        strat = _make_strategy(n_gate=3, snap=None, detectors=dets,
                               corr_map={"A.T": {_TODAY: 0.1}})
        proposals = strat.propose(_DT)
        assert len(proposals) == 1
        assert proposals[0].kumo_state == 0
        assert math.isnan(proposals[0].adx)

    def test_regime_fields_are_placeholder_zero(self) -> None:
        dets = _detectors_for("A.T", "str_hold", "brk_sma", "rev_lo")
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map={"A.T": {_TODAY: 0.1}})
        p = strat.propose(_DT)[0]
        assert p.regime_ev == 0.0
        assert p.regime_dr == 0.0
        assert p.regime_bench_flw == 0.0
        assert p.regime_n == 0


# ── Ordering across stocks ──────────────────────────────────────────────────────

class TestOrdering:
    def test_sorted_by_count_desc_then_code(self) -> None:
        """Higher confluence count first; ties broken by stock code ascending."""
        dets: dict = {}
        dets.update(_detectors_for("A.T", "str_hold", "brk_sma", "rev_lo"))            # 3
        dets.update(_detectors_for("B.T", "str_hold", "brk_sma", "rev_lo", "brk_bol")) # 4
        dets.update(_detectors_for("C.T", "str_hold", "brk_sma", "rev_lo"))            # 3
        corr_map = {c: {_TODAY: 0.1} for c in ("A.T", "B.T", "C.T")}
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map=corr_map)
        proposals = strat.propose(_DT)
        assert [p.stock_code for p in proposals] == ["B.T", "A.T", "C.T"]
        assert [p.sign_score for p in proposals] == [4.0, 3.0, 3.0]

    def test_multiple_independent_stocks_all_returned(self) -> None:
        """Confluence is long-only and not slot-limited at propose() level —
        every qualifying stock yields a proposal (slot logic lives in the
        simulator, not here)."""
        dets: dict = {}
        for code in ("A.T", "B.T", "C.T"):
            dets.update(_detectors_for(code, "str_hold", "brk_sma", "rev_lo"))
        corr_map = {c: {_TODAY: 0.1} for c in ("A.T", "B.T", "C.T")}
        strat = _make_strategy(n_gate=3, detectors=dets, corr_map=corr_map)
        assert len(strat.propose(_DT)) == 3


# ── __init__ validation (runs before any DB access) ─────────────────────────────

class TestInitValidation:
    def test_n_gate_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="n_gate must be"):
            ConfluenceSignStrategy(
                session=None, stock_set="x", start=_DT, end=_DT, n_gate=0,
            )

    def test_n_gate_above_bullish_set_raises(self) -> None:
        too_big = len(_BULLISH_SIGNS) + 1
        with pytest.raises(ValueError, match="exceeds bullish-set size"):
            ConfluenceSignStrategy(
                session=None, stock_set="x", start=_DT, end=_DT, n_gate=too_big,
            )
