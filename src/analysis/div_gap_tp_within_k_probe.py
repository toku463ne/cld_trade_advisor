"""div_gap_tp_within_k_probe — measures the §5.3 definition-drift risk for the
proposed `_WAIT_BARS` change.

For each multi-year `div_gap` event (FY2018–FY2024), simulate the actual live
exit rule `ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)` from K=0 entry
(open of fire+1, the existing two-bar fill model) and report the fraction of
events whose TP fires within bars 1–3 of entry.

Why: the Wait-K IV measures `remaining_signed_return = (peak − entry_K)/entry_K × dir`
against the **original zigzag peak**, not the live ZsTpSl exit. The previous
sign-debate Judge flagged that if many K=0 trades hit TP within 3 bars, the
proposed wait converts those wins into **missed entries** — the same mechanism
that cost Sharpe 3.25→1.13 in the 2026-05-12 score-retire A/B.

Gate per Judge falsifier:
  - <5%  TP-within-3-bars  → proceed to Phase 2 (env-gated A/B)
  - 5-15%                  → hold (Insufficient evidence; re-derive IV)
  - ≥15%                   → reject (definition-drift dominant)

CLI: uv run --env-file devenv python -m src.analysis.div_gap_tp_within_k_probe
"""

from __future__ import annotations

import bisect
import datetime
import math
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.sign_wait_iv import _load_bars_1d, _classify_corr
from src.data.db import get_session
from src.data.models import Ohlcv1d
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.portfolio.crud import _ZS_LOOKBACK, _ZZ_SIZE, _ZZ_MIDDLE

_BENCH_MD = Path(__file__).parent / "benchmark.md"

_MULTIYEAR_MIN_RUN_ID = 47
_SIGN                 = "div_gap"
_CORR_WINDOW          = 20
_CORR_MIN_PERIODS     = 10
_N225_CODE            = "^N225"
_LOOKBACK_DAYS        = 400          # for zs leg computation
_FORWARD_BARS         = 12           # walk forward up to 12 bars
_K_BUCKETS            = (1, 2, 3, 5, 10)
_TP_MULT              = 2.0
_SL_MULT              = 2.0
_ALPHA                = 0.3
_FALLBACK_PCT         = 0.02


def _load_bars_full(code: str, start: datetime.date, end: datetime.date):
    """Like _load_bars_1d but also returns high and low arrays."""
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    end_dt   = datetime.datetime.combine(end,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.high_price,
                   Ohlcv1d.low_price, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == code)
            .where(Ohlcv1d.ts >= start_dt)
            .where(Ohlcv1d.ts <= end_dt)
            .order_by(Ohlcv1d.ts)
        ).all()
    dates, opens, highs, lows, closes = [], [], [], [], []
    seen: set[datetime.date] = set()
    for ts, op, hi, lo, cl in rows:
        d = ts.date()
        if d in seen:
            continue
        seen.add(d)
        dates.append(d)
        opens.append(float(op))
        highs.append(float(hi))
        lows.append(float(lo))
        closes.append(float(cl))
    return dates, opens, highs, lows, closes


def _zs_legs_at(stock_bars: tuple, n225_dates_set: set, idx_upto: int) -> tuple[float, ...]:
    """Compute zigzag leg sizes from stock bars up to (and including) idx_upto."""
    dates, _, highs, lows, _ = stock_bars
    # Filter to bars within window ending at idx_upto, intersecting with N225 trading days
    pairs = [(dates[i], highs[i], lows[i]) for i in range(0, idx_upto + 1) if dates[i] in n225_dates_set]
    if len(pairs) < _ZZ_SIZE * 2 + 1:
        return ()
    hs = [p[1] for p in pairs]
    ls = [p[2] for p in pairs]
    peaks = sorted(detect_peaks(hs, ls, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE),
                   key=lambda p: p.bar_index)
    legs: list[float] = []
    prev_price: float | None = None
    for p in peaks:
        if prev_price is not None:
            legs.append(abs(p.price - prev_price))
        prev_price = p.price
    return tuple(legs[-_ZS_LOOKBACK:])


def phase_probe() -> dict:
    # 1. Load div_gap events
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun)
            .where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
            .where(SignBenchmarkRun.sign_type == _SIGN)
        ).scalars().all()
    run_ids = [r.id for r in runs]
    if not run_ids:
        logger.warning("No multi-year runs for {}", _SIGN)
        return {}
    rows = []
    with get_session() as s:
        evts = s.execute(
            select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(run_ids))
        ).scalars().all()
    for e in evts:
        if e.trend_direction is None:
            continue
        rows.append({
            "stock":     e.stock_code,
            "fire_date": e.fired_at.date(),
            "dir":       int(e.trend_direction),
        })
    df = pd.DataFrame(rows)
    logger.info("Loaded {:,} {} events", len(df), _SIGN)

    # 2. Date range for bar loads (include lookback for zigzag + forward for hit-test)
    min_d = df["fire_date"].min() - datetime.timedelta(days=_LOOKBACK_DAYS + 30)
    max_d = df["fire_date"].max() + datetime.timedelta(days=30)

    # 3. N225 for corr_mode tagging
    n225_dates, _, _, _, n225_cl = _load_bars_full(_N225_CODE, min_d, max_d)
    n225_close = pd.Series(n225_cl, index=n225_dates).sort_index()
    n225_ret   = n225_close.pct_change()
    n225_dates_set = set(n225_dates)

    rule = ZsTpSl(tp_mult=_TP_MULT, sl_mult=_SL_MULT, alpha=_ALPHA)

    # Per-(corr_mode) tallies of (n_total, [tp_within_k for k in _K_BUCKETS], [sl_within_k])
    tally = {m: {"n": 0,
                 "tp": [0] * len(_K_BUCKETS),
                 "sl": [0] * len(_K_BUCKETS),
                 "none": 0} for m in ("high", "mid", "low")}

    skipped = defaultdict(int)

    for stock, sub in df.groupby("stock"):
        bars = _load_bars_full(stock, min_d, max_d)
        s_dates, s_opens, s_highs, s_lows, s_closes = bars
        if len(s_dates) < _CORR_WINDOW + 5:
            skipped["no_bars"] += len(sub)
            continue
        # Build corr series vs N225
        s_close_ser = pd.Series(s_closes, index=s_dates)
        s_ret = s_close_ser.pct_change()
        common = s_ret.index.intersection(n225_ret.index)
        if len(common) < _CORR_WINDOW + 5:
            skipped["no_corr"] += len(sub)
            continue
        corr = (
            s_ret.reindex(common)
                 .rolling(_CORR_WINDOW, min_periods=_CORR_MIN_PERIODS)
                 .corr(n225_ret.reindex(common))
        )

        for _, e in sub.iterrows():
            fd = e["fire_date"]
            # Find fire bar idx in stock series
            fire_idx = bisect.bisect_right(s_dates, fd) - 1
            if fire_idx < 0 or fire_idx + 1 >= len(s_dates):
                skipped["no_entry"] += 1
                continue
            entry_idx = fire_idx + 1
            entry_price = s_opens[entry_idx]
            if entry_price <= 0:
                skipped["bad_entry"] += 1
                continue
            # corr_mode
            mode = _classify_corr(corr.get(s_dates[fire_idx], float("nan")))
            if mode == "unknown":
                skipped["no_corr_tag"] += 1
                continue
            # zigzag legs up to fire bar
            legs = _zs_legs_at(bars, n225_dates_set, fire_idx)
            tp_price, sl_price = rule.preview_levels(entry_price, legs)
            # Walk forward bars 1..max(_K_BUCKETS) checking hit
            hit_bar = None
            hit_kind = None
            for bar_offset in range(1, _FORWARD_BARS + 1):
                pos = entry_idx + bar_offset - 1   # bar at entry_idx is K=0 entry day = bar 0; bar 1 = entry_idx+1? Off-by-one — see comment
                # Convention: ZsTpSl.should_exit is called with bar_index starting from
                # the entry bar (bar 0 = entry day). Hit-test on its high/low is meaningful
                # from bar 0 onward. For "TP within K bars" we count bars AFTER entry,
                # so bar 1 = day at entry_idx + 1 (= entry day + 1). Use the same logic.
                pos = entry_idx + bar_offset
                if pos >= len(s_dates):
                    break
                hi = s_highs[pos]
                lo = s_lows[pos]
                if hi >= tp_price:
                    hit_bar = bar_offset
                    hit_kind = "tp"
                    break
                if lo <= sl_price:
                    hit_bar = bar_offset
                    hit_kind = "sl"
                    break
            t = tally[mode]
            t["n"] += 1
            if hit_kind is None:
                t["none"] += 1
            else:
                for i, K in enumerate(_K_BUCKETS):
                    if hit_bar <= K:
                        t[hit_kind][i] += 1

    if skipped:
        logger.info("Skipped: {}", dict(skipped))
    logger.info("Per corr_mode counts: " + ", ".join(f"{m}={tally[m]['n']}" for m in ("high","mid","low")))
    return tally


def phase_report(tally: dict) -> None:
    if not tally:
        logger.warning("No tally")
        return
    today = datetime.date.today().isoformat()
    md = [
        "", "---", "",
        "## div_gap TP-within-K Probe (FY2018–FY2024)",
        "",
        f"Generated: {today}  ",
        f"Probes the §5.3 definition-drift risk for the proposed `_WAIT_BARS = {{('div_gap','high'):3, ('div_gap','mid'):2}}` change.  ",
        f"For each multi-year div_gap event: simulate `ZsTpSl(tp={_TP_MULT}, sl={_SL_MULT}, α={_ALPHA})` from K=0 entry (open of fire+1) using zigzag leg history at fire_date; walk forward up to {_FORWARD_BARS} bars; report fraction of events whose TP or SL fires within K bars.  ",
        "",
        "If a large fraction of K=0 trades exit at TP within 3 bars, the proposed wait converts those wins into missed entries — the same mechanism that cost Sharpe 3.25→1.13 in the 2026-05-12 score-retire A/B.  ",
        "",
        f"Judge falsifier gate: TP-within-3-bars fraction:  **<5% → Accept Phase 2  |  5–15% → Insufficient evidence  |  ≥15% → Reject**.  ",
        "",
        "| corr_mode | n | " + " | ".join(f"TP≤K={K}" for K in _K_BUCKETS) + " | " + " | ".join(f"SL≤K={K}" for K in _K_BUCKETS) + " | no_exit |",
        "|-----------|--:|" + "--:|" * (2 * len(_K_BUCKETS) + 1),
    ]
    for mode in ("high", "mid", "low"):
        t = tally[mode]
        n = t["n"]
        if n == 0:
            continue
        tp_cells = " | ".join(f"{t['tp'][i]/n*100:.1f}%" for i in range(len(_K_BUCKETS)))
        sl_cells = " | ".join(f"{t['sl'][i]/n*100:.1f}%" for i in range(len(_K_BUCKETS)))
        none_pct = t["none"] / n * 100
        md.append(f"| **{mode}** | {n} | {tp_cells} | {sl_cells} | {none_pct:.1f}% |")
    md.append("")
    md.append("**Verdict by gate** (TP-within-3-bars):  ")
    for mode in ("high", "mid"):
        t = tally[mode]
        if t["n"] == 0:
            continue
        tp3_pct = t["tp"][2] / t["n"] * 100
        if tp3_pct < 5:
            verdict = "✅ <5% — Accept Phase 2 (env-gated A/B)"
        elif tp3_pct < 15:
            verdict = "⚠️ 5–15% — Insufficient evidence; re-derive IV"
        else:
            verdict = "❌ ≥15% — Reject (definition-drift dominant)"
        md.append(f"- **div_gap {mode}**: TP≤3 = {tp3_pct:.1f}% → {verdict}  ")
    md.append("")
    with open(_BENCH_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(md))
    logger.info("Appended TP-within-K probe section to {}", _BENCH_MD)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    tally = phase_probe()
    phase_report(tally)


if __name__ == "__main__":
    main()
