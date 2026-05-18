"""brk_wall contrarian probe — Stage 1 cohort EV table.

Hypothesis: "when bearish_consensus is extreme, brk_wall becomes
CONTRARIAN bullish".  Tests this at the per-fire level using existing
brk_wall events in `sign_benchmark_events` — no simulator, no A/B.

Conditioning variable: SMA(50) breadth on the fire date.
  - "bearish_extreme"  = breadth percentile ≤ 20 (bottom quintile —
                         most stocks BELOW their 50-day SMA, the
                         "everyone is bearish" days)
  - "normal"           = 20 < percentile < 80
  - "bullish_extreme"  = percentile ≥ 80 (top quintile — most stocks
                         already above SMA, trend-extension regime)

Per-cohort metrics: n, DR, mag_follow, mag_reverse, signed-mean,
plus FY split.  If the contrarian hypothesis is real, the bearish-
extreme cohort should show materially higher DR + signed-mean than
the pooled average.

Run:
    uv run --env-file devenv python -m src.analysis.brk_wall_contrarian_probe

Output: docs/analysis/brk_wall_contrarian_probe.md  (overwrites).
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

from src.analysis.exit_benchmark import _load_rep_codes
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.sma_regime import SMARegime
from src.simulator.cache import DataCache

_SIGN          = "brk_wall"
_UNIVERSE_SET  = "classified2024"   # used only for breadth-signal computation
_GRAN          = "1d"
_SPAN_START    = datetime.date(2017, 10, 1)   # >120d lookback before FY2018 fires
_SPAN_END      = datetime.date(2026, 4, 1)
_BEARISH_CUT   = 20.0
_BULLISH_CUT   = 80.0

_DOC_PATH = Path("docs/analysis/brk_wall_contrarian_probe.md")


@dataclass
class _FireRow:
    fy:         str
    stock_code: str
    fired_at:   datetime.date
    dir:        int           # +1 HIGH first, -1 LOW first
    mag:        float         # |peak - entry| / entry
    breadth_pct: float        # 0-100 percentile of SMA50 breadth on fired date

    @property
    def signed_mag(self) -> float:
        return self.dir * self.mag


def _fy_label(d: datetime.date) -> str:
    """Map a date to fiscal-year label (Apr-Mar)."""
    if d.month >= 4:
        return f"FY{d.year}"
    return f"FY{d.year - 1}"


def _build_breadth(stock_codes: list[str]) -> SMARegime:
    """Load classified2024 caches across span, build SMARegime."""
    logger.info("Loading {} universe caches for breadth signal", len(stock_codes))
    span_start_dt = datetime.datetime.combine(_SPAN_START, datetime.time.min,
                                              tzinfo=datetime.timezone.utc)
    span_end_dt   = datetime.datetime.combine(_SPAN_END,   datetime.time.max,
                                              tzinfo=datetime.timezone.utc)
    caches: dict[str, DataCache] = {}
    with get_session() as s:
        for i, code in enumerate(stock_codes):
            c = DataCache(code, _GRAN)
            try:
                c.load(s, span_start_dt, span_end_dt)
            except Exception as exc:
                logger.warning("  {}: load failed — {}", code, exc)
                continue
            if c.bars:
                caches[code] = c
            if (i + 1) % 50 == 0:
                logger.info("  loaded {}/{}", i + 1, len(stock_codes))
    logger.info("Loaded {} non-empty caches", len(caches))

    # Trading dates = union of all cache dates within span
    date_set: set[datetime.date] = set()
    for cache in caches.values():
        for bar in cache.bars:
            d = bar.dt.date()
            if _SPAN_START <= d <= _SPAN_END:
                date_set.add(d)
    dates = sorted(date_set)
    logger.info("Building SMARegime over {} trading dates", len(dates))
    return SMARegime.build(caches, dates, sma_n=50, regime_percentile=0.80)


def _load_brk_wall_events() -> list[tuple[int, str, datetime.datetime, int, float]]:
    """Return list of (run_id, stock_code, fired_at, dir, mag) for brk_wall.

    Pulls from all multi-year runs (the rebenchmark pipeline writes one
    per cluster_set per FY).
    """
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun.id)
            .where(SignBenchmarkRun.sign_type == _SIGN)
        ).scalars().all()
    logger.info("Found {} {} runs", len(runs), _SIGN)
    rows: list[tuple] = []
    batch = 500
    for i in range(0, len(runs), batch):
        chunk = runs[i:i + batch]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append((e.run_id, e.stock_code, e.fired_at,
                         int(e.trend_direction), float(e.trend_magnitude)))
    logger.info("Loaded {} {} events with outcome", len(rows), _SIGN)
    return rows


def _tag_and_filter(events: list[tuple], regime: SMARegime) -> list[_FireRow]:
    out: list[_FireRow] = []
    n_skipped = 0
    for run_id, code, fired_at, d, m in events:
        fdate = fired_at.date()
        pct = regime.percentile(fdate)
        if pct != pct:  # NaN
            n_skipped += 1
            continue
        out.append(_FireRow(
            fy=_fy_label(fdate), stock_code=code, fired_at=fdate,
            dir=d, mag=m, breadth_pct=pct,
        ))
    logger.info("Tagged {} events; skipped {} (no breadth data)",
                len(out), n_skipped)
    return out


def _bucket(row: _FireRow) -> str:
    if row.breadth_pct <= _BEARISH_CUT:
        return "bearish_extreme"
    if row.breadth_pct >= _BULLISH_CUT:
        return "bullish_extreme"
    return "normal"


def _cohort_stats(rows: list[_FireRow]) -> dict:
    if not rows:
        return dict(n=0, dr=None, mag_flw=None, mag_rev=None, signed_mean=None)
    n = len(rows)
    n_flw = sum(1 for r in rows if r.dir == +1)
    dr = n_flw / n
    flw = [r.mag for r in rows if r.dir == +1]
    rev = [r.mag for r in rows if r.dir == -1]
    signed = [r.signed_mag for r in rows]
    return dict(
        n=n, dr=dr,
        mag_flw=statistics.mean(flw) if flw else None,
        mag_rev=statistics.mean(rev) if rev else None,
        signed_mean=statistics.mean(signed),
    )


def _fmt_row(label: str, st: dict) -> str:
    def _pct(v): return "—" if v is None else f"{v*100:+.2f}%"
    def _dr(v):  return "—" if v is None else f"{v*100:.1f}%"
    return (f"| {label} | {st['n']:>5} | {_dr(st['dr'])} | "
            f"{_pct(st['mag_flw'])} | {_pct(st['mag_rev'])} | "
            f"{_pct(st['signed_mean'])} |")


def _format_report(rows: list[_FireRow]) -> str:
    by_bucket: dict[str, list[_FireRow]] = defaultdict(list)
    for r in rows:
        by_bucket[_bucket(r)].append(r)
    by_fy_bucket: dict[tuple[str, str], list[_FireRow]] = defaultdict(list)
    for r in rows:
        by_fy_bucket[(r.fy, _bucket(r))].append(r)

    lines = [
        "# brk_wall contrarian probe — Stage 1 cohort EV table",
        "",
        f"Probe run: {datetime.date.today()}.  Tests whether brk_wall fires "
        "on extreme-bearish-breadth days deliver materially better outcomes "
        "than on normal/extreme-bullish days.",
        "",
        f"- **Sign**: `{_SIGN}` (K=10 production fires, all multi-year runs)",
        f"- **Conditioning signal**: SMA(50) breadth = fraction of "
        f"`{_UNIVERSE_SET}` universe whose close > own SMA(50)",
        f"- **bearish_extreme**: breadth percentile ≤ {_BEARISH_CUT:.0f} "
        "(bottom quintile)",
        f"- **bullish_extreme**: breadth percentile ≥ {_BULLISH_CUT:.0f} "
        "(top quintile)",
        f"- **normal**: in between",
        "",
        "**Signed mean** = E[trend_direction × trend_magnitude] (proxy for "
        "long-side mean return per fire if you blindly go long every fire).  "
        "DR > 50% and signed_mean > 0 together = sign predicts upward "
        "follow-through.",
        "",
        "## Pooled across all FYs",
        "",
        "| cohort | n | DR | mag_flw | mag_rev | signed_mean |",
        "|--------|---:|---:|---:|---:|---:|",
        _fmt_row("**bearish_extreme**", _cohort_stats(by_bucket["bearish_extreme"])),
        _fmt_row("normal",              _cohort_stats(by_bucket["normal"])),
        _fmt_row("**bullish_extreme**", _cohort_stats(by_bucket["bullish_extreme"])),
        _fmt_row("ALL",                 _cohort_stats(rows)),
        "",
        "## Per-FY breakdown",
        "",
        "| FY | cohort | n | DR | signed_mean |",
        "|----|--------|---:|---:|---:|",
    ]
    fys = sorted({r.fy for r in rows})
    for fy in fys:
        for bkt in ("bearish_extreme", "normal", "bullish_extreme"):
            st = _cohort_stats(by_fy_bucket[(fy, bkt)])
            if st["n"] == 0:
                continue
            lines.append(f"| {fy} | {bkt} | {st['n']:>4} | "
                         f"{'—' if st['dr'] is None else f'{st['dr']*100:.1f}%'} | "
                         f"{'—' if st['signed_mean'] is None else f'{st['signed_mean']*100:+.2f}%'} |")

    # ── Lift vs ALL (the headline number) ──
    all_st = _cohort_stats(rows)
    be_st  = _cohort_stats(by_bucket["bearish_extreme"])
    bl_st  = _cohort_stats(by_bucket["bullish_extreme"])

    def _delta(s):
        if not (s and s["n"] and all_st["n"]):
            return "—"
        d_dr = (s["dr"] - all_st["dr"]) * 100
        d_sm = (s["signed_mean"] - all_st["signed_mean"]) * 100
        return f"DR Δ {d_dr:+.1f}pp / signed_mean Δ {d_sm:+.2f}pp"

    lines += [
        "",
        "## Verdict shape (lift vs ALL pooled)",
        "",
        f"- **bearish_extreme** ({be_st['n']} fires): {_delta(be_st)}",
        f"- **bullish_extreme** ({bl_st['n']} fires): {_delta(bl_st)}",
        "",
        "## Interpretation",
        "",
        "- **PASS (contrarian-bullish real)**: bearish_extreme shows "
        "DR ≥ ALL+5pp AND signed_mean ≥ ALL+0.5pp AND per-FY direction "
        "consistent in ≥ 5/8 FYs.",
        "- **REJECT (no contrarian effect)**: bearish_extreme close to "
        "ALL or worse.  Hypothesis dies here, no need to run Stage 2 A/B.",
        "- **PARTIAL (weak signal)**: lift present but per-FY noisy or "
        "n too small.  Document as not-actionable; revisit on universe "
        "expansion.",
    ]
    return "\n".join(lines)


def main() -> None:
    universe = _load_rep_codes(_UNIVERSE_SET)
    regime   = _build_breadth(universe)
    events   = _load_brk_wall_events()
    rows     = _tag_and_filter(events, regime)
    report   = _format_report(rows)
    print(report)
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(report + "\n")
    logger.info("Wrote {}", _DOC_PATH)


if __name__ == "__main__":
    main()
