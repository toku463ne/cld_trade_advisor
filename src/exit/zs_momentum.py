"""ZS-momentum-scaled TP/SL exit rule (approach 3).

Compares the *most recent* zigzag leg size to the EWA of all available
legs.  The ratio captures whether recent volatility is expanding or
contracting relative to historical norm:

    ratio = legs[-1] / ewa(legs, alpha)

Both TP and SL multipliers are scaled by this ratio (clipped to
[clip_lo, clip_hi] to prevent extreme bands):

    tp_mult_eff = base_tp * clip(ratio, clip_lo, clip_hi)
    sl_mult_eff = base_sl * clip(ratio, clip_lo, clip_hi)

Interpretation:
  ratio > 1  → legs getting bigger → widen both TP and SL (expect larger
               next move AND larger pullbacks before that move)
  ratio < 1  → legs contracting   → tighten both (smaller targets, less
               tolerance for adverse moves)

If fewer than ``min_legs`` legs exist, ratio is assumed to be 1.0
(no scaling) and the base multipliers apply directly.
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


def _ewa(legs: tuple[float, ...], alpha: float) -> float:
    val = legs[0]
    for leg in legs[1:]:
        val = alpha * leg + (1.0 - alpha) * val
    return val


class ZsMomentumTpSl(ExitRule):
    """EWA-momentum-scaled ZS TP/SL.

    Args:
        base_tp:   Base TP multiplier before momentum scaling.
        base_sl:   Base SL multiplier before momentum scaling.
        alpha:     EWA smoothing for both the band estimate and momentum.
        min_legs:  Minimum legs to trust the momentum ratio; uses ratio=1
                   (no scaling) otherwise.
        clip_lo:   Minimum ratio clip (prevent bands collapsing to zero).
        clip_hi:   Maximum ratio clip (prevent runaway bands).
        max_bars:  Hard time-stop.
        fallback_pct: Fallback band if fewer than min_legs available.
    """

    def __init__(
        self,
        base_tp:      float = 1.0,
        base_sl:      float = 0.75,
        alpha:        float = 0.3,
        min_legs:     int   = 3,
        clip_lo:      float = 0.5,
        clip_hi:      float = 2.0,
        max_bars:     int   = 15,
        fallback_pct: float = 0.05,
    ) -> None:
        self._base_tp      = base_tp
        self._base_sl      = base_sl
        self._alpha        = alpha
        self._min_legs     = min_legs
        self._clip_lo      = clip_lo
        self._clip_hi      = clip_hi
        self._max_bars     = max_bars
        self._fallback_pct = fallback_pct
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    @property
    def name(self) -> str:
        return f"zs_mom_tp{self._base_tp}_sl{self._base_sl}_a{self._alpha}"

    def reset(self) -> None:
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _init_levels(self, ctx: ExitContext) -> None:
        entry = ctx.entry_price
        legs  = ctx.zs_history

        if len(legs) >= self._min_legs:
            ewa   = _ewa(legs, self._alpha)
            ratio = legs[-1] / ewa if ewa > 0 else 1.0
            ratio = max(self._clip_lo, min(self._clip_hi, ratio))
            band  = ewa
        else:
            ratio = 1.0
            band  = entry * self._fallback_pct

        tp_mult = self._base_tp * ratio
        sl_mult = self._base_sl * ratio

        self._tp_price = entry + tp_mult * band
        self._sl_price = entry - sl_mult * band

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
