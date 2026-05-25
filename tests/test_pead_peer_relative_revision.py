"""Unit tests for the pure peer-relative PEAD logic (no DB / no network)."""

from __future__ import annotations

import datetime

import numpy as np

from src.analysis.pead_peer_relative_revision import (
    bin_edges, bin_of, peer_reference_surprise,
)

_D = datetime.date


def test_peer_reference_median_in_window():
    t = _D(2024, 5, 1)
    peers = [
        [(_D(2024, 4, 20), 0.02)],                       # in window, most recent 0.02
        [(_D(2024, 4, 10), -0.01)],                      # in window
        [(_D(2024, 4, 25), 0.05), (_D(2024, 3, 1), 0.9)],  # most recent before t = 0.05
    ]
    # median of {0.02, -0.01, 0.05} = 0.02
    assert peer_reference_surprise(peers, t, window_days=90, floor=3) == 0.02


def test_peer_reference_excludes_on_or_after_t_and_stale():
    t = _D(2024, 5, 1)
    peers = [
        [(_D(2024, 5, 1), 0.10)],     # ON t → excluded (strictly before)
        [(_D(2024, 1, 1), 0.10)],     # stale (>90d) → excluded
        [(_D(2024, 4, 20), 0.02)],    # valid
    ]
    # only 1 valid < floor 3 → None
    assert peer_reference_surprise(peers, t, window_days=90, floor=3) is None


def test_peer_reference_picks_most_recent_strictly_before():
    t = _D(2024, 5, 1)
    peers = [
        [(_D(2024, 4, 1), 0.01), (_D(2024, 4, 28), 0.09)],
        [(_D(2024, 4, 2), 0.03)],
        [(_D(2024, 4, 3), 0.05)],
    ]
    # peer1 most-recent = 0.09 → median{0.09,0.03,0.05}=0.05
    assert peer_reference_surprise(peers, t, window_days=90, floor=3) == 0.05


def test_peer_reference_empty_histories_skipped():
    t = _D(2024, 5, 1)
    peers = [[], [], [(_D(2024, 4, 20), 0.02)]]
    assert peer_reference_surprise(peers, t, floor=3) is None


def test_bin_edges_and_of():
    vals = np.array([float(i) for i in range(100)])
    e = bin_edges(vals, 5)
    assert len(e) == 4
    assert bin_of(-1.0, e) == 0
    assert bin_of(1000.0, e) == 4
    assert bin_of(float(np.median(vals)), e) == 2
