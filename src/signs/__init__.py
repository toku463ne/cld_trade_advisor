"""Sign detectors — all fire on hourly bar timestamps.

Naming convention: <group>_<mechanism>
  div_*  — stock diverges from a benchmark or peer group
  corr_* — correlation regime change
  str_*  — relative strength / resilience
  brk_*  — price-level breakout (technical)

Typical usage from simulator::

    from src.signs import (
        SignResult,
        DivBarDetector, DivVolDetector, DivGapDetector, DivPeerDetector,
        CorrFlipDetector, CorrShiftDetector, CorrPeakDetector,
        StrHoldDetector, StrLeadDetector,
        BrkSmaDetector, BrkBolDetector,
    )

    # Initialise once (pre-computes indicator series from loaded caches)
    div_bar  = DivBarDetector(stock_cache_1h, n225_cache_1h, window=20)
    str_hold = StrHoldDetector(stock_cache_1h, n225_cache_1h)   # hourly caches

    # Query per bar — O(valid_bars), returns None when no valid sign
    for bar in stock_cache_1h.bars:
        sign = div_bar.detect(bar.dt, valid_bars=5)
        if sign:
            print(sign.sign_type, sign.score, sign.fired_at)
"""

from src.signs.base import SignResult
from src.signs.div_bar import DivBarDetector
from src.signs.div_vol import DivVolDetector
from src.signs.div_gap import DivGapDetector
from src.signs.div_peer import DivPeerDetector
from src.signs.corr_flip import CorrFlipDetector
from src.signs.corr_shift import CorrShiftDetector
from src.signs.corr_peak import CorrPeakDetector
from src.signs.str_hold import StrHoldDetector
from src.signs.str_lead import StrLeadDetector
from src.signs.brk_sma import BrkSmaDetector
from src.signs.brk_bol import BrkBolDetector
from src.signs.brk_hi_sideway import BrkHiSidewayDetector
from src.signs.rev_peak import RevPeakDetector
from src.signs.rev_nday import RevNDayDetector
from src.signs.rev_nhold import RevNholdDetector
from src.signs.rev_nlo import RevNloDetector
from src.signs.str_lag import StrLagDetector

__all__ = [
    "SignResult",
    "DivBarDetector",
    "DivVolDetector",
    "DivGapDetector",
    "DivPeerDetector",
    "CorrFlipDetector",
    "CorrShiftDetector",
    "CorrPeakDetector",
    "StrHoldDetector",
    "StrLeadDetector",
    "StrLagDetector",
    "BrkSmaDetector",
    "BrkBolDetector",
    "BrkHiSidewayDetector",
    "RevPeakDetector",
    "RevNDayDetector",
    "RevNholdDetector",
    "RevNloDetector",
]
