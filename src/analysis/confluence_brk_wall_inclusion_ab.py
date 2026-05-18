"""confluence_brk_wall_inclusion_ab — test adding brk_wall@K=15 to confluence bullish set.

Operator follow-up after brk_wall K-sweep showed K=15 is marginally
better per-fire.  Confluence A/B is the binding test (brk_wall was
prior excluded for K=10 due to confluence dilution finding).

Two arms:
  ARM A (baseline) = current 10-sign bullish set (no brk_wall)
  ARM B (+brk_wall@K=15) = baseline + brk_wall@K=15 (in-memory fires)

For chiko/kumo/tenkan _hi signs at K=1 (current production): fires
pulled from DB.  brk_wall fires recomputed in-memory with K=15.

Decision rule: ship brk_wall inclusion if Sharpe at N=3 ≥ baseline
AND ≥6/7 FYs non-negative.
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from pathlib import Path

from loguru import logger

from src.analysis.confluence_ichimoku_ab import (
    _EXPANDED_SIGNS,    # current 10-sign bullish set
    _VALID_BARS_EXTRA,
    _candidates_for_stock_with_extra_valid,
    _load_fires,
)
from src.analysis.confluence_strategy_backtest import (
    _ArmRow,
    _build_corr_map,
    _stocks_for_fy,
    _LOOKBACK_DAYS_CACHE,
    _EXIT_RULE,
    _N_VALUES,
)
from src.analysis.exit_benchmark import _metrics
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.signs import BrkWallDetector
from src.simulator.cache import DataCache

_DOC_PATH = Path("docs/analysis/brk_wall_tuning.md")
_HEADER   = "## Confluence inclusion A/B — brk_wall@K=15"

_BRK_WALL_K = 15

# Add brk_wall to valid_bars dict for this A/B (vb=5 to match other breakouts).
_VALID_BARS_EXTRA_WITH_WALL = {**_VALID_BARS_EXTRA, "brk_wall": 5}


def _build_brk_wall_fires(stock_caches: dict[str, DataCache]
                         ) -> dict[str, list[tuple[str, datetime.date]]]:
    out = defaultdict(list)
    for code, cache in stock_caches.items():
        if not cache.bars:
            continue
        det = BrkWallDetector(cache, K=_BRK_WALL_K)
        for bar_idx, _ in det._fire_events:
            d = cache.bars[bar_idx].dt.date()
            out[code].append(("brk_wall", d))
    return out


def _merge(*srcs):
    out = defaultdict(list)
    for src in srcs:
        for code, fires in src.items():
            out[code].extend(fires)
    return out


def _run_arm(label, cfg, fires, stock_caches, corr_maps, zs_maps, valid_bars_extra):
    out = []
    for n_gate in _N_VALUES:
        cands = []
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
        out.append(_ArmRow(
            fy=cfg.label, n_gate=n_gate, n_trades=m.n, n_props=len(cands),
            mean_r=m.mean_r if m.n > 0 else None,
            sharpe=m.sharpe if (m.n > 0 and not math.isnan(m.sharpe)) else None,
            win_rate=m.win_rate if m.n > 0 else None,
            hold_bars=m.hold_bars if m.n > 0 else None,
        ))
    return out


def _run_fy(cfg, base_fires):
    logger.info("── {} ──", cfg.label)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return [], []

    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE + 180)
    span_end   = cfg.end   + datetime.timedelta(days=60)
    with get_session() as s:
        n225 = DataCache("^N225", "1d")
        n225.load(s,
            datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
            datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc),
        )
        stock_caches = {}
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

    # Build brk_wall@K=15 fires in-memory
    wall_fires = _build_brk_wall_fires(stock_caches)

    arm_a = _run_arm("A baseline", cfg, base_fires,
                     stock_caches, corr_maps, zs_maps, _VALID_BARS_EXTRA)
    arm_b = _run_arm("B +brk_wall", cfg, _merge(base_fires, wall_fires),
                     stock_caches, corr_maps, zs_maps, _VALID_BARS_EXTRA_WITH_WALL)
    return arm_a, arm_b


def _fmt(x):
    return "—" if x is None else f"{x:+.2f}"


def _format_report(a_rows, b_rows) -> str:
    by_n_a = defaultdict(list); by_n_b = defaultdict(list)
    for r in a_rows: by_n_a[r.n_gate].append(r)
    for r in b_rows: by_n_b[r.n_gate].append(r)

    lines = [
        "",
        _HEADER,
        "",
        f"Probe run: {datetime.date.today()}.  Tests whether adding "
        f"brk_wall@K={_BRK_WALL_K} to the current 10-sign bullish set "
        "improves the confluence strategy.",
        "",
        "- **A baseline** = current 10-sign bullish set",
        f"- **B +brk_wall** = baseline + brk_wall@K={_BRK_WALL_K} (in-memory)",
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
                f" | {rb.n_trades} | {_fmt(rb.sharpe)} | {_fmt(d)} |"
            )
        lines.append("")

    lines += [
        "### Aggregate (FY-equal-weighted)",
        "",
        "| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|---|-----|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        for label, by_n in [("A baseline", by_n_a), (f"B +brk_wall(K={_BRK_WALL_K})", by_n_b)]:
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
    return "\n".join(lines)


def _append_to_doc(md: str) -> None:
    existing = _DOC_PATH.read_text() if _DOC_PATH.exists() else ""
    if _HEADER in existing:
        idx = existing.index(_HEADER)
        rest = existing[idx + len(_HEADER):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _DOC_PATH.write_text(existing.rstrip() + "\n" + md)
    logger.info("Appended report to {}", _DOC_PATH)


def main() -> None:
    base_fires = _load_fires(_EXPANDED_SIGNS)  # current 10-sign bullish set
    logger.info("Loaded baseline fires for {} stocks", len(base_fires))

    a_rows: list[_ArmRow] = []
    b_rows: list[_ArmRow] = []
    for cfg in RS_FY_CONFIGS:
        ra, rb = _run_fy(cfg, base_fires)
        a_rows.extend(ra)
        b_rows.extend(rb)

    report = _format_report(a_rows, b_rows)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
