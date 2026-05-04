"""Generic markdown report builder — works for any StrategyPlugin.

All strategy-specific text (title, param names, entry/exit description) comes
from the plugin.  No strategy names are hardcoded here.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
from pathlib import Path
from typing import Any

from src.backtest.metrics import BacktestMetrics
from src.backtest.trainer import TrainResult

_REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def generate_report(
    plugin: Any,  # StrategyPlugin — avoids circular import at module level
    stock_code: str,
    gran: str,
    start: datetime.datetime,
    end: datetime.datetime,
    results: list[TrainResult[Any]],
    top_n: int = 20,
) -> Path:
    _REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{plugin.cli_name}_{stock_code.replace('.', '')}_{ts}.md"
    path = _REPORTS_DIR / fname
    path.write_text(
        "\n".join(_build(plugin, stock_code, gran, start, end, results, top_n)),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------


def _build(
    plugin: Any,
    stock_code: str,
    gran: str,
    start: datetime.datetime,
    end: datetime.datetime,
    results: list[TrainResult[Any]],
    top_n: int,
) -> list[str]:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    valid    = [r for r in results if r.metrics.total_trades > 0]
    positive = [r for r in valid   if r.metrics.total_return_pct > 0]
    best     = results[0] if results else None

    labels = plugin.param_labels()  # [(key, display, desc), ...]

    lines: list[str] = []
    lines += _header(plugin, stock_code, gran, start, end, now, results, valid, positive)
    if best:
        lines += _best_params_section(plugin.name, best, labels)
        lines += _best_metrics_section(best)
        lines += _equity_curve_section(best)
    lines += _top_results_table(results, labels, top_n)
    lines += _full_results_table(results, labels)
    lines += _interpretation_guide(plugin)
    return lines


def _header(
    plugin: Any,
    stock_code: str,
    gran: str,
    start: datetime.datetime,
    end: datetime.datetime,
    now: str,
    results: list[Any],
    valid: list[Any],
    positive: list[Any],
) -> list[str]:
    lines = [
        f"# {plugin.name} Strategy — Training Report",
        "",
        f"**Stock:** `{stock_code}` &nbsp;|&nbsp; "
        f"**Granularity:** `{gran}` &nbsp;|&nbsp; "
        f"**Period:** {start.date()} → {end.date()}",
        f"**Generated:** {now}",
        "",
        "---",
        "",
        "## Overview",
        "",
        "| Item | Value |",
        "|------|-------|",
        f"| Total combinations tested | {len(results)} |",
        f"| Combinations with ≥1 trade | {len(valid)} |",
        f"| Combinations with positive return | {len(positive)} |",
    ]
    if results:
        lines += [
            f"| Best score | {results[0].metrics.score:.3f} |",
            f"| Worst score | {results[-1].metrics.score:.3f} |",
        ]
    lines.append("")
    return lines


def _best_params_section(
    strategy_name: str,
    best: TrainResult[Any],
    labels: list[tuple[str, str, str]],
) -> list[str]:
    p = dataclasses.asdict(best.params)
    lines = [
        "## Best Parameters (by Score)",
        "",
        "| Parameter | Value | Description |",
        "|-----------|-------|-------------|",
    ]
    for key, display, desc in labels:
        val = p.get(key)
        lines.append(f"| {display} | {_fmt(key, val)} | {desc} |")
    if "units" in p:
        lines.append(f"| Units per trade | {p['units']} | Shares per order |")
    lines.append("")
    return lines


def _best_metrics_section(best: TrainResult[Any]) -> list[str]:
    m = best.metrics
    pf_str = f"{m.profit_factor:.2f}" if math.isfinite(m.profit_factor) else "∞"
    return [
        "## Best Result — Performance Metrics",
        "",
        "| Metric | Value | Interpretation |",
        "|--------|-------|----------------|",
        f"| Total Return | {m.total_return_pct:+.2f}% | Overall gain/loss |",
        f"| Annualised Return (CAGR) | {m.annualized_return_pct:+.2f}% | Compound annual growth |",
        f"| Sharpe Ratio | {m.sharpe_ratio:.3f} | Risk-adjusted return (>1 = good) |",
        f"| Max Drawdown | {m.max_drawdown_pct:.2f}% | Worst peak-to-trough |",
        f"| Win Rate | {m.win_rate_pct:.1f}% | % of trades closed in profit |",
        f"| Profit Factor | {pf_str} | Gross profit / gross loss (>1.5 = good) |",
        f"| Total Trades | {m.total_trades} | Completed round-trips |",
        f"| Avg Holding | {m.avg_holding_days:.1f} days | Mean calendar days per trade |",
        f"| **Score** | **{m.score:.3f}** | CAGR / |MaxDD| (higher = better) |",
        "",
        "> **Open position at end:** "
        f"{'Yes — unrealized P&L included in equity' if best.result.open_position_pnl != 0 else 'No — fully flat at end'}",
        "",
    ]


def _equity_curve_section(best: TrainResult[Any]) -> list[str]:
    curve = best.result.equity_curve
    dts   = best.result.bar_dts
    if not curve:
        return []

    n    = len(curve)
    step = max(1, n // 20)
    idxs = list(range(0, n, step))
    if (n - 1) % step != 0:
        idxs.append(n - 1)

    initial = best.result.initial_capital
    lines = [
        "## Equity Curve (Best Parameters)",
        "",
        "| Date | Equity | Return % |",
        "|------|--------|----------|",
    ]
    for i in idxs:
        ret = (curve[i] / initial - 1) * 100
        lines.append(f"| {dts[i].date()} | {curve[i]:,.0f} | {ret:+.2f}% |")

    lines += [
        "",
        "```",
        _ascii_chart(curve, dts),
        "```",
        "",
    ]
    return lines


def _ascii_chart(curve: list[float], dts: list[datetime.datetime]) -> str:
    width, height = 60, 10
    n = len(curve)
    if n < 2:
        return "(insufficient data)"

    lo, hi = min(curve), max(curve)
    rng = hi - lo or 1.0
    xs   = [int(round(i * (n - 1) / (width - 1))) for i in range(width)]
    vals = [curve[x] for x in xs]

    grid = [[" "] * width for _ in range(height)]
    for col, v in enumerate(vals):
        row = height - 1 - int(round((v - lo) / rng * (height - 1)))
        grid[max(0, min(height - 1, row))][col] = "█"

    chart_lines = []
    for r, row in enumerate(grid):
        prefix = f"{(hi if r == 0 else lo if r == height - 1 else ''):>10} |"
        chart_lines.append(prefix + "".join(row))

    chart_lines.append(" " * 11 + "+" + "-" * width)
    chart_lines.append(
        f" {dts[0].strftime('%Y-%m-%d'):>20}{'':>20}{dts[-1].strftime('%Y-%m-%d')}"
    )
    return "\n".join(chart_lines)


def _top_results_table(
    results: list[TrainResult[Any]],
    labels: list[tuple[str, str, str]],
    top_n: int,
) -> list[str]:
    top     = results[:top_n]
    headers = ["Rank"] + [d for _, d, _ in labels] + [
        "Return%", "Ann.Ret%", "Sharpe", "MaxDD%", "Win%", "PF", "Trades", "Score",
    ]
    sep = ["-" * max(4, len(h)) for h in headers]
    lines = [
        f"## Top {min(top_n, len(top))} Results (by Score)",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for rank, r in enumerate(top, 1):
        p   = dataclasses.asdict(r.params)
        m   = r.metrics
        pf  = f"{m.profit_factor:.2f}" if math.isfinite(m.profit_factor) else "∞"
        row = [str(rank)] + [_fmt(key, p.get(key)) for key, _, _ in labels]
        row += [
            f"{m.total_return_pct:+.2f}",
            f"{m.annualized_return_pct:+.2f}",
            f"{m.sharpe_ratio:.2f}",
            f"{m.max_drawdown_pct:.2f}",
            f"{m.win_rate_pct:.1f}",
            pf,
            str(m.total_trades),
            f"{m.score:.3f}",
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines


def _full_results_table(
    results: list[TrainResult[Any]],
    labels: list[tuple[str, str, str]],
) -> list[str]:
    headers = [d for _, d, _ in labels] + [
        "Return%", "Sharpe", "MaxDD%", "Win%", "Trades", "Score",
    ]
    sep = ["-" * max(4, len(h)) for h in headers]
    lines = [
        "## All Results (sorted by Score)",
        "",
        "<details><summary>Click to expand all combinations</summary>",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in results:
        p  = dataclasses.asdict(r.params)
        m  = r.metrics
        row = [_fmt(key, p.get(key)) for key, _, _ in labels]
        row += [
            f"{m.total_return_pct:+.2f}",
            f"{m.sharpe_ratio:.2f}",
            f"{m.max_drawdown_pct:.2f}",
            f"{m.win_rate_pct:.1f}",
            str(m.total_trades),
            f"{m.score:.3f}",
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "</details>", ""]
    return lines


def _interpretation_guide(plugin: Any) -> list[str]:
    lines = [
        "---",
        "",
        "## How to Read This Report",
        "",
        "| Metric | Formula | What it means |",
        "|--------|---------|---------------|",
        "| Total Return | (final / initial − 1) × 100 | Gross P&L over the full period |",
        "| CAGR | (final / initial)^(1/years) − 1 | Equivalent steady annual growth |",
        "| Sharpe Ratio | mean(returns) / std(returns) × √bars_per_year "
        "| Return per unit of volatility. >1 acceptable, >2 strong |",
        "| Max Drawdown | Worst peak-to-trough decline | How much you could lose from a peak |",
        "| Win Rate | profitable trades / total trades | % of round-trips closed in profit |",
        "| Profit Factor | gross profit / gross loss | >1.5 means winners outweigh losers |",
        "| **Score** | CAGR / \\|MaxDD\\| | Calmar-ratio proxy |",
        "",
        "> **Note:** Score is set to −999 for parameter sets with fewer than 3 completed trades.",
        "",
        "### Entry / Exit Logic",
        "",
    ]
    lines += plugin.entry_exit_lines()
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_PCT_KEYS = frozenset({"tp", "sl", "take_profit", "stop_loss"})


def _fmt(key: str, val: Any) -> str:
    if val is None:
        return ""
    if key in _PCT_KEYS:
        return f"{val:.0%}"
    if isinstance(val, float):
        return f"{val:.2f}" if val >= 1.0 else f"{val:.1%}"
    return str(val)
