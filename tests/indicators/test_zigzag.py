"""Tests for src.indicators.zigzag.detect_peaks."""

from __future__ import annotations

import pytest

from src.indicators.zigzag import Peak, detect_peaks


def _prices(
    values: list[float],
) -> tuple[list[float], list[float]]:
    """Return (highs, lows) where each bar's high == low == value."""
    return values, values


class TestDetectPeaksBasic:
    def test_single_peak(self) -> None:
        # 0 1 2 3 4 5 6 7 8 9 10  (11 bars, size=3)
        # detect_peaks checks midi=i+size; with size=3, i in range(5,0,-1)
        # so midi=5 is checked when i=2: win_full=h[2:8]=[3,4,5,6,5,4], max at bar 5
        h = [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]
        l = [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]
        peaks = detect_peaks(h, l, size=3, middle_size=2)
        highs = [p for p in peaks if p.direction == 2]
        assert len(highs) == 1
        assert highs[0].bar_index == 5
        assert highs[0].price == pytest.approx(6.0)

    def test_single_trough(self) -> None:
        h = [6, 5, 4, 3, 2, 1, 2, 3, 4, 5, 6]
        l = [6, 5, 4, 3, 2, 1, 2, 3, 4, 5, 6]
        peaks = detect_peaks(h, l, size=3, middle_size=2)
        lows = [p for p in peaks if p.direction == -2]
        assert len(lows) == 1
        assert lows[0].bar_index == 5
        assert lows[0].price == pytest.approx(1.0)

    def test_empty_input(self) -> None:
        assert detect_peaks([], [], size=5, middle_size=2) == []

    def test_short_input_returns_empty(self) -> None:
        # Not enough bars to find anything with size=5
        h = [1, 2, 3, 4, 5]
        l = [1, 2, 3, 4, 5]
        peaks = detect_peaks(h, l, size=5, middle_size=2)
        assert peaks == []

    def test_flat_input_at_most_one_confirmed_per_sign(self) -> None:
        # For a constant series every bar ties for max and min. The algorithm
        # can only assign direction=2 to the first candidate; subsequent equal-
        # price bars are demoted to early (dir=1) by the walk-back logic.
        h = [10.0] * 20
        l = [10.0] * 20
        peaks = detect_peaks(h, l, size=5, middle_size=2)
        conf_highs = [p for p in peaks if p.direction == 2]
        conf_lows  = [p for p in peaks if p.direction == -2]
        assert len(conf_highs) <= 1
        assert len(conf_lows)  <= 1

    def test_multiple_peaks(self) -> None:
        # Two clear peaks separated by a trough
        #  0  1  2  3  4  5  6  7  8  9  10 11 12 13 14
        #  1  2  3  4  5  4  3  2  3  4  5  4  3  2  1
        h = [1, 2, 3, 4, 5, 4, 3, 2, 3, 4, 5, 4, 3, 2, 1]
        l = [1, 2, 3, 4, 5, 4, 3, 2, 3, 4, 5, 4, 3, 2, 1]
        peaks = detect_peaks(h, l, size=3, middle_size=2)
        types = [p.direction for p in peaks]
        # Should have at least confirmed highs and a trough
        assert 2 in types  # confirmed high

    def test_alternating_confirmed_types(self) -> None:
        peaks = detect_peaks(
            [1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5],
            [1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5],
            size=1, middle_size=1,
        )
        dirs = [p.direction for p in peaks if abs(p.direction) == 2]
        # Peaks and troughs should alternate in sign
        for a, b in zip(dirs, dirs[1:]):
            assert a * b < 0, "Confirmed peaks should alternate high/low"

    def test_prices_are_correct(self) -> None:
        h = [1, 2, 3, 4, 5, 4, 3, 2, 1]
        l = [0, 1, 2, 3, 4, 3, 2, 1, 0]
        peaks = detect_peaks(h, l, size=4, middle_size=2)
        for p in peaks:
            if p.direction > 0:
                assert p.price == h[p.bar_index]
            else:
                assert p.price == l[p.bar_index]


class TestDetectPeaksEdgeCases:
    def test_all_ascending_no_confirmed_high(self) -> None:
        h = list(range(1, 20))
        l = list(range(1, 20))
        peaks = detect_peaks(h, l, size=5, middle_size=2)
        conf_highs = [p for p in peaks if p.direction == 2]
        assert len(conf_highs) == 0

    def test_direction_values_valid(self) -> None:
        h = [1, 3, 5, 7, 9, 7, 5, 3, 1, 3, 5, 7, 9, 7, 5, 3, 1]
        l = [1, 3, 5, 7, 9, 7, 5, 3, 1, 3, 5, 7, 9, 7, 5, 3, 1]
        peaks = detect_peaks(h, l, size=4, middle_size=2)
        for p in peaks:
            assert p.direction in {2, -2, 1, -1}

    def test_bar_index_in_range(self) -> None:
        n = 30
        h = [float(i % 10) for i in range(n)]
        l = [float(i % 10) for i in range(n)]
        peaks = detect_peaks(h, l, size=4, middle_size=2)
        for p in peaks:
            assert 0 <= p.bar_index < n
