"""ADX-adaptive exit rule (approaches 1 + 2 combined).

Reads the stock's ADX at the entry bar and selects both the *rule type*
and *parameters* accordingly:

    ADX ≥ 25  (strong trend)   → AdxTrail(drop=5, min=3, max=15)
                                  ride the momentum until it fades
    15 ≤ ADX < 25  (moderate)  → ZsTpSl(tp=1.0, sl=0.75, a=0.3, max=15)
                                  balanced TP/SL, medium hold
    ADX < 15  (choppy/weak)    → ZsTpSl(tp=0.75, sl=0.5, a=0.3, max=10)
                                  grab the quick bounce, exit fast

The thresholds encode two ideas from the design session:
  - Approach 1: TP/SL parameters scale with signal strength (DR proxy = ADX)
  - Approach 2: ADX selects between a trailing rule and a TP/SL rule
"""

from __future__ import annotations

import copy

from src.exit.adx_trail import AdxTrail
from src.exit.base import ExitContext, ExitRule
from src.exit.zs_tp_sl import ZsTpSl

_HIGH_ADX = 25.0
_MID_ADX  = 15.0

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


def _make_delegate(adx: float) -> ExitRule:
    if adx >= _HIGH_ADX:
        return AdxTrail(drop_threshold=5.0, min_bars=3, max_bars=15)
    if adx >= _MID_ADX:
        return ZsTpSl(tp_mult=1.0, sl_mult=0.75, alpha=0.3, max_bars=15)
    return ZsTpSl(tp_mult=0.75, sl_mult=0.5, alpha=0.3, max_bars=10)


class AdxAdaptiveRule(ExitRule):
    """Exit rule whose type and parameters are selected by entry ADX.

    The delegate is created on the first bar (bar_index == 0) and reused
    for the entire trade.  ``reset()`` clears the delegate so the next
    trade gets a fresh selection.
    """

    def __init__(self) -> None:
        self._delegate: ExitRule | None = None

    @property
    def name(self) -> str:
        return "adx_adaptive"

    def reset(self) -> None:
        self._delegate = None

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        if self._delegate is None:
            self._delegate = _make_delegate(ctx.adx)
            self._delegate.reset()
        return self._delegate.should_exit(ctx)

    def __deepcopy__(self, memo: dict) -> "AdxAdaptiveRule":
        new = AdxAdaptiveRule()
        new._delegate = copy.deepcopy(self._delegate, memo)
        return new
