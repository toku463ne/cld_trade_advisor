"""regime_sign_brk_wall_ab — A/B comparison: regime strategy with vs without brk_wall.

Runs `regime_sign_backtest.run_fy` twice for each FY (FY2019–FY2025):
  - WITH brk_wall (current shipped state)
  - WITHOUT brk_wall (baseline before sign was added)

Compares aggregate Sharpe / mean_r / DR / trade count per FY, plus
overall.  Tells us whether shipping brk_wall actually improved the
live strategy or just added a new (sign, kumo) cell that displaces
better-EV cells in the regime ranking.

Read-only.  Monkeypatches EXCLUDE_SIGNS to flip between configs; no
edits to regime_sign_backtest.py.

Output: src/analysis/benchmark.md § Strategy A/B: brk_wall on/off
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

import src.analysis.regime_sign_backtest as rsb
from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis.exit_benchmark import _metrics

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Strategy A/B: brk_wall on/off"


@dataclass
class _ArmRow:
    fy:        str
    n_trades:  int
    n_props:   int
    mean_r:    float | None
    sharpe:    float | None
    win_rate:  float | None
    hold_bars: float | None
    # 2026-05-18 evaluation upgrade
    sortino:   float | None = None
    avg_win:   float | None = None
    avg_loss:  float | None = None


def _row_from_metrics(fy: str, n_props: int, m) -> _ArmRow:
    import math
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
    logger.info("=== ARM: {} (EXCLUDE_SIGNS={}) ===", label, set(exclude))
    rsb.EXCLUDE_SIGNS = exclude
    rows: list[_ArmRow] = []
    all_results: list = []
    for cfg in rsb.RS_FY_CONFIGS:
        result = rsb.run_fy(cfg)
        n_props = result.n_proposals
        m       = _metrics(result.results)   # FyBacktestResult.results is list[ExitResult]
        rows.append(_row_from_metrics(cfg.label, n_props, m))
        all_results.extend(result.results)
        logger.info("  {}: trades={} sharpe={} mean_r={}",
                    cfg.label, m.n,
                    f"{m.sharpe:.3f}" if m.sharpe == m.sharpe else "—",  # NaN check
                    f"{m.mean_r*100:+.2f}%")
    return rows, all_results


def _format_report(with_rows: list[_ArmRow],
                   without_rows: list[_ArmRow],
                   with_results: list,
                   without_results: list) -> str:
    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Walk-forward regime-sign "
        "strategy backtest run twice: once WITH brk_wall in the sign set "
        "(current shipped state) and once WITHOUT (the state before "
        "commit 52bde03).  Same min_dr=0.52 threshold, same ZsTpSl exit, "
        "same portfolio cap (≤1 high-corr, ≤3 low/mid-corr).",
        "",
        "### Per-FY summary",
        "",
        "| FY | with: trades / mean_r / Sharpe / win% | without: trades / mean_r / Sharpe / win% | Δ Sharpe | Δ mean_r |",
        "|----|---|---|---:|---:|",
    ]
    by_fy_with    = {r.fy: r for r in with_rows}
    by_fy_without = {r.fy: r for r in without_rows}
    for fy in sorted(by_fy_with):
        w = by_fy_with[fy]
        wo = by_fy_without.get(fy, _ArmRow(fy, 0, 0, None, None, None, None))
        def _f(r: _ArmRow) -> str:
            mean_s = f"{r.mean_r*100:+.2f}%" if r.mean_r is not None else "—"
            sh_s = f"{r.sharpe:+.2f}" if r.sharpe is not None else "—"
            wr_s = f"{r.win_rate*100:.0f}%" if r.win_rate is not None else "—"
            return f"{r.n_trades} / {mean_s} / {sh_s} / {wr_s}"
        d_sh = (w.sharpe - wo.sharpe) if (w.sharpe is not None and wo.sharpe is not None) else None
        d_mr = (w.mean_r - wo.mean_r) if (w.mean_r is not None and wo.mean_r is not None) else None
        d_sh_s = f"{d_sh:+.2f}" if d_sh is not None else "—"
        d_mr_s = f"{d_mr*100:+.2f}pp" if d_mr is not None else "—"
        lines.append(f"| {fy} | {_f(w)} | {_f(wo)} | **{d_sh_s}** | **{d_mr_s}** |")

    # Aggregate across all FYs (simple averages / sums)
    def _agg(rows: list[_ArmRow]) -> tuple[int, float | None, float | None]:
        ns = [r.n_trades for r in rows]
        sh = [r.sharpe for r in rows if r.sharpe is not None]
        mr = [r.mean_r for r in rows if r.mean_r is not None]
        total_n = sum(ns)
        avg_sh = (sum(sh) / len(sh)) if sh else None
        avg_mr = (sum(mr) / len(mr)) if mr else None
        return total_n, avg_sh, avg_mr

    n_w, sh_w, mr_w = _agg(with_rows)
    n_wo, sh_wo, mr_wo = _agg(without_rows)
    d_sh = (sh_w - sh_wo) if (sh_w is not None and sh_wo is not None) else None
    d_mr = (mr_w - mr_wo) if (mr_w is not None and mr_wo is not None) else None

    lines += [
        "",
        "### Aggregate (FY-equal-weighted)",
        "",
        f"- WITH brk_wall:    total trades = {n_w}, avg Sharpe = "
        f"{sh_w:+.2f}, avg mean_r = {mr_w*100:+.2f}%" if sh_w and mr_w
        else f"- WITH brk_wall:    total trades = {n_w}, partial data",
        f"- WITHOUT brk_wall: total trades = {n_wo}, avg Sharpe = "
        f"{sh_wo:+.2f}, avg mean_r = {mr_wo*100:+.2f}%" if sh_wo and mr_wo
        else f"- WITHOUT brk_wall: total trades = {n_wo}, partial data",
        "",
        f"- **Δ Sharpe = {d_sh:+.2f}** ; **Δ mean_r = {d_mr*100:+.2f}pp**"
        if d_sh is not None and d_mr is not None
        else "- Δ aggregate could not be computed (some FYs have 0 trades)",
        "",
        "### Verdict",
        "",
    ]
    if d_sh is not None and d_sh > 0.10:
        lines.append(
            "**brk_wall improves the strategy** (Δ Sharpe > +0.10). "
            "Shipping it was the right call — keep it in the ranking."
        )
    elif d_sh is not None and d_sh < -0.10:
        lines.append(
            "**brk_wall HURTS the strategy** (Δ Sharpe < −0.10).  Likely "
            "displaces better-EV cells in the regime ranking, or competes "
            "with stronger signs for portfolio slots.  Consider hiding "
            "from regime_sign or restricting to its strongest cells only."
        )
    elif d_sh is not None:
        lines.append(
            f"**brk_wall is roughly neutral on aggregate Sharpe** "
            f"(|Δ| {abs(d_sh):.2f} ≤ 0.10).  Sign is harmless but not "
            "load-bearing for live strategy performance.  Keep for "
            "informational value; don't expect it to shift Sharpe materially."
        )
    else:
        lines.append("**Verdict pending** — too many FYs with 0 trades to compute.")

    # 2026-05-18 evaluation upgrade: Sortino + EV decomposition (FY-equal-weighted)
    import statistics as _stats
    def _ev_row(label: str, rows: list[_ArmRow]) -> str:
        sh = [r.sharpe   for r in rows if r.sharpe   is not None]
        so = [r.sortino  for r in rows if r.sortino  is not None]
        pw = [r.win_rate for r in rows if r.win_rate is not None]
        aw = [r.avg_win  for r in rows if r.avg_win  is not None]
        al = [r.avg_loss for r in rows if r.avg_loss is not None]
        avg_sh = _stats.mean(sh) if sh else None
        avg_so = _stats.mean(so) if so else None
        avg_pw = _stats.mean(pw) if pw else None
        avg_aw = _stats.mean(aw) if aw else None
        avg_al = _stats.mean(al) if al else None
        ev_check = (avg_pw * avg_aw + (1 - avg_pw) * avg_al) \
                   if (avg_pw is not None and avg_aw is not None and avg_al is not None) \
                   else None
        f = lambda v, p=False: ("—" if v is None
                                else (f"{v*100:+.2f}%" if p else f"{v:+.2f}"))
        return (f"| {label} | {f(avg_sh)} | **{f(avg_so)}** | "
                f"{'—' if avg_pw is None else f'{avg_pw*100:.1f}%'} | "
                f"{f(avg_aw, p=True)} | {f(avg_al, p=True)} | "
                f"{f(ev_check, p=True)} |")

    lines += [
        "",
        "### Sortino + EV decomposition (added 2026-05-18)",
        "",
        "EV = P(win)·E[win] + P(loss)·E[loss].  EV check should ≈ mean_r.  "
        "Sortino penalizes only downside variance.",
        "",
        "| arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |",
        "|-----|---:|---:|---:|---:|---:|---:|",
        _ev_row("WITH brk_wall",    with_rows),
        _ev_row("WITHOUT brk_wall", without_rows),
        "",
    ]

    # 2026-05-18 evaluation upgrade: marginal contribution analysis
    # A = WITHOUT (baseline), B = WITH (proposed addition)
    if with_results and without_results:
        report = compute_marginal(without_results, with_results)
        lines.append("### Marginal contribution (WITH brk_wall vs WITHOUT)")
        lines.append(marginal_table(report,
                                    a_label="WITHOUT brk_wall",
                                    b_label="WITH brk_wall"))

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
    logger.info("Appended A/B report to {}", _BENCH_MD)


def main() -> None:
    with_rows,    with_results    = _run_arm(frozenset(), "WITH brk_wall")
    without_rows, without_results = _run_arm(frozenset({"brk_wall"}), "WITHOUT brk_wall")
    report = _format_report(with_rows, without_rows, with_results, without_results)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
