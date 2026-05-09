"""ADX trailing-stop exit rule.

Exits when the ADX drops by *drop_threshold* points from its peak since entry.
A minimum hold period (*min_bars*) prevents premature exits on noise at entry.

Optionally combines with a time-stop (*max_bars*) so the position never
lingers if ADX stays flat.
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


class AdxTrail(ExitRule):
    """ADX trailing stop.

    Args:
        drop_threshold: Exit when ADX falls this many points from its peak
                        since entry (e.g. 5.0).
        min_bars:       Minimum bars before the trail activates; prevents
                        exit on day-1 ADX dip.
        max_bars:       Hard time-stop as safety net.
    """

    def __init__(
        self,
        drop_threshold: float = 5.0,
        min_bars:       int   = 5,
        max_bars:       int   = 40,
    ) -> None:
        self._drop  = drop_threshold
        self._min   = min_bars
        self._max   = max_bars

    @property
    def name(self) -> str:
        return f"adx_trail_d{self._drop}"

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        if ctx.bar_index >= self._max:
            return True, "time"
        if ctx.bar_index < self._min:
            return False, ""
        # peak_adx is maintained by the simulator (max ADX seen since entry)
        if ctx.adx <= ctx.peak_adx - self._drop:
            return True, "adx_trail"
        return False, ""
