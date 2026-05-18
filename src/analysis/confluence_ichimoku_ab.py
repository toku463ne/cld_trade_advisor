"""confluence_ichimoku_ab — A/B for adding ichimoku _hi signs to bullish confluence.

Two arms:
  - **Baseline (7 signs)**: current shipped ConfluenceSignStrategy set
    [str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo]
  - **Expanded (10 signs)**: baseline + brk_kumo_hi + brk_tenkan_hi + chiko_hi

Reuses the per-FY runner from `confluence_strategy_backtest` — exact same
exit (ZsTpSl 2.0/2.0/0.3), portfolio cap, two-bar fill, cooldown.  Sweeps
N ∈ {1, 2, 3} for both arms.

The 3 added _hi signs are bullish-direction by construction and passed
the per-sign canonical gate (DR > 53% in FY2025 OOS).  The question this
script answers: does adding them as confluence inputs *improve* the
strategy's Sharpe / mean_r, or do they dilute (like brk_wall did)?

Decision rule: ship the expanded set only if avg Sharpe at N=3 ≥ baseline
N=3 and ≥6 of 7 FYs remain non-negative.

Read-only.  Output: src/analysis/benchmark.md
§ Confluence A/B: Ichimoku _hi signs.
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

from src.analysis.confluence_strategy_backtest import (
    _ArmRow,
    _arm_row_from_metrics,
    _build_corr_map,
    _candidates_for_stock,
    _ev_decomp_table,
    _stocks_for_fy,
    _LOOKBACK_DAYS_CACHE,
    _EXIT_RULE,
    _N_VALUES,
)
from src.analysis.exit_benchmark import _metrics
from src.analysis.models import (
    SignBenchmarkEvent,
    SignBenchmarkRun,
)
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Confluence A/B: Ichimoku _hi signs"
_MULTIYEAR_MIN_RUN_ID = 47

# Baseline (current shipped set) — 7 signs.
_BASE_SIGNS: tuple[str, ...] = (
    "str_hold", "str_lead", "str_lag",
    "brk_sma",  "brk_bol",
    "rev_lo",   "rev_nlo",
)

# Expanded — baseline + 3 ichimoku _hi signs.
_NEW_HI_SIGNS: tuple[str, ...] = (
    "brk_kumo_hi", "brk_tenkan_hi", "chiko_hi",
)
_EXPANDED_SIGNS: tuple[str, ...] = _BASE_SIGNS + _NEW_HI_SIGNS

# valid_bars for the 3 new signs = 5 (detector default).
_VALID_BARS_EXTRA: dict[str, int] = {s: 5 for s in _NEW_HI_SIGNS}


def _load_fires(signs: tuple[str, ...]) -> dict[str, list[tuple[str, datetime.date]]]:
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkRun.sign_type.in_(signs),
            )
        ).all()
    by_stock: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sign, stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        by_stock[stock].append((sign, d))
    return by_stock


def _candidates_for_stock_with_extra_valid(
    stock: str, fires: list[tuple[str, datetime.date]],
    cache: DataCache, corr_map: dict[datetime.date, str],
    zs_map: dict[datetime.date, tuple[float, ...]],
    fy_start: datetime.date, fy_end: datetime.date,
    n_gate: int,
    extra_valid: dict[str, int],
) -> list[EntryCandidate]:
    """Wraps _candidates_for_stock but uses a merged _VALID_BARS dict.

    Same logic as the original; the only delta is the per-sign valid_bars
    lookup includes _NEW_HI_SIGNS at vb=5.  We re-implement here rather
    than monkey-patching the imported _VALID_BARS to keep behaviour pure.
    """
    from src.analysis.bullish_confluence_v2_probe import _VALID_BARS as _BASE_VB
    valid_bars = {**_BASE_VB, **extra_valid}
    _COOLDOWN_BARS = 10

    if not cache.bars:
        return []
    by_date: dict[datetime.date, dict] = {}
    trading_dates: list[datetime.date] = []
    seen: set[datetime.date] = set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d)
        by_date[d] = {"close": b.close}
        trading_dates.append(d)
    trading_dates.sort()
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    valid_per_date: dict[int, set[str]] = defaultdict(set)
    for sign, fd in fires:
        if fd not in date_to_idx:
            continue
        fi = date_to_idx[fd]
        vb = valid_bars.get(sign, 5)
        for j in range(fi, min(fi + vb + 1, len(trading_dates))):
            valid_per_date[j].add(sign)

    cands: list[EntryCandidate] = []
    last_fire_idx = -10_000
    for i, d in enumerate(trading_dates):
        if d < fy_start or d > fy_end:
            continue
        count = len(valid_per_date.get(i, set()))
        if count < n_gate:
            continue
        if i - last_fire_idx < _COOLDOWN_BARS:
            continue
        cm = corr_map.get(d, "mid")
        cands.append(EntryCandidate(
            stock_code  = stock,
            entry_date  = d,
            entry_price = by_date[d]["close"],
            corr_mode   = cm,
            corr_n225   = 0.0,
            zs_history  = zs_map.get(d, ()),
        ))
        last_fire_idx = i
    return cands


def _run_fy_for_arm(arm_label: str, cfg, fires_by_stock,
                    stock_caches, corr_maps, zs_maps,
                    extra_valid: dict[str, int]) -> list[_ArmRow]:
    out: list[_ArmRow] = []
    for n_gate in _N_VALUES:
        all_cands: list[EntryCandidate] = []
        for code in stock_caches:
            cands = _candidates_for_stock_with_extra_valid(
                code, fires_by_stock.get(code, []),
                stock_caches[code], corr_maps.get(code, {}),
                zs_maps.get(code, {}),
                cfg.start, cfg.end, n_gate,
                extra_valid,
            )
            all_cands.extend(cands)
        results = run_simulation(all_cands, _EXIT_RULE, stock_caches, cfg.end)
        m = _metrics(results)
        logger.info("  [{}] N={}: {} trades, sharpe={:.2f}",
                    arm_label, n_gate, m.n,
                    m.sharpe if not math.isnan(m.sharpe) else float("nan"))
        out.append(_arm_row_from_metrics(m, cfg.label, n_gate, len(all_cands)))
    return out


def _run_fy(cfg, fires_a, fires_b):
    logger.info("── {} ── stocks={} fy={}..{}", cfg.label,
                cfg.stock_set, cfg.start, cfg.end)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        logger.warning("  no cluster — skip")
        return [], []

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
    logger.info("  loaded {} stock caches", len(stock_caches))

    corr_maps: dict[str, dict[datetime.date, str]] = {}
    zs_maps:   dict[str, dict[datetime.date, tuple[float, ...]]] = {}
    for code, c in stock_caches.items():
        corr_maps[code] = _build_corr_map(c, n225)
        zs_maps[code]   = _build_zs_map(c, n225)

    arm_a = _run_fy_for_arm("BASE", cfg, fires_a, stock_caches, corr_maps, zs_maps, {})
    arm_b = _run_fy_for_arm("EXPN", cfg, fires_b, stock_caches, corr_maps, zs_maps,
                            _VALID_BARS_EXTRA)
    return arm_a, arm_b


def _format_report(rows_a: list[_ArmRow], rows_b: list[_ArmRow]) -> str:
    by_n_a: dict[int, list[_ArmRow]] = defaultdict(list)
    by_n_b: dict[int, list[_ArmRow]] = defaultdict(list)
    for r in rows_a: by_n_a[r.n_gate].append(r)
    for r in rows_b: by_n_b[r.n_gate].append(r)

    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.",
        "",
        f"**Baseline arm (7 signs)**: " + ", ".join(_BASE_SIGNS),
        "",
        f"**Expanded arm (+{len(_NEW_HI_SIGNS)} ichimoku _hi)**: "
        + ", ".join(_NEW_HI_SIGNS),
        "",
        "Same ZsTpSl(2.0,2.0,0.3) exit, same portfolio cap, same 10-bar "
        "cooldown.  N=3 is the recommended gate per "
        "[confluence_strategy_backtest](benchmark.md#confluence-strategy-ab-n1-2-3).",
        "",
        "### Per-FY side-by-side",
        "",
    ]
    fys = sorted({r.fy for r in (rows_a + rows_b)})
    for n_gate in _N_VALUES:
        lines += [
            f"#### N ≥ {n_gate}",
            "",
            "| FY | base trades | base Sharpe | expn trades | expn Sharpe | Δ Sharpe | Δ trades |",
            "|----|---:|---:|---:|---:|---:|---:|",
        ]
        a_rows = {r.fy: r for r in by_n_a[n_gate]}
        b_rows = {r.fy: r for r in by_n_b[n_gate]}
        for fy in fys:
            ra = a_rows.get(fy)
            rb = b_rows.get(fy)
            if ra is None or rb is None:
                continue
            a_n = ra.n_trades; b_n = rb.n_trades
            a_s = ra.sharpe; b_s = rb.sharpe
            d_s = (b_s - a_s) if (a_s is not None and b_s is not None) else None
            d_n = b_n - a_n
            a_s_str = f"{a_s:+.2f}" if a_s is not None else "—"
            b_s_str = f"{b_s:+.2f}" if b_s is not None else "—"
            d_s_str = f"{d_s:+.2f}" if d_s is not None else "—"
            lines.append(f"| {fy} | {a_n} | {a_s_str} | {b_n} | {b_s_str} | {d_s_str} | {d_n:+} |")
        lines.append("")

    # Aggregate
    lines += [
        "### Aggregate (FY-equal-weighted across all FYs with trades)",
        "",
        "| N gate | arm | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|--------|-----|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        for arm_label, by_n in [("baseline", by_n_a), ("expanded", by_n_b)]:
            rows = by_n[n_gate]
            total_n = sum(r.n_trades for r in rows)
            sh = [r.sharpe for r in rows if r.sharpe is not None]
            mr = [r.mean_r for r in rows if r.mean_r is not None]
            wr = [r.win_rate for r in rows if r.win_rate is not None]
            avg_sh = statistics.mean(sh) if sh else None
            avg_mr = statistics.mean(mr) if mr else None
            avg_wr = statistics.mean(wr) if wr else None
            sh_s = f"{avg_sh:+.2f}" if avg_sh is not None else "—"
            mr_s = f"{avg_mr*100:+.2f}%" if avg_mr is not None else "—"
            wr_s = f"{avg_wr*100:.0f}%" if avg_wr is not None else "—"
            lines.append(f"| N ≥ {n_gate} | {arm_label} | {total_n} | **{sh_s}** | {mr_s} | {wr_s} |")
        lines.append("")

    # Sortino + EV decomposition (2026-05-18 evaluation upgrade)
    lines.append(_ev_decomp_table(
        [("baseline (7)", rows_a), ("expanded (+3 _hi)", rows_b)],
        _N_VALUES,
    ))

    lines += [
        "### Decision rule applied",
        "",
        "Ship the expanded set only if (a) avg Sharpe at N=3 ≥ baseline N=3 "
        "AND (b) ≥6 of 7 FYs remain non-negative.  Otherwise the 3 new _hi "
        "signs stay out of `ConfluenceSignStrategy._BULLISH_SIGNS` but remain "
        "in the catalogue for Daily-tab situational display.",
        "",
    ]
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
    logger.info("Appended report to {}", _BENCH_MD)


def main() -> None:
    fires_a = _load_fires(_BASE_SIGNS)
    fires_b = _load_fires(_EXPANDED_SIGNS)
    logger.info("Baseline fires: {} stocks; Expanded fires: {} stocks",
                len(fires_a), len(fires_b))

    rows_a: list[_ArmRow] = []
    rows_b: list[_ArmRow] = []
    for cfg in RS_FY_CONFIGS:
        ra, rb = _run_fy(cfg, fires_a, fires_b)
        rows_a.extend(ra)
        rows_b.extend(rb)

    report = _format_report(rows_a, rows_b)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
