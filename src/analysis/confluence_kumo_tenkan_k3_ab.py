"""confluence_kumo_tenkan_k3_ab — 3-arm A/B sweeping K∈{1,3,5} for brk_kumo + brk_tenkan.

Operator follow-up (2026-05-18) to the K=5 strict probe: "for brk_kumo,
brk_tenkan K=3 is also evaluated?"  K=3 was NOT covered by
confluence_strict_k_ab; this script fills the gap.

Three arms, each differs only in the gate_lookback for the two
ichimoku _hi signs that brk_kumo_hi / brk_tenkan_hi contribute to the
confluence bullish set (chiko_hi unchanged across all arms):

  ARM K1: gate_lookback=1 (current production)
  ARM K3: gate_lookback=3 (operator's K=3 test, the brk_sma sweet spot)
  ARM K5: gate_lookback=5 (previously REJECTed at N=3; included for reference)

For each arm: 7 base signs + chiko_hi from DB + brk_kumo_hi/brk_tenkan_hi
recomputed in-memory at that K.

Decision rule: ship K=3 if Sharpe at N=3 ≥ K=1 AND no per-FY regression
that flips a prior positive year negative.

Read-only.  Output appended to docs/analysis/ichimoku_signs.md.
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from pathlib import Path

from loguru import logger

from src.analysis.confluence_ichimoku_ab import (
    _BASE_SIGNS,
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
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.signs import BrkKumoDetector, BrkTenkanDetector
from src.simulator.cache import DataCache

_DOC_PATH = Path("docs/analysis/ichimoku_signs.md")
_HEADER   = "## Confluence A/B — brk_kumo + brk_tenkan K-sweep (2026-05-18)"

_K_VALUES = [1, 3, 5]


def _build_k_fires(stock_caches, K: int):
    out = defaultdict(list)
    for code, cache in stock_caches.items():
        if not cache.bars:
            continue
        for det in (BrkKumoDetector(cache, side="hi", gate_lookback=K),
                    BrkTenkanDetector(cache, side="hi", gate_lookback=K)):
            sign = det._sign_type
            for bar_idx, _ in det._fire_events:
                d = cache.bars[bar_idx].dt.date()
                out[code].append((sign, d))
    return out


def _merge(*srcs):
    out = defaultdict(list)
    for src in srcs:
        for code, fires in src.items():
            out[code].extend(fires)
    return out


def _run_arm(label, cfg, fires, stock_caches, corr_maps, zs_maps):
    out = []
    for n_gate in _N_VALUES:
        cands = []
        for code in stock_caches:
            cands.extend(_candidates_for_stock_with_extra_valid(
                code, fires.get(code, []),
                stock_caches[code], corr_maps.get(code, {}),
                zs_maps.get(code, {}),
                cfg.start, cfg.end, n_gate,
                _VALID_BARS_EXTRA,
            ))
        results = run_simulation(cands, _EXIT_RULE, stock_caches, cfg.end)
        m = _metrics(results)
        logger.info("  [{}] N={}: {} trades, sharpe={:.2f}",
                    label, n_gate, m.n,
                    m.sharpe if not math.isnan(m.sharpe) else float("nan"))
        out.append(_arm_row_from_metrics(m, cfg.label, n_gate, len(cands)))
    return out


def _run_fy(cfg, base_fires, chiko_fires):
    logger.info("── {} ──", cfg.label)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return {K: [] for K in _K_VALUES}

    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE + 120)
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

    results: dict[int, list[_ArmRow]] = {}
    for K in _K_VALUES:
        k_fires = _build_k_fires(stock_caches, K)
        merged = _merge(base_fires, chiko_fires, k_fires)
        results[K] = _run_arm(f"K={K}", cfg, merged, stock_caches, corr_maps, zs_maps)
    return results


def _fmt(x):
    return "—" if x is None else f"{x:+.2f}"


def _format_report(by_K: dict[int, list[_ArmRow]]) -> str:
    by_K_n = {K: defaultdict(list) for K in _K_VALUES}
    for K, rows in by_K.items():
        for r in rows:
            by_K_n[K][r.n_gate].append(r)

    lines = [
        "",
        _HEADER,
        "",
        f"Probe run: {datetime.date.today()}.  brk_kumo_hi + brk_tenkan_hi "
        "fires recomputed in-memory at each K; baseline 7 signs + chiko_hi "
        "pulled from DB.  Strict whole-bar low-edge gate; only the "
        "gate_lookback K varies.",
        "",
        "- **K=1** = current production",
        "- **K=3** = operator's new test (brk_sma sweet spot)",
        "- **K=5** = strict (previously REJECTed at N≥3)",
        "",
    ]
    fys = sorted({r.fy for rows in by_K.values() for r in rows})
    for n_gate in _N_VALUES:
        lines += [
            f"### N ≥ {n_gate}",
            "",
            "| FY | K=1 trades | K=1 Sh | K=3 trades | K=3 Sh | K=5 trades | K=5 Sh | K3−K1 |",
            "|----|---:|---:|---:|---:|---:|---:|---:|",
        ]
        by_fy = {K: {r.fy: r for r in by_K_n[K][n_gate]} for K in _K_VALUES}
        for fy in fys:
            r1 = by_fy[1].get(fy); r3 = by_fy[3].get(fy); r5 = by_fy[5].get(fy)
            if not (r1 and r3 and r5):
                continue
            d = (r3.sharpe - r1.sharpe) if (r3.sharpe is not None and r1.sharpe is not None) else None
            lines.append(
                f"| {fy} | {r1.n_trades} | {_fmt(r1.sharpe)}"
                f" | {r3.n_trades} | {_fmt(r3.sharpe)}"
                f" | {r5.n_trades} | {_fmt(r5.sharpe)}"
                f" | {_fmt(d)} |"
            )
        lines.append("")

    lines += [
        "### Aggregate (FY-equal-weighted)",
        "",
        "| N | K | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        for K in _K_VALUES:
            rows = by_K_n[K][n_gate]
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
                f"| N≥{n_gate} | K={K} | {total_n} | **{_fmt(avg_sh)}** | {mr_s} | {wr_s} |"
            )
        lines.append("")

    # Sortino + EV decomposition (2026-05-18 evaluation upgrade)
    lines.append(_ev_decomp_table(
        [(f"K={K}", by_K[K]) for K in _K_VALUES],
        _N_VALUES,
    ))

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
    base_fires  = _load_fires(_BASE_SIGNS)
    chiko_fires = _load_fires(("chiko_hi",))
    logger.info("Loaded base 7 fires for {} stocks, chiko for {} stocks",
                len(base_fires), len(chiko_fires))

    by_K: dict[int, list[_ArmRow]] = {K: [] for K in _K_VALUES}
    for cfg in RS_FY_CONFIGS:
        fy_results = _run_fy(cfg, base_fires, chiko_fires)
        for K, rows in fy_results.items():
            by_K[K].extend(rows)

    report = _format_report(by_K)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
