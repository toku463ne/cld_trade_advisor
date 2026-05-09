"""ATR trailing-stop exit rule.

Trails a stop loss at ``running_high − k × ATR(period)`` below the
highest close seen since entry.  ATR is computed on-the-fly from bars
accumulated during the trade using Wilder's EMA smoothing.

The stop tightens naturally when bars narrow (low ATR) and widens
during volatility spikes, adapting to intrabar range rather than
zigzag structure.  Typical hold: 5–12 bars.
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


class AtrTrail(ExitRule):
    """ATR-based trailing stop.

    Args:
        k:          Stop distance = k × ATR below running high.
        atr_period: Wilder smoothing period for ATR.
        max_bars:   Hard time-stop safety net.
    """

    def __init__(
        self,
        k:          float = 1.5,
        atr_period: int   = 5,
        max_bars:   int   = 15,
    ) -> None:
        self._k      = k
        self._period = atr_period
        self._alpha  = 1.0 / atr_period   # Wilder smoothing
        self._max    = max_bars
        # per-trade state
        self._prev_close:   float | None = None
        self._atr:          float | None = None
        self._tr_sum:       float        = 0.0
        self._warmup:       int          = 0
        self._running_high: float        = 0.0

    @property
    def name(self) -> str:
        return f"atr_trail_k{self._k}_p{self._period}"

    def reset(self) -> None:
        self._prev_close   = None
        self._atr          = None
        self._tr_sum       = 0.0
        self._warmup       = 0
        self._running_high = 0.0

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        # True Range
        if self._prev_close is None:
            tr = ctx.high - ctx.low
        else:
            tr = max(
                ctx.high - ctx.low,
                abs(ctx.high - self._prev_close),
                abs(ctx.low  - self._prev_close),
            )
        self._prev_close = ctx.close

        # Wilder ATR warmup then EMA
        if self._atr is None:
            self._warmup  += 1
            self._tr_sum  += tr
            if self._warmup >= self._period:
                self._atr = self._tr_sum / self._period
        else:
            self._atr = self._alpha * tr + (1.0 - self._alpha) * self._atr

        # Running high (close-based to avoid noise from intrabar spikes)
        self._running_high = max(self._running_high, ctx.close)

        if ctx.bar_index >= self._max:
            return True, "time"

        if self._atr is None:
            return False, ""

        stop = self._running_high - self._k * self._atr
        if ctx.low <= stop:
            return True, "atr_trail"
        return False, ""
