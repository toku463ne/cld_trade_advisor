"""brk_hi_sideway_probe — break above a recent sideways-range wall.

Operator hypothesis (2026-05-17): sideways price ranges in the recent
past form "walls" that act as tested support/resistance.  A clean
breakout above such a wall (`low[T] > wall AND low[T-1] ≤ wall`)
should be a meaningful bullish event — distinct from generic rolling-
N-max breakouts because the wall is from a tight consolidation, not
from a single spike high.

Parameters (operator-chosen 2026-05-17):
    K        = 10 trading-day sideways window
    theta    = 0.05  (range/mean tightness)
    lookback = 120 bars (~6 months for finding walls)

Sideways range at bar i:
    window = [i-K+1, i]
    (max(high[window]) − min(low[window])) / mean(close[window]) ≤ theta

Wall at bar T:
    wall[T] = max(tight_window_high[j] for j in [T-lookback, T-K-1])

Fire (strict, transition-gated):
    fire[T] = (low[T] > wall[T-1]) AND (low[T-1] ≤ wall[T-1])

Two evaluations:
  1. Standalone EV (same gate as long_high_continuation probes)
  2. Confluence-incremental: add brk_hi_sideway fires to the v2
     bullish set and re-bucket — does ΔEV(uplift[≥3]−[1]) improve?

Read-only.  Output: src/analysis/benchmark.md
§ brk_hi_sideway Probe (standalone + confluence-incremental).
"""
from __future__ import annotations

import datetime
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.bullish_confluence_v2_probe import (
    _BULLISH_SIGNS,
    _VALID_BARS,
    _ev as _conf_ev,
    _next_peak_from,
    _Bar,
)
from src.analysis.long_high_continuation_probe import (
    _FY_CONFIG,
    _FY_OOS,
    _FY_TRAINING,
    _GATE_DR_MIN,
    _GATE_POOLED_EV,
    _stocks_for_fy,
)
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.indicators.zigzag import detect_peaks

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## brk_hi_sideway Probe"
_MULTIYEAR_MIN_RUN_ID = 47

# Operator-specified parameters
_K        = 10
_THETA    = 0.05
_LOOKBACK = 120
_VALID_BARS_NEW = 5      # new sign uses 5-bar validity (mid of range)

_TREND_CAP = 30
_ZZ_SIZE   = 5
_ZZ_MID    = 2

# Confluence gate (same as v2)
_GATE_UPLIFT_3 = 0.010
_GATE_UPLIFT_2 = 0.005
_GATE_FY_CONSIST_CONF = 4
_GATE_OOS_N_MIN_CONF  = 50


# ── 1. Detect brk_hi_sideway fires ────────────────────────────────────


@dataclass
class _Fire:
    stock:     str
    fire_date: datetime.date
    fy:        str
    wall:      float
    score:     float       # (close − wall) / wall, dimensionless
    trend_dir: int | None
    trend_mag: float | None


def _detect_for_stock(stock: str, fy_label: str,
                      bench_start: datetime.date,
                      bench_end: datetime.date) -> list[_Fire]:
    Ohlcv = OHLCV_MODEL_MAP["1d"]
    span_start = bench_start - datetime.timedelta(days=_LOOKBACK + 60)
    span_end   = bench_end   + datetime.timedelta(days=60)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv.ts, Ohlcv.open_price, Ohlcv.high_price,
                   Ohlcv.low_price, Ohlcv.close_price)
            .where(
                Ohlcv.stock_code == stock,
                Ohlcv.ts >= datetime.datetime.combine(span_start,
                    datetime.time.min, tzinfo=datetime.timezone.utc),
                Ohlcv.ts <= datetime.datetime.combine(span_end,
                    datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            .order_by(Ohlcv.ts)
        ).all()
    if len(rows) < _LOOKBACK + 30:
        return []

    bars: list[_Bar] = []
    seen: set[datetime.date] = set()
    for r in rows:
        d = r.ts.date() if hasattr(r.ts, "date") else r.ts
        if d in seen:
            continue
        seen.add(d)
        ts = r.ts if hasattr(r.ts, "tzinfo") else \
             datetime.datetime.combine(r.ts, datetime.time.min,
                                       tzinfo=datetime.timezone.utc)
        bars.append(_Bar(dt=ts, open=float(r.open_price),
                         high=float(r.high_price),
                         low=float(r.low_price),
                         close=float(r.close_price)))
    if not bars:
        return []

    n = len(bars)
    closes = np.array([b.close for b in bars])
    highs  = np.array([b.high  for b in bars])
    lows   = np.array([b.low   for b in bars])
    dates  = [b.dt.date() for b in bars]

    # 1. tight_window_high[i] = highs[i-K+1..i].max() if window tight, else nan
    tight_high = np.full(n, np.nan)
    for i in range(_K - 1, n):
        wnd_hi = highs[i - _K + 1 : i + 1].max()
        wnd_lo = lows[i  - _K + 1 : i + 1].min()
        wnd_mn = closes[i - _K + 1 : i + 1].mean()
        if wnd_mn > 0 and (wnd_hi - wnd_lo) / wnd_mn <= _THETA:
            tight_high[i] = wnd_hi

    # 2. wall[T] = max of tight_high over [T-lookback, T-K-1]
    #    shift then rolling-max
    s = pd.Series(tight_high).shift(_K + 1)   # value at T → tight_high[T-K-1]
    wall = s.rolling(_LOOKBACK - _K, min_periods=1).max().to_numpy()

    # Pre-compute peaks once for trend outcome
    peaks = sorted(detect_peaks(list(highs), list(lows), size=_ZZ_SIZE,
                                middle_size=_ZZ_MID), key=lambda p: p.bar_index)

    fires: list[_Fire] = []
    for T in range(1, n):
        d = dates[T]
        if d < bench_start or d > bench_end:
            continue
        w_prev = wall[T - 1]
        if np.isnan(w_prev) or w_prev <= 0:
            continue
        if not (lows[T] > w_prev and lows[T - 1] <= w_prev):
            continue
        tdir, tmag = _next_peak_from(T, bars, peaks)
        fires.append(_Fire(
            stock=stock, fire_date=d, fy=fy_label,
            wall=float(w_prev),
            score=float((closes[T] - w_prev) / w_prev),
            trend_dir=tdir, trend_mag=tmag,
        ))
    return fires


# ── 2. Standalone metrics ─────────────────────────────────────────────


def _ev_standalone(sub: pd.DataFrame) -> tuple[float, float, int]:
    n = len(sub)
    if n == 0:
        return (float("nan"), float("nan"), 0)
    wd = sub.dropna(subset=["trend_dir"])
    if wd.empty:
        return (float("nan"), float("nan"), n)
    dr = (wd["trend_dir"] == 1).sum() / len(wd)
    flw = wd[wd["trend_dir"] == 1]["trend_mag"].dropna()
    rev = wd[wd["trend_dir"] == -1]["trend_mag"].dropna()
    if flw.empty or rev.empty:
        return (float("nan"), dr, n)
    return (dr * float(flw.mean()) - (1 - dr) * float(rev.mean()), dr, n)


# ── 3. Confluence-incremental ─────────────────────────────────────────


def _load_existing_fires() -> dict[str, list[tuple[str, datetime.date]]]:
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkRun.sign_type.in_(_BULLISH_SIGNS),
            )
        ).all()
    by_stock: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sign, stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        by_stock[stock].append((sign, d))
    return by_stock


def _confluence_table(
    existing_fires: dict[str, list[tuple[str, datetime.date]]],
    new_fires:      list[_Fire],
    include_new:    bool,
) -> dict[str, dict[str, tuple[float, float, int]]]:
    """Run v2 confluence with optional new sign included.

    Returns: bucket_name → fy → (ev, dr, n)
    """
    NEW_NAME = "brk_hi_sideway"
    VALID = dict(_VALID_BARS)
    if include_new:
        VALID[NEW_NAME] = _VALID_BARS_NEW

    # Merge new fires by stock
    by_stock = defaultdict(list)
    for stock, fires in existing_fires.items():
        by_stock[stock].extend(fires)
    if include_new:
        for f in new_fires:
            by_stock[f.stock].append((NEW_NAME, f.fire_date))

    Ohlcv = OHLCV_MODEL_MAP["1d"]
    # Aggregate per (stock, trade_date)
    rows: list[dict] = []
    for fy_label, start_str, end_str, cluster_year in _FY_CONFIG:
        bench_start = datetime.date.fromisoformat(start_str)
        bench_end   = datetime.date.fromisoformat(end_str)
        codes = _stocks_for_fy(f"classified{cluster_year}")
        if not codes:
            continue
        for code in codes:
            stock_fires = by_stock.get(code, [])
            if not stock_fires:
                continue
            with get_session() as s:
                bar_rows = s.execute(
                    select(Ohlcv.ts, Ohlcv.open_price, Ohlcv.high_price,
                           Ohlcv.low_price, Ohlcv.close_price)
                    .where(
                        Ohlcv.stock_code == code,
                        Ohlcv.ts >= datetime.datetime.combine(
                            bench_start - datetime.timedelta(days=15),
                            datetime.time.min, tzinfo=datetime.timezone.utc),
                        Ohlcv.ts <= datetime.datetime.combine(
                            bench_end + datetime.timedelta(days=60),
                            datetime.time.max, tzinfo=datetime.timezone.utc),
                    )
                    .order_by(Ohlcv.ts)
                ).all()
            if len(bar_rows) < 40:
                continue
            bars: list[_Bar] = []
            seen: set[datetime.date] = set()
            for r in bar_rows:
                d = r.ts.date() if hasattr(r.ts, "date") else r.ts
                if d in seen:
                    continue
                seen.add(d)
                ts = r.ts if hasattr(r.ts, "tzinfo") else \
                     datetime.datetime.combine(r.ts, datetime.time.min,
                                               tzinfo=datetime.timezone.utc)
                bars.append(_Bar(dt=ts, open=float(r.open_price),
                                 high=float(r.high_price),
                                 low=float(r.low_price),
                                 close=float(r.close_price)))
            if not bars:
                continue
            trading_dates = [b.dt.date() for b in bars]
            date_to_idx = {d: i for i, d in enumerate(trading_dates)}
            highs = [b.high for b in bars]
            lows  = [b.low for b in bars]
            peaks = sorted(detect_peaks(highs, lows, size=_ZZ_SIZE,
                                        middle_size=_ZZ_MID),
                           key=lambda p: p.bar_index)
            valid_per_date: dict[int, set[str]] = defaultdict(set)
            for sign, fd in stock_fires:
                if fd not in date_to_idx:
                    continue
                fi = date_to_idx[fd]
                vb = VALID.get(sign, 5)
                for j in range(fi, min(fi + vb + 1, len(bars))):
                    valid_per_date[j].add(sign)
            for i, d in enumerate(trading_dates):
                if d < bench_start or d > bench_end:
                    continue
                valid = valid_per_date.get(i, set())
                if not valid:
                    continue
                tdir, tmag = _next_peak_from(i, bars, peaks)
                rows.append({
                    "stock":     code,
                    "trade_date": d,
                    "fy":        fy_label,
                    "n_signs":   len(valid),
                    "trend_dir": tdir,
                    "trend_mag": tmag,
                })
    df = pd.DataFrame(rows)
    # Bucket
    def _b(n: int) -> str:
        return "≥3" if n >= 3 else str(n)
    df["bucket"] = df["n_signs"].apply(_b)
    out: dict[str, dict[str, tuple[float, float, int]]] = {}
    for b in ["1", "2", "≥3"]:
        out[b] = {}
        for fy in _FY_TRAINING + [_FY_OOS]:
            sub = df[(df["bucket"] == b) & (df["fy"] == fy)]
            out[b][fy] = _conf_ev(sub)
        sub_t = df[(df["bucket"] == b) & (df["fy"].isin(_FY_TRAINING))]
        out[b]["__pooled__"] = _conf_ev(sub_t)
        sub_o = df[(df["bucket"] == b) & (df["fy"] == _FY_OOS)]
        out[b]["__oos__"] = _conf_ev(sub_o)
    return out


# ── 4. Report ─────────────────────────────────────────────────────────


def _format_report(df_fires: pd.DataFrame,
                   conf_before: dict, conf_after: dict) -> str:
    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Fires when a bar's low "
        f"breaks above a recent sideways-range wall:",
        "",
        "```",
        f"sideways range at i: (max H − min L) / mean C ≤ θ on bars [i-K+1, i]",
        f"wall[T] = max(tight_window_high[j] for j in [T-lookback, T-K-1])",
        f"fire[T] = (low[T] > wall[T-1]) AND (low[T-1] ≤ wall[T-1])",
        "",
        f"K        = {_K} bars (sideways window)",
        f"θ        = {_THETA} (range/mean tightness)",
        f"lookback = {_LOOKBACK} bars (~6 months)",
        f"validity = {_VALID_BARS_NEW} trading days (for confluence inclusion)",
        "```",
        "",
        "### 1. Standalone fire-rate and EV",
        "",
        "| FY | n fires | DR | EV | mean score |",
        "|----|---:|---:|---:|---:|",
    ]
    for fy in _FY_TRAINING + [_FY_OOS]:
        sub = df_fires[df_fires["fy"] == fy]
        ev, dr, n = _ev_standalone(sub)
        sc = sub["score"].mean() if not sub.empty else float("nan")
        dr_s = f"{dr*100:.1f}%" if not math.isnan(dr) else "—"
        ev_s = f"{ev:+.4f}" if not math.isnan(ev) else "—"
        sc_s = f"{sc*100:+.2f}%" if not math.isnan(sc) else "—"
        lines.append(f"| {fy} | {n} | {dr_s} | {ev_s} | {sc_s} |")

    train = df_fires[df_fires["fy"].isin(_FY_TRAINING)]
    oos   = df_fires[df_fires["fy"] == _FY_OOS]
    ev_t, dr_t, n_t = _ev_standalone(train)
    ev_o, dr_o, n_o = _ev_standalone(oos)
    lines.append(f"| **pooled train** | **{n_t}** | **{dr_t*100:.1f}%** | "
                 f"**{ev_t:+.4f}** | — |")
    lines.append(f"| **FY2025 OOS** | **{n_o}** | **{dr_o*100:.1f}%** | "
                 f"**{ev_o:+.4f}** | — |")

    standalone_pass = (not math.isnan(ev_t) and ev_t >= _GATE_POOLED_EV
                       and not math.isnan(ev_o) and ev_o > 0
                       and not math.isnan(dr_t) and dr_t >= _GATE_DR_MIN)
    lines += [
        "",
        f"**Standalone gate** (same as long-high probes — pooled EV ≥ "
        f"{_GATE_POOLED_EV:+.3f}, FY2025 EV > 0, DR ≥ {_GATE_DR_MIN*100:.0f}%): "
        f"**{'PASS' if standalone_pass else 'FAIL'}**",
        "",
        "### 2. Confluence-incremental value (vs v2 7-sign baseline)",
        "",
        f"Compares EV uplifts (≥3 sign confluence vs 1 sign) WITHOUT brk_hi_sideway "
        f"in the bullish set vs WITH it included.  If brk_hi_sideway pushes more "
        f"days into the ≥2/≥3 buckets AND those new entries carry the same edge, "
        f"the uplift gap should widen.",
        "",
        "| FY | EV[1] before | EV[≥3] before | uplift before | EV[1] after | EV[≥3] after | uplift after | Δ uplift |",
        "|----|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fy in _FY_TRAINING + [_FY_OOS]:
        ev1_b = conf_before["1"][fy][0]
        ev3_b = conf_before["≥3"][fy][0]
        ev1_a = conf_after["1"][fy][0]
        ev3_a = conf_after["≥3"][fy][0]
        u_b = ev3_b - ev1_b if not (math.isnan(ev3_b) or math.isnan(ev1_b)) else float("nan")
        u_a = ev3_a - ev1_a if not (math.isnan(ev3_a) or math.isnan(ev1_a)) else float("nan")
        d_u = u_a - u_b if not (math.isnan(u_a) or math.isnan(u_b)) else float("nan")
        def _fmt(x):
            return f"{x:+.4f}" if not math.isnan(x) else "—"
        def _fmt_pp(x):
            return f"{x*100:+.2f}pp" if not math.isnan(x) else "—"
        lines.append(f"| {fy} | {_fmt(ev1_b)} | {_fmt(ev3_b)} | "
                     f"{_fmt_pp(u_b)} | {_fmt(ev1_a)} | {_fmt(ev3_a)} | "
                     f"{_fmt_pp(u_a)} | **{_fmt_pp(d_u)}** |")

    # Pooled comparison
    ev1_b_p = conf_before["1"]["__pooled__"][0]
    ev3_b_p = conf_before["≥3"]["__pooled__"][0]
    ev1_a_p = conf_after["1"]["__pooled__"][0]
    ev3_a_p = conf_after["≥3"]["__pooled__"][0]
    u_b_p = ev3_b_p - ev1_b_p
    u_a_p = ev3_a_p - ev1_a_p
    d_pooled = u_a_p - u_b_p

    n3_b = conf_before["≥3"]["__pooled__"][2]
    n3_a = conf_after["≥3"]["__pooled__"][2]
    n3_oos_a = conf_after["≥3"]["__oos__"][2]

    lines += [
        "",
        f"**Pooled training**: uplift before = {u_b_p*100:+.2f}pp (n[≥3]={n3_b}), "
        f"uplift after = {u_a_p*100:+.2f}pp (n[≥3]={n3_a}); "
        f"**Δ uplift = {d_pooled*100:+.2f}pp**, n[≥3] grew by {n3_a - n3_b}.",
        f"**FY2025 OOS n[≥3] after** = {n3_oos_a} (gate ≥ {_GATE_OOS_N_MIN_CONF}).",
        "",
        "### Verdict",
        "",
    ]

    # Decision
    if standalone_pass:
        lines.append("**Standalone PASS** — brk_hi_sideway clears the same gate "
                     "as long_high_continuation did NOT. Authorize detector "
                     "build + full rebench cycle.")
    elif d_pooled > 0 and n3_a > n3_b and n3_oos_a >= _GATE_OOS_N_MIN_CONF:
        lines.append("**Standalone FAIL but confluence-incremental POSITIVE** — "
                     "brk_hi_sideway adds value as a confluence input even if its "
                     "standalone EV is weak.  Authorize detector build + wire as "
                     "confluence input only (no standalone proposal row).  "
                     "Matches the rev_nhi UI-only salvage pattern.")
    elif d_pooled <= 0:
        lines.append(f"**Standalone FAIL AND confluence-incremental flat/negative** "
                     f"(Δ uplift = {d_pooled*100:+.2f}pp).  Adding brk_hi_sideway "
                     "to the confluence tally does not improve uplift — the new "
                     "fires either don't agree with other bullish signs or dilute "
                     "the existing 7-sign signal.  REJECT.")
    else:
        lines.append("**Standalone FAIL; confluence gain positive but FY2025 n[≥3] "
                     f"too thin** (got {n3_oos_a}, need ≥{_GATE_OOS_N_MIN_CONF}).  "
                     "Defer; revisit if universe expands.")

    return "\n".join(lines)


def _append_to_benchmark(md: str) -> None:
    existing = _BENCH_MD.read_text() if _BENCH_MD.exists() else ""
    if _SECTION_HEADER in existing:
        idx = existing.index(_SECTION_HEADER)
        rest = existing[idx + len(_SECTION_HEADER):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _BENCH_MD.write_text(existing.rstrip() + "\n" + md.lstrip("\n"))
    logger.info("Appended report to {}", _BENCH_MD)


def main() -> None:
    logger.info("Detecting brk_hi_sideway fires...")
    all_fires: list[_Fire] = []
    for fy_label, start_str, end_str, cluster_year in _FY_CONFIG:
        bench_start = datetime.date.fromisoformat(start_str)
        bench_end   = datetime.date.fromisoformat(end_str)
        codes = _stocks_for_fy(f"classified{cluster_year}")
        if not codes:
            continue
        logger.info("{}: detecting on {} stocks", fy_label, len(codes))
        for i, code in enumerate(codes):
            if i and i % 50 == 0:
                logger.info("  {}: {}/{} done, fires={}",
                            fy_label, i, len(codes), len(all_fires))
            all_fires.extend(_detect_for_stock(code, fy_label, bench_start, bench_end))
        logger.info("{}: cumulative fires={}", fy_label, len(all_fires))

    df_fires = pd.DataFrame([f.__dict__ for f in all_fires])
    if df_fires.empty:
        logger.error("No fires — aborting")
        return
    logger.info("Total fires across all FYs: {}", len(df_fires))

    logger.info("Running v2 confluence baseline (7 signs)...")
    existing = _load_existing_fires()
    conf_before = _confluence_table(existing, all_fires, include_new=False)

    logger.info("Running v2 confluence WITH brk_hi_sideway (8 signs)...")
    conf_after  = _confluence_table(existing, all_fires, include_new=True)

    report = _format_report(df_fires, conf_before, conf_after)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
