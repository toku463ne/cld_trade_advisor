"""universe_baseline — all-stock-day forward-return baseline per feature bucket.

For each of the 8 *stock-state* features (correlations + indicator distances +
zigzag momentum), the mean fixed-H forward return over EVERY stock-day (not just
sign fires), bucketed the same way `sign_characteristics.py` buckets them, per
temporal split. This is the "universe beta" null: what a random stock-day in a
given bucket returns, regardless of any sign.

`sign_characteristics.py` subtracts these bucket means before measuring per-sign
effects, so a reported effect is the sign's *excess* over the universe tilt — not
inherited beta (the corr_hsi finding generalized to all stock-state features).

Output: /tmp/universe_baseline.pkl  →  {feature: {split: {bucket: mean_fwd}}}

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_baseline
"""
from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.analysis.sign_features import (
    _FWD_H, _LOAD_END, _LOAD_START, _corr_series, _distance_frame,
)
from src.data.db import get_session
from src.simulator.cache import DataCache

_INDICES = {"^N225", "^GSPC", "^HSI"}
_OUT = "/tmp/universe_baseline.pkl"
# All-stock-day data contains glitch bars (unadjusted corporate actions →
# astronomical 20-bar returns) that sign detectors never fire on. Winsorize so a
# handful of glitches don't dominate the bucket means. ±60% over 20 bars is well
# beyond any normal move (fire 99.9th pct ≈ +45%). MUST match the analyzer.
_WINSOR = 0.6

# Must match sign_characteristics.py bucketers.
_CORR_FEATS = ["corr_n225", "corr_gspc", "corr_hsi"]
_DIST_FEATS = ["sma_dist", "kumo_dist", "chiko_dist", "tenkan_dist", "zz_momentum"]


def _split(fy: int) -> str:
    return ("discover" if fy <= 2016 else "validate" if fy <= 2021
            else "holdout" if fy <= 2024 else "strategy")


def _corr_bucket(c: float) -> str | None:
    if pd.isna(c):
        return None
    a = abs(c)
    return "high" if a >= 0.6 else "low" if a <= 0.3 else "mid"


def _sign_bucket(v: float) -> str | None:
    if pd.isna(v):
        return None
    return "above" if v >= 0 else "below"


def run(out_path: str = _OUT) -> dict:
    with get_session() as s:
        n225 = DataCache("^N225", "1d"); n225.load(s, _LOAD_START, _LOAD_END)
        gspc = DataCache("^GSPC", "1d"); gspc.load(s, _LOAD_START, _LOAD_END)
        hsi = DataCache("^HSI", "1d"); hsi.load(s, _LOAD_START, _LOAD_END)
        codes = [r[0] for r in s.execute(text(
            "select distinct stock_code from ohlcv_1d order by stock_code")).all()]
    codes = [c for c in codes if c not in _INDICES]

    # accumulators: feature -> split -> bucket -> [sum, n]
    acc: dict = {f: defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
                 for f in _CORR_FEATS + _DIST_FEATS}

    for i, code in enumerate(codes, 1):
        with get_session() as s:
            c = DataCache(code, "1d"); c.load(s, _LOAD_START, _LOAD_END)
        if len(c.bars) < 60:
            continue
        dist = _distance_frame(c)
        corr = {"corr_n225": _corr_series(c, n225),
                "corr_gspc": _corr_series(c, gspc),
                "corr_hsi": _corr_series(c, hsi)}
        opens = [b.open for b in c.bars]
        closes = [b.close for b in c.bars]
        for idx, bar in enumerate(c.bars):
            ei, xi = idx + 1, idx + 1 + _FWD_H
            if xi >= len(closes) or not opens[ei]:
                continue
            d = bar.dt.date()
            fwd = (closes[xi] - opens[ei]) / opens[ei]
            if fwd > _WINSOR:
                fwd = _WINSOR
            elif fwd < -_WINSOR:
                fwd = -_WINSOR
            sp = _split(d.year if d.month >= 4 else d.year - 1)
            for f in _CORR_FEATS:
                v = corr[f].loc[d] if d in corr[f].index else np.nan
                b = _corr_bucket(v)
                if b:
                    acc[f][sp][b][0] += fwd; acc[f][sp][b][1] += 1
            if d in dist.index:
                for f in _DIST_FEATS:
                    b = _sign_bucket(dist.loc[d, f])
                    if b:
                        acc[f][sp][b][0] += fwd; acc[f][sp][b][1] += 1
        if i % 50 == 0:
            logger.info("  [{}/{}] {}", i, len(codes), code)

    baseline: dict = {}
    for f, splits in acc.items():
        baseline[f] = {sp: {b: (s_n[0] / s_n[1]) for b, s_n in bk.items() if s_n[1] > 0}
                       for sp, bk in splits.items()}
    pd.to_pickle(baseline, out_path)
    logger.info("Wrote universe baseline → {}", out_path)
    # quick sanity print
    for f in _CORR_FEATS + _DIST_FEATS:
        d = baseline[f].get("discover", {})
        logger.info("  {}: discover {}", f, {b: round(m * 100, 2) for b, m in d.items()})
    return baseline


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()


if __name__ == "__main__":
    main()
