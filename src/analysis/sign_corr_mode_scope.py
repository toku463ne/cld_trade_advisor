"""sign_corr_mode_scope — per-sign fire counts split by corr_mode.

One-off scoping diagnostic for the within-cell tiebreak debate.
Question: how many sign-events fire as high-corr vs mid vs low across
FY2018-FY2024? The strategy narrows to top-1 only in the high-corr bucket
(in trade mode), so signs with mostly low-corr fires are unaffected by the
sign_score tiebreak proposal.

Compares two definitions:
  - **returns_corr**: pct_change correlation (the CORRECT definition that
    `_rolling_corr_series` and the `moving_corr` table use).
  - **levels_corr**: raw-close correlation (the BUGGY definition currently
    in `src/strategy/regime_sign.py:_rolling_corr`).

Pipeline mirrors `sign_score_decomposition.py`.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d

_MULTIYEAR_MIN_RUN_ID = 47
_N225_CODE = "^N225"
_WINDOW = 20
_HIGH_THRESH = 0.6
_LOW_THRESH = 0.3


class _Ev(NamedTuple):
    sign_type: str
    stock_code: str
    fire_date: datetime.date


def _load_events() -> list[_Ev]:
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
            )
            .join(SignBenchmarkEvent, SignBenchmarkEvent.run_id == SignBenchmarkRun.id)
            .where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).all()
    return [_Ev(r[0], r[1], r[2].date()) for r in rows]


def _load_closes(stock_code: str, start: datetime.date, end: datetime.date) -> pd.Series:
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == stock_code)
            .where(Ohlcv1d.ts >= datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc))
            .where(Ohlcv1d.ts <= datetime.datetime.combine(end, datetime.time.max, tzinfo=datetime.timezone.utc))
            .order_by(Ohlcv1d.ts)
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([float(r[1]) for r in rows], index=[r[0].date() for r in rows], dtype=float)
    s = s[~s.index.duplicated(keep="last")]
    return s


def _classify(corr: float) -> str:
    if pd.isna(corr):
        return "unknown"
    a = abs(corr)
    if a >= _HIGH_THRESH:
        return "high"
    if a <= _LOW_THRESH:
        return "low"
    return "mid"


def main() -> None:
    events = _load_events()
    logger.info("Loaded {} multi-year sign events", len(events))

    by_stock: dict[str, list[_Ev]] = defaultdict(list)
    for e in events:
        by_stock[e.stock_code].append(e)

    min_date = min(e.fire_date for e in events) - datetime.timedelta(days=90)
    max_date = max(e.fire_date for e in events) + datetime.timedelta(days=2)

    n225 = _load_closes(_N225_CODE, min_date, max_date)
    if n225.empty:
        logger.error("No ^N225 1d bars in window")
        return
    n225_ret = n225.pct_change()
    logger.info("Loaded ^N225 daily closes: {} bars", len(n225))

    # counts[sign_type][mode_returns][mode_levels] += 1
    counts: dict[str, dict[tuple[str, str], int]] = defaultdict(lambda: defaultdict(int))
    skipped = 0

    for stock_code, evs in by_stock.items():
        closes = _load_closes(stock_code, min_date, max_date)
        if closes.empty:
            skipped += len(evs)
            continue

        ret = closes.pct_change()
        common = ret.index.intersection(n225_ret.index)
        if len(common) < _WINDOW + 5:
            skipped += len(evs)
            continue

        ret_aligned = ret.reindex(common)
        n225_ret_aligned = n225_ret.reindex(common)
        close_aligned = closes.reindex(common)
        n225_close_aligned = n225.reindex(common)

        roll_ret = ret_aligned.rolling(_WINDOW).corr(n225_ret_aligned)
        roll_lvl = close_aligned.rolling(_WINDOW).corr(n225_close_aligned)

        for e in evs:
            d = e.fire_date
            cr = roll_ret.get(d, np.nan)
            cl = roll_lvl.get(d, np.nan)
            mode_r = _classify(cr)
            mode_l = _classify(cl)
            counts[e.sign_type][(mode_r, mode_l)] += 1

    print(f"\nProcessed {len(events) - skipped:,} events (skipped {skipped:,} for missing bars)")
    print()

    # === Per-sign × returns_corr counts ===
    print("=" * 72)
    print("Per-sign fire counts by returns_corr mode (CORRECT definition)")
    print("=" * 72)
    print(f"{'sign':<14}{'high':>10}{'mid':>10}{'low':>10}{'unknown':>10}{'total':>10}{'high%':>8}")
    sign_totals: dict[str, dict[str, int]] = {}
    for sign in sorted(counts):
        bucket = {"high": 0, "mid": 0, "low": 0, "unknown": 0}
        for (mr, _ml), n in counts[sign].items():
            bucket[mr] += n
        total = sum(bucket.values())
        hi_pct = 100.0 * bucket["high"] / total if total else 0.0
        sign_totals[sign] = bucket
        print(f"{sign:<14}{bucket['high']:>10,}{bucket['mid']:>10,}{bucket['low']:>10,}"
              f"{bucket['unknown']:>10,}{total:>10,}{hi_pct:>7.1f}%")

    # === Buggy vs correct comparison (aggregate) ===
    print()
    print("=" * 72)
    print("Bug impact: how many events change corr_mode when fixed?")
    print("=" * 72)
    print(f"{'sign':<14}{'agree':>10}{'r→l: hi→lo':>14}{'r→l: lo→hi':>14}{'other':>10}{'total':>10}{'churn%':>8}")
    for sign in sorted(counts):
        agree = mod_hl = mod_lh = other = 0
        for (mr, ml), n in counts[sign].items():
            if mr == ml:
                agree += n
            elif mr == "high" and ml == "low":
                mod_hl += n
            elif mr == "low" and ml == "high":
                mod_lh += n
            else:
                other += n
        total = agree + mod_hl + mod_lh + other
        churn = 100.0 * (mod_hl + mod_lh + other) / total if total else 0.0
        print(f"{sign:<14}{agree:>10,}{mod_hl:>14,}{mod_lh:>14,}{other:>10,}{total:>10,}{churn:>7.1f}%")

    # === Scoping verdict ===
    print()
    print("=" * 72)
    print("Scoping: which signs have meaningful high-corr fire volume?")
    print("=" * 72)
    print("(thresh: >100 high-corr fires across 7 FYs = meaningful surface for tiebreak)")
    print()
    sig_signs = [s for s, b in sign_totals.items() if b["high"] > 100]
    nonsig_signs = [s for s, b in sign_totals.items() if b["high"] <= 100]
    print(f"MEANINGFUL high-corr surface ({len(sig_signs)} signs):")
    for s in sig_signs:
        b = sign_totals[s]
        total = sum(b.values())
        print(f"  {s:<14}  high={b['high']:,} ({100*b['high']/total:.0f}%)")
    print()
    print(f"NEGLIGIBLE high-corr surface ({len(nonsig_signs)} signs):")
    for s in nonsig_signs:
        b = sign_totals[s]
        total = sum(b.values())
        print(f"  {s:<14}  high={b['high']:,} ({100*b['high']/max(total,1):.0f}%)")


if __name__ == "__main__":
    main()
