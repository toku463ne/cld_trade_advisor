"""confluence_trend_score_floor_ab — Stage 1 path A (floor) for confluence.

Stage 0 ([[project-trend-score-stage0]]) identified two confluence-bullish
signs whose D1-D2 (trend_score < ~25) decile is materially weaker than
their D3-D10 average: **brk_kumo_hi** and **chiko_hi**.  This A/B tests
whether dropping those weak-context fires lifts the confluence
strategy.

Arms
----
- ARM A (baseline) = current 10-sign bullish set, all DB fires kept.
- ARM B (+floor)   = same 10-sign set, but DROP fires of
                     {brk_kumo_hi, chiko_hi} when trend_score at fire
                     date is < 25 (floor sign-specific filter).

Score
-----
- trend_score from `src.analysis._trend_score.compute_trend_score`
- Per-stock 250-bar rolling percentile rank over 5 features.
- If the score is missing (cache too short), the fire is treated as
  pass (we don't drop fires we can't score — keeps A & B closer to
  same-event evaluation; same-sign baseline always kept).

Read-only.  Output: docs/analysis/trend_score_stage1.md
§ Confluence floor A/B (brk_kumo_hi, chiko_hi).

Pre-registered ship gate (locked 2026-05-19 before run):
  - avg Sharpe at N=3 in B ≥ A
  - ≥ 5 / 7 FYs non-negative ΔSharpe
  - FY2024 + FY2025 both non-negative (holdout requirement)
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from pathlib import Path

from loguru import logger

from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis._trend_score import build_score_map
from src.analysis.confluence_ichimoku_ab import (
    _EXPANDED_SIGNS,
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
    _EXIT_RULE,
    _N_VALUES,
)
from src.analysis.exit_benchmark import _metrics
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_DOC_PATH = Path("docs/analysis/trend_score_stage1.md")
_SECTION  = "## Confluence floor A/B (brk_kumo_hi, chiko_hi ≥ 25)"

# Score cache needs ~500 bars before FY start for the percentile rank window
# to be valid at FY-start dates.  900 calendar days ≈ 600 trading days.
_LOOKBACK_DAYS_CACHE = 900
_FLOOR_SIGNS  = ("brk_kumo_hi", "chiko_hi")
_FLOOR_SCORE  = 25.0


def _filter_floor(
    fires: dict[str, list[tuple[str, datetime.date]]],
    score_map: dict[str, dict[datetime.date, float]],
) -> tuple[dict[str, list[tuple[str, datetime.date]]], int, int]:
    """Return (filtered_fires, n_dropped_floor, n_kept_floor)."""
    out: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    n_dropped = 0
    n_kept_floor = 0
    for code, lst in fires.items():
        ss = score_map.get(code, {})
        for sign, d in lst:
            if sign in _FLOOR_SIGNS:
                ts = ss.get(d)
                if ts is not None and ts < _FLOOR_SCORE:
                    n_dropped += 1
                    continue
                if ts is not None:
                    n_kept_floor += 1
            out[code].append((sign, d))
    return dict(out), n_dropped, n_kept_floor


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


def _run_fy(cfg, base_fires):
    logger.info("── {} ──", cfg.label)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return [], [], {}, {}, 0, 0

    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE)
    span_end   = cfg.end   + datetime.timedelta(days=60)
    with get_session() as s:
        n225 = DataCache("^N225", "1d")
        n225.load(s,
            datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
            datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc),
        )
        stock_caches: dict[str, DataCache] = {}
        for code in codes:
            c = DataCache(code, "1d")
            c.load(s,
                datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
                datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            if c.bars:
                stock_caches[code] = c
    logger.info("  {} stock caches", len(stock_caches))

    corr_maps = {c: _build_corr_map(stock_caches[c], n225) for c in stock_caches}
    zs_maps   = {c: _build_zs_map(stock_caches[c], n225) for c in stock_caches}

    score_map = build_score_map(stock_caches)
    n_scored = sum(len(v) for v in score_map.values())
    logger.info("  score_map: {} (stock, date) entries across {} stocks",
                n_scored, sum(1 for v in score_map.values() if v))

    b_fires, n_drop, n_keep = _filter_floor(base_fires, score_map)
    logger.info("  floor filter: dropped {} fires (kept {} above floor), "
                "{} floor-sign total events scored",
                n_drop, n_keep, n_drop + n_keep)

    a_rows, a_results = _run_arm("A baseline", cfg, base_fires,
        stock_caches, corr_maps, zs_maps, _VALID_BARS_EXTRA)
    b_rows, b_results = _run_arm("B +floor", cfg, b_fires,
        stock_caches, corr_maps, zs_maps, _VALID_BARS_EXTRA)
    return a_rows, b_rows, a_results, b_results, n_drop, n_keep


def _fmt(x):
    return "—" if x is None else f"{x:+.2f}"


def _format_report(a_rows, b_rows,
                   a_results_all, b_results_all,
                   total_dropped, total_kept_floor) -> str:
    by_n_a = defaultdict(list); by_n_b = defaultdict(list)
    for r in a_rows: by_n_a[r.n_gate].append(r)
    for r in b_rows: by_n_b[r.n_gate].append(r)

    lines = [
        "",
        _SECTION,
        "",
        f"Probe run: {datetime.date.today()}.  Stage 1 path A for "
        "trend_score: drop floor-sign fires when trend_score < "
        f"{_FLOOR_SCORE:.0f}.",
        "",
        f"- **Floor signs**: {', '.join(_FLOOR_SIGNS)}",
        f"- **Floor**: trend_score < {_FLOOR_SCORE:.0f} → fire dropped",
        f"- **Score**: 5-feature 250-bar pct-rank per stock "
        "(`src.analysis._trend_score`)",
        f"- **Floor fires dropped (pooled across FYs)**: {total_dropped} of "
        f"{total_dropped + total_kept_floor} scored floor-sign fires "
        f"({(100*total_dropped/(total_dropped+total_kept_floor)) if (total_dropped+total_kept_floor) else 0:.1f}%)",
        "",
    ]
    fys = sorted({r.fy for r in (a_rows + b_rows)})
    for n_gate in _N_VALUES:
        lines += [
            f"### N ≥ {n_gate}",
            "",
            "| FY | A trades | A Sh | B trades | B Sh | ΔSh | Δtrades |",
            "|----|---:|---:|---:|---:|---:|---:|",
        ]
        a_by_fy = {r.fy: r for r in by_n_a[n_gate]}
        b_by_fy = {r.fy: r for r in by_n_b[n_gate]}
        for fy in fys:
            ra = a_by_fy.get(fy); rb = b_by_fy.get(fy)
            if not (ra and rb):
                continue
            d = (rb.sharpe - ra.sharpe) if (rb.sharpe is not None and ra.sharpe is not None) else None
            d_n = rb.n_trades - ra.n_trades
            lines.append(
                f"| {fy} | {ra.n_trades} | {_fmt(ra.sharpe)}"
                f" | {rb.n_trades} | {_fmt(rb.sharpe)} | **{_fmt(d)}** | {d_n:+} |"
            )
        lines.append("")

    lines += [
        "### Aggregate (FY-equal-weighted)",
        "",
        "| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|---|-----|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        for label, by_n in [("A baseline", by_n_a), ("B +floor", by_n_b)]:
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
                f"| N≥{n_gate} | {label} | {total_n} | **{_fmt(avg_sh)}** | {mr_s} | {wr_s} |"
            )
        lines.append("")

    lines.append(_ev_decomp_table(
        [("A baseline", a_rows), ("B +floor", b_rows)],
        _N_VALUES,
    ))

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
        lines.append(marginal_table(report,
                                    a_label="A baseline",
                                    b_label="B +floor"))

    lines += [
        "",
        "### Ship gate",
        "",
        "Pre-registered (locked before run):",
        "- avg Sharpe at N=3 in B ≥ A",
        "- ≥ 5 / 7 FYs non-negative ΔSharpe",
        "- FY2024 + FY2025 both non-negative ΔSharpe (holdout)",
        "",
    ]
    return "\n".join(lines)


def _append_to_doc(md: str) -> None:
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _DOC_PATH.read_text() if _DOC_PATH.exists() else \
               "# trend_score Stage 1 — A/B reports\n"
    if _SECTION in existing:
        idx = existing.index(_SECTION)
        rest = existing[idx + len(_SECTION):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _DOC_PATH.write_text(existing.rstrip() + "\n" + md)
    logger.info("Appended report to {}", _DOC_PATH)


def main() -> None:
    base_fires = _load_fires(_EXPANDED_SIGNS)
    logger.info("Loaded baseline fires for {} stocks", len(base_fires))

    a_rows: list[_ArmRow] = []
    b_rows: list[_ArmRow] = []
    a_results_all: list[tuple[int, list]] = []
    b_results_all: list[tuple[int, list]] = []
    total_dropped = 0
    total_kept_floor = 0
    for cfg in RS_FY_CONFIGS:
        ra, rb, ra_res, rb_res, n_drop, n_keep = _run_fy(cfg, base_fires)
        a_rows.extend(ra); b_rows.extend(rb)
        for n_gate, results in ra_res.items():
            a_results_all.append((n_gate, results))
        for n_gate, results in rb_res.items():
            b_results_all.append((n_gate, results))
        total_dropped    += n_drop
        total_kept_floor += n_keep

    report = _format_report(a_rows, b_rows,
                            a_results_all, b_results_all,
                            total_dropped, total_kept_floor)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
