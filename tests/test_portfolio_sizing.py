"""Unit tests for lot-aware sizing (pure, no DB)."""

from __future__ import annotations

import pytest

from src.portfolio.sizing import (
    NEUTRAL_TILT_TAU, n225_momentum_regime, neutral_trim_lots,
    position_notional, position_weight, recommended_lots,
)

_BUDGET = 2_000_000   # 6-slot => 333,333 per slot


@pytest.mark.parametrize("price,expected_lots", [
    (1000, 3),     # 100*1000=100k -> 333k//100k = 3 lots
    (3000, 1),     # 300k -> 1 lot
    (500, 6),      # 50k -> 6 lots (cheap names get more lots)
    (3400, 0),     # one lot (340k) > 333k slot -> unaffordable, skip
    (100000, 0),   # pricey heavyweight -> 0
])
def test_recommended_lots(price, expected_lots):
    assert recommended_lots(_BUDGET, price, n_slots=6) == expected_lots


def test_recommended_lots_guards():
    assert recommended_lots(0, 1000) == 0
    assert recommended_lots(_BUDGET, 0) == 0
    assert recommended_lots(_BUDGET, -10) == 0
    assert recommended_lots(_BUDGET, 1000, n_slots=0) == 0


def test_unaffordable_threshold_is_one_lot_over_slot():
    # exactly at the slot budget per lot is affordable; just over is not.
    slot = _BUDGET / 6
    price_at = slot / 100              # one lot == slot budget
    assert recommended_lots(_BUDGET, price_at, 6) == 1
    assert recommended_lots(_BUDGET, price_at * 1.001, 6) == 0


def test_notional_and_weight():
    assert position_notional(3, 1000) == 300_000
    assert position_weight(3, 1000, _BUDGET) == pytest.approx(0.15)
    assert position_weight(0, 1000, _BUDGET) == 0.0
    assert position_weight(3, 1000, 0) == 0.0


# ── Conditional-EV sizing tilt (backlog item 2) ───────────────────────────────

@pytest.mark.parametrize("mom,regime", [
    (None, None),
    (-0.05, "bear"),       # mom <= -0.1%
    (-0.001, "bear"),      # boundary: exactly -0.1% is bear
    (0.0, "neutral"),
    (0.04, "neutral"),
    (0.081, "neutral"),    # boundary: exactly +8.1% is neutral
    (0.10, "bull"),        # > +8.1%
])
def test_n225_momentum_regime(mom, regime):
    assert n225_momentum_regime(mom) == regime


@pytest.mark.parametrize("base_lots,regime,expected", [
    (4, "neutral", 2),     # half-size an expensive name
    (3, "neutral", 1),     # floor(1.5) = 1
    (1, "neutral", 0),     # bimodal: a cheap 1-lot name rounds to 0 = SKIP
    (4, "bull", 4),        # bull/bear unchanged
    (4, "bear", 4),
    (4, None, 4),          # unknown regime → full lots
    (0, "neutral", 0),
])
def test_neutral_trim_lots(base_lots, regime, expected):
    assert neutral_trim_lots(base_lots, regime) == expected


def test_neutral_trim_tau_is_half():
    assert NEUTRAL_TILT_TAU == 0.5
