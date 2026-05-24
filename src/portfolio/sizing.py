"""Lot-aware position sizing — single source of truth for live UI and backtest.

The book is an equal-yen, fixed-slot allocation traded in 単元株 (100-share lots):
each of ``n_slots`` slots gets ``budget / n_slots``, and a name is bought in whole
100-share lots up to that per-slot budget. A name whose single lot already exceeds the
slot budget is **unaffordable** and skipped (``recommended_lots == 0``) — the slot just
stays empty rather than over-committing capital.

Why share price (not market cap) drives this: at a real ¥2,000,000 / 6-slot account the
binding constraint is "can one 100-share lot fit in a ~333k slot", i.e. share price ≲
¥3,300. This module is imported by both the Daily-tab register box (recommended default
lots) and the capital-aware backtest (deployed-capital weighting), so live sizing and
simulated sizing cannot drift apart.
"""
from __future__ import annotations

LOT_SHARES = 100        # 単元株: one lot = 100 shares
DEFAULT_SLOTS = 6       # production book: 1 high-corr + 5 low-corr (_MAX_HIGH/_MAX_LOW_CORR)


def recommended_lots(budget: float, price: float, n_slots: int = DEFAULT_SLOTS,
                     lot_shares: int = LOT_SHARES) -> int:
    """Whole 100-share lots that fit one equal-yen slot.

    ``floor((budget / n_slots) / (lot_shares * price))``. Returns ``0`` when one lot
    exceeds the per-slot budget (unaffordable → skip) or when any input is non-positive.
    """
    if budget <= 0 or price <= 0 or n_slots <= 0 or lot_shares <= 0:
        return 0
    slot_budget = budget / n_slots
    return int(slot_budget // (lot_shares * price))


def position_notional(lots: int, price: float, lot_shares: int = LOT_SHARES) -> float:
    """Yen committed to a position of ``lots`` × ``lot_shares`` shares at ``price``."""
    return lots * lot_shares * price


def position_weight(lots: int, price: float, budget: float,
                    lot_shares: int = LOT_SHARES) -> float:
    """Fraction of total ``budget`` actually deployed in this position (0.0 if none).

    Sum over the book is < 1.0 by the integer-lot remainder (cash drag) — this is what
    the backtest weights daily returns by, instead of an idealized ``1 / n_slots``.
    """
    if budget <= 0:
        return 0.0
    return position_notional(lots, price, lot_shares) / budget
