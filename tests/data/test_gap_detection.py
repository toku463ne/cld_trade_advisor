"""Unit tests for compute_gaps — no DB required."""

from __future__ import annotations

import datetime

import pytest

from src.data.collect import compute_gaps

UTC = datetime.timezone.utc


def dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=UTC)


class TestComputeGaps:
    def test_no_existing_data_returns_full_range(self) -> None:
        gaps = compute_gaps(dt("2024-01-01"), dt("2024-12-31"), None, None)
        assert gaps == [(dt("2024-01-01"), dt("2024-12-31"))]

    def test_data_fully_covers_range_returns_no_gaps(self) -> None:
        gaps = compute_gaps(
            dt("2024-03-01"),
            dt("2024-06-01"),
            dt("2024-01-01"),
            dt("2024-12-31"),
        )
        assert gaps == []

    def test_left_gap_only(self) -> None:
        gaps = compute_gaps(
            dt("2024-01-01"),
            dt("2024-12-31"),
            dt("2024-06-01"),
            dt("2024-12-31"),
        )
        assert gaps == [(dt("2024-01-01"), dt("2024-06-01"))]

    def test_right_gap_only(self) -> None:
        gaps = compute_gaps(
            dt("2024-01-01"),
            dt("2024-12-31"),
            dt("2024-01-01"),
            dt("2024-06-01"),
        )
        assert gaps == [(dt("2024-06-01"), dt("2024-12-31"))]

    def test_both_gaps(self) -> None:
        gaps = compute_gaps(
            dt("2024-01-01"),
            dt("2024-12-31"),
            dt("2024-04-01"),
            dt("2024-09-01"),
        )
        assert gaps == [
            (dt("2024-01-01"), dt("2024-04-01")),
            (dt("2024-09-01"), dt("2024-12-31")),
        ]
