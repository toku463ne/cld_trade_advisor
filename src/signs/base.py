"""SignResult — value object returned by all sign detectors."""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class SignResult:
    """A sign that fired and is still valid at the query datetime.

    score > 0 always (callers receive None when no valid sign exists).
    fired_at is the bar where the condition was first satisfied.
    valid_until is the last bar within the validity window; the sign may
    expire earlier if its situational validity check fails.
    """

    sign_type: str                   # "div_bar" | "corr_flip" | "str_hold" | ...
    stock_code: str
    score: float
    fired_at: datetime.datetime
    valid_until: datetime.datetime
