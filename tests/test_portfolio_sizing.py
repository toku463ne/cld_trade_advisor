"""Unit tests for lot-aware sizing (pure, no DB)."""

from __future__ import annotations

import pytest

from src.portfolio.sizing import (
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
