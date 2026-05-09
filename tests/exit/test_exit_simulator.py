"""Tests for src.exit.exit_simulator.run_simulation.

DataCache objects are built synthetically (no DB access needed) by directly
assigning _bars and _dts after construction, bypassing the load() method.
"""

from __future__ import annotations

import datetime

import numpy as np
import pytest

from src.exit.base import EntryCandidate, ExitContext, ExitResult, ExitRule
from src.exit.exit_simulator import run_simulation
from src.exit.time_stop import TimeStop
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.bar import BarData
from src.simulator.cache import DataCache


UTC = datetime.timezone.utc


# ── Helpers ────────────────────────────────────────────────────────────────────

def _dt(year: int, month: int, day: int) -> datetime.datetime:
    return datetime.datetime(year, month, day, 9, 0, tzinfo=UTC)


def _make_cache(
    code: str,
    price_seq: list[float],
    start: datetime.date = datetime.date(2024, 1, 1),
) -> DataCache:
    """Build a synthetic daily DataCache without any DB call."""
    cache = DataCache(code, "1d")
    for i, p in enumerate(price_seq):
        d = start + datetime.timedelta(days=i)
        bar = BarData(
            dt=datetime.datetime(d.year, d.month, d.day, 9, 0, tzinfo=UTC),
            open=p,
            high=p * 1.01,
            low=p * 0.99,
            close=p,
            volume=1000,
            indicators={},
        )
        cache._bars.append(bar)
        cache._dts.append(bar.dt)
    cache._closes = np.array([b.close for b in cache._bars], dtype=np.float64)
    return cache


def _candidate(
    code: str,
    entry_date: datetime.date,
    entry_price: float = 100.0,
    corr_mode: str = "low",
    zs: tuple[float, ...] = (10.0, 10.0, 10.0),
) -> EntryCandidate:
    return EntryCandidate(
        stock_code=code,
        entry_date=entry_date,
        entry_price=entry_price,
        corr_mode=corr_mode,
        corr_n225=0.8 if corr_mode == "high" else 0.1,
        zs_history=zs,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestBasicTimeStop:
    def test_single_trade_exits_at_max_bars(self) -> None:
        # 8 bars: entry on day 0, fill at day 1, exit at bar_index=3 (day 4)
        cache = _make_cache("A.T", [100.0] * 8)
        start = datetime.date(2024, 1, 1)
        cand = _candidate("A.T", start)
        rule = TimeStop(max_bars=3)
        results = run_simulation([cand], rule, {"A.T": cache}, end_date=start + datetime.timedelta(days=7))
        assert len(results) == 1
        r = results[0]
        assert r.exit_reason == "time"
        assert r.hold_bars == 3

    def test_empty_candidates(self) -> None:
        cache = _make_cache("A.T", [100.0] * 5)
        start = datetime.date(2024, 1, 1)
        results = run_simulation([], TimeStop(max_bars=5), {"A.T": cache}, end_date=start + datetime.timedelta(days=4))
        assert results == []

    def test_no_next_bar_skips_candidate(self) -> None:
        # Only 1 bar: no next bar to fill, candidate is skipped
        cache = _make_cache("A.T", [100.0])
        start = datetime.date(2024, 1, 1)
        cand = _candidate("A.T", start)
        results = run_simulation([cand], TimeStop(max_bars=5), {"A.T": cache}, end_date=start)
        assert results == []

    def test_unknown_stock_skips_candidate(self) -> None:
        # Candidate references "X.T" but cache is for "A.T" — no bars → skipped
        cache = _make_cache("A.T", [100.0] * 5)
        start = datetime.date(2024, 1, 1)
        cand = _candidate("X.T", start)
        results = run_simulation([cand], TimeStop(max_bars=3), {"A.T": cache}, end_date=start + datetime.timedelta(days=4))
        assert results == []


class TestTwoBarFill:
    def test_fill_at_next_bar_open(self) -> None:
        # Day 0: open=100, Day 1: open=105 — fill should be 105
        cache = _make_cache("A.T", [])
        start = datetime.date(2024, 1, 1)
        bars = [
            BarData(dt=_dt(2024,1,1), open=100.0, high=102.0, low=98.0, close=101.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,2), open=105.0, high=107.0, low=104.0, close=106.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,3), open=108.0, high=110.0, low=107.0, close=109.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,4), open=106.0, high=108.0, low=105.0, close=107.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,5), open=104.0, high=106.0, low=103.0, close=105.0, volume=1000, indicators={}),
        ]
        for b in bars:
            cache._bars.append(b)
            cache._dts.append(b.dt)
        cache._closes = np.array([b.close for b in bars])

        cand = _candidate("A.T", datetime.date(2024, 1, 1), entry_price=100.0)
        results = run_simulation([cand], TimeStop(max_bars=2), {"A.T": cache}, end_date=datetime.date(2024, 1, 5))
        assert len(results) == 1
        assert results[0].entry_price == pytest.approx(105.0)  # D1 open = fill price


class TestPortfolioConstraints:
    def test_high_corr_capacity_one(self) -> None:
        # Two high-corr candidates on the same day — only first is accepted
        cache_a = _make_cache("A.T", [100.0] * 8)
        cache_b = _make_cache("B.T", [200.0] * 8)
        start = datetime.date(2024, 1, 1)
        cand_a = _candidate("A.T", start, corr_mode="high")
        cand_b = _candidate("B.T", start, corr_mode="high")
        rule = TimeStop(max_bars=5)
        results = run_simulation(
            [cand_a, cand_b], rule,
            {"A.T": cache_a, "B.T": cache_b},
            end_date=start + datetime.timedelta(days=7),
        )
        assert len(results) == 1

    def test_low_corr_capacity_three(self) -> None:
        # Four low-corr candidates on the same day — only first 3 accepted
        caches = {f"{i}.T": _make_cache(f"{i}.T", [100.0] * 8) for i in range(4)}
        start = datetime.date(2024, 1, 1)
        cands = [_candidate(f"{i}.T", start, corr_mode="low") for i in range(4)]
        rule = TimeStop(max_bars=5)
        results = run_simulation(
            cands, rule, caches,
            end_date=start + datetime.timedelta(days=7),
        )
        assert len(results) == 3

    def test_high_and_low_corr_coexist(self) -> None:
        # 1 high-corr + 1 low-corr simultaneously — both accepted
        cache_h = _make_cache("H.T", [100.0] * 8)
        cache_l = _make_cache("L.T", [100.0] * 8)
        start = datetime.date(2024, 1, 1)
        cands = [
            _candidate("H.T", start, corr_mode="high"),
            _candidate("L.T", start, corr_mode="low"),
        ]
        results = run_simulation(
            cands, TimeStop(max_bars=5),
            {"H.T": cache_h, "L.T": cache_l},
            end_date=start + datetime.timedelta(days=7),
        )
        assert len(results) == 2

    def test_second_high_corr_after_first_closes(self) -> None:
        # high-corr slot frees up after first position closes → second accepted
        start = datetime.date(2024, 1, 1)
        cache_a = _make_cache("A.T", [100.0] * 10)
        cache_b = _make_cache("B.T", [100.0] * 10)
        cand_a = _candidate("A.T", start, corr_mode="high")
        # B enters 2 days later (after A has had bar_index=0,1)
        cand_b = _candidate("B.T", start + datetime.timedelta(days=3), corr_mode="high")
        rule = TimeStop(max_bars=1)  # A exits after bar_index=1 (fast)
        results = run_simulation(
            [cand_a, cand_b], rule,
            {"A.T": cache_a, "B.T": cache_b},
            end_date=start + datetime.timedelta(days=9),
        )
        assert len(results) == 2


class TestEndOfData:
    def test_open_position_force_closed_at_end_date(self) -> None:
        # Large max_bars so time-stop doesn't trigger — position force-closed at end
        cache = _make_cache("A.T", [100.0] * 6)
        start = datetime.date(2024, 1, 1)
        cand = _candidate("A.T", start)
        results = run_simulation(
            [cand], TimeStop(max_bars=100),
            {"A.T": cache},
            end_date=start + datetime.timedelta(days=5),
        )
        assert len(results) == 1
        assert results[0].exit_reason == "end_of_data"

    def test_candidate_past_end_date_skipped(self) -> None:
        # Candidate's entry_date is after end_date — must be ignored
        cache = _make_cache("A.T", [100.0] * 5)
        start = datetime.date(2024, 1, 1)
        cand = _candidate("A.T", start + datetime.timedelta(days=10))
        results = run_simulation(
            [cand], TimeStop(max_bars=3),
            {"A.T": cache},
            end_date=start + datetime.timedelta(days=4),
        )
        assert results == []


class TestReturnCalculation:
    def test_profitable_trade_positive_return(self) -> None:
        cache = _make_cache("A.T", [])
        bars = [
            BarData(dt=_dt(2024,1,1), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,2), open=110.0, high=115.0, low=109.0, close=112.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,3), open=115.0, high=118.0, low=114.0, close=116.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,4), open=118.0, high=120.0, low=117.0, close=119.0, volume=1000, indicators={}),
        ]
        for b in bars:
            cache._bars.append(b)
            cache._dts.append(b.dt)
        cache._closes = np.array([b.close for b in bars])

        cand = _candidate("A.T", datetime.date(2024, 1, 1), entry_price=100.0)
        results = run_simulation([cand], TimeStop(max_bars=2), {"A.T": cache}, end_date=datetime.date(2024, 1, 4))
        assert len(results) == 1
        assert results[0].entry_price == pytest.approx(110.0)  # D1 open
        assert results[0].return_pct > 0


class TestTpSlExit:
    def test_tp_triggered_before_time_stop(self) -> None:
        # Build a price sequence where day 3's high breaches TP
        cache = _make_cache("A.T", [])
        # Wide zs legs → wide TP band
        zs_legs = (5.0, 5.0, 5.0, 5.0)
        entry_p = 100.0
        rule = ZsTpSl(tp_mult=1.0, sl_mult=10.0, alpha=0.5, max_bars=20)
        tp, _ = rule.preview_levels(entry_p, zs_legs)

        bars = [
            BarData(dt=_dt(2024,1,1), open=100.0, high=101.0,  low=99.0,  close=100.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,2), open=entry_p, high=entry_p * 1.01, low=entry_p * 0.99, close=entry_p, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,3), open=100.0, high=100.0,  low=100.0, close=100.0, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,4), open=tp + 2, high=tp + 5, low=tp + 1, close=tp + 3, volume=1000, indicators={}),
            BarData(dt=_dt(2024,1,5), open=100.0, high=100.0,  low=100.0, close=100.0, volume=1000, indicators={}),
        ]
        for b in bars:
            cache._bars.append(b)
            cache._dts.append(b.dt)
        cache._closes = np.array([b.close for b in bars])

        cand = _candidate("A.T", datetime.date(2024, 1, 1), entry_price=entry_p, zs=zs_legs)
        results = run_simulation([cand], rule, {"A.T": cache}, end_date=datetime.date(2024, 1, 5))
        assert len(results) == 1
        assert results[0].exit_reason == "tp"
