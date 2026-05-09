"""ZS-based Take-Profit / Stop-Loss exit rule.

Uses an exponentially weighted average (EWA) of recent zigzag leg sizes as
an adaptive volatility estimate.  Older legs are down-weighted by factor
``(1 - alpha)`` per step, so recent volatility dominates:

    ewa = alpha × leg_N  +  (1-alpha) × alpha × leg_(N-1)  +  …

TP and SL levels are multiples of this EWA:

    TP = entry + tp_mult × ewa_ZS
    SL = entry - sl_mult × ewa_ZS

If fewer than ``min_legs`` legs are available, falls back to a plain
percentage TP/SL using ``fallback_pct`` (default 5 %).
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = True


def _ewa(legs: tuple[float, ...], alpha: float) -> float:
    """Exponentially weighted average of zigzag leg sizes.

    ``legs`` is ordered oldest-first.  The newest leg carries weight
    ``alpha``, the one before ``alpha*(1-alpha)``, etc.
    """
    ewa = legs[0]
    for leg in legs[1:]:
        ewa = alpha * leg + (1.0 - alpha) * ewa
    return ewa


class ZsTpSl(ExitRule):
    """ZS-adaptive TP/SL with EWA volatility estimate.

    Args:
        tp_mult:      TP = entry + tp_mult × ewa_ZS.
        sl_mult:      SL = entry − sl_mult × ewa_ZS.
        alpha:        EWA smoothing factor (0 < alpha ≤ 1).
                      Higher = more weight on recent legs.
                      alpha=0.3 → half-life ≈ 2 legs.
                      alpha=0.5 → half-life ≈ 1 leg.
        min_legs:     Minimum history entries before EWA is trusted;
                      fallback to ``fallback_pct`` otherwise.
        fallback_pct: Fallback band as fraction of entry price.
        max_bars:     Hard time-stop safety net.
    """

    def __init__(
        self,
        tp_mult:      float = 1.5,
        sl_mult:      float = 1.0,
        alpha:        float = 0.3,
        min_legs:     int   = 3,
        fallback_pct: float = 0.05,
        max_bars:     int   = 40,
    ) -> None:
        self._tp_mult      = tp_mult
        self._sl_mult      = sl_mult
        self._alpha        = alpha
        self._min_legs     = min_legs
        self._fallback_pct = fallback_pct
        self._max_bars     = max_bars
        self._tp_price:  float = 0.0
        self._sl_price:  float = 0.0

    @property
    def name(self) -> str:
        return f"zs_tp{self._tp_mult}_sl{self._sl_mult}_a{self._alpha}"

    def reset(self) -> None:
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _init_levels(self, ctx: ExitContext) -> None:
        entry = ctx.entry_price
        legs  = ctx.zs_history
        if len(legs) >= self._min_legs:
            band = _ewa(legs, self._alpha)
        else:
            band = entry * self._fallback_pct
        self._tp_price = entry + self._tp_mult * band
        self._sl_price = entry - self._sl_mult * band

    def preview_levels(
        self,
        entry_price: float,
        zs_history: tuple[float, ...],
    ) -> tuple[float, float]:
        """Return (tp_price, sl_price) for a prospective entry without running the sim."""
        if len(zs_history) >= self._min_legs:
            band = _ewa(zs_history, self._alpha)
        else:
            band = entry_price * self._fallback_pct
        return (
            entry_price + self._tp_mult * band,
            entry_price - self._sl_mult * band,
        )

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        if ctx.bar_index == 0:
            self._init_levels(ctx)

        if ctx.bar_index >= self._max_bars:
            return True, "time"
        if ctx.high >= self._tp_price:
            return True, "tp"
        if ctx.low <= self._sl_price:
            return True, "sl"
        return False, ""


EXIT_RULE: "ZsTpSl | None" = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
