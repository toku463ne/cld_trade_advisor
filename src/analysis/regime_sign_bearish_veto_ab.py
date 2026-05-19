"""regime_sign_bearish_veto_ab — Stage 0 + Stage 1 for "skip regime_sign when bearish ≥ 2".

Operator follow-up after [[project-confluence-bearish-veto-stage0-reject]]:
the bowl-shape (bearish=1 highest DR, bearish≥2 worst) was found on
confluence entries.  Does it transfer to regime_sign entries, and does
a loose-gate veto ("skip when bearish ≥ 2") improve the strategy?

This script combines Stage 0 (measurement) and Stage 1 (A/B) in one
run since the baseline arm produces both.

Arms
----
- A baseline = current regime_sign (no filter)
- B veto     = same, but PROPOSAL_FILTER drops any proposal whose
               stock × date has bearish_count ≥ 2

Bearish set (locked from confluence Stage 0):
    {rev_nhi, rev_hi, brk_kumo_lo, brk_tenkan_lo, chiko_lo}

Pre-registered ship gate (same shape as combined-drop A/B):
  - ΔSharpe (FY-equal-weighted) ≥ +0.30
  - ΔSortino ≥ +0.50
  - ≥ 5 / 7 FYs non-negative ΔSharpe (FY2019+FY2020 = 0 trades = 5 testable)
  - FY2024 + FY2025 both non-negative

If PASS, bootstrap CI is the required next step (per
[[project-rev-nhi-ui-only-salvage]] precedent).

Output: src/analysis/benchmark.md § regime_sign × bearish veto (2026-05-19)
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select

import src.analysis.regime_sign_backtest as rsb
from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis.exit_benchmark import _metrics
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.strategy.proposal import SignalProposal

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION  = "## regime_sign × bearish veto (2026-05-19)"

_BEARISH_SIGNS: tuple[str, ...] = (
    "rev_nhi", "rev_hi",
    "brk_kumo_lo", "brk_tenkan_lo", "chiko_lo",
)
_BEARISH_VALID_BARS = 5
_VETO_THRESHOLD = 2          # skip proposals when bearish_count ≥ this
_HOLDOUT_FYS = {"FY2024", "FY2025"}
_LOOKBACK_DAYS = 400         # enough lookback for the bearish_count map


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


def _load_bearish_fires() -> dict[str, list[tuple[str, datetime.date]]]:
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(_BEARISH_SIGNS))
        ).all()
    by_stock: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sign, stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        by_stock[stock].append((sign, d))
    logger.info("Loaded bearish fires for {} stocks ({} events)",
                len(by_stock), sum(len(v) for v in by_stock.values()))
    return by_stock


def _build_bearish_count_map_for_fy(cfg) -> dict[str, dict[datetime.date, int]]:
    """For each stock in the FY universe, return {date: bearish_count}.

    Uses the stock's own trading-date calendar (loaded from the cache).
    Counts distinct bearish signs whose valid window
    [fire, fire + _BEARISH_VALID_BARS] covers each date.
    """
    from src.analysis.confluence_strategy_backtest import _stocks_for_fy
    codes = _stocks_for_fy(cfg.stock_set)
    span_s = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS)
    span_e = cfg.end   + datetime.timedelta(days=60)
    s_dt = datetime.datetime.combine(span_s, datetime.time.min, tzinfo=datetime.timezone.utc)
    e_dt = datetime.datetime.combine(span_e, datetime.time.max, tzinfo=datetime.timezone.utc)
    # Per-stock trading-date calendar
    trading_dates: dict[str, list[datetime.date]] = {}
    with get_session() as s:
        for code in codes:
            c = DataCache(code, "1d")
            try:
                c.load(s, s_dt, e_dt)
            except Exception:
                continue
            if not c.bars:
                continue
            seen: set[datetime.date] = set()
            d_list: list[datetime.date] = []
            for b in c.bars:
                d = b.dt.date()
                if d not in seen:
                    seen.add(d)
                    d_list.append(d)
            d_list.sort()
            trading_dates[code] = d_list

    bear_fires = _load_bearish_fires()
    out: dict[str, dict[datetime.date, int]] = {}
    for code, dates in trading_dates.items():
        fires = bear_fires.get(code, [])
        date_to_idx = {d: i for i, d in enumerate(dates)}
        per_date_signs: dict[int, set[str]] = defaultdict(set)
        for sign, fd in fires:
            if fd not in date_to_idx:
                continue
            fi = date_to_idx[fd]
            for j in range(fi, min(fi + _BEARISH_VALID_BARS + 1, len(dates))):
                per_date_signs[j].add(sign)
        out[code] = {dates[i]: len(per_date_signs[i]) for i in range(len(dates))}
    return out


def _run_arm_baseline() -> tuple[list[_ArmRow], list, dict]:
    """Returns (rows, results, bearish_count_at_entry).

    bearish_count_at_entry: dict[(stock, entry_date)] → int  — used for
    Stage 0 bucket reporting.
    """
    logger.info("=== ARM A baseline ===")
    rsb.EXCLUDE_SIGNS  = frozenset()
    rsb.PROPOSAL_FILTER = None
    rows: list[_ArmRow] = []
    all_results: list = []
    bear_at_entry: dict[tuple[str, datetime.date], int] = {}
    for cfg in rsb.RS_FY_CONFIGS:
        # Per-FY bearish_count map
        bear_map = _build_bearish_count_map_for_fy(cfg)
        # Run baseline regime_sign
        res = rsb.run_fy(cfg)
        m = _metrics(res.results)
        rows.append(_row_from_metrics(cfg.label, res.n_proposals, m))
        all_results.extend(res.results)
        # Tag each trade by bearish_count at entry
        for r in res.results:
            cnt = bear_map.get(r.stock_code, {}).get(r.entry_date, 0)
            bear_at_entry[(r.stock_code, r.entry_date)] = cnt
        logger.info("  {}: n={} sharpe={}",
                    cfg.label, m.n,
                    f"{m.sharpe:+.2f}" if m.n > 0 and m.sharpe == m.sharpe else "—")
    return rows, all_results, bear_at_entry


def _run_arm_veto() -> tuple[list[_ArmRow], list]:
    logger.info("=== ARM B +bearish-veto (≥{}) ===", _VETO_THRESHOLD)
    rsb.EXCLUDE_SIGNS = frozenset()
    rows: list[_ArmRow] = []
    all_results: list = []
    for cfg in rsb.RS_FY_CONFIGS:
        bear_map = _build_bearish_count_map_for_fy(cfg)
        def _filter(p: SignalProposal, _bm=bear_map) -> bool:
            cnt = _bm.get(p.stock_code, {}).get(p.fired_at.date(), 0)
            return cnt < _VETO_THRESHOLD
        rsb.PROPOSAL_FILTER = _filter
        res = rsb.run_fy(cfg)
        rsb.PROPOSAL_FILTER = None
        m = _metrics(res.results)
        rows.append(_row_from_metrics(cfg.label, res.n_proposals, m))
        all_results.extend(res.results)
        logger.info("  {}: n={} sharpe={}",
                    cfg.label, m.n,
                    f"{m.sharpe:+.2f}" if m.n > 0 and m.sharpe == m.sharpe else "—")
    return rows, all_results


def _stats(returns: list[float]) -> dict:
    if not returns:
        return {"n": 0, "dr": None, "mean_r": None, "sharpe": None,
                "avg_win": None, "avg_loss": None}
    wins  = [r for r in returns if r > 0]
    loses = [r for r in returns if r <= 0]
    m = statistics.mean(returns)
    s = statistics.stdev(returns) if len(returns) >= 2 else 0.0
    sh = m / s * math.sqrt(252) if s > 0 else None
    return {"n": len(returns), "dr": len(wins) / len(returns),
            "mean_r": m, "sharpe": sh,
            "avg_win": statistics.mean(wins) if wins else 0.0,
            "avg_loss": statistics.mean(loses) if loses else 0.0}


def _fmt_s(v): return "—" if v is None else f"{v:+.2f}"
def _fmt_p(v): return "—" if v is None else f"{v*100:+.2f}%"


def _agg(rows: list[_ArmRow]) -> dict:
    sh = [r.sharpe   for r in rows if r.sharpe   is not None]
    so = [r.sortino  for r in rows if r.sortino  is not None]
    mr = [r.mean_r   for r in rows if r.mean_r   is not None]
    wr = [r.win_rate for r in rows if r.win_rate is not None]
    aw = [r.avg_win  for r in rows if r.avg_win  is not None]
    al = [r.avg_loss for r in rows if r.avg_loss is not None]
    return {"n_trades": sum(r.n_trades for r in rows),
            "sharpe":   statistics.mean(sh) if sh else None,
            "sortino":  statistics.mean(so) if so else None,
            "mean_r":   statistics.mean(mr) if mr else None,
            "win_rate": statistics.mean(wr) if wr else None,
            "avg_win":  statistics.mean(aw) if aw else None,
            "avg_loss": statistics.mean(al) if al else None}


def _format_report(base_rows, veto_rows, base_results, veto_results,
                   bear_at_entry) -> str:
    # Stage 0 — per-bucket on baseline
    bucket_rows: dict[str, list[float]] = defaultdict(list)
    bucket_hold: dict[str, list[float]] = defaultdict(list)
    for r in base_results:
        cnt = bear_at_entry.get((r.stock_code, r.entry_date), 0)
        bk = "0" if cnt == 0 else "1" if cnt == 1 else "≥2"
        bucket_rows[bk].append(r.return_pct)
        if rsb_fy_label(r.entry_date) in _HOLDOUT_FYS:
            bucket_hold[bk].append(r.return_pct)

    lines = [
        "",
        _SECTION,
        "",
        f"Probe run: {datetime.date.today()}.  Tests whether vetoing "
        f"regime_sign entries when bearish_count ≥ {_VETO_THRESHOLD} at "
        "proposal date improves the strategy.",
        "",
        f"- Bearish set: `{', '.join(_BEARISH_SIGNS)}`",
        f"- Bearish valid_bars: {_BEARISH_VALID_BARS}",
        f"- Veto threshold: skip proposals when bearish_count ≥ {_VETO_THRESHOLD}",
        "",
        "## Stage 0 — baseline trades stratified by bearish_count",
        "",
        "| bucket | n_pool | DR_pool | mean_r_pool | Sharpe_pool | n_hold | DR_hold | mean_r_hold |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def _row(s):
        if s["n"] == 0:
            return "0 | — | — | —"
        sh = s["sharpe"]
        sh_s = "—" if sh is None else f"{sh:+.2f}"
        return (f"{s['n']} | {s['dr']*100:.1f}% | "
                f"{s['mean_r']*100:+.2f}% | {sh_s}")

    for bk in ("0", "1", "≥2"):
        ps = _stats(bucket_rows.get(bk, []))
        hs = _stats(bucket_hold.get(bk, []))
        # Holdout has no Sharpe column in this view (kept compact)
        if hs["n"] == 0:
            hs_str = "0 | — | —"
        else:
            hs_str = (f"{hs['n']} | {hs['dr']*100:.1f}% | "
                      f"{hs['mean_r']*100:+.2f}%")
        lines.append(f"| bearish = {bk} | {_row(ps)} | {hs_str} |")

    # Pool row
    all_ps = _stats([r.return_pct for r in base_results])
    all_hs = _stats(
        [r.return_pct for r in base_results
         if rsb_fy_label(r.entry_date) in _HOLDOUT_FYS])
    sh_p = all_ps["sharpe"]
    sh_p_s = "—" if sh_p is None else f"{sh_p:+.2f}"
    if all_hs["n"] == 0:
        hold_str = "0 | — | —"
    else:
        hold_str = (f"{all_hs['n']} | {all_hs['dr']*100:.1f}% | "
                    f"{all_hs['mean_r']*100:+.2f}%")
    lines.append(
        f"| **pool** | {all_ps['n']} | {all_ps['dr']*100:.1f}% | "
        f"{all_ps['mean_r']*100:+.2f}% | {sh_p_s} | {hold_str} |")
    lines.append("")

    # Stage 1 — A/B aggregate
    base_agg = _agg(base_rows)
    veto_agg = _agg(veto_rows)
    d_sh = (veto_agg["sharpe"]  - base_agg["sharpe"])  if (veto_agg["sharpe"]  and base_agg["sharpe"])  else None
    d_so = (veto_agg["sortino"] - base_agg["sortino"]) if (veto_agg["sortino"] and base_agg["sortino"]) else None
    d_mr = (veto_agg["mean_r"]  - base_agg["mean_r"])  if (veto_agg["mean_r"]  and base_agg["mean_r"])  else None

    lines += [
        "## Stage 1 — A/B aggregate (FY-equal-weighted)",
        "",
        "| arm | n | Sharpe | Sortino | mean_r | win% | avg_win | avg_loss |",
        "|-----|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def _arow(lbl, a):
        wr = a["win_rate"]
        wr_s = "—" if wr is None else f"{wr*100:.1f}%"
        return (f"| {lbl} | {a['n_trades']} | **{_fmt_s(a['sharpe'])}** | "
                f"**{_fmt_s(a['sortino'])}** | {_fmt_p(a['mean_r'])} | "
                f"{wr_s} | "
                f"{_fmt_p(a['avg_win'])} | {_fmt_p(a['avg_loss'])} |")
    lines.append(_arow("A baseline",  base_agg))
    lines.append(_arow(f"B veto≥{_VETO_THRESHOLD}", veto_agg))
    lines += [
        "",
        f"**Deltas**: ΔSh = **{_fmt_s(d_sh)}**, ΔSo = **{_fmt_s(d_so)}**, "
        f"ΔmeanR = **{_fmt_p(d_mr)}**, Δn = {veto_agg['n_trades'] - base_agg['n_trades']:+}",
        "",
    ]

    # Per-FY table
    by_b = {r.fy: r for r in base_rows}
    by_v = {r.fy: r for r in veto_rows}
    lines += [
        "### Per-FY",
        "",
        "| FY | base n | base Sh | veto n | veto Sh | ΔSh | ΔmeanR |",
        "|----|---:|---:|---:|---:|---:|---:|",
    ]
    fy_deltas = []
    for fy in sorted(by_b):
        b = by_b[fy]
        v = by_v.get(fy)
        if v is None:
            continue
        ds = (v.sharpe - b.sharpe) if (v.sharpe is not None and b.sharpe is not None) else None
        dm = (v.mean_r - b.mean_r) if (v.mean_r is not None and b.mean_r is not None) else None
        fy_deltas.append((fy, ds, dm))
        lines.append(f"| {fy} | {b.n_trades} | {_fmt_s(b.sharpe)} | "
                     f"{v.n_trades} | {_fmt_s(v.sharpe)} | "
                     f"**{_fmt_s(ds)}** | {_fmt_p(dm)} |")
    lines.append("")

    # Gate
    fy_non_neg = sum(1 for _, ds, _ in fy_deltas if ds is not None and ds >= -0.001)
    fy_total   = len([1 for _, ds, _ in fy_deltas if ds is not None])
    hold_fys   = [(fy, ds) for fy, ds, _ in fy_deltas if fy in _HOLDOUT_FYS]
    hold_ok = all(ds is not None and ds >= -0.001 for _, ds in hold_fys) and len(hold_fys) == 2

    pass_sh = d_sh is not None and d_sh >= 0.30
    pass_so = d_so is not None and d_so >= 0.50
    pass_fy = fy_non_neg >= 5
    pass_all = pass_sh and pass_so and pass_fy and hold_ok
    verdict = "**PASS**" if pass_all else "**REJECT**"

    lines += [
        "## Pre-registered gate",
        "",
        f"- ΔSharpe ≥ +0.30 → {_fmt_s(d_sh)} ({'✓' if pass_sh else '✗'})",
        f"- ΔSortino ≥ +0.50 → {_fmt_s(d_so)} ({'✓' if pass_so else '✗'})",
        f"- ≥ 5/7 FYs non-negative → {fy_non_neg}/{fy_total} ({'✓' if pass_fy else '✗'})",
        f"- FY2024 + FY2025 both non-negative → {'✓' if hold_ok else '✗'}",
        "",
        f"## Verdict: {verdict}",
        "",
    ]
    if base_results and veto_results:
        mc = compute_marginal(base_results, veto_results)
        lines.append("## Marginal contribution: baseline → veto")
        lines.append(marginal_table(mc, a_label="baseline", b_label=f"veto≥{_VETO_THRESHOLD}"))
    return "\n".join(lines)


def rsb_fy_label(d: datetime.date) -> str:
    return f"FY{d.year}" if d.month >= 4 else f"FY{d.year - 1}"


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
    base_rows, base_results, bear_at_entry = _run_arm_baseline()
    veto_rows, veto_results = _run_arm_veto()
    rsb.PROPOSAL_FILTER = None

    report = _format_report(base_rows, veto_rows, base_results, veto_results,
                            bear_at_entry)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
