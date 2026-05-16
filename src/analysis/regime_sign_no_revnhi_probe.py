"""A/B: regime_sign_backtest with rev_nhi excluded from the ranking.

Question
--------
Operator is considering downgrading ``rev_nhi`` from a standalone entry
sign to one factor among many displayed in the Daily-tab decision panel.
Existing aggregate (FY2019–FY2025) shows ``rev_nhi`` is a net-negative
contributor: n=11, mean_r=−3.81%, Sharpe=−6.75 vs aggregate Sharpe 3.25.

But the per-sign rows are NOT additive: when rev_nhi is removed from the
ranking, on days where it was picked by argmax a DIFFERENT sign gets
picked instead.  Whether the replacement picks help or hurt is the only
question the existing per-sign table can't answer.

This probe answers it by re-running :mod:`regime_sign_backtest` for all 7
FYs twice — baseline (no exclusion) vs. ``EXCLUDE_SIGNS={"rev_nhi"}`` —
and comparing aggregate Sharpe / mean_r per FY and overall.

Run
---
    uv run --env-file devenv python -m src.analysis.regime_sign_no_revnhi_probe

(devenv — same as `regime_sign_backtest` itself, which reads from
``sign_benchmark_runs``.)
"""

from __future__ import annotations

import statistics
from pathlib import Path

from loguru import logger

from src.analysis import regime_sign_backtest as rsb
from src.analysis.exit_benchmark import Metrics, _metrics


REPORT_PATH = Path(__file__).parent / "regime_sign_no_revnhi_probe.md"


def _run_arm(exclude: frozenset[str], label: str) -> list[rsb.FyBacktestResult]:
    """Monkey-patch EXCLUDE_SIGNS and run all FYs."""
    rsb.EXCLUDE_SIGNS = exclude
    logger.info("─" * 70)
    logger.info("ARM = {}  (EXCLUDE_SIGNS = {})", label, sorted(exclude))
    logger.info("─" * 70)
    out: list[rsb.FyBacktestResult] = []
    for cfg in rsb.RS_FY_CONFIGS:
        out.append(rsb.run_fy(cfg))
    return out


def _agg_metrics(fy_results: list[rsb.FyBacktestResult]) -> Metrics:
    all_results = [r for fyr in fy_results for r in fyr.results]
    return _metrics(all_results)


def _per_fy_table(
    baseline: list[rsb.FyBacktestResult],
    treatment: list[rsb.FyBacktestResult],
) -> str:
    rows = [
        "| FY | baseline n | baseline mean_r | baseline Sharpe "
        "| no-rev_nhi n | no-rev_nhi mean_r | no-rev_nhi Sharpe "
        "| Δn | Δmean_r | ΔSharpe |",
        "|----|-----------:|----------------:|----------------:"
        "|-------------:|------------------:|------------------:"
        "|---:|--------:|--------:|",
    ]
    for b, t in zip(baseline, treatment):
        mb = _metrics(b.results)
        mt = _metrics(t.results)
        d_n   = mt.n - mb.n
        d_mr  = _mr(mt) - _mr(mb)
        d_sh  = _sharpe_or_zero(mt) - _sharpe_or_zero(mb)
        rows.append(
            f"| {b.config.label} | {mb.n} | {mb.fmt_mean_r()} | {mb.fmt_sharpe()} "
            f"| {mt.n} | {mt.fmt_mean_r()} | {mt.fmt_sharpe()} "
            f"| {d_n:+d} | {d_mr*100:+.2f}% | {d_sh:+.2f} |"
        )
    return "\n".join(rows)


def _sharpe_or_zero(m: Metrics) -> float:
    import math
    return 0.0 if math.isnan(m.sharpe) else m.sharpe


def _aggregate_block(
    baseline: list[rsb.FyBacktestResult],
    treatment: list[rsb.FyBacktestResult],
) -> str:
    mb = _agg_metrics(baseline)
    mt = _agg_metrics(treatment)
    sb = _sharpe_or_zero(mb)
    st = _sharpe_or_zero(mt)
    return (
        "| arm | n | mean_r | Sharpe | win% | hold |\n"
        "|---|--:|---:|---:|---:|---:|\n"
        f"| baseline (all signs)        | {mb.n} | {mb.fmt_mean_r()} "
        f"| {mb.fmt_sharpe()} | {mb.fmt_win()} | {mb.fmt_hold()} |\n"
        f"| no-rev_nhi (in ranking)     | {mt.n} | {mt.fmt_mean_r()} "
        f"| {mt.fmt_sharpe()} | {mt.fmt_win()} | {mt.fmt_hold()} |\n"
        f"| **Δ (no-rev_nhi − baseline)** | **{mt.n-mb.n:+d}** "
        f"| **{(_mr(mt)-_mr(mb))*100:+.2f}%** "
        f"| **{st-sb:+.2f}** | — | — |\n"
    )


def _mr(m: Metrics) -> float:
    return m.mean_r


def main() -> None:
    baseline  = _run_arm(frozenset(),                 label="baseline (all signs)")
    treatment = _run_arm(frozenset({"rev_nhi"}),      label="no-rev_nhi in ranking")

    per_fy = _per_fy_table(baseline, treatment)
    agg    = _aggregate_block(baseline, treatment)

    lines = [
        "# regime_sign_backtest — A/B with rev_nhi excluded from ranking",
        "",
        f"Generated: {__import__('datetime').date.today()}",
        "",
        "## Question",
        "",
        "Operator wants to demote `rev_nhi` from standalone entry sign to a UI",
        "decision factor.  Does removing it from the ranking hurt the backtest?",
        "",
        "## Methodology",
        "",
        "Two arms, identical except for `EXCLUDE_SIGNS`:",
        "- **baseline**: `EXCLUDE_SIGNS = frozenset()` (current production)",
        "- **no-rev_nhi**: `EXCLUDE_SIGNS = frozenset({\"rev_nhi\"})` — `rev_nhi`",
        "  is filtered out of `SignBenchmarkRun` lookup in `_load_run_ids`, so",
        "  the regime-ranking table excludes all `(rev_nhi, kumo)` cells and",
        "  no `rev_nhi` detectors are built.  On days where rev_nhi was previously",
        "  picked by argmax, the next-best (sign, kumo) cell gets picked.",
        "",
        f"FY range: FY2019 → FY2025 (7 fiscal years, walk-forward).",
        "",
        "## Per-FY",
        "",
        per_fy,
        "",
        "## Aggregate (FY2019–FY2025)",
        "",
        agg,
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines))
    logger.info("Wrote {}", REPORT_PATH)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
