"""Time-stop exit rule: exit after a fixed number of bars.

This is the baseline rule.  Every trade exits at bar N regardless of P&L.
Useful as a control against which dynamic rules are compared.
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


class TimeStop(ExitRule):
    """Exit unconditionally after *max_bars* bars.

    Args:
        max_bars: Number of bars to hold (1 = exit on the bar after entry).
    """

    def __init__(self, max_bars: int = 20) -> None:
        self._max_bars = max_bars

    @property
    def name(self) -> str:
        return f"time_{self._max_bars}b"

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        if ctx.bar_index >= self._max_bars:
            return True, "time"
        return False, ""
