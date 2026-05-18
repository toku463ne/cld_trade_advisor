"""confluence_strict_k_ab — 3-arm confluence A/B: baseline vs K=1 _hi vs K=5 _hi.

Operator (2026-05-18) requested strategy-level test of strict K=5
breakout gate.  Three arms:

  ARM A (baseline)        : 7 original signs (str_hold, ..., rev_nlo)
  ARM B (K=1, current)    : ARM A + brk_kumo_hi + brk_tenkan_hi + chiko_hi (K=1)
  ARM C (K=5, strict)     : ARM A + brk_kumo_hi + brk_tenkan_hi + chiko_hi (K=5)

For arms A/B fires are pulled from `SignBenchmarkEvent`.  For arm C the
3 _hi signs are recomputed in-memory at K=5 using the detector
classes' `gate_lookback=5` parameter (chiko unchanged — its strict-zone
gate is intrinsic and doesn't use gate_lookback).

Decision rule (same as confluence_ichimoku_ab):
  Ship K=5 if (a) avg Sharpe at N=3 ≥ K=1 Sharpe AND (b) ≥6/7 FYs non-negative.

Read-only.  Output: docs/analysis/ichimoku_signs.md (appended).
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.confluence_ichimoku_ab import (
    _BASE_SIGNS,
    _EXPANDED_SIGNS,
    _NEW_HI_SIGNS,
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

_DOC_PATH    = Path("docs/analysis/ichimoku_signs.md")
_HEADER      = "## Strict K=5 confluence A/B (2026-05-18)"


def _build_k5_fires(stock_caches: dict[str, DataCache]
                   ) -> dict[str, list[tuple[str, datetime.date]]]:
    """Run K=5 detectors in-memory for the 3 ichimoku _hi signs across all stocks."""
    out: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for code, cache in stock_caches.items():
        if not cache.bars:
            continue
        # brk_kumo_hi @ K=5
        det = BrkKumoDetector(cache, side="hi", gate_lookback=5)
        for bar_idx, _ in det._fire_events:
            d = cache.bars[bar_idx].dt.date()
            out[code].append(("brk_kumo_hi", d))
        # brk_tenkan_hi @ K=5
        det = BrkTenkanDetector(cache, side="hi", gate_lookback=5)
        for bar_idx, _ in det._fire_events:
            d = cache.bars[bar_idx].dt.date()
            out[code].append(("brk_tenkan_hi", d))
        # chiko_hi: NOT changed by K parameter — pull from DB later
    return out


def _merge_fires(*sources: dict[str, list[tuple[str, datetime.date]]]
                ) -> dict[str, list[tuple[str, datetime.date]]]:
    out: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for src in sources:
        for code, fires in src.items():
            out[code].extend(fires)
    return out


def _run_arm(arm_label: str, cfg, fires_by_stock, stock_caches,
             corr_maps, zs_maps) -> list[_ArmRow]:
    out: list[_ArmRow] = []
    for n_gate in _N_VALUES:
        all_cands: list[EntryCandidate] = []
        for code in stock_caches:
            cands = _candidates_for_stock_with_extra_valid(
                code, fires_by_stock.get(code, []),
                stock_caches[code], corr_maps.get(code, {}),
                zs_maps.get(code, {}),
                cfg.start, cfg.end, n_gate,
                _VALID_BARS_EXTRA if arm_label != "A" else {},
            )
            all_cands.extend(cands)
        results = run_simulation(all_cands, _EXIT_RULE, stock_caches, cfg.end)
        m = _metrics(results)
        logger.info("  [{}] N={}: {} trades, sharpe={:.2f}",
                    arm_label, n_gate, m.n,
                    m.sharpe if not math.isnan(m.sharpe) else float("nan"))
        out.append(_arm_row_from_metrics(m, cfg.label, n_gate, len(all_cands)))
    return out


def _run_fy(cfg, fires_a, fires_b_k1, chiko_hi_fires):
    """For each FY: load caches, compute K=5 fires in-memory, run 3 arms."""
    logger.info("── {} ──", cfg.label)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return [], [], []

    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE + 120)  # K=5 lookback + ichimoku
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

    # K=5 fires in-memory for brk_kumo_hi + brk_tenkan_hi
    fires_k5_new = _build_k5_fires(stock_caches)
    # chiko_hi fires (unchanged) pulled from DB
    chiko_only = {code: [(s, d) for (s, d) in v if s == "chiko_hi"]
                  for code, v in chiko_hi_fires.items()}
    # Arm C = base 7 + K=5 brk_kumo_hi + K=5 brk_tenkan_hi + DB chiko_hi
    fires_c = _merge_fires(fires_a, fires_k5_new, chiko_only)

    arm_a = _run_arm("A", cfg, fires_a,    stock_caches, corr_maps, zs_maps)
    arm_b = _run_arm("B", cfg, fires_b_k1, stock_caches, corr_maps, zs_maps)
    arm_c = _run_arm("C", cfg, fires_c,    stock_caches, corr_maps, zs_maps)
    return arm_a, arm_b, arm_c


def _format_report(a_rows, b_rows, c_rows) -> str:
    by_n_a = defaultdict(list); by_n_b = defaultdict(list); by_n_c = defaultdict(list)
    for r in a_rows: by_n_a[r.n_gate].append(r)
    for r in b_rows: by_n_b[r.n_gate].append(r)
    for r in c_rows: by_n_c[r.n_gate].append(r)

    lines = [
        "",
        _HEADER,
        "",
        f"Probe run: {datetime.date.today()}.  Three arms:",
        "",
        "- **A (baseline)** = 7 original signs",
        "- **B (K=1, current ship)** = baseline + brk_kumo_hi + brk_tenkan_hi + chiko_hi (K=1)",
        "- **C (K=5, strict)** = baseline + brk_kumo_hi(K=5) + brk_tenkan_hi(K=5) + chiko_hi",
        "",
    ]
    fys = sorted({r.fy for r in (a_rows + b_rows + c_rows)})
    for n_gate in _N_VALUES:
        lines += [
            f"### N ≥ {n_gate}",
            "",
            "| FY | A trades | A Sharpe | B trades | B Sharpe | C trades | C Sharpe | C−B Δ Sh |",
            "|----|---:|---:|---:|---:|---:|---:|---:|",
        ]
        a_rows_n = {r.fy: r for r in by_n_a[n_gate]}
        b_rows_n = {r.fy: r for r in by_n_b[n_gate]}
        c_rows_n = {r.fy: r for r in by_n_c[n_gate]}
        for fy in fys:
            ra = a_rows_n.get(fy); rb = b_rows_n.get(fy); rc = c_rows_n.get(fy)
            if not (ra and rb and rc):
                continue
            d_cb = (rc.sharpe - rb.sharpe) if (rc.sharpe is not None and rb.sharpe is not None) else None
            lines.append(
                f"| {fy} | {ra.n_trades} | {_fmt_sh(ra.sharpe)}"
                f" | {rb.n_trades} | {_fmt_sh(rb.sharpe)}"
                f" | {rc.n_trades} | {_fmt_sh(rc.sharpe)}"
                f" | {_fmt_sh(d_cb)} |"
            )
        lines.append("")

    # Aggregate
    lines += [
        "### Aggregate (FY-equal-weighted)",
        "",
        "| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|---|-----|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        for label, by_n in [("A baseline", by_n_a), ("B K=1 expanded", by_n_b), ("C K=5 strict", by_n_c)]:
            rows = by_n[n_gate]
            total_n = sum(r.n_trades for r in rows)
            sh = [r.sharpe for r in rows if r.sharpe is not None]
            mr = [r.mean_r for r in rows if r.mean_r is not None]
            wr = [r.win_rate for r in rows if r.win_rate is not None]
            avg_sh = statistics.mean(sh) if sh else None
            avg_mr = statistics.mean(mr) if mr else None
            avg_wr = statistics.mean(wr) if wr else None
            lines.append(
                f"| N≥{n_gate} | {label} | {total_n}"
                f" | **{_fmt_sh(avg_sh)}**"
                f" | {avg_mr*100:+.2f}%" if avg_mr is not None else f" | —"
                f" | {avg_wr*100:.0f}%" if avg_wr is not None else f" | —"
            )
        lines.append("")

    # Sortino + EV decomposition (2026-05-18 evaluation upgrade)
    lines.append(_ev_decomp_table(
        [("A baseline", a_rows), ("B K=1 expanded", b_rows), ("C K=5 strict", c_rows)],
        _N_VALUES,
    ))

    return "\n".join(lines)


def _fmt_sh(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}"


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
    fires_a    = _load_fires(_BASE_SIGNS)
    fires_b_k1 = _load_fires(_EXPANDED_SIGNS)
    # Pre-load chiko_hi fires (unchanged across K — chiko ignores gate_lookback)
    chiko_hi   = _load_fires(("chiko_hi",))
    logger.info("Loaded fires: A={} stocks, B={} stocks, chiko={} stocks",
                len(fires_a), len(fires_b_k1), len(chiko_hi))

    a_rows: list[_ArmRow] = []
    b_rows: list[_ArmRow] = []
    c_rows: list[_ArmRow] = []
    for cfg in RS_FY_CONFIGS:
        ra, rb, rc = _run_fy(cfg, fires_a, fires_b_k1, chiko_hi)
        a_rows.extend(ra); b_rows.extend(rb); c_rows.extend(rc)

    report = _format_report(a_rows, b_rows, c_rows)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
