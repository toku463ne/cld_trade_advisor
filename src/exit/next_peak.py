"""Next-peak exit rule.

Exits a long trade when the *next early HIGH* zigzag peak is detected
after entry.  Since entry is at an early LOW trough, this closes the
natural swing: buy the dip, sell the next recognised bounce high.

Detection logic mirrors detect_peaks() from src/indicators/zigzag.py:
an early HIGH at bar ``i`` (0-indexed from fill) is confirmed when

    highs[i] == max(highs[i - size : i + middle_size + 1])

which requires ``middle_size`` bars of right-side confirmation.  The
exit fires on the bar that provides that confirmation (i.e. ``middle_size``
bars after the actual peak), and the exit price is that bar's close
(approximating a next-open fill under the two-bar rule).

``max_bars`` is a hard time-stop so the position never stalls indefinitely
if no peak forms within the window.
"""

from __future__ import annotations

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


class NextPeakExit(ExitRule):
    """Exit when the next early HIGH zigzag peak is confirmed.

    Args:
        size:        Bars to the *left* of the candidate that must be lower.
                     Matches the zigzag ``size`` parameter used for entry.
        middle_size: Bars to the *right* required for early confirmation.
                     Matches the zigzag ``middle_size`` parameter.
        max_bars:    Hard time-stop if no peak forms.
    """

    def __init__(
        self,
        size:        int = 5,
        middle_size: int = 2,
        max_bars:    int = 20,
    ) -> None:
        self._size  = size
        self._mid   = middle_size
        self._max   = max_bars
        self._highs: list[float] = []

    @property
    def name(self) -> str:
        return f"next_peak_s{self._size}m{self._mid}"

    def reset(self) -> None:
        self._highs = []

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        self._highs.append(ctx.high)
        b = ctx.bar_index

        if b >= self._max:
            return True, "time"

        # Need at least size bars to the left + middle_size to the right
        if b < self._size + self._mid:
            return False, ""

        # Candidate peak is middle_size bars ago
        candidate_idx = b - self._mid
        candidate_high = self._highs[candidate_idx]

        # Window: size bars left of candidate through middle_size bars right
        left  = max(0, candidate_idx - self._size)
        window = self._highs[left : b + 1]   # b+1 inclusive

        if candidate_high == max(window):
            return True, "next_peak"
        return False, ""
