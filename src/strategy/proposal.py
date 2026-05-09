"""SignalProposal — lightweight proposal type for RegimeSignStrategy."""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class SignalProposal:
    """One trade candidate emitted by RegimeSignStrategy.propose().

    Attributes:
        sign_type:        Sign detector that fired (e.g. "str_hold").
        stock_code:       Ticker of the stock.
        sign_score:       Raw score from the detector (higher = stronger signal).
        fired_at:         Datetime the sign condition was first satisfied.
        valid_until:      Last bar within the sign's validity window.
        corr_mode:        "high" (|corr|≥0.6) / "mid" / "low" (|corr|≤0.3) vs ^N225.
        corr_n225:        Actual 20-bar rolling correlation at fired_at.
        kumo_state:       N225 Ichimoku Kumo state at fired_at: +1 above / 0 inside / -1 below.
        adx:              N225 ADX(14) at fired_at.
        adx_pos:          N225 +DI at fired_at.
        adx_neg:          N225 -DI at fired_at.
        regime_bench_flw: Expected bench_flw for (sign, kumo_state) from benchmark history.
        regime_dr:        Direction-rate for (sign, kumo_state) from benchmark history.
        regime_n:         Event count behind the benchmark estimate.
    """

    sign_type:        str
    stock_code:       str
    sign_score:       float
    fired_at:         datetime.datetime
    valid_until:      datetime.datetime
    corr_mode:        str    # "high" | "mid" | "low"
    corr_n225:        float
    kumo_state:       int    # +1 / 0 / -1
    adx:              float
    adx_pos:          float
    adx_neg:          float
    regime_bench_flw: float
    regime_dr:        float
    regime_n:         int

    def __str__(self) -> str:
        return (
            f"{self.sign_type:<12} {self.stock_code:<10} "
            f"score={self.sign_score:.3f}  corr={self.corr_mode}({self.corr_n225:+.2f})  "
            f"kumo={'▲' if self.kumo_state==1 else ('▼' if self.kumo_state==-1 else '~')}  "
            f"adx={self.adx:.1f}  bench_flw={self.regime_bench_flw:.4f}  dr={self.regime_dr:.1%}"
        )
