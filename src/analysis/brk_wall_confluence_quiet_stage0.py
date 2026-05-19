"""brk_wall_confluence_quiet_stage0 — Stage 0 for "brk_wall as confluence fallback".

Operator hypothesis (2026-05-19): brk_wall is structurally inert in regime_sign
and dilutes confluence if added.  But the 2026-05-19 marginal-helper re-eval
surfaced +4.93pp tail-hedge lift on confluence's worst-quintile days — so
brk_wall might add value SPECIFICALLY when confluence is NOT firing.

This Stage 0 measures brk_wall's per-fire EV stratified by the live
confluence_count at the fire's (stock, date).  Cheap measurement-only
probe — no strategy A/B yet.

Pre-registered gate (locked BEFORE run):
  - PASS: brk_wall DR on confluence_count=0 days ≥ pool DR + 3pp
          AND replicates on FY2024+FY2025 holdout (≥ pool holdout + 3pp)
  - PARTIAL: meets pool but not holdout
  - FAIL: anything else → do not proceed to Stage 1

Buckets: count=0 / count=1 / count=2 / count≥3 (production gate).

Output: docs/analysis/brk_wall_confluence_quiet_stage0.md
"""
from __future__ import annotations

import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.confluence_ichimoku_ab import _EXPANDED_SIGNS, _load_fires
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.strategy.confluence_sign import _VALID_BARS

_DOC_PATH = Path("docs/analysis/brk_wall_confluence_quiet_stage0.md")

_HOLDOUT_FYS = {"FY2024", "FY2025"}
_BUCKETS = (0, 1, 2, 3)  # bucket label = count; 3 means "≥3"
_PROD_N_GATE = 3


def _fy_label(d: datetime.date) -> str:
    return f"FY{d.year}" if d.month >= 4 else f"FY{d.year - 1}"


def _build_confluence_count_map(
    fires_by_stock: dict[str, list[tuple[str, datetime.date]]]
) -> dict[str, dict[datetime.date, int]]:
    """Return {stock: {date: confluence_count}} using same valid_bars logic
    as ConfluenceSignStrategy / confluence_ichimoku_ab.

    For each (stock, date), counts the DISTINCT bullish signs whose fire
    window covers that date (valid_bars-many trading days after the fire).
    """
    out: dict[str, dict[datetime.date, int]] = {}
    for stock, fires in fires_by_stock.items():
        # Group fires by sign so we can walk forward and accumulate
        # "still-active" windows per date.
        # We use calendar-day delta (≤ valid_bars * 2 calendar days) as a
        # cheap approximation since SignBenchmarkEvents are per-fire and we
        # don't have the stock's trading-day calendar handy.  Production
        # uses trading-bar steps; for Stage 0 measurement this is close
        # enough — the difference only matters on weekend/holiday boundaries.
        # To stay faithful, we'll build the trading-day set from the fires
        # themselves (union of dates seen across all signs) and walk it.
        dates_seen = sorted({d for _, d in fires})
        if not dates_seen:
            continue
        date_to_idx = {d: i for i, d in enumerate(dates_seen)}
        per_date_signs: dict[int, set[str]] = defaultdict(set)
        for sign, fd in fires:
            if fd not in date_to_idx:
                continue
            vb = _VALID_BARS.get(sign, 5)
            fi = date_to_idx[fd]
            for j in range(fi, min(fi + vb + 1, len(dates_seen))):
                per_date_signs[j].add(sign)
        out[stock] = {dates_seen[i]: len(per_date_signs[i])
                      for i in range(len(dates_seen))}
    return out


def _load_brk_wall_events() -> list[tuple[str, datetime.datetime, int, float]]:
    """Return list of (stock, fired_at, direction, magnitude) for brk_wall."""
    with get_session() as s:
        run_ids = s.execute(
            select(SignBenchmarkRun.id).where(SignBenchmarkRun.sign_type == "brk_wall")
        ).scalars().all()
    if not run_ids:
        logger.warning("No brk_wall SignBenchmarkRun rows found")
        return []
    rows: list[tuple] = []
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append((e.stock_code, e.fired_at,
                         int(e.trend_direction), float(e.trend_magnitude)))
    logger.info("Loaded {} brk_wall events with outcome", len(rows))
    return rows


@dataclass
class _Row:
    stock: str
    date:  datetime.date
    fy:    str
    direction: int
    magnitude: float
    conf_count: int

    @property
    def signed_mag(self) -> float:
        return self.direction * self.magnitude


def _cell_stats(rows: list[_Row]) -> tuple[int, float | None, float | None, float | None]:
    """Return (n, DR, signed_mean, follow_mean)."""
    n = len(rows)
    if n == 0:
        return 0, None, None, None
    n_follow = sum(1 for r in rows if r.direction == +1)
    follow_rs = [r.magnitude for r in rows if r.direction == +1]
    return (
        n,
        n_follow / n,
        statistics.mean(r.signed_mag for r in rows),
        statistics.mean(follow_rs) if follow_rs else None,
    )


def _fmt(n, dr, sm, fm) -> str:
    if n == 0:
        return "  —  "
    return (f"{n} / {dr*100:.1f}% / "
            f"{sm*100:+.2f}% / "
            f"{(fm*100 if fm is not None else 0):+.2f}%")


def _bucket_for(c: int) -> int:
    return min(c, _PROD_N_GATE)


def _bucket_table(rows: list[_Row], heading: str) -> list[str]:
    lines = [
        f"### {heading}",
        "",
        f"Pooled brk_wall fires (n={len(rows)}).  Buckets are confluence_count "
        "on the brk_wall fire's stock × date.",
        "",
        "| confluence_count | n / DR / signed_mean / follow_mag |",
        "|---|---|",
    ]
    pool = _cell_stats(rows)
    lines.append(f"| **all (pool)** | {_fmt(*pool)} |")
    for b in _BUCKETS:
        sub = [r for r in rows if _bucket_for(r.conf_count) == b]
        label = f"= {b}" if b < _PROD_N_GATE else f"≥ {b}"
        lines.append(f"| count {label} | {_fmt(*_cell_stats(sub))} |")
    # also break out 1, 2, 3+ explicitly for "quiet vs anything firing"
    quiet = [r for r in rows if r.conf_count == 0]
    any_  = [r for r in rows if r.conf_count >= 1]
    lines.append(f"| count = 0 ('quiet') | {_fmt(*_cell_stats(quiet))} |")
    lines.append(f"| count ≥ 1 ('any other sign live') | {_fmt(*_cell_stats(any_))} |")
    return lines + [""]


def _format_report(rows: list[_Row]) -> str:
    train_rows = [r for r in rows if r.fy not in _HOLDOUT_FYS]
    hold_rows  = [r for r in rows if r.fy in _HOLDOUT_FYS]

    lines = [
        "# brk_wall × confluence_count — Stage 0",
        "",
        f"Probe run: {datetime.date.today()}.  Measures brk_wall per-fire EV "
        "stratified by the live confluence-bullish-sign count on the same "
        "(stock, date).  Tests the operator hypothesis (2026-05-19) that "
        "brk_wall might add Sortino value as a FALLBACK on confluence-quiet "
        "days, even though it dilutes when added to the confluence count "
        "([[project-brk-wall-k-sweep-reject]]).",
        "",
        "## Setup",
        "",
        "- brk_wall fires sourced from SignBenchmarkRun (production K=10).",
        "- Confluence-bullish set (10 signs): `str_hold, str_lead, str_lag, "
        "brk_sma, brk_bol, rev_lo, rev_nlo, brk_kumo_hi, brk_tenkan_hi, chiko_hi`.",
        "- valid_bars per sign matches `src.strategy.confluence_sign._VALID_BARS`.",
        "- For each brk_wall fire, count = number of DISTINCT bullish signs "
        "whose valid window covers the brk_wall fire's date "
        "(EXCLUDES brk_wall itself).",
        "",
    ]

    # FY breakdown of bucket distribution
    lines.append("## Bucket distribution by FY")
    lines.append("")
    lines.append("| FY | n=0 | n=1 | n=2 | n≥3 | total |")
    lines.append("|----|---:|---:|---:|---:|---:|")
    by_fy: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        by_fy[r.fy].append(r)
    for fy in sorted(by_fy):
        fr = by_fy[fy]
        c0 = sum(1 for r in fr if r.conf_count == 0)
        c1 = sum(1 for r in fr if r.conf_count == 1)
        c2 = sum(1 for r in fr if r.conf_count == 2)
        c3 = sum(1 for r in fr if r.conf_count >= 3)
        lines.append(f"| {fy} | {c0} | {c1} | {c2} | {c3} | {len(fr)} |")
    lines.append("")

    lines.extend(_bucket_table(rows,       "Pooled (FY2017-FY2025)"))
    lines.extend(_bucket_table(train_rows, "Train (pre-FY2024)"))
    lines.extend(_bucket_table(hold_rows,  "Holdout (FY2024+FY2025)"))

    # Gate check
    pool_dr   = _cell_stats(rows)[1] or 0.0
    quiet_pool = [r for r in rows if r.conf_count == 0]
    quiet_pool_dr = _cell_stats(quiet_pool)[1]
    quiet_hold = [r for r in hold_rows if r.conf_count == 0]
    quiet_hold_dr = _cell_stats(quiet_hold)[1]
    hold_pool_dr = _cell_stats(hold_rows)[1] or 0.0

    lines.append("## Pre-registered gate")
    lines.append("")
    lines.append(
        f"- **PASS**: quiet-day (count=0) DR ≥ pool DR + 3pp "
        f"AND quiet-day FY2024+FY2025 DR ≥ holdout pool DR + 3pp\n"
    )
    if quiet_pool_dr is None or quiet_hold_dr is None:
        lines.append("**Verdict: INSUFFICIENT DATA** — empty quiet cohort.")
    else:
        pool_lift = (quiet_pool_dr - pool_dr) * 100
        hold_lift = (quiet_hold_dr - hold_pool_dr) * 100
        pool_pass = pool_lift >= 3.0
        hold_pass = hold_lift >= 3.0
        lines.append(f"- Pool: quiet DR = {quiet_pool_dr*100:.1f}%, "
                     f"pool DR = {pool_dr*100:.1f}%, lift = {pool_lift:+.1f}pp "
                     f"({'✓' if pool_pass else '✗'} ≥ +3pp)")
        lines.append(f"- Holdout: quiet DR = {quiet_hold_dr*100:.1f}%, "
                     f"holdout DR = {hold_pool_dr*100:.1f}%, "
                     f"lift = {hold_lift:+.1f}pp "
                     f"({'✓' if hold_pass else '✗'} ≥ +3pp)")
        lines.append("")
        if pool_pass and hold_pass:
            verdict = "**PASS** — proceed to Stage 1 (strategy A/B with brk_wall as standalone fallback)."
        elif pool_pass:
            verdict = "**PARTIAL** — pool passes but holdout fails.  Same trap as the last several Stage-0 PARTIALs (e.g., brk_wall contrarian probe FY2025 zero-fire).  Do not proceed to Stage 1."
        else:
            verdict = "**FAIL** — quiet-day cohort does not lift over pool.  brk_wall as fallback offers no per-fire edge."
        lines.append(verdict)
    return "\n".join(lines)


def main() -> None:
    # Load fires for all 10 bullish signs (used to build confluence_count).
    bullish_fires = _load_fires(_EXPANDED_SIGNS)
    logger.info("Loaded bullish fires for {} stocks", len(bullish_fires))

    # Build the confluence_count map per stock.
    conf_count_map = _build_confluence_count_map(bullish_fires)
    logger.info("Built confluence_count map for {} stocks", len(conf_count_map))

    # Load brk_wall fires (separate query).
    bw_events = _load_brk_wall_events()

    # Tag each brk_wall fire with confluence_count at its stock × date.
    rows: list[_Row] = []
    n_no_stock = 0
    for stock, fired_at, dirn, mag in bw_events:
        d = fired_at.date()
        cc = conf_count_map.get(stock, {}).get(d)
        if cc is None:
            # No bullish-sign data for this stock × date (or stock has zero
            # bullish fires anywhere); treat as count=0.
            cc = 0
            n_no_stock += 1
        rows.append(_Row(stock=stock, date=d, fy=_fy_label(d),
                         direction=dirn, magnitude=mag, conf_count=cc))
    logger.info("Tagged {} brk_wall fires ({} treated as count=0 by absence)",
                len(rows), n_no_stock)

    report = _format_report(rows)
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(report + "\n")
    logger.info("Wrote {}", _DOC_PATH)
    # Print everything from gate downward
    g_idx = report.find("## Pre-registered gate")
    print(report[:report.find("## Pooled")])
    print()
    print(report[g_idx:])


if __name__ == "__main__":
    main()
