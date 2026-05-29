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
DEFAULT_BUDGET = 2_000_000   # ¥ fallback when the active account has no budget set (live plan)


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


# ── Conditional-EV sizing tilt (confluence backlog item 2; /sign-debate ACCEPT 2026-05-29) ──
# A DRAWDOWN guideline: on a confluence entry whose ^N225 trailing-60-bar momentum is in the
# NEUTRAL tercile, trim the position to ~half lots; keep bull/bear entries at full lots.
# Validated across fill-order, phase+order, integer-lot, and held-out cutoff-CV paired nulls
# (~4.5pp shallower maxDD, forward-stable). It is explicitly NOT a return or Sharpe claim — the
# held-out return is ~−5pp (an insurance premium). Integer rounding makes the live rule BIMODAL:
# a 1-lot (cheap) neutral name rounds to 0 → SKIP; a >=2-lot name is genuinely half-sized.
# Cutoffs are frozen from the certified result; the cutoff-CV showed the edge is robust to the
# exact cutoff. See docs/analysis/confluence_improvement_backlog.md item 2 +
# docs/analysis/confluence_evtilt_sizing_preregistration.md.
N225_MOM_BARS = 60                  # trailing bars for the ^N225 momentum regime
NEUTRAL_TILT_TAU = 0.5              # neutral-regime lot trim factor (applied with floor)
N225_REGIME_BEAR_MAX = -0.001       # mom <= -0.1%             -> bear    (full lots)
N225_REGIME_NEUTRAL_MAX = 0.081     # -0.1% < mom <= +8.1%     -> neutral (trim); else bull (full)


def n225_momentum_regime(mom: float | None) -> str | None:
    """Classify ^N225 trailing-60-bar momentum into ``"bear"`` / ``"neutral"`` / ``"bull"``.

    Returns ``None`` when momentum is unavailable (insufficient history). The neutral tercile
    is the β-stripped EV weak spot the sizing tilt trims.
    """
    if mom is None:
        return None
    if mom <= N225_REGIME_BEAR_MAX:
        return "bear"
    if mom <= N225_REGIME_NEUTRAL_MAX:
        return "neutral"
    return "bull"


def neutral_trim_lots(base_lots: int, regime: str | None,
                      tau: float = NEUTRAL_TILT_TAU) -> int:
    """Lots to actually buy after the conditional-EV sizing tilt.

    In the NEUTRAL ^N225-momentum regime, trim to ``floor(tau * base_lots)`` — a cheap 1-lot
    name rounds to 0 (skip), a >=2-lot name is half-sized. bear / bull / unknown regimes keep
    ``base_lots`` unchanged. This is a drawdown guideline, not a return play.
    """
    if regime == "neutral":
        return int(tau * base_lots)
    return base_lots
