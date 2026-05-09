"""Tests for src.analysis.regime_ranking — build_regime_ranking and rank_for_regime."""

from __future__ import annotations

import datetime
import math

import pytest
from sqlalchemy.orm import Session

from src.analysis.models import N225RegimeSnapshot, SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_ranking import RankEntry, build_regime_ranking, rank_for_regime


UTC = datetime.timezone.utc


def _make_run(session: Session, sign_type: str, run_id_hint: int | None = None) -> SignBenchmarkRun:
    run = SignBenchmarkRun(
        sign_type=sign_type,
        stock_set="test_set",
        gran="1d",
        start_dt=datetime.datetime(2020, 1, 1, tzinfo=UTC),
        end_dt=datetime.datetime(2021, 1, 1, tzinfo=UTC),
        window=10,
        valid_bars=5,
        zz_size=5,
        zz_mid_size=2,
        trend_cap_days=30,
        n_stocks=10,
        n_events=0,
        created_at=datetime.datetime(2021, 1, 1, tzinfo=UTC),
    )
    session.add(run)
    session.flush()
    return run


def _make_event(
    session: Session,
    run_id: int,
    fired_at: datetime.date,
    direction: int,
    magnitude: float | None = 0.05,
) -> SignBenchmarkEvent:
    evt = SignBenchmarkEvent(
        run_id=run_id,
        stock_code="1234.T",
        fired_at=datetime.datetime(fired_at.year, fired_at.month, fired_at.day, tzinfo=UTC),
        sign_score=1.0,
        trend_direction=direction,
        trend_bars=10,
        trend_magnitude=magnitude,
    )
    session.add(evt)
    session.flush()
    return evt


def _make_snapshot(
    session: Session,
    date: datetime.date,
    kumo_state: int,
    close: float = 30000.0,
) -> N225RegimeSnapshot:
    snap = N225RegimeSnapshot(
        date=date,
        close=close,
        kumo_state=kumo_state,
    )
    session.add(snap)
    session.flush()
    return snap


class TestBuildRegimeRanking:
    def test_basic_ranking(self, session: Session) -> None:
        """A single (sign, kumo) cell with enough events returns a RankEntry."""
        run = _make_run(session, "str_hold")
        d = datetime.date(2020, 6, 1)
        _make_snapshot(session, d, kumo_state=1)
        for _ in range(35):
            _make_event(session, run.id, d, direction=1, magnitude=0.05)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.0)

        assert ("str_hold", 1) in result
        entry = result[("str_hold", 1)]
        assert entry.dr == pytest.approx(1.0)
        assert entry.bench_flw == pytest.approx(entry.dr * entry.mag_flw)
        assert entry.n == 35

    def test_min_n_filters_small_cells(self, session: Session) -> None:
        """Cells with fewer events than min_n are excluded."""
        run = _make_run(session, "div_bar")
        d = datetime.date(2020, 6, 1)
        _make_snapshot(session, d, kumo_state=1)
        for _ in range(10):
            _make_event(session, run.id, d, direction=1)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30)
        assert ("div_bar", 1) not in result

    def test_min_dr_filters_low_direction_rate(self, session: Session) -> None:
        """Cells where DR <= min_dr are excluded."""
        run = _make_run(session, "brk_sma")
        d = datetime.date(2020, 6, 1)
        _make_snapshot(session, d, kumo_state=-1)
        # 15 up, 20 down → DR = 15/35 ≈ 0.43
        for _ in range(15):
            _make_event(session, run.id, d, direction=1)
        for _ in range(20):
            _make_event(session, run.id, d, direction=-1, magnitude=0.03)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.50)
        assert ("brk_sma", -1) not in result

    def test_dr_exactly_equal_to_min_dr_excluded(self, session: Session) -> None:
        """DR == min_dr (not strictly greater) is also excluded."""
        run = _make_run(session, "brk_sma")
        d = datetime.date(2020, 6, 1)
        _make_snapshot(session, d, kumo_state=1)
        # 30 up, 30 down → DR = 0.5
        for _ in range(30):
            _make_event(session, run.id, d, direction=1)
        for _ in range(30):
            _make_event(session, run.id, d, direction=-1, magnitude=0.02)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.50)
        assert ("brk_sma", 1) not in result

    def test_events_without_snapshot_skipped(self, session: Session) -> None:
        """Events whose fired_at date has no snapshot row are silently ignored."""
        run = _make_run(session, "str_hold")
        d_no_snap = datetime.date(2020, 7, 15)
        for _ in range(40):
            _make_event(session, run.id, d_no_snap, direction=1)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.0)
        assert len(result) == 0

    def test_bench_flw_is_dr_times_mag_flw(self, session: Session) -> None:
        """bench_flw == dr * mag_flw by definition."""
        run = _make_run(session, "corr_flip")
        d = datetime.date(2020, 6, 1)
        _make_snapshot(session, d, kumo_state=0)
        for _ in range(20):
            _make_event(session, run.id, d, direction=1, magnitude=0.10)
        for _ in range(10):
            _make_event(session, run.id, d, direction=-1, magnitude=0.03)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.0)
        entry = result[("corr_flip", 0)]
        assert entry.bench_flw == pytest.approx(entry.dr * entry.mag_flw)
        # DR should be 20/30 = 2/3
        assert entry.dr == pytest.approx(20 / 30)

    def test_mag_flw_only_from_direction_1_events(self, session: Session) -> None:
        """mag_flw is the mean magnitude of direction==1 events only."""
        run = _make_run(session, "div_vol")
        d = datetime.date(2020, 6, 1)
        _make_snapshot(session, d, kumo_state=1)
        for _ in range(30):
            _make_event(session, run.id, d, direction=1, magnitude=0.08)
        for _ in range(10):
            _make_event(session, run.id, d, direction=-1, magnitude=0.99)  # should not affect mag_flw

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.0)
        entry = result[("div_vol", 1)]
        assert entry.mag_flw == pytest.approx(0.08)

    def test_multiple_kumo_states_separated(self, session: Session) -> None:
        """Events split across kumo_state +1 and −1 produce separate RankEntries."""
        run = _make_run(session, "str_hold")
        d_bull = datetime.date(2020, 6, 1)
        d_bear = datetime.date(2020, 9, 1)
        _make_snapshot(session, d_bull, kumo_state=1)
        _make_snapshot(session, d_bear, kumo_state=-1)
        for _ in range(35):
            _make_event(session, run.id, d_bull, direction=1, magnitude=0.06)
        for _ in range(35):
            _make_event(session, run.id, d_bear, direction=1, magnitude=0.04)

        result = build_regime_ranking(session, run_ids=[run.id], min_n=30, min_dr=0.0)
        assert ("str_hold", 1) in result
        assert ("str_hold", -1) in result
        assert result[("str_hold", 1)].mag_flw == pytest.approx(0.06)
        assert result[("str_hold", -1)].mag_flw == pytest.approx(0.04)

    def test_run_id_not_in_db_is_ignored(self, session: Session) -> None:
        """run_ids that don't exist in DB produce an empty result."""
        result = build_regime_ranking(session, run_ids=[99999], min_n=1, min_dr=0.0)
        assert result == {}


class TestRankForRegime:
    def _make_entry(self, sign: str, kumo: int, bench_flw: float) -> RankEntry:
        dr = 0.6
        mag = bench_flw / dr
        return RankEntry(sign_type=sign, kumo_state=kumo, n=40, dr=dr, mag_flw=mag, bench_flw=bench_flw)

    def test_filters_by_kumo_state(self) -> None:
        """Only entries matching kumo_state are returned."""
        ranking = {
            ("str_hold", 1):  self._make_entry("str_hold", 1, 0.05),
            ("str_hold", -1): self._make_entry("str_hold", -1, 0.04),
            ("div_bar",  1):  self._make_entry("div_bar", 1, 0.03),
        }
        result = rank_for_regime(ranking, kumo_state=-1, adx=10.0, adx_pos=5.0, adx_neg=6.0)
        assert all(e.kumo_state == -1 for e in result)
        assert len(result) == 1

    def test_sorted_by_bench_flw_desc(self) -> None:
        """Results are ordered by bench_flw descending."""
        ranking = {
            ("div_bar",   1): self._make_entry("div_bar", 1, 0.02),
            ("str_hold",  1): self._make_entry("str_hold", 1, 0.07),
            ("brk_sma",   1): self._make_entry("brk_sma", 1, 0.05),
        }
        result = rank_for_regime(ranking, kumo_state=1, adx=10.0, adx_pos=5.0, adx_neg=4.0)
        assert result[0].bench_flw >= result[1].bench_flw >= result[2].bench_flw

    def test_adx_veto_str_lead_excluded_in_choppy(self) -> None:
        """str_lead is excluded when ADX < 20 (choppy state)."""
        ranking = {
            ("str_lead",  1): self._make_entry("str_lead", 1, 0.08),
            ("div_bar",   1): self._make_entry("div_bar", 1, 0.05),
        }
        result = rank_for_regime(ranking, kumo_state=1, adx=15.0, adx_pos=5.0, adx_neg=8.0)
        signs = {e.sign_type for e in result}
        assert "str_lead" not in signs
        assert "div_bar" in signs

    def test_adx_veto_str_lead_excluded_in_bull(self) -> None:
        """str_lead is excluded when ADX >= 20 but +DI > -DI (bull state)."""
        ranking = {("str_lead", 1): self._make_entry("str_lead", 1, 0.08)}
        result = rank_for_regime(ranking, kumo_state=1, adx=25.0, adx_pos=15.0, adx_neg=10.0)
        assert result == []

    def test_adx_veto_str_lead_included_in_bear(self) -> None:
        """str_lead is included when ADX >= 20 and -DI > +DI (bear state)."""
        ranking = {("str_lead", 1): self._make_entry("str_lead", 1, 0.08)}
        result = rank_for_regime(ranking, kumo_state=1, adx=25.0, adx_pos=10.0, adx_neg=18.0)
        assert len(result) == 1
        assert result[0].sign_type == "str_lead"

    def test_adx_veto_rev_nlo_excluded_in_bull(self) -> None:
        """rev_nlo requires bear ADX state; excluded when bull."""
        ranking = {("rev_nlo", -1): self._make_entry("rev_nlo", -1, 0.06)}
        result = rank_for_regime(ranking, kumo_state=-1, adx=25.0, adx_pos=18.0, adx_neg=10.0)
        assert result == []

    def test_adx_nan_results_in_choppy(self) -> None:
        """NaN ADX is treated as choppy — vetoed signs are excluded."""
        ranking = {
            ("str_lead", 1): self._make_entry("str_lead", 1, 0.08),
            ("div_bar",  1): self._make_entry("div_bar", 1, 0.05),
        }
        result = rank_for_regime(ranking, kumo_state=1, adx=math.nan, adx_pos=0.0, adx_neg=0.0)
        signs = {e.sign_type for e in result}
        assert "str_lead" not in signs
        assert "div_bar" in signs

    def test_empty_ranking_returns_empty_list(self) -> None:
        result = rank_for_regime({}, kumo_state=1, adx=15.0, adx_pos=5.0, adx_neg=6.0)
        assert result == []
