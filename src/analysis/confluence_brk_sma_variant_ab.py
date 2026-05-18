"""confluence_brk_sma_variant_ab — 3-arm A/B for brk_sma variant.

Operator (2026-05-18) requested strategy-level test of two brk_sma
variants against current production:

  ARM A: current (close, K=5) — shipped
  ARM B: operator variant (low, K=3)
  ARM C: control (close, K=3)

All other 9 signs in the bullish set stay the same.  Only brk_sma
fires differ between arms (recomputed in-memory using the appropriate
detector parameters).

Decision rule (matching prior A/Bs):
  Ship variant if avg Sharpe at N=3 ≥ baseline AND ≥6/7 FYs non-negative.

Read-only.  Output appended to docs/analysis/brk_sma_variant.md.
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
    _EXPANDED_SIGNS,
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
from src.signs import BrkSmaDetector
from src.simulator.cache import DataCache

_DOC_PATH = Path("docs/analysis/brk_sma_variant.md")
_HEADER   = "## Confluence A/B — brk_sma variant"

# (arm_label, gate_use_low, min_below_bars)
_ARM_CONFIGS = [
    ("A current (close,K=5)", False, 5),
    ("B operator (low,K=3)",  True,  3),
    ("C control (close,K=3)", False, 3),
]

# Other 9 signs in the bullish set (everything except brk_sma).
_OTHER_BULLISH = tuple(s for s in _EXPANDED_SIGNS if s != "brk_sma")


def _build_brk_sma_fires(stock_caches: dict[str, DataCache],
                         gate_use_low: bool, K: int
                        ) -> dict[str, list[tuple[str, datetime.date]]]:
    out: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for code, cache in stock_caches.items():
        if not cache.bars:
            continue
        det = BrkSmaDetector(cache, window=20, min_below_bars=K,
                             gate_use_low=gate_use_low)
        for bar_idx, _ in det._fire_events:
            d = cache.bars[bar_idx].dt.date()
            out[code].append(("brk_sma", d))
    return out


def _merge_fires(*sources) -> dict[str, list[tuple[str, datetime.date]]]:
    out: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for src in sources:
        for code, fires in src.items():
            out[code].extend(fires)
    return out


def _run_arm(arm_label, cfg, fires_by_stock, stock_caches, corr_maps, zs_maps):
    out: list[_ArmRow] = []
    for n_gate in _N_VALUES:
        all_cands: list[EntryCandidate] = []
        for code in stock_caches:
            cands = _candidates_for_stock_with_extra_valid(
                code, fires_by_stock.get(code, []),
                stock_caches[code], corr_maps.get(code, {}),
                zs_maps.get(code, {}),
                cfg.start, cfg.end, n_gate,
                _VALID_BARS_EXTRA,
            )
            all_cands.extend(cands)
        results = run_simulation(all_cands, _EXIT_RULE, stock_caches, cfg.end)
        m = _metrics(results)
        logger.info("  [{}] N={}: {} trades, sharpe={:.2f}",
                    arm_label, n_gate, m.n,
                    m.sharpe if not math.isnan(m.sharpe) else float("nan"))
        out.append(_ArmRow(
            fy=cfg.label, n_gate=n_gate, n_trades=m.n, n_props=len(all_cands),
            mean_r=m.mean_r if m.n > 0 else None,
            sharpe=m.sharpe if (m.n > 0 and not math.isnan(m.sharpe)) else None,
            win_rate=m.win_rate if m.n > 0 else None,
            hold_bars=m.hold_bars if m.n > 0 else None,
        ))
    return out


def _run_fy(cfg, other_fires):
    logger.info("── {} ──", cfg.label)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return [[] for _ in _ARM_CONFIGS]

    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE + 60)
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

    arm_results = []
    for arm_label, gate_use_low, K in _ARM_CONFIGS:
        brk_sma_fires = _build_brk_sma_fires(stock_caches, gate_use_low, K)
        merged = _merge_fires(other_fires, brk_sma_fires)
        arm_results.append(
            _run_arm(arm_label, cfg, merged, stock_caches, corr_maps, zs_maps)
        )
    return arm_results


def _format_report(rows_per_arm: list[list[_ArmRow]]) -> str:
    by_arm_n = [defaultdict(list) for _ in _ARM_CONFIGS]
    for arm_i, rows in enumerate(rows_per_arm):
        for r in rows:
            by_arm_n[arm_i][r.n_gate].append(r)

    lines = [
        "",
        _HEADER,
        "",
        f"Probe run: {datetime.date.today()}.  Bullish set fixed at 10 "
        "signs; only brk_sma fires differ per arm.",
        "",
    ]
    for arm_label, _gul, _K in _ARM_CONFIGS:
        lines.append(f"- **{arm_label}**")
    lines.append("")

    fys = sorted({r.fy for rows in rows_per_arm for r in rows})
    for n_gate in _N_VALUES:
        lines += [
            f"### N ≥ {n_gate}",
            "",
            "| FY | A trades | A Sh | B trades | B Sh | C trades | C Sh | B−A | C−A |",
            "|----|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        arm_n = [{r.fy: r for r in by_arm_n[i][n_gate]} for i in range(3)]
        for fy in fys:
            ra = arm_n[0].get(fy); rb = arm_n[1].get(fy); rc = arm_n[2].get(fy)
            if not (ra and rb and rc):
                continue
            dba = (rb.sharpe - ra.sharpe) if (rb.sharpe is not None and ra.sharpe is not None) else None
            dca = (rc.sharpe - ra.sharpe) if (rc.sharpe is not None and ra.sharpe is not None) else None
            lines.append(
                f"| {fy} | {ra.n_trades} | {_fmt_sh(ra.sharpe)}"
                f" | {rb.n_trades} | {_fmt_sh(rb.sharpe)}"
                f" | {rc.n_trades} | {_fmt_sh(rc.sharpe)}"
                f" | {_fmt_sh(dba)} | {_fmt_sh(dca)} |"
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
        for arm_i, (arm_label, _gul, _K) in enumerate(_ARM_CONFIGS):
            rows = by_arm_n[arm_i][n_gate]
            total_n = sum(r.n_trades for r in rows)
            sh = [r.sharpe for r in rows if r.sharpe is not None]
            mr = [r.mean_r for r in rows if r.mean_r is not None]
            wr = [r.win_rate for r in rows if r.win_rate is not None]
            avg_sh = statistics.mean(sh) if sh else None
            avg_mr = statistics.mean(mr) if mr else None
            avg_wr = statistics.mean(wr) if wr else None
            sh_s = _fmt_sh(avg_sh)
            mr_s = f"{avg_mr*100:+.2f}%" if avg_mr is not None else "—"
            wr_s = f"{avg_wr*100:.0f}%"  if avg_wr is not None else "—"
            lines.append(
                f"| N≥{n_gate} | {arm_label} | {total_n} | **{sh_s}** | {mr_s} | {wr_s} |"
            )
        lines.append("")
    return "\n".join(lines)


def _fmt_sh(x):
    return "—" if x is None else f"{x:+.2f}"


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
    # Load fires for the other 9 bullish signs (NOT brk_sma).
    other_fires = _load_fires(_OTHER_BULLISH)
    logger.info("Loaded other-9-signs fires for {} stocks", len(other_fires))

    rows_per_arm: list[list[_ArmRow]] = [[] for _ in _ARM_CONFIGS]
    for cfg in RS_FY_CONFIGS:
        fy_arms = _run_fy(cfg, other_fires)
        for i, fy_rows in enumerate(fy_arms):
            rows_per_arm[i].extend(fy_rows)

    report = _format_report(rows_per_arm)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
