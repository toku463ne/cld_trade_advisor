"""ZS TP/SL with directional-ratio scaling of the take-profit target.

At entry (bar_index == 0) the directional ratio is computed from the ADX
+DI / −DI balance:

    dr = adx_pos / (adx_pos + adx_neg)   ∈ [0, 1]

    dr > 0.5  → +DI dominates (bullish)  → widen TP
    dr = 0.5  → balanced                 → base TP
    dr < 0.5  → −DI dominates (bearish)  → tighten TP

The TP multiplier is scaled linearly around the neutral midpoint:

    tp_mult_eff = base_tp × (1 + k × (dr − 0.5) / 0.5)

clipped to [clip_lo, clip_hi] to prevent degenerate bands.

The SL multiplier is intentionally fixed at base_sl regardless of regime,
so losses are bounded consistently while profits are allowed to run further
in strong bullish conditions.
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


def _ewa(legs: tuple[float, ...], alpha: float) -> float:
    ewa = legs[0]
    for leg in legs[1:]:
        ewa = alpha * leg + (1.0 - alpha) * ewa
    return ewa


class ZsDrTpSl(ExitRule):
    """ZS TP/SL with directional-ratio-scaled take-profit.

    Args:
        base_tp:      TP multiplier at neutral regime (dr = 0.5).
        base_sl:      SL multiplier (fixed, not scaled by regime).
        k:            Scaling sensitivity.  k=0.5 → TP ranges
                      [base_tp×0.5, base_tp×1.5] across dr ∈ [0, 1].
        alpha:        EWA smoothing for zigzag-leg band estimate.
        min_legs:     Minimum history entries before EWA is trusted.
        fallback_pct: Fallback band as fraction of entry price.
        max_bars:     Hard time-stop safety net.
        clip_lo:      Minimum tp_mult_eff (prevent TP collapsing to zero).
        clip_hi:      Maximum tp_mult_eff (prevent runaway TP).
    """

    def __init__(
        self,
        base_tp:      float = 1.5,
        base_sl:      float = 1.0,
        k:            float = 0.5,
        alpha:        float = 0.3,
        min_legs:     int   = 3,
        fallback_pct: float = 0.05,
        max_bars:     int   = 40,
        clip_lo:      float = 0.2,
        clip_hi:      float = 3.0,
    ) -> None:
        self._base_tp      = base_tp
        self._base_sl      = base_sl
        self._k            = k
        self._alpha        = alpha
        self._min_legs     = min_legs
        self._fallback_pct = fallback_pct
        self._max_bars     = max_bars
        self._clip_lo      = clip_lo
        self._clip_hi      = clip_hi
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    @property
    def name(self) -> str:
        return f"zs_dr_tp{self._base_tp}_sl{self._base_sl}_k{self._k}"

    def reset(self) -> None:
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _init_levels(self, ctx: ExitContext) -> None:
        entry = ctx.entry_price
        legs  = ctx.zs_history

        band = (
            _ewa(legs, self._alpha)
            if len(legs) >= self._min_legs
            else entry * self._fallback_pct
        )

        total_di = ctx.adx_pos + ctx.adx_neg
        dr = ctx.adx_pos / total_di if total_di > 0 else 0.5

        tp_mult = self._base_tp * (1.0 + self._k * (dr - 0.5) / 0.5)
        tp_mult = max(self._clip_lo, min(self._clip_hi, tp_mult))

        self._tp_price = entry + tp_mult * band
        self._sl_price = entry - self._base_sl * band

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
