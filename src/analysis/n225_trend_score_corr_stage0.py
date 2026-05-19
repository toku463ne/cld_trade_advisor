"""n225_trend_score_corr_stage0 — Operator follow-up after Stage 1A REJECT.

Hypothesis (2026-05-19): per-stock trend_score didn't survive strategy
gates because the gates already select for the same trend qualities.
**N225 trend_score** is NEW information — it's not in any individual
stock's Kumo/ADX/SMA, and the existing pipeline doesn't filter for
"index regime is bullish/bearish".

Per CLAUDE.md trading philosophy:
  - High-corr stocks ARE index proxies → entries should track N225's
    own trend context.
  - Low-corr stocks carry independent alpha → score-agnostic to N225.

This is Stage 0 (measurement only).  Per-sign EV table stratified by
(stock corr regime at fire) × (N225 trend_score tercile at fire).

Hypothesis (pre-registered before run):
  H1: For high-corr cohort, sign DR shows monotone response to N225
      score tercile (low → high).  Effect-size threshold ≥ 5pp DR per
      step.
  H2: For low-corr cohort, sign DR is flat across N225 score terciles
      (within ±3pp).
  H3: ≥ 1 sign passes both H1 and H2 at n ≥ 30 per cell.

If H3 holds, Stage 1 = sizing tilt or candidate filter on
high-corr long entries when N225 score < some threshold.
If H3 fails, document and stop.

Output: docs/analysis/n225_trend_score_corr_stage0.md

Run:
    uv run --env-file devenv python -m src.analysis.n225_trend_score_corr_stage0
"""
from __future__ import annotations

import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis._trend_score import compute_trend_score
from src.analysis.exit_benchmark import _load_rep_codes
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.simulator.cache import DataCache

_UNIVERSE_SET = "classified2024"
_GRAN         = "1d"
_SPAN_START   = datetime.date(2017, 4, 1)
_SPAN_END     = datetime.date(2026, 4, 1)

_CORR_WINDOW  = 20      # matches confluence_strategy_backtest._CORR_WINDOW
_HIGH_CORR    = 0.6     # matches _HIGH_CORR_THRESH
_LOW_CORR     = 0.3     # matches _LOW_CORR_THRESH

_HOLDOUT_FYS = {"FY2024", "FY2025"}
_MIN_N_CELL  = 30       # pre-registered minimum n per cell

_DOC_PATH = Path("docs/analysis/n225_trend_score_corr_stage0.md")


def _fy_label(d: datetime.date) -> str:
    return f"FY{d.year}" if d.month >= 4 else f"FY{d.year - 1}"


def _compute_n225_trend_score() -> dict[datetime.date, float]:
    span_s = datetime.datetime.combine(_SPAN_START, datetime.time.min, tzinfo=datetime.timezone.utc)
    span_e = datetime.datetime.combine(_SPAN_END,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        c = DataCache("^N225", _GRAN)
        c.load(s, span_s, span_e)
    if not c.bars:
        raise RuntimeError("^N225 cache empty")
    ts = compute_trend_score(c)
    logger.info("^N225 trend_score: {} dates with score "
                "(range {:.1f} - {:.1f})",
                len(ts),
                min(ts.values()), max(ts.values()))
    return ts


def _build_corr_map_for_stock(cache: DataCache, n225: DataCache
                              ) -> dict[datetime.date, str]:
    n225_dates = {b.dt.date() for b in n225.bars}
    stock_close = {}
    for b in cache.bars:
        d = b.dt.date()
        if d in n225_dates:
            stock_close[d] = b.close
    n225_close = {b.dt.date(): b.close for b in n225.bars}
    common = sorted(set(stock_close) & set(n225_close))
    if len(common) < _CORR_WINDOW + 2:
        return {}
    s = pd.Series([stock_close[d] for d in common], index=common).pct_change()
    n = pd.Series([n225_close[d] for d in common], index=common).pct_change()
    corr = s.rolling(_CORR_WINDOW, min_periods=_CORR_WINDOW // 2).corr(n)
    out: dict[datetime.date, str] = {}
    for d, v in corr.items():
        if pd.isna(v):
            continue
        a = abs(v)
        if a >= _HIGH_CORR:
            out[d] = "high"
        elif a <= _LOW_CORR:
            out[d] = "low"
        else:
            out[d] = "mid"
    return out


def _build_all_corr_maps(universe: list[str]
                        ) -> tuple[dict[str, dict[datetime.date, str]], DataCache]:
    span_s = datetime.datetime.combine(_SPAN_START, datetime.time.min, tzinfo=datetime.timezone.utc)
    span_e = datetime.datetime.combine(_SPAN_END,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        n225 = DataCache("^N225", _GRAN)
        n225.load(s, span_s, span_e)
        out: dict[str, dict[datetime.date, str]] = {}
        for i, code in enumerate(universe):
            c = DataCache(code, _GRAN)
            try:
                c.load(s, span_s, span_e)
            except Exception as exc:
                logger.warning("  {}: load failed — {}", code, exc)
                continue
            if not c.bars:
                continue
            out[code] = _build_corr_map_for_stock(c, n225)
            if (i + 1) % 25 == 0:
                logger.info("  corr_map: {}/{} stocks", i + 1, len(universe))
    return out, n225


def _load_events() -> list[tuple]:
    """Return list of (sign, stock, fired_at, direction, magnitude)."""
    with get_session() as s:
        runs = s.execute(select(SignBenchmarkRun)).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    rows: list[tuple] = []
    ids = list(run_map)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append((run_map[e.run_id], e.stock_code, e.fired_at,
                         int(e.trend_direction), float(e.trend_magnitude)))
    logger.info("Loaded {} events with outcome", len(rows))
    return rows


@dataclass
class _Tagged:
    sign:        str
    fy:          str
    stock:       str
    date:        datetime.date
    direction:   int
    magnitude:   float
    corr_mode:   str       # "high" / "mid" / "low"
    n225_score:  float

    @property
    def signed_mag(self) -> float:
        return self.direction * self.magnitude


def _tercile_label(score: float, cuts: tuple[float, float]) -> str:
    if score < cuts[0]:
        return "lo"
    if score < cuts[1]:
        return "mid"
    return "hi"


def _tag_events(events, corr_maps, n225_scores) -> list[_Tagged]:
    out: list[_Tagged] = []
    n_no_corr = 0
    n_no_score = 0
    for sign, code, fired_at, dirn, mag in events:
        d = fired_at.date()
        cm = corr_maps.get(code, {}).get(d)
        if cm is None:
            n_no_corr += 1
            continue
        ts = n225_scores.get(d)
        if ts is None:
            n_no_score += 1
            continue
        out.append(_Tagged(sign, _fy_label(d), code, d, dirn, mag, cm, ts))
    logger.info("Tagged {} events (skipped {} no-corr, {} no-score)",
                len(out), n_no_corr, n_no_score)
    return out


def _cell_stats(rows: list[_Tagged]) -> tuple[int, float | None, float | None]:
    n = len(rows)
    if n == 0:
        return 0, None, None
    n_follow = sum(1 for r in rows if r.direction == +1)
    return n, n_follow / n, statistics.mean(r.signed_mag for r in rows)


def _format_cell(s: tuple[int, float | None, float | None]) -> str:
    n, dr, sm = s
    if n == 0:
        return "  —  "
    return f"{n}/{dr*100:.0f}%/{sm*100:+.1f}%"


def _per_sign_table(rows: list[_Tagged], corr_modes, score_buckets) -> list[str]:
    lines = []
    by_sign: dict[str, list[_Tagged]] = defaultdict(list)
    for r in rows:
        by_sign[r.sign].append(r)
    for sign in sorted(by_sign):
        sign_rows = by_sign[sign]
        lines.append(f"#### {sign}")
        lines.append("")
        lines.append("Format: n / DR / signed_mean.  *italic* = n < "
                     f"{_MIN_N_CELL}.")
        lines.append("")
        header = "| corr \\\\ N225 score | " + " | ".join(score_buckets) + " | row total |"
        sep    = "|---|" + ":---:|" * (len(score_buckets) + 1)
        lines.append(header)
        lines.append(sep)
        for cm in corr_modes:
            row_cells = []
            row_total_rows: list[_Tagged] = []
            for sb in score_buckets:
                cell = [r for r in sign_rows if r.corr_mode == cm and r.n225_bucket == sb]
                row_total_rows.extend(cell)
                s = _cell_stats(cell)
                cell_s = _format_cell(s)
                if s[0] > 0 and s[0] < _MIN_N_CELL:
                    cell_s = f"*{cell_s}*"
                row_cells.append(cell_s)
            row_total = _cell_stats(row_total_rows)
            row_cells.append(_format_cell(row_total))
            lines.append(f"| **{cm}** | " + " | ".join(row_cells) + " |")
        # column totals
        col_cells = []
        for sb in score_buckets:
            col_rows = [r for r in sign_rows if r.n225_bucket == sb]
            col_cells.append(_format_cell(_cell_stats(col_rows)))
        col_cells.append(_format_cell(_cell_stats(sign_rows)))
        lines.append(f"| **all** | " + " | ".join(col_cells) + " |")
        lines.append("")
    return lines


def _hypothesis_check(rows: list[_Tagged], cuts) -> list[str]:
    """Per-sign check: H1 monotone in high-corr, H2 flat in low-corr."""
    lines = [
        "## Hypothesis check (pre-registered)",
        "",
        "**H1**: For HIGH-corr cohort, sign DR shows monotone response "
        "to N225 score tercile (≥5pp per step).",
        "",
        "**H2**: For LOW-corr cohort, sign DR is flat across N225 "
        "terciles (max - min ≤ 3pp).",
        "",
        "**H3**: ≥1 sign passes both at n ≥ "
        f"{_MIN_N_CELL} per cell.",
        "",
        "| sign | high cohort: lo→mid→hi DR | H1 mono ≥5pp | low cohort range | H2 flat | n_cells ≥ 30 |",
        "|---|---|:---:|---|:---:|:---:|",
    ]
    by_sign: dict[str, list[_Tagged]] = defaultdict(list)
    for r in rows:
        by_sign[r.sign].append(r)
    n_pass_h3 = 0
    for sign in sorted(by_sign):
        sr = by_sign[sign]
        def _cell(cm: str, sb: str):
            return [r for r in sr if r.corr_mode == cm and r.n225_bucket == sb]
        high_lo  = _cell("high", "lo")
        high_mid = _cell("high", "mid")
        high_hi  = _cell("high", "hi")
        low_lo   = _cell("low",  "lo")
        low_mid  = _cell("low",  "mid")
        low_hi   = _cell("low",  "hi")

        def _dr(cell): return (sum(1 for r in cell if r.direction == +1) / len(cell)) if cell else None
        hL, hM, hH = _dr(high_lo), _dr(high_mid), _dr(high_hi)
        lL, lM, lH = _dr(low_lo),  _dr(low_mid),  _dr(low_hi)

        h_drs = [v for v in [hL, hM, hH] if v is not None]
        h1_pass = "—"
        if hL is not None and hM is not None and hH is not None:
            step1 = hM - hL
            step2 = hH - hM
            h1_pass = "✓" if (step1 >= 0.05 and step2 >= 0.05) else "·"
        h_str = " → ".join(["—" if v is None else f"{v*100:.0f}%"
                            for v in (hL, hM, hH)])

        l_drs = [v for v in [lL, lM, lH] if v is not None]
        l_range = (max(l_drs) - min(l_drs)) if len(l_drs) >= 2 else None
        h2_pass = "—" if l_range is None else ("✓" if l_range <= 0.03 else "·")
        l_str = "—" if l_range is None else f"max-min {l_range*100:.1f}pp"

        all_cells_n = [
            len(high_lo), len(high_mid), len(high_hi),
            len(low_lo),  len(low_mid),  len(low_hi),
        ]
        n_ok = sum(1 for n in all_cells_n if n >= _MIN_N_CELL)
        h3_mark = "✓✓" if h1_pass == "✓" and h2_pass == "✓" and n_ok >= 4 else " "
        if h3_mark == "✓✓":
            n_pass_h3 += 1

        lines.append(f"| {sign} | {h_str} | {h1_pass} | {l_str} | "
                     f"{h2_pass} | {n_ok}/6 {h3_mark} |")
    lines.append("")
    if n_pass_h3 > 0:
        lines.append(f"**H3 result**: {n_pass_h3} sign(s) pass — Stage 1 "
                     "(sizing tilt or candidate filter) is justified.")
    else:
        lines.append("**H3 result**: NO sign passes H1+H2+n≥30. "
                     "Stage 1 NOT justified by Stage 0 finding.")
    return lines


def _holdout_replication(rows: list[_Tagged], cuts) -> list[str]:
    lines = ["## FY2024+FY2025 holdout replication", "",
             "Repeats the per-sign hypothesis check on holdout-only events.",
             ""]
    hold_rows = [r for r in rows if r.fy in _HOLDOUT_FYS]
    if not hold_rows:
        lines.append("(no holdout rows)")
        return lines
    lines.extend(_hypothesis_check(hold_rows, cuts))
    return lines


def _format_report(events, corr_maps, n225_scores) -> str:
    tagged = _tag_events(events, corr_maps, n225_scores)
    score_values = sorted(n225_scores.values())
    # Use cross-section terciles of N225 score (33rd/66th percentile)
    n = len(score_values)
    cut_lo = score_values[n // 3]
    cut_hi = score_values[2 * n // 3]
    for r in tagged:
        r.n225_bucket = (
            "lo"  if r.n225_score < cut_lo
            else "mid" if r.n225_score < cut_hi
            else "hi"
        )

    corr_modes    = ("high", "mid", "low")
    score_buckets = ("lo", "mid", "hi")

    lines = [
        "# N225 trend_score × stock corr regime — Stage 0",
        "",
        f"Probe run: {datetime.date.today()}.  Stratified per-sign EV "
        "table testing whether N225's own trend regime matters more for "
        "high-corr stocks (index proxies) than low-corr stocks "
        "(independent alpha).",
        "",
        "## Setup",
        "",
        f"- Universe: {_UNIVERSE_SET} ({len(corr_maps)} stocks loaded)",
        "- N225 trend_score: same 5-feature 250-bar pct-rank "
        "(`src.analysis._trend_score`) applied to ^N225 OHLC",
        f"- N225 score terciles (cross-section over {n} dates): "
        f"lo < {cut_lo:.1f} ≤ mid < {cut_hi:.1f} ≤ hi",
        f"- Corr regime: |20-bar rolling corr stock vs ^N225| → "
        f"high (≥{_HIGH_CORR}) / mid / low (≤{_LOW_CORR})",
        f"- Events tagged: {len(tagged)} (skipped: no-corr or no-score)",
        f"- Min n per cell to count: {_MIN_N_CELL}",
        "",
        f"- FYs: pooled across all (FY{_SPAN_START.year}–FY{_SPAN_END.year-1}); "
        f"holdout: {', '.join(sorted(_HOLDOUT_FYS))}",
        "",
        "## Per-sign 2×3 EV table — POOLED",
        "",
    ]
    lines.extend(_per_sign_table(tagged, corr_modes, score_buckets))
    lines.append("---")
    lines.append("")
    lines.extend(_hypothesis_check(tagged, (cut_lo, cut_hi)))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_holdout_replication(tagged, (cut_lo, cut_hi)))
    return "\n".join(lines)


def main() -> None:
    universe = _load_rep_codes(_UNIVERSE_SET)
    logger.info("Universe: {} stocks", len(universe))
    n225_scores = _compute_n225_trend_score()
    corr_maps, _ = _build_all_corr_maps(universe)
    events = _load_events()
    report = _format_report(events, corr_maps, n225_scores)
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(report + "\n")
    logger.info("Wrote {}", _DOC_PATH)
    # Print summary section (everything up to "## Per-sign 2×3 EV table")
    head_end = report.find("## Per-sign 2×3 EV table")
    if head_end > 0:
        print(report[:head_end])
    print(report[report.find("## Hypothesis check"):])


if __name__ == "__main__":
    main()
