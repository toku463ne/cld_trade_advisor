"""CompositeExitRule — first-fires-wins combination of multiple exit rules."""

from __future__ import annotations

import copy

from src.exit.base import ExitContext, ExitRule

EXIT_VALID: bool = False
EXIT_RULE: "ExitRule | None" = None


class CompositeExitRule(ExitRule):
    """Wraps N exit rules and exits on whichever fires first.

    On each bar, sub-rules are evaluated left-to-right; the first one
    that returns ``exit_now=True`` determines the exit reason.
    ``reset()`` propagates to all sub-rules so per-trade state is cleared.

    Args:
        rules: Two or more ExitRule instances.
        name:  Optional name override; defaults to "composite(<r1>+<r2>+...)".
    """

    def __init__(self, rules: list[ExitRule], name: str | None = None) -> None:
        if len(rules) < 2:
            raise ValueError("CompositeExitRule requires at least 2 rules")
        self._rules = rules
        self._name  = name or "composite(" + "+".join(r.name for r in rules) + ")"

    @property
    def name(self) -> str:
        return self._name

    def reset(self) -> None:
        for r in self._rules:
            r.reset()

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        for rule in self._rules:
            exit_now, reason = rule.should_exit(ctx)
            if exit_now:
                return True, reason
        return False, ""

    def __copy__(self) -> "CompositeExitRule":
        return CompositeExitRule(
            rules=[copy.deepcopy(r) for r in self._rules],
            name=self._name,
        )
