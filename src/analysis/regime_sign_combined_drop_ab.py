"""regime_sign_combined_drop_ab — combined removal A/B for 3 near-miss signs.

Follow-up to [[project-regime-sign-loo-sweep-2026-05-19]].  Three signs
were near-misses in the leave-one-out sweep (each individually missed
gate by 1-2 criteria, but Sortino + Sharpe directions all positive):

    corr_shift  ΔSh +0.62  ΔSo +1.25  (only FY2022 lost)
    div_peer    ΔSh +0.28  ΔSo +0.52  (ΔSh −0.02 short)
    str_lag     ΔSh +0.38  ΔSo +0.68  (FY2024 −0.19)

This A/B tests whether removing ALL THREE simultaneously clears the
strict gate.

rev_nhi NOT included — leave-one-out sweep showed it only gives +0.07
ΔSh now (down from +1.23 on 2026-05-16) because the (sign, kumo) ranking
rotated after the 2026-05-18 ichimoku additions.  rev_nhi can stay
UI-hidden + in-ranking; including it here would just add noise.

Design
------
- Arm A = baseline (EXCLUDE_SIGNS = frozenset())
- Arm B = combined drop (EXCLUDE_SIGNS = frozenset({corr_shift, div_peer, str_lag}))
- Same min_dr=0.52, same ZsTpSl exit, same portfolio cap
- Per-FY + aggregate Sharpe / Sortino / mean_r / win% / EV decomp /
  marginal contribution (2026-05-18 evaluation framework)

Pre-registered ship gate (locked before run, identical to LOO sweep):
  - Δ Sharpe (FY-equal-weighted) ≥ +0.30
  - Δ Sortino ≥ +0.50
  - ≥ 5 / 7 FYs non-negative ΔSharpe (FY2019+FY2020 = 0 trades = 5 testable)
  - FY2024 + FY2025 both non-negative

If PASS: still requires bootstrap CI before production swap, per the
2026-05-16 rev_nhi pattern.

Output: src/analysis/benchmark.md § Combined-drop A/B (regime_sign)
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from pathlib import Path

from loguru import logger

import src.analysis.regime_sign_backtest as rsb
from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis.regime_sign_leaveoneout_sweep import (
    _agg, _fmt_p, _fmt_s, _gate_decision,
    _per_fy_table, _row_from_metrics,
)
from src.analysis.exit_benchmark import _metrics

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION  = "## Combined-drop A/B (regime_sign 2026-05-19)"

_DROP: frozenset[str] = frozenset({"corr_shift", "div_peer", "str_lag"})


def _run_arm(exclude: frozenset[str], label: str):
    logger.info("=== ARM: {} (EXCLUDE={}) ===", label, set(exclude) or "{}")
    rsb.EXCLUDE_SIGNS = exclude
    rows = []
    all_results = []
    for cfg in rsb.RS_FY_CONFIGS:
        res = rsb.run_fy(cfg)
        m = _metrics(res.results)
        rows.append(_row_from_metrics(cfg.label, res.n_proposals, m))
        all_results.extend(res.results)
        logger.info("  {}: n={} sharpe={}",
                    cfg.label, m.n,
                    f"{m.sharpe:+.2f}" if m.n > 0 and m.sharpe == m.sharpe else "—")
    return rows, all_results


def _format_report(base_rows, arm_rows, base_results, arm_results) -> str:
    base_agg = _agg(base_rows)
    arm_agg  = _agg(arm_rows)
    d_sh = (arm_agg["sharpe"]  - base_agg["sharpe"])  if (arm_agg["sharpe"]  and base_agg["sharpe"])  else None
    d_so = (arm_agg["sortino"] - base_agg["sortino"]) if (arm_agg["sortino"] and base_agg["sortino"]) else None
    d_mr = (arm_agg["mean_r"]  - base_agg["mean_r"])  if (arm_agg["mean_r"]  and base_agg["mean_r"])  else None

    lines = [
        "",
        _SECTION,
        "",
        f"Probe run: {datetime.date.today()}.  Combined removal of "
        f"{{{', '.join(sorted(_DROP))}}} from regime_sign ranking.",
        "",
        "Follow-up to the 2026-05-19 leave-one-out sweep — each of these "
        "3 signs was a near-miss individually.  Tests whether bundling "
        "the removals clears the same pre-registered gate.",
        "",
        "## Aggregate (FY-equal-weighted)",
        "",
        "| arm | n | Sharpe | Sortino | mean_r | win% | avg_win | avg_loss |",
        "|-----|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def _row(label, agg):
        return (f"| {label} | {agg['n_trades']} | **{_fmt_s(agg['sharpe'])}** | "
                f"**{_fmt_s(agg['sortino'])}** | {_fmt_p(agg['mean_r'])} | "
                f"{'—' if agg['win_rate'] is None else f'{agg['win_rate']*100:.1f}%'} | "
                f"{_fmt_p(agg['avg_win'])} | {_fmt_p(agg['avg_loss'])} |")

    lines.append(_row("baseline", base_agg))
    lines.append(_row(f"−{{{', '.join(sorted(_DROP))}}}", arm_agg))
    lines.append("")
    lines += [
        "**Aggregate deltas:**",
        "",
        f"- ΔSharpe = **{_fmt_s(d_sh)}**",
        f"- ΔSortino = **{_fmt_s(d_so)}**",
        f"- ΔmeanR = **{_fmt_p(d_mr)}**",
        f"- Δn_trades = {arm_agg['n_trades'] - base_agg['n_trades']:+}",
        "",
    ]

    # Per-FY
    per_fy_lines, fy_deltas = _per_fy_table(
        f"−{{{', '.join(sorted(_DROP))}}}", base_rows, arm_rows)
    lines.extend(per_fy_lines)
    lines.append("")

    # Gate
    verdict, notes = _gate_decision(
        "combined", base_agg, arm_agg, fy_deltas)
    lines += [
        f"## Verdict: {verdict}",
        "",
        "Pre-registered gate:",
        *notes,
        "",
    ]

    # Marginal contribution
    if base_results and arm_results:
        mc = compute_marginal(base_results, arm_results)
        lines.append("## Marginal contribution: baseline → combined-drop")
        lines.append("")
        lines.append(marginal_table(
            mc, a_label="baseline", b_label=f"−{{{', '.join(sorted(_DROP))}}}"))
        lines.append("")

    if "PASS" in verdict:
        lines += [
            "## Required follow-up before ship",
            "",
            "Per [[project-rev-nhi-ui-only-salvage]], an aggregate PASS does "
            "NOT clear production swap.  Need bootstrap CI (both FY-level "
            "AND trade-level) showing lower CI bound above 0 before any "
            "EXCLUDE_SIGNS production change.  Failure mode to watch for: "
            "trade-level CI [−2, +4] (n thin) → AND-gate fail same as 2026-05-16.",
            "",
        ]
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
    base_rows, base_results = _run_arm(frozenset(),  "baseline")
    arm_rows,  arm_results  = _run_arm(_DROP,        f"-{','.join(sorted(_DROP))}")
    rsb.EXCLUDE_SIGNS = frozenset()  # leave clean

    report = _format_report(base_rows, arm_rows, base_results, arm_results)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
