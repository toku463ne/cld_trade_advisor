"""Tests for ZsTpSl exit rule and preview_levels."""

from __future__ import annotations

import pytest

from src.exit.base import ExitContext
from src.exit.zs_tp_sl import ZsTpSl, _ewa


def _ctx(
    bar_index: int,
    entry: float,
    high: float,
    low: float,
    zs: tuple[float, ...] = (100.0, 90.0, 110.0, 95.0),
) -> ExitContext:
    return ExitContext(
        bar_index=bar_index,
        entry_price=entry,
        high=high,
        low=low,
        close=high,
        zs_history=zs,
        adx=15.0,
        adx_pos=10.0,
        adx_neg=8.0,
        peak_adx=15.0,
    )


class TestEwa:
    def test_single_leg(self) -> None:
        assert _ewa((100.0,), 0.3) == pytest.approx(100.0)

    def test_two_legs_alpha_one(self) -> None:
        # alpha=1: newest leg dominates entirely
        assert _ewa((80.0, 120.0), 1.0) == pytest.approx(120.0)

    def test_two_legs_alpha_half(self) -> None:
        # 0.5 * 120 + 0.5 * 80 = 100
        assert _ewa((80.0, 120.0), 0.5) == pytest.approx(100.0)

    def test_monotone_convergence(self) -> None:
        # With identical legs, EWA should equal the value
        val = _ewa((50.0,) * 10, 0.3)
        assert val == pytest.approx(50.0)


class TestZsTpSlLevels:
    def test_tp_above_entry(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        tp, sl = rule.preview_levels(1000.0, (100.0, 90.0, 110.0, 95.0))
        assert tp > 1000.0

    def test_sl_below_entry(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        tp, sl = rule.preview_levels(1000.0, (100.0, 90.0, 110.0, 95.0))
        assert sl < 1000.0

    def test_fallback_when_few_legs(self) -> None:
        # Fewer legs than min_legs (default 3) → fallback_pct
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3, min_legs=3, fallback_pct=0.05)
        tp, sl = rule.preview_levels(1000.0, (100.0,))
        expected_band = 1000.0 * 0.05
        assert tp == pytest.approx(1000.0 + 2.0 * expected_band)
        assert sl == pytest.approx(1000.0 - 2.0 * expected_band)

    def test_tp_sl_symmetric_with_equal_mults(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        legs = (100.0, 100.0, 100.0, 100.0)
        tp, sl = rule.preview_levels(1000.0, legs)
        assert pytest.approx(tp - 1000.0) == pytest.approx(1000.0 - sl)

    def test_larger_mult_wider_tp(self) -> None:
        legs = (80.0, 90.0, 100.0, 110.0)
        r1   = ZsTpSl(tp_mult=1.0, sl_mult=1.0, alpha=0.3)
        r2   = ZsTpSl(tp_mult=3.0, sl_mult=1.0, alpha=0.3)
        tp1, _ = r1.preview_levels(1000.0, legs)
        tp2, _ = r2.preview_levels(1000.0, legs)
        assert tp2 > tp1


class TestZsTpSlShouldExit:
    def test_no_exit_on_entry_bar(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        # Bar 0 just initialises levels; price is midpoint, should not exit
        ctx = _ctx(bar_index=0, entry=1000.0, high=1010.0, low=995.0)
        exited, reason = rule.should_exit(ctx)
        assert not exited

    def test_tp_triggered(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        legs = (100.0, 100.0, 100.0, 100.0)
        # Initialise levels on bar 0
        ctx0 = _ctx(0, entry=1000.0, high=1001.0, low=999.0, zs=legs)
        rule.should_exit(ctx0)
        tp, _ = rule.preview_levels(1000.0, legs)
        # Now bar 1 breaches TP
        ctx1 = _ctx(1, entry=1000.0, high=tp + 1.0, low=tp - 1.0, zs=legs)
        exited, reason = rule.should_exit(ctx1)
        assert exited
        assert reason == "tp"

    def test_sl_triggered(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        legs = (100.0, 100.0, 100.0, 100.0)
        ctx0 = _ctx(0, entry=1000.0, high=1001.0, low=999.0, zs=legs)
        rule.should_exit(ctx0)
        _, sl = rule.preview_levels(1000.0, legs)
        ctx1 = _ctx(1, entry=1000.0, high=sl + 1.0, low=sl - 1.0, zs=legs)
        exited, reason = rule.should_exit(ctx1)
        assert exited
        assert reason == "sl"

    def test_time_stop(self) -> None:
        rule = ZsTpSl(tp_mult=10.0, sl_mult=10.0, alpha=0.3, max_bars=5)
        legs = (100.0,) * 4
        ctx0 = _ctx(0, 1000.0, 1001.0, 999.0, zs=legs)
        rule.should_exit(ctx0)
        for i in range(1, 5):
            exited, reason = rule.should_exit(_ctx(i, 1000.0, 1001.0, 999.0, zs=legs))
            assert not exited, f"Should not exit at bar {i}"
        exited, reason = rule.should_exit(_ctx(5, 1000.0, 1001.0, 999.0, zs=legs))
        assert exited
        assert reason == "time"

    def test_reset_clears_levels(self) -> None:
        rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        ctx0 = _ctx(0, 1000.0, 1001.0, 999.0)
        rule.should_exit(ctx0)
        rule.reset()
        assert rule._tp_price == 0.0
        assert rule._sl_price == 0.0
