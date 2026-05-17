"""long_high_continuation_probe — does breaking out of an N-bar close-high carry signal?

Read-only diagnostic (Probe A1 per /sign-debate 2026-05-17).  Emulates
a candidate "long-term peak breakout (continuation)" detector for
N ∈ {60, 120, 250} bars without writing any production sign code.

Fire rule:
    close[T] > rolling_max(close, N)[T-1]   (close-based; ignores intraday)

For each fire, compute:
    score        = (close[T] − prior_N_max) / ATR(14)[T]   (ATR-units)
    r_h10        = (close[T+10] − open[T+1]) / open[T+1]   (two-bar fill)
    trend_dir    = next confirmed zigzag peak direction (+1 high / −1 low)
                   via _first_zigzag_peak, matching benchmark.md convention
    trend_mag    = |peak_price − entry_price| / entry_price

Universe: per-FY classified cluster representatives (same as
sign_benchmark_multiyear).  FYs: FY2018..FY2024 training + FY2025 OOS.

Pre-registered gate (must all hold for at least one N):
    pooled EV (training) ≥ +0.020
    FY2025 EV > 0
    no training FY with EV < 0
    DR ≥ 53% (secondary)
    overlap with rev_nhi same-bar fires ≤ 50%
    non-overlap subset still clears EV pooled ≥ +0.020 AND FY2025 EV > 0

If no N clears all five gates → REJECT (A); B-probe deferred forever
unless a different framing surfaces.

No DB writes; no detector code.  Output appended to
src/analysis/benchmark.md under `## Long-Term High Continuation Probe`.
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

from src.analysis.models import (
    SignBenchmarkEvent,
    SignBenchmarkRun,
    StockClusterMember,
    StockClusterRun,
)
from src.analysis.sign_benchmark import _first_zigzag_peak
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.simulator.cache import DataCache

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Long-Term High Continuation Probe"

# FY config — mirrors sign_benchmark_multiyear.FY_CONFIG.  (label, start, end, cluster_year)
_FY_CONFIG: list[tuple[str, str, str, str]] = [
    ("FY2018", "2018-04-01", "2019-03-31", "2017"),
    ("FY2019", "2019-04-01", "2020-03-31", "2018"),
    ("FY2020", "2020-04-01", "2021-03-31", "2019"),
    ("FY2021", "2021-04-01", "2022-03-31", "2020"),
    ("FY2022", "2022-04-01", "2023-03-31", "2021"),
    ("FY2023", "2023-04-01", "2024-03-31", "2022"),
    ("FY2024", "2024-04-01", "2025-03-31", "2023"),
    ("FY2025", "2025-04-01", "2026-03-31", "2024"),
]
_FY_TRAINING = [c[0] for c in _FY_CONFIG[:-1]]
_FY_OOS = "FY2025"

_NS = [60, 120, 250]
_H_FORWARD = 10
_TREND_CAP = 30
_ZZ_SIZE = 5
_ZZ_MID_SIZE = 2

# Critic/Judge-mandated gate (2026-05-17)
_GATE_POOLED_EV   = 0.020   # EV-primary
_GATE_DR_MIN      = 0.53
_GATE_OVERLAP_MAX = 0.50    # rev_nhi co-fire rate cap


# ── 1. Universe per FY ────────────────────────────────────────────────


def _stocks_for_fy(cluster_set: str) -> list[str]:
    with get_session() as s:
        run = s.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == cluster_set)
        ).scalar_one_or_none()
        if run is None:
            return []
        return list(s.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())


# ── 2. Detect fires + measure outcomes ────────────────────────────────


@dataclass
class _Fire:
    stock:        str
    fire_date:    datetime.date
    n_window:     int
    fy:           str
    score:        float | None     # (close − prior_max) / ATR(14)
    r_h10:        float | None
    trend_dir:    int | None       # +1 / -1
    trend_mag:    float | None


def _atr_14(highs: pd.Series, lows: pd.Series, closes: pd.Series) -> pd.Series:
    prev_c = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_c).abs(),
        (lows  - prev_c).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=7).mean()


def _measure_stock(stock: str, fy_label: str, bench_start: datetime.date,
                   bench_end: datetime.date) -> list[_Fire]:
    """Find candidate fires for one stock in one FY across all N ∈ _NS."""
    # Load daily bars with enough history for N=250 lookback + 30 lookahead
    Ohlcv = OHLCV_MODEL_MAP["1d"]
    lookback_start = bench_start - datetime.timedelta(days=400)
    lookahead_end  = bench_end   + datetime.timedelta(days=60)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv.ts, Ohlcv.open_price, Ohlcv.high_price,
                   Ohlcv.low_price, Ohlcv.close_price)
            .where(
                Ohlcv.stock_code == stock,
                Ohlcv.ts >= datetime.datetime.combine(lookback_start,
                    datetime.time.min, tzinfo=datetime.timezone.utc),
                Ohlcv.ts <= datetime.datetime.combine(lookahead_end,
                    datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            .order_by(Ohlcv.ts)
        ).all()
    if len(rows) < max(_NS) + 30:
        return []

    df = pd.DataFrame({
        "ts":    [r.ts for r in rows],
        "open":  [float(r.open_price)  for r in rows],
        "high":  [float(r.high_price)  for r in rows],
        "low":   [float(r.low_price)   for r in rows],
        "close": [float(r.close_price) for r in rows],
    })
    df["date"] = df["ts"].apply(lambda t: t.date() if hasattr(t, "date") else t)
    df = df.drop_duplicates(subset=["date"]).reset_index(drop=True)

    atr = _atr_14(df["high"], df["low"], df["close"])

    # Build a barlike list for _first_zigzag_peak
    @dataclass
    class _Bar:
        dt:    datetime.datetime
        open:  float
        high:  float
        low:   float
        close: float
    bars_1d = [
        _Bar(dt=row.ts if hasattr(row.ts, "tzinfo") else
                 datetime.datetime.combine(row.ts, datetime.time.min,
                                           tzinfo=datetime.timezone.utc),
             open=row["open"], high=row["high"],
             low=row["low"],   close=row["close"])
        for _, row in df.iterrows()
    ]

    fires: list[_Fire] = []
    closes = df["close"].to_numpy()
    opens  = df["open"].to_numpy()
    dates  = df["date"].to_numpy()
    n_bars = len(df)

    for n in _NS:
        # rolling close max over [T-N, T-1] inclusive (prior, not including T)
        for i in range(n, n_bars):
            d = dates[i]
            if d < bench_start or d > bench_end:
                continue
            prior_max = closes[i - n : i].max()
            if closes[i] <= prior_max:
                continue
            sc_atr = atr.iloc[i]
            score = ((closes[i] - prior_max) / sc_atr) if sc_atr and sc_atr > 0 else None

            r10: float | None = None
            if i + _H_FORWARD < n_bars and i + 1 < n_bars:
                r10 = (closes[i + _H_FORWARD] - opens[i + 1]) / opens[i + 1]

            tdir, _tbars, tmag = _first_zigzag_peak(
                bars_1d[i].dt, bars_1d, _TREND_CAP, _ZZ_SIZE, _ZZ_MID_SIZE,
            )
            fires.append(_Fire(
                stock=stock, fire_date=d, n_window=n, fy=fy_label,
                score=score, r_h10=r10, trend_dir=tdir, trend_mag=tmag,
            ))
    return fires


# ── 3. rev_nhi same-bar overlap ───────────────────────────────────────


def _load_rev_nhi_fires() -> set[tuple[str, datetime.date]]:
    """Return {(stock, fire_date)} for rev_nhi across run_ids >= 47."""
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkEvent.stock_code, SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type == "rev_nhi",
                   SignBenchmarkRun.id >= 47)
        ).all()
    pairs: set[tuple[str, datetime.date]] = set()
    for stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        pairs.add((stock, d))
    return pairs


# ── 4. Aggregate metrics ──────────────────────────────────────────────


def _ev(sub: pd.DataFrame) -> tuple[float, float, int]:
    """Return (EV, DR, n) using trend_direction × trend_magnitude."""
    n = len(sub)
    if n == 0:
        return (float("nan"), float("nan"), 0)
    with_dir = sub.dropna(subset=["trend_dir"])
    if with_dir.empty:
        return (float("nan"), float("nan"), n)
    dr = (with_dir["trend_dir"] == 1).sum() / len(with_dir)
    flw = with_dir[with_dir["trend_dir"] == 1]["trend_mag"].dropna()
    rev = with_dir[with_dir["trend_dir"] == -1]["trend_mag"].dropna()
    if flw.empty or rev.empty:
        return (float("nan"), dr, n)
    ev = dr * float(flw.mean()) - (1 - dr) * float(rev.mean())
    return (ev, dr, n)


# ── 5. Report ─────────────────────────────────────────────────────────


def _format_report(df: pd.DataFrame, overlap_pct: dict[int, float]) -> str:
    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Read-only diagnostic — "
        f"does a close-based N-bar high carry continuation signal for "
        f"N ∈ {_NS}?",
        "",
        "**Pre-registered gate** (per /sign-debate 2026-05-17, judge-mandated):",
        f"  - pooled EV (training FYs {_FY_TRAINING[0]}..{_FY_TRAINING[-1]}) ≥ {_GATE_POOLED_EV:+.3f}",
        f"  - FY2025 OOS EV > 0",
        f"  - all training FYs EV ≥ 0",
        f"  - DR ≥ {_GATE_DR_MIN*100:.0f}% (secondary)",
        f"  - rev_nhi same-bar overlap ≤ {_GATE_OVERLAP_MAX*100:.0f}%",
        f"  - non-overlap subset still clears pooled EV ≥ {_GATE_POOLED_EV:+.3f} AND FY2025 EV > 0",
        "",
        f"Metrics use `trend_direction` (next confirmed zigzag, ZZ size={_ZZ_SIZE}, "
        f"mid={_ZZ_MID_SIZE}, cap={_TREND_CAP} bars) — matches benchmark.md convention. "
        f"Forward return at H={_H_FORWARD} (two-bar fill) is reported as secondary.",
        "",
        "### Per N — Pooled (training) + per-FY EV table",
        "",
        "| N | n_total | n_train | overlap rev_nhi | pooled EV | DR | mean r_h10 | "
        + " | ".join([f"EV {fy}" for fy in _FY_TRAINING + [_FY_OOS]])
        + " | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|"
        + "---:|" * (len(_FY_TRAINING) + 1) + "---|",
    ]
    for n in _NS:
        sub = df[df["n_window"] == n]
        sub_train = sub[sub["fy"].isin(_FY_TRAINING)]
        sub_oos   = sub[sub["fy"] == _FY_OOS]
        ev_pool, dr_pool, n_pool = _ev(sub_train)
        per_fy: dict[str, tuple[float, float, int]] = {}
        for fy in _FY_TRAINING:
            per_fy[fy] = _ev(sub_train[sub_train["fy"] == fy])
        per_fy[_FY_OOS] = _ev(sub_oos)

        mean_r10 = sub["r_h10"].dropna().mean() if not sub.empty else float("nan")

        # Gate evaluation
        ok = True
        notes: list[str] = []
        if math.isnan(ev_pool) or ev_pool < _GATE_POOLED_EV:
            ok = False
            notes.append(f"pooled EV {ev_pool:+.4f} < {_GATE_POOLED_EV:+.4f}")
        if math.isnan(per_fy[_FY_OOS][0]) or per_fy[_FY_OOS][0] <= 0:
            ok = False
            notes.append(f"FY2025 EV {per_fy[_FY_OOS][0]:+.4f} ≤ 0")
        neg_fy = [fy for fy in _FY_TRAINING
                  if not math.isnan(per_fy[fy][0]) and per_fy[fy][0] < 0]
        if neg_fy:
            ok = False
            notes.append(f"negative-EV FYs: {','.join(neg_fy)}")
        if math.isnan(dr_pool) or dr_pool < _GATE_DR_MIN:
            ok = False
            notes.append(f"DR {dr_pool*100:.1f}% < {_GATE_DR_MIN*100:.0f}%")
        if overlap_pct.get(n, 1.0) > _GATE_OVERLAP_MAX:
            ok = False
            notes.append(f"overlap {overlap_pct[n]*100:.0f}% > {_GATE_OVERLAP_MAX*100:.0f}%")

        cells = [f"| {n} | {len(sub)} | {n_pool} | {overlap_pct.get(n, float('nan'))*100:.1f}% | "
                 f"{ev_pool:+.4f} | {dr_pool*100:.1f}% | {mean_r10*100:+.2f}pp"]
        for fy in _FY_TRAINING + [_FY_OOS]:
            e, _, nn = per_fy[fy]
            cells.append(f" | {e:+.4f} (n={nn})" if not math.isnan(e) else " | —")
        cells.append(f" | **{'PASS' if ok else 'FAIL'}** |")
        lines.append("".join(cells))
        if notes:
            lines.append(f"|  |  |  |  |  |  |  |"
                         + " |" * (len(_FY_TRAINING) + 1)
                         + f" notes: {'; '.join(notes)} |")
    return "\n".join(lines)


def _non_overlap_metrics(df: pd.DataFrame,
                         rev_nhi_pairs: set[tuple[str, datetime.date]]) -> str:
    lines = [
        "",
        "### Non-overlap subset (fires NOT same-bar with rev_nhi)",
        "",
        "| N | n_train | n_oos | pooled EV (train) | FY2025 EV | DR | Non-overlap gate |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for n in _NS:
        sub = df[df["n_window"] == n].copy()
        sub["overlap"] = sub.apply(
            lambda r: (r["stock"], r["fire_date"]) in rev_nhi_pairs, axis=1,
        )
        no = sub[~sub["overlap"]]
        no_train = no[no["fy"].isin(_FY_TRAINING)]
        no_oos   = no[no["fy"] == _FY_OOS]
        ev_t, dr_t, nt = _ev(no_train)
        ev_o, _, no_n = _ev(no_oos)
        ok = (not math.isnan(ev_t) and ev_t >= _GATE_POOLED_EV
              and not math.isnan(ev_o) and ev_o > 0)
        lines.append(
            f"| {n} | {nt} | {no_n} | {ev_t:+.4f} | {ev_o:+.4f} | "
            f"{dr_t*100:.1f}% | **{'PASS' if ok else 'FAIL'}** |"
        )
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


# ── 6. Main ───────────────────────────────────────────────────────────


def main() -> None:
    rev_nhi_pairs = _load_rev_nhi_fires()
    logger.info("Loaded {} rev_nhi fire (stock,date) pairs", len(rev_nhi_pairs))

    all_fires: list[_Fire] = []
    for fy_label, start_str, end_str, cluster_year in _FY_CONFIG:
        cluster_set = f"classified{cluster_year}"
        bench_start = datetime.date.fromisoformat(start_str)
        bench_end   = datetime.date.fromisoformat(end_str)
        codes = _stocks_for_fy(cluster_set)
        if not codes:
            logger.warning("No cluster members for {} ({}) — skipping", fy_label, cluster_set)
            continue
        logger.info("{}: probing {} stocks", fy_label, len(codes))
        for i, code in enumerate(codes):
            if i and i % 25 == 0:
                logger.info("  {}: {}/{} stocks done, fires so far={}",
                            fy_label, i, len(codes), len(all_fires))
            all_fires.extend(_measure_stock(code, fy_label, bench_start, bench_end))
        logger.info("{}: cumulative fires={}", fy_label, len(all_fires))

    df = pd.DataFrame([f.__dict__ for f in all_fires])
    if df.empty:
        logger.error("No fires found — aborting")
        return

    # Overlap %
    overlap_pct: dict[int, float] = {}
    for n in _NS:
        sub = df[df["n_window"] == n]
        if sub.empty:
            overlap_pct[n] = float("nan")
            continue
        hits = sub.apply(lambda r: (r["stock"], r["fire_date"]) in rev_nhi_pairs,
                         axis=1).sum()
        overlap_pct[n] = float(hits) / len(sub)

    report = _format_report(df, overlap_pct)
    report += _non_overlap_metrics(df, rev_nhi_pairs)

    # Verdict
    pass_ns: list[int] = []
    for n in _NS:
        sub_train = df[(df["n_window"] == n) & (df["fy"].isin(_FY_TRAINING))]
        sub_oos   = df[(df["n_window"] == n) & (df["fy"] == _FY_OOS)]
        ev_pool, dr_pool, _ = _ev(sub_train)
        ev_oos, _, _        = _ev(sub_oos)
        ok = (not math.isnan(ev_pool) and ev_pool >= _GATE_POOLED_EV
              and not math.isnan(ev_oos) and ev_oos > 0
              and not math.isnan(dr_pool) and dr_pool >= _GATE_DR_MIN
              and overlap_pct.get(n, 1.0) <= _GATE_OVERLAP_MAX)
        if ok:
            all_neg = any(
                not math.isnan(_ev(sub_train[sub_train["fy"] == fy])[0])
                and _ev(sub_train[sub_train["fy"] == fy])[0] < 0
                for fy in _FY_TRAINING
            )
            if not all_neg:
                pass_ns.append(n)

    report += "\n\n### Verdict\n\n"
    if pass_ns:
        report += (f"**At least one N cleared the gate** (N={pass_ns}).  "
                   "Recommend follow-up debate to authorize a real detector "
                   "+ rebench.  Probe B1 (sideways breakout) can now be spec'd "
                   "using the surviving N as the sideways-window floor.")
    else:
        report += ("**No N cleared the gate.**  Q1 (long-term peak breakout "
                   "as a new sign) is REJECTED for this iteration.  Probe B1 "
                   "(sideways breakout) deferred — the parent hypothesis "
                   "(long-window high carries continuation signal) does not "
                   "hold on this universe / framing.\n\n"
                   "Per /sign-debate Path E: log gap in docs/followups.md "
                   "and close cycle.")
    report += "\n"

    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
