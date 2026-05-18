"""_marginal — marginal contribution analysis for A/B testing.

Helper module for comparing two arms (baseline A vs variant B = A + new_X)
beyond aggregate Sharpe.  Operator (2026-05-18) flagged that the
brk_wall confluence-dilution finding might have been caught earlier
with marginal analysis.

Provides:
  - **Turnover impact** — Δ trade count
  - **Drawdown impact** — Δ max peak-to-trough on cumulative returns
  - **Diversification** — Pearson correlation of per-day arm returns
    (low correlation = the new sign trades on different days from
    existing, i.e., real diversification)
  - **Tail-hedge** — when arm A has its worst quintile of days, what's
    the arm B mean return?  If positive, the new sign hedges A's tail.
  - **New-trade win rate** — of trades B has that A doesn't, what
    fraction won?

All metrics computed from `list[ExitResult]` per arm.
"""
from __future__ import annotations

import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from src.exit.base import ExitResult


@dataclass
class MarginalReport:
    """Summary of how arm B differs from arm A (B = A + new sign)."""
    a_n:                int
    b_n:                int
    delta_n:            int      # b_n - a_n (extra trades in B)
    a_max_drawdown:     float
    b_max_drawdown:     float
    delta_drawdown:     float    # b_dd - a_dd (positive = WORSE drawdown)
    daily_corr:         float | None    # Pearson corr of per-day returns
    a_tail_mean:        float | None    # A's mean return on its worst-quintile days
    b_on_a_tail_mean:   float | None    # B's mean return on those SAME days
    tail_hedge_lift:    float | None    # b_on_a_tail - a_tail (positive = hedges)
    new_trades_n:       int      # trades in B not in A (matched by stock+date)
    new_trades_win_rate: float | None


def _max_drawdown(rets: list[float]) -> float:
    """Compute peak-to-trough drawdown on cumulative (sum) returns.

    Approximation since our trades are sparse (no continuous equity curve).
    Returns 0 if no trades or no drawdown.
    """
    if not rets:
        return 0.0
    cum  = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rets:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _daily_returns(results: Iterable[ExitResult]) -> dict[datetime.date, float]:
    """Sum trade returns per entry_date.  Multiple trades same day → summed."""
    out: dict[datetime.date, float] = defaultdict(float)
    for r in results:
        out[r.entry_date] += r.return_pct
    return dict(out)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    try:
        mx = statistics.mean(xs); my = statistics.mean(ys)
        sx = statistics.stdev(xs); sy = statistics.stdev(ys)
        if sx == 0 or sy == 0:
            return None
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
        return cov / (sx * sy)
    except statistics.StatisticsError:
        return None


def compute_marginal(
    a_results: list[ExitResult],
    b_results: list[ExitResult],
) -> MarginalReport:
    """Compare arm B against arm A (where B is intended to include A + new sign)."""
    a_rets = [r.return_pct for r in a_results]
    b_rets = [r.return_pct for r in b_results]

    # ── Drawdown (order by entry_date for sensible cum-sum equity curve) ──
    a_sorted = sorted(a_results, key=lambda r: r.entry_date)
    b_sorted = sorted(b_results, key=lambda r: r.entry_date)
    a_dd = _max_drawdown([r.return_pct for r in a_sorted])
    b_dd = _max_drawdown([r.return_pct for r in b_sorted])

    # ── Daily return correlation (union of dates with trades in either arm) ──
    a_daily = _daily_returns(a_results)
    b_daily = _daily_returns(b_results)
    common_dates = sorted(set(a_daily) | set(b_daily))
    a_seq = [a_daily.get(d, 0.0) for d in common_dates]
    b_seq = [b_daily.get(d, 0.0) for d in common_dates]
    daily_corr = _pearson(a_seq, b_seq)

    # ── Tail-hedge: identify A's worst-quintile days, look at B on those ──
    if len(a_daily) >= 5:
        a_days_sorted = sorted(a_daily.items(), key=lambda kv: kv[1])
        tail_n = max(1, len(a_days_sorted) // 5)
        tail_days = [d for d, _ in a_days_sorted[:tail_n]]
        a_tail_mean = statistics.mean(a_daily[d] for d in tail_days)
        b_on_tail = [b_daily.get(d, 0.0) for d in tail_days]
        b_tail_mean = statistics.mean(b_on_tail)
        tail_lift = b_tail_mean - a_tail_mean
    else:
        a_tail_mean = b_tail_mean = tail_lift = None

    # ── New-trade identification (B-only trades, matched by stock+date) ──
    a_keys = {(r.stock_code, r.entry_date) for r in a_results}
    new_trades = [r for r in b_results
                  if (r.stock_code, r.entry_date) not in a_keys]
    new_n = len(new_trades)
    new_wr = (sum(1 for r in new_trades if r.return_pct > 0) / new_n
              if new_n else None)

    return MarginalReport(
        a_n=len(a_results), b_n=len(b_results), delta_n=len(b_results) - len(a_results),
        a_max_drawdown=a_dd, b_max_drawdown=b_dd, delta_drawdown=b_dd - a_dd,
        daily_corr=daily_corr,
        a_tail_mean=a_tail_mean, b_on_a_tail_mean=b_tail_mean,
        tail_hedge_lift=tail_lift,
        new_trades_n=new_n, new_trades_win_rate=new_wr,
    )


def marginal_table(report: MarginalReport, a_label: str, b_label: str) -> str:
    """Render a MarginalReport as a markdown sub-section.

    Designed to be appended after the main aggregate table in any A/B
    `_format_report()`.  Helps catch confluence-dilution / no-diversification
    issues that aggregate Sharpe can hide.
    """
    def _pct(x: float | None) -> str:
        return f"{x*100:+.2f}%" if x is not None else "—"
    def _f(x: float | None) -> str:
        return f"{x:+.3f}" if x is not None else "—"

    lines = [
        "",
        "### Marginal contribution (added 2026-05-18)",
        "",
        f"Comparing **{b_label}** against **{a_label}** at the per-trade level.",
        "",
        "| Metric | Value | Interpretation |",
        "|--------|------:|----------------|",
        f"| Δ trade count | **{report.delta_n:+}** | {b_label} − {a_label} (turnover impact) |",
        f"| {a_label} max drawdown | {_pct(report.a_max_drawdown)} | peak-to-trough on cumulative trade returns |",
        f"| {b_label} max drawdown | {_pct(report.b_max_drawdown)} | same metric, expanded arm |",
        f"| Δ drawdown | {_pct(report.delta_drawdown)} | + = drawdown got WORSE under {b_label} |",
        f"| Daily-return correlation | **{_f(report.daily_corr)}** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |",
        f"| {a_label}'s worst-quintile day mean | {_pct(report.a_tail_mean)} | A's bad days |",
        f"| {b_label} on those same days | {_pct(report.b_on_a_tail_mean)} | does new sign help when A loses? |",
        f"| Tail-hedge lift | **{_pct(report.tail_hedge_lift)}** | + = {b_label} cushions {a_label}'s tail |",
        f"| New-trade count (B-only) | {report.new_trades_n} | trades introduced by the change |",
        f"| New-trade win rate | {(f'{report.new_trades_win_rate*100:.1f}%' if report.new_trades_win_rate is not None else '—')} | quality of the marginal trades |",
        "",
    ]
    return "\n".join(lines)
