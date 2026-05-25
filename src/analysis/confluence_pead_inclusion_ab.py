"""confluence_pead_inclusion_ab — test adding pead_up (forecast up-revision) to confluence.

Follow-up to the PEAD forecast-revision study (ACCEPT, all 7 gates; binding N225
cohort gate +2.51% — see docs/analysis/pead_forecast_revision_results.md). The
purpose of that study was "add a sign to the confluence strategy"; this is the
binding strategy-level test of doing so.

`pead_up` is an EVENT sign, not a price detector: it fires on the tradable entry
day of a management-forecast **up-revision** (ΔFEPS > 0, same-FY pairing, after-close
→ next session), and stays valid for **60 trading bars** (~3 months — the
pre-registered PEAD drift horizon, and the operator's stated PEAD validity). Fires
are built in-memory from `jq_statements` (cohort = the confluence universe, mapped
via `to_yf_code`); nothing is written to `sign_benchmark`.

Two arms (mirrors confluence_brk_wall_inclusion_ab):
  ARM A (baseline)  = current 10-sign bullish set
  ARM B (+pead_up)  = baseline + pead_up (vb=60), one extra confluence vote

Decision rule (point-estimate prerequisite; NOT the final bar): inclusion is worth
escalating to the capital-aware book + paired fill-order null only if Sharpe at N=3
≥ baseline AND ≥6/8 FYs non-negative (B−A) AND OOS FY2025 (B−A) ≥ 0. The fill-order
null remains the binding gate for any ship (see project_confluence_fill_order_null).

Read-only.  Output: docs/analysis/pead_confluence_inclusion_ab.md
Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_pead_inclusion_ab
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis.confluence_ichimoku_ab import (
    _EXPANDED_SIGNS,    # current 10-sign bullish set
    _VALID_BARS_EXTRA,
    _candidates_for_stock_with_extra_valid,
    _load_fires,
)
from src.analysis.confluence_strategy_backtest import (
    _ArmRow,
    _arm_row_from_metrics,
    _build_corr_map,
    _ev_decomp_table,
    _stocks_for_fy,
    _LOOKBACK_DAYS_CACHE,
    _EXIT_RULE,
    _N_VALUES,
)
from src.analysis.exit_benchmark import _metrics
from src.analysis.pead_forecast_revision import (
    Disclosure, doc_basis, pair_same_fy_revisions, tradable_entry_day,
)
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.data.jquants_collector import to_yf_code
from src.data.jquants_models import JqStatement
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_DOC_PATH = Path("docs/analysis/pead_confluence_inclusion_ab.md")
_HEADER   = "## Confluence inclusion A/B — pead_up (forecast up-revision, vb=60)"

_PEAD_VALID_BARS = 60   # ~3-month PEAD drift horizon (pre-registered H=60)
_VALID_BARS_EXTRA_WITH_PEAD = {**_VALID_BARS_EXTRA, "pead_up": _PEAD_VALID_BARS}


def _load_pead_statements() -> dict[str, list[tuple]]:
    """All jq_statements rows, grouped by the yfinance-form stock code (to_yf_code)."""
    with get_session() as s:
        rows = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)
        ).all()
    by_yf: dict[str, list[tuple]] = defaultdict(list)
    for code, dd, dt, fy, feps, tod in rows:
        by_yf[to_yf_code(code)].append((dd, dt, fy, feps, tod))
    return by_yf


def _build_pead_up_fires(stock_caches: dict[str, DataCache],
                         stmts_by_yf: dict[str, list[tuple]]
                         ) -> dict[str, list[tuple[str, datetime.date]]]:
    """One ('pead_up', entry_day) fire per same-FY UP-revision (ΔFEPS > 0).

    Entry day = first trading day on/after the after-close-shifted disclosure, on the
    stock's own calendar. Forecast-revision rows inherit the code's modal accounting
    basis so they pair into the same-FY chain (same as the PEAD driver).
    """
    out: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for yf, cache in stock_caches.items():
        rows = stmts_by_yf.get(yf)
        if not rows or not cache.bars:
            continue
        cal = sorted({b.dt.date() for b in cache.bars})
        bases = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = bases.most_common(1)[0][0] if bases else None
        discs = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                 for dd, dt, fy, feps, tod in rows]
        for prev, curr in pair_same_fy_revisions(discs):
            if curr.forecast_eps <= prev.forecast_eps:      # up-revisions only
                continue
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is not None:
                out[yf].append(("pead_up", entry))
    return out


def _merge(*srcs):
    out = defaultdict(list)
    for src in srcs:
        for code, fires in src.items():
            out[code].extend(fires)
    return out


def _run_arm(label, cfg, fires, stock_caches, corr_maps, zs_maps, valid_bars_extra):
    out_rows: list[_ArmRow] = []
    out_results: dict[int, list] = {}
    for n_gate in _N_VALUES:
        cands: list[EntryCandidate] = []
        for code in stock_caches:
            cands.extend(_candidates_for_stock_with_extra_valid(
                code, fires.get(code, []),
                stock_caches[code], corr_maps.get(code, {}),
                zs_maps.get(code, {}),
                cfg.start, cfg.end, n_gate,
                valid_bars_extra,
            ))
        results = run_simulation(cands, _EXIT_RULE, stock_caches, cfg.end)
        m = _metrics(results)
        logger.info("  [{}] N={}: {} trades, sharpe={:.2f}",
                    label, n_gate, m.n,
                    m.sharpe if not math.isnan(m.sharpe) else float("nan"))
        out_rows.append(_arm_row_from_metrics(m, cfg.label, n_gate, len(cands)))
        out_results[n_gate] = list(results)
    return out_rows, out_results


def _run_fy(cfg, base_fires, stmts_by_yf):
    logger.info("── {} ──", cfg.label)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return [], [], {}, {}

    # +180d lookback so up-revision fires up to ~60 trading bars before fy_start can
    # still be valid into the early FY (pead_up validity reaches back further than price signs).
    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE + 180)
    span_end   = cfg.end   + datetime.timedelta(days=60)
    with get_session() as s:
        n225 = DataCache("^N225", "1d")
        n225.load(s,
            datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
            datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc))
        stock_caches: dict[str, DataCache] = {}
        for code in codes:
            c = DataCache(code, "1d")
            c.load(s,
                datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
                datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc))
            if c.bars:
                stock_caches[code] = c
    logger.info("  {} stock caches", len(stock_caches))

    corr_maps = {c: _build_corr_map(stock_caches[c], n225) for c in stock_caches}
    zs_maps   = {c: _build_zs_map(stock_caches[c], n225) for c in stock_caches}

    pead_fires = _build_pead_up_fires(stock_caches, stmts_by_yf)
    n_pead = sum(len(v) for v in pead_fires.values())
    logger.info("  {} pead_up up-revision fires across {} stocks", n_pead, len(pead_fires))

    a_rows, a_res = _run_arm("A baseline", cfg, base_fires,
                             stock_caches, corr_maps, zs_maps, _VALID_BARS_EXTRA)
    b_rows, b_res = _run_arm("B +pead_up", cfg, _merge(base_fires, pead_fires),
                             stock_caches, corr_maps, zs_maps, _VALID_BARS_EXTRA_WITH_PEAD)
    return a_rows, b_rows, a_res, b_res


def _fmt(x):
    return "—" if x is None else f"{x:+.2f}"


def _format_report(a_rows, b_rows, a_results_all, b_results_all) -> str:
    by_n_a: dict[int, list[_ArmRow]] = defaultdict(list)
    by_n_b: dict[int, list[_ArmRow]] = defaultdict(list)
    for r in a_rows:
        by_n_a[r.n_gate].append(r)
    for r in b_rows:
        by_n_b[r.n_gate].append(r)

    lines = [
        "",
        _HEADER,
        "",
        f"Probe run: {datetime.date.today()}.  Tests whether adding **pead_up** "
        "(management-forecast up-revision, valid 60 trading bars) as an 11th "
        "confluence vote improves the shipped strategy.",
        "",
        "- **A baseline** = current 10-sign bullish set",
        f"- **B +pead_up** = baseline + pead_up (vb={_PEAD_VALID_BARS}, in-memory from jq_statements)",
        "",
        "Per-trade Sharpe (matches the brk_sma / ichimoku inclusion-A/B precedents). If B "
        "wins at N=3 with per-FY robustness + OOS, escalate to the capital-aware 6-slot book "
        "+ paired fill-order null (the binding ship gate).",
        "",
    ]
    fys = sorted({r.fy for r in (a_rows + b_rows)})
    for n_gate in _N_VALUES:
        lines += [
            f"### N ≥ {n_gate}",
            "",
            "| FY | A trades | A Sh | B trades | B Sh | B−A |",
            "|----|---:|---:|---:|---:|---:|",
        ]
        a_by_fy = {r.fy: r for r in by_n_a[n_gate]}
        b_by_fy = {r.fy: r for r in by_n_b[n_gate]}
        for fy in fys:
            ra = a_by_fy.get(fy); rb = b_by_fy.get(fy)
            if not (ra and rb):
                continue
            d = (rb.sharpe - ra.sharpe) if (rb.sharpe is not None and ra.sharpe is not None) else None
            lines.append(
                f"| {fy} | {ra.n_trades} | {_fmt(ra.sharpe)}"
                f" | {rb.n_trades} | {_fmt(rb.sharpe)} | {_fmt(d)} |")
        lines.append("")

    lines += [
        "### Aggregate (FY-equal-weighted)",
        "",
        "| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|---|-----|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        for label, by_n in [("A baseline", by_n_a), ("B +pead_up", by_n_b)]:
            rows = by_n[n_gate]
            total_n = sum(r.n_trades for r in rows)
            sh = [r.sharpe for r in rows if r.sharpe is not None]
            mr = [r.mean_r for r in rows if r.mean_r is not None]
            wr = [r.win_rate for r in rows if r.win_rate is not None]
            avg_sh = statistics.mean(sh) if sh else None
            avg_mr = statistics.mean(mr) if mr else None
            avg_wr = statistics.mean(wr) if wr else None
            mr_s = f"{avg_mr*100:+.2f}%" if avg_mr is not None else "—"
            wr_s = f"{avg_wr*100:.0f}%"  if avg_wr is not None else "—"
            lines.append(
                f"| N≥{n_gate} | {label} | {total_n} | **{_fmt(avg_sh)}** | {mr_s} | {wr_s} |")
        lines.append("")

    lines.append(_ev_decomp_table([("A baseline", a_rows), ("B +pead_up", b_rows)], _N_VALUES))

    a_by_n: dict[int, list] = defaultdict(list)
    b_by_n: dict[int, list] = defaultdict(list)
    for n_gate, res in a_results_all:
        a_by_n[n_gate].extend(res)
    for n_gate, res in b_results_all:
        b_by_n[n_gate].extend(res)
    for n_gate in _N_VALUES:
        if not a_by_n.get(n_gate) or not b_by_n.get(n_gate):
            continue
        report = compute_marginal(a_by_n[n_gate], b_by_n[n_gate])
        lines.append(f"\n#### Marginal contribution at N≥{n_gate}")
        lines.append(marginal_table(report, a_label="A baseline", b_label="B +pead_up"))

    return "\n".join(lines)


def _append_to_doc(md: str) -> None:
    existing = _DOC_PATH.read_text() if _DOC_PATH.exists() else ""
    if _HEADER in existing:
        idx = existing.index(_HEADER)
        rest = existing[idx + len(_HEADER):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(existing.rstrip() + "\n" + md.lstrip("\n"))
    logger.info("Appended report to {}", _DOC_PATH)


def main() -> None:
    base_fires = _load_fires(_EXPANDED_SIGNS)
    stmts_by_yf = _load_pead_statements()
    logger.info("Loaded baseline fires ({} stocks) + statements ({} codes)",
                len(base_fires), len(stmts_by_yf))

    a_rows: list[_ArmRow] = []
    b_rows: list[_ArmRow] = []
    a_results_all: list[tuple[int, list]] = []
    b_results_all: list[tuple[int, list]] = []
    for cfg in RS_FY_CONFIGS:
        ra, rb, ra_res, rb_res = _run_fy(cfg, base_fires, stmts_by_yf)
        a_rows.extend(ra)
        b_rows.extend(rb)
        for n_gate, results in ra_res.items():
            a_results_all.append((n_gate, results))
        for n_gate, results in rb_res.items():
            b_results_all.append((n_gate, results))

    report = _format_report(a_rows, b_rows, a_results_all, b_results_all)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
