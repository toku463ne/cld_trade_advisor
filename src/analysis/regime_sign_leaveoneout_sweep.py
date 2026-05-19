"""regime_sign_leaveoneout_sweep — A/B sweep removing each negative-Sharpe sign.

Operator question (2026-05-19): which signs are dragging RegimeSignStrategy
down?  Aggregate per-sign breakdown in regime_sign_backtest.md (FY2019-FY2024)
flagged 4 candidates with negative aggregate Sharpe:

    rev_nhi     n=11  Sharpe −6.75   (already UI-hidden but still in ranking)
    corr_shift  n=17  Sharpe −3.36
    div_peer    n=11  Sharpe −1.86
    str_lag     n=11  Sharpe −0.84

rev_nhi's binary swap was rejected by bootstrap AND-gate (2026-05-16,
[[project-rev-nhi-ui-only-salvage]]) — so this sweep re-tests it under
the new evaluation framework AND tests the other 3.

Design
------
- Baseline arm: EXCLUDE_SIGNS = frozenset()  (current production state)
- 4 leave-one-out arms: EXCLUDE_SIGNS = frozenset({sign}) for each candidate
- Same min_dr=0.52, same ZsTpSl exit, same portfolio cap
- Per-FY + aggregate Sharpe / Sortino / mean_r / win_rate / hold
- Sortino + EV decomposition (2026-05-18 framework)
- Marginal contribution per arm vs baseline (turnover, drawdown, daily
  corr, tail-hedge lift, new-trade win rate)

Pre-registered ship gate (locked before run):
  - Δ Sharpe ≥ +0.30 (FY-equal-weighted)
  - Δ Sortino ≥ +0.50
  - ≥ 5 / 7 FYs non-negative ΔSharpe
  - FY2024 + FY2025 both non-negative ΔSharpe (holdout)

Output: src/analysis/benchmark.md § Leave-one-out sweep
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

import src.analysis.regime_sign_backtest as rsb
from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis.exit_benchmark import _metrics

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION  = "## Leave-one-out sweep (regime_sign 2026-05-19)"

_CANDIDATES: tuple[str, ...] = ("rev_nhi", "corr_shift", "div_peer", "str_lag")

_HOLDOUT_FYS = {"FY2024", "FY2025"}


@dataclass
class _ArmRow:
    fy:        str
    n_trades:  int
    n_props:   int
    mean_r:    float | None
    sharpe:    float | None
    win_rate:  float | None
    hold_bars: float | None
    sortino:   float | None = None
    avg_win:   float | None = None
    avg_loss:  float | None = None


def _row_from_metrics(fy: str, n_props: int, m) -> _ArmRow:
    safe = lambda v: v if not math.isnan(v) else None
    return _ArmRow(
        fy=fy, n_trades=m.n, n_props=n_props,
        mean_r=m.mean_r if m.n > 0 else None,
        sharpe=safe(m.sharpe) if m.n > 0 else None,
        win_rate=m.win_rate if m.n > 0 else None,
        hold_bars=m.hold_bars if m.n > 0 else None,
        sortino=safe(m.sortino) if m.n > 0 else None,
        avg_win=m.avg_win if (m.n > 0 and m.avg_win != 0.0) else None,
        avg_loss=m.avg_loss if (m.n > 0 and m.avg_loss != 0.0) else None,
    )


def _run_arm(exclude: frozenset[str], label: str
            ) -> tuple[list[_ArmRow], list]:
    logger.info("=== ARM: {} (EXCLUDE={}) ===", label, set(exclude) or "{}")
    rsb.EXCLUDE_SIGNS = exclude
    rows: list[_ArmRow] = []
    all_results: list = []
    for cfg in rsb.RS_FY_CONFIGS:
        res = rsb.run_fy(cfg)
        m   = _metrics(res.results)
        rows.append(_row_from_metrics(cfg.label, res.n_proposals, m))
        all_results.extend(res.results)
        logger.info("  {}: n={} sharpe={}",
                    cfg.label, m.n,
                    f"{m.sharpe:+.2f}" if m.n > 0 and m.sharpe == m.sharpe else "—")
    return rows, all_results


def _fmt_s(v):  return "—" if v is None else f"{v:+.2f}"
def _fmt_p(v):  return "—" if v is None else f"{v*100:+.2f}%"


def _agg(rows: list[_ArmRow]) -> dict:
    sh = [r.sharpe   for r in rows if r.sharpe   is not None]
    so = [r.sortino  for r in rows if r.sortino  is not None]
    mr = [r.mean_r   for r in rows if r.mean_r   is not None]
    wr = [r.win_rate for r in rows if r.win_rate is not None]
    aw = [r.avg_win  for r in rows if r.avg_win  is not None]
    al = [r.avg_loss for r in rows if r.avg_loss is not None]
    return {
        "n_trades": sum(r.n_trades for r in rows),
        "sharpe":   statistics.mean(sh) if sh else None,
        "sortino":  statistics.mean(so) if so else None,
        "mean_r":   statistics.mean(mr) if mr else None,
        "win_rate": statistics.mean(wr) if wr else None,
        "avg_win":  statistics.mean(aw) if aw else None,
        "avg_loss": statistics.mean(al) if al else None,
    }


def _per_fy_table(label: str,
                  base: list[_ArmRow], arm: list[_ArmRow]) -> list[str]:
    by_b = {r.fy: r for r in base}
    by_a = {r.fy: r for r in arm}
    lines = [
        f"#### {label} — per-FY",
        "",
        "| FY | base n | base Sh | arm n | arm Sh | ΔSh | ΔmeanR |",
        "|----|---:|---:|---:|---:|---:|---:|",
    ]
    fy_deltas: list[tuple[str, float | None, float | None]] = []
    for fy in sorted(by_b):
        b = by_b[fy]
        a = by_a.get(fy)
        if a is None:
            continue
        d_sh = (a.sharpe - b.sharpe) if (a.sharpe is not None and b.sharpe is not None) else None
        d_mr = (a.mean_r - b.mean_r) if (a.mean_r is not None and b.mean_r is not None) else None
        fy_deltas.append((fy, d_sh, d_mr))
        lines.append(
            f"| {fy} | {b.n_trades} | {_fmt_s(b.sharpe)} | "
            f"{a.n_trades} | {_fmt_s(a.sharpe)} | **{_fmt_s(d_sh)}** | {_fmt_p(d_mr)} |"
        )
    return lines, fy_deltas


def _gate_decision(arm: str,
                   base_agg: dict, arm_agg: dict,
                   fy_deltas: list[tuple[str, float | None, float | None]]
                  ) -> tuple[str, list[str]]:
    """Apply pre-registered gate; return (verdict, reasoning_lines)."""
    d_sh = (arm_agg["sharpe"] - base_agg["sharpe"]) \
           if (arm_agg["sharpe"] is not None and base_agg["sharpe"] is not None) else None
    d_so = (arm_agg["sortino"] - base_agg["sortino"]) \
           if (arm_agg["sortino"] is not None and base_agg["sortino"] is not None) else None
    fy_non_neg = sum(1 for _, ds, _ in fy_deltas if ds is not None and ds >= -0.001)
    fy_total   = len([1 for _, ds, _ in fy_deltas if ds is not None])
    hold_fys   = [(fy, ds) for fy, ds, _ in fy_deltas if fy in _HOLDOUT_FYS]
    hold_both_ok = all(ds is not None and ds >= -0.001 for _, ds in hold_fys)

    notes = [
        f"- Δ Sharpe (FY-equal-weighted) = {_fmt_s(d_sh)} "
        f"({'✓' if d_sh is not None and d_sh >= 0.30 else '✗'} ≥ +0.30)",
        f"- Δ Sortino                    = {_fmt_s(d_so)} "
        f"({'✓' if d_so is not None and d_so >= 0.50 else '✗'} ≥ +0.50)",
        f"- FYs with non-negative ΔSharpe = {fy_non_neg}/{fy_total} "
        f"({'✓' if fy_non_neg >= 5 else '✗'} ≥ 5)",
        f"- FY2024 + FY2025 both non-negative = "
        f"{'✓' if hold_both_ok and len(hold_fys) == 2 else '✗'}",
    ]
    pass_all = (
        d_sh is not None and d_sh >= 0.30 and
        d_so is not None and d_so >= 0.50 and
        fy_non_neg >= 5 and
        hold_both_ok and len(hold_fys) == 2
    )
    verdict = "**PASS**" if pass_all else "**REJECT**"
    return verdict, notes


def _format_report(
    base_rows:    list[_ArmRow],
    arm_rows_map: dict[str, list[_ArmRow]],
    base_results: list,
    arm_results_map: dict[str, list],
) -> str:
    lines = [
        "",
        _SECTION,
        "",
        f"Probe run: {datetime.date.today()}.  Tests whether removing any "
        "individually-negative-Sharpe sign from the regime_sign ranking "
        "improves the strategy.  Same min_dr=0.52, same ZsTpSl(2.0,2.0,0.3) "
        "exit, same portfolio cap as production.",
        "",
        "**Candidates** (selected from aggregate per-sign breakdown in "
        "regime_sign_backtest.md, all negative aggregate Sharpe over "
        "FY2019-FY2024):",
        "",
        "| sign | prior aggregate n | prior Sharpe |",
        "|---|---:|---:|",
        "| rev_nhi    | 11 | −6.75 |",
        "| corr_shift | 17 | −3.36 |",
        "| div_peer   | 11 | −1.86 |",
        "| str_lag    | 11 | −0.84 |",
        "",
        "## Aggregate (FY-equal-weighted)",
        "",
        "| arm | n | Sharpe | Sortino | mean_r | win% | avg_win | avg_loss |",
        "|-----|---:|---:|---:|---:|---:|---:|---:|",
    ]
    base_agg = _agg(base_rows)

    def _row(label, agg):
        return (f"| {label} | {agg['n_trades']} | **{_fmt_s(agg['sharpe'])}** | "
                f"**{_fmt_s(agg['sortino'])}** | {_fmt_p(agg['mean_r'])} | "
                f"{'—' if agg['win_rate'] is None else f'{agg['win_rate']*100:.1f}%'} | "
                f"{_fmt_p(agg['avg_win'])} | {_fmt_p(agg['avg_loss'])} |")

    lines.append(_row("baseline", base_agg))
    arm_aggs: dict[str, dict] = {}
    for sign in _CANDIDATES:
        agg = _agg(arm_rows_map[sign])
        arm_aggs[sign] = agg
        lines.append(_row(f"−{sign}", agg))
    lines.append("")

    # Δ table
    lines += [
        "### Aggregate deltas vs baseline",
        "",
        "| arm | ΔSharpe | ΔSortino | ΔmeanR | Δn_trades |",
        "|-----|---:|---:|---:|---:|",
    ]
    for sign in _CANDIDATES:
        a = arm_aggs[sign]
        d_sh = (a["sharpe"] - base_agg["sharpe"]) if (a["sharpe"] and base_agg["sharpe"]) else None
        d_so = (a["sortino"] - base_agg["sortino"]) if (a["sortino"] and base_agg["sortino"]) else None
        d_mr = (a["mean_r"] - base_agg["mean_r"]) if (a["mean_r"] and base_agg["mean_r"]) else None
        d_n  = a["n_trades"] - base_agg["n_trades"]
        lines.append(f"| −{sign} | **{_fmt_s(d_sh)}** | **{_fmt_s(d_so)}** | "
                     f"**{_fmt_p(d_mr)}** | {d_n:+} |")
    lines.append("")

    # Per-arm sections
    for sign in _CANDIDATES:
        arm_rows = arm_rows_map[sign]
        per_fy_lines, fy_deltas = _per_fy_table(f"− {sign}", base_rows, arm_rows)
        lines.append(f"### Drop `{sign}`")
        lines.append("")
        lines.extend(per_fy_lines)
        # Gate
        verdict, notes = _gate_decision(sign, base_agg, arm_aggs[sign], fy_deltas)
        lines += [
            "",
            f"**Verdict for `−{sign}`**: {verdict}",
            "",
            "Pre-registered gate:",
            *notes,
            "",
        ]
        # Marginal contribution: A = baseline, B = arm (with sign removed)
        a_res = base_results
        b_res = arm_results_map[sign]
        if a_res and b_res:
            mc = compute_marginal(a_res, b_res)
            lines.append(f"#### Marginal contribution: baseline → −{sign}")
            lines.append(marginal_table(mc,
                                        a_label="baseline",
                                        b_label=f"−{sign}"))
            lines.append("")
    return "\n".join(lines)


def _append_to_benchmark(md: str) -> None:
    existing = _BENCH_MD.read_text() if _BENCH_MD.exists() else ""
    if _SECTION in existing:
        idx = existing.index(_SECTION)
        rest = existing[idx + len(_SECTION):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _BENCH_MD.write_text(existing.rstrip() + "\n" + md.lstrip("\n"))
    logger.info("Appended report to {}", _BENCH_MD)


def main() -> None:
    base_rows, base_results = _run_arm(frozenset(), "baseline")
    arm_rows_map: dict[str, list[_ArmRow]] = {}
    arm_results_map: dict[str, list] = {}
    for sign in _CANDIDATES:
        rows, results = _run_arm(frozenset({sign}), f"-{sign}")
        arm_rows_map[sign]    = rows
        arm_results_map[sign] = results
    rsb.EXCLUDE_SIGNS = frozenset()  # leave clean

    report = _format_report(base_rows, arm_rows_map, base_results, arm_results_map)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
