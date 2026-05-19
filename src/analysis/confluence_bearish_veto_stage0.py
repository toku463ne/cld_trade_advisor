"""confluence_bearish_veto_stage0 — Stage 0 for "skip confluence when bearish-coactive".

Operator hypothesis (2026-05-19): ConfluenceSignStrategy requires ≥ 3
bullish signs to fire, but ignores bearish signs co-firing on the same
stock × date.  Bearish co-fires (e.g., rev_nhi during a rally,
brk_kumo_lo trend-break warning) might be net-negative information
the strategy currently throws away.

Stage 0 = measurement only.  Per-entry EV stratified by
`bearish_count` at entry date.  If the bearish-co-active sub-population
has materially lower DR/EV, Stage 1 = strategy A/B with a veto rule.

Pre-registered Stage-0 PASS gate (locked before run):
  - DR(bearish_count ≥ 1) ≤ DR(pool) − 5pp
  - AND replicates on FY2024+FY2025 holdout (≥ 5pp gap there too)
  - AND n(bearish_count ≥ 1) ≥ 30 (otherwise too thin)

Bearish set (LOCKED): {rev_nhi, rev_hi, brk_kumo_lo, brk_tenkan_lo,
chiko_lo}.  Excludes corr_shift / str_lag (direction-ambiguous).

Output: docs/analysis/confluence_bearish_veto_stage0.md
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
    _EXPANDED_SIGNS, _VALID_BARS_EXTRA,
    _candidates_for_stock_with_extra_valid, _load_fires,
)
from src.analysis.confluence_strategy_backtest import (
    _build_corr_map, _stocks_for_fy,
    _EXIT_RULE, _LOOKBACK_DAYS_CACHE,
)
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_REPORT = Path("docs/analysis/confluence_bearish_veto_stage0.md")

_BEARISH_SIGNS: tuple[str, ...] = (
    "rev_nhi", "rev_hi",
    "brk_kumo_lo", "brk_tenkan_lo", "chiko_lo",
)
_BEARISH_VALID_BARS = 5      # detector default for _lo/_hi signs

_HOLDOUT_FYS = {"FY2024", "FY2025"}
_N_GATE      = 3              # production confluence gate
_MIN_N_CELL  = 30


def _fy_label(d: datetime.date) -> str:
    return f"FY{d.year}" if d.month >= 4 else f"FY{d.year - 1}"


def _load_bearish_fires() -> dict[str, list[tuple[str, datetime.date]]]:
    """Load all SignBenchmarkEvents for the bearish set."""
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
    logger.info("Loaded bearish fires for {} stocks ({} total events)",
                len(by_stock), sum(len(v) for v in by_stock.values()))
    return by_stock


def _build_bearish_count_map(
    fires_by_stock: dict[str, list[tuple[str, datetime.date]]],
    trading_dates_by_stock: dict[str, list[datetime.date]],
) -> dict[str, dict[datetime.date, int]]:
    """For each stock, walk its trading dates and count distinct bearish
    signs whose valid window covers each date."""
    out: dict[str, dict[datetime.date, int]] = {}
    for stock, fires in fires_by_stock.items():
        dates = trading_dates_by_stock.get(stock)
        if not dates:
            continue
        date_to_idx = {d: i for i, d in enumerate(dates)}
        per_date_signs: dict[int, set[str]] = defaultdict(set)
        for sign, fd in fires:
            if fd not in date_to_idx:
                continue
            fi = date_to_idx[fd]
            for j in range(fi, min(fi + _BEARISH_VALID_BARS + 1, len(dates))):
                per_date_signs[j].add(sign)
        out[stock] = {dates[i]: len(per_date_signs[i]) for i in range(len(dates))}
    return out


def _stats(returns: list[float]) -> dict:
    if not returns:
        return {"n": 0, "dr": None, "mean_r": None,
                "sharpe": None, "avg_win": None, "avg_loss": None}
    wins  = [r for r in returns if r > 0]
    loses = [r for r in returns if r <= 0]
    m = statistics.mean(returns)
    try:
        s = statistics.stdev(returns) if len(returns) >= 2 else 0.0
    except statistics.StatisticsError:
        s = 0.0
    sh = m / s * math.sqrt(252) if s > 0 else None
    return {
        "n":         len(returns),
        "dr":        len(wins) / len(returns),
        "mean_r":    m,
        "sharpe":    sh,
        "avg_win":   statistics.mean(wins)  if wins  else 0.0,
        "avg_loss":  statistics.mean(loses) if loses else 0.0,
    }


def _run_fy(cfg) -> tuple[list, dict]:
    """Return (results, bearish_count_at_entry_map).

    bearish_count_at_entry_map: (stock, entry_date) → int
    """
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        return [], {}

    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE + 180)
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

    corr_maps = {c: _build_corr_map(stock_caches[c], n225) for c in stock_caches}
    zs_maps   = {c: _build_zs_map(stock_caches[c], n225) for c in stock_caches}

    # Trading-date list per stock for bearish_count map.
    trading_dates: dict[str, list[datetime.date]] = {}
    for code, cache in stock_caches.items():
        seen: set[datetime.date] = set()
        d_list: list[datetime.date] = []
        for b in cache.bars:
            d = b.dt.date()
            if d not in seen:
                seen.add(d)
                d_list.append(d)
        d_list.sort()
        trading_dates[code] = d_list

    # Bullish fires → confluence entry candidates at N=3
    bull_fires = _load_fires(_EXPANDED_SIGNS)
    cands = []
    for code in stock_caches:
        cands.extend(_candidates_for_stock_with_extra_valid(
            code, bull_fires.get(code, []),
            stock_caches[code], corr_maps.get(code, {}),
            zs_maps.get(code, {}),
            cfg.start, cfg.end, _N_GATE,
            _VALID_BARS_EXTRA,
        ))

    # Bearish fires → per-stock bearish_count map
    bear_fires = _load_bearish_fires()
    bear_count = _build_bearish_count_map(bear_fires, trading_dates)

    # Run simulation, then map results back to bearish_count at entry
    results = run_simulation(cands, _EXIT_RULE, stock_caches, cfg.end)
    bear_at_entry: dict[tuple[str, datetime.date], int] = {}
    for c in cands:
        cnt = bear_count.get(c.stock_code, {}).get(c.entry_date, 0)
        bear_at_entry[(c.stock_code, c.entry_date)] = cnt
    return list(results), bear_at_entry


def _bucket_for(c: int) -> str:
    if c == 0: return "0"
    if c == 1: return "1"
    return "≥2"


def _format_report(all_results, all_bear) -> str:
    # Tag each trade by bearish_count and FY
    rows: list[tuple[str, str, float, int]] = []  # (fy, bucket, return_pct, bear_n)
    for r in all_results:
        bear = all_bear.get((r.stock_code, r.entry_date), 0)
        rows.append((_fy_label(r.entry_date), _bucket_for(bear), r.return_pct, bear))

    n_total = len(rows)
    train_rows = [(fy, b, ret, bn) for fy, b, ret, bn in rows if fy not in _HOLDOUT_FYS]
    hold_rows  = [(fy, b, ret, bn) for fy, b, ret, bn in rows if fy in _HOLDOUT_FYS]

    lines = [
        "# Confluence × bearish co-active — Stage 0",
        "",
        f"Probe run: {datetime.date.today()}.  Measures whether bearish-sign "
        "co-activity at confluence entry date predicts worse trade outcomes.",
        "",
        "## Setup",
        "",
        f"- Bullish set (10): {', '.join(_EXPANDED_SIGNS)}",
        f"- Bearish set (5): {', '.join(_BEARISH_SIGNS)}",
        f"- Confluence gate: N=3 bullish co-active",
        f"- Bearish valid_bars: {_BEARISH_VALID_BARS}",
        f"- Total confluence trades: **{n_total}** "
        f"(train {len(train_rows)}, holdout {len(hold_rows)})",
        f"- Min n per bucket: {_MIN_N_CELL}",
        "",
    ]

    def _section(label: str, rs: list) -> list[str]:
        out = [f"### {label}", "",
               "| bearish_count | n | DR | mean_r | Sharpe | avg_win | avg_loss |",
               "|---|---:|---:|---:|---:|---:|---:|"]
        pool_ret = [ret for _, _, ret, _ in rs]
        pool_s = _stats(pool_ret)
        out.append(_fmt_row("pool", pool_s))
        for bucket in ("0", "1", "≥2"):
            sub = [ret for _, b, ret, _ in rs if b == bucket]
            s = _stats(sub)
            label = f"= {bucket}" if bucket != "≥2" else "≥ 2"
            out.append(_fmt_row(f"bearish {label}", s))
        out.append("")
        # Combined "≥1" line for the headline test
        any_ret = [ret for _, b, ret, _ in rs if b != "0"]
        out.append("| **bearish ≥ 1 (combined)** | "
                   f"{len(any_ret)} | "
                   f"{(sum(1 for r in any_ret if r>0)/len(any_ret)*100 if any_ret else 0):.1f}% | "
                   f"{(statistics.mean(any_ret)*100 if any_ret else 0):+.2f}% | — | — | — |")
        out.append("")
        return out

    lines += _section("Pooled (FY2019-FY2025)", rows)
    lines += _section("Train (pre-FY2024)",     train_rows)
    lines += _section("Holdout (FY2024+FY2025)", hold_rows)

    # Gate
    pool_dr_all = sum(1 for _, _, r, _ in rows if r > 0) / max(len(rows), 1)
    any_pool    = [r for _, b, r, _ in rows      if b != "0"]
    any_hold    = [r for _, b, r, _ in hold_rows if b != "0"]
    any_pool_dr = (sum(1 for r in any_pool if r>0) / len(any_pool)) if any_pool else None
    hold_dr_all = (sum(1 for _, _, r, _ in hold_rows if r > 0) /
                   max(len(hold_rows), 1)) if hold_rows else None
    any_hold_dr = (sum(1 for r in any_hold if r>0) / len(any_hold)) if any_hold else None

    lines += [
        "## Pre-registered gate",
        "",
        f"- PASS if: DR(bearish ≥ 1) ≤ pool DR − 5pp, "
        f"AND replicates on FY2024+FY2025 (≥ 5pp gap there too), "
        f"AND n(bearish ≥ 1) ≥ {_MIN_N_CELL}.",
        "",
    ]
    n_any = len(any_pool)
    if any_pool_dr is None:
        lines.append("**INSUFFICIENT DATA** — no bearish ≥ 1 entries.")
    else:
        pool_gap = (pool_dr_all - any_pool_dr) * 100
        hold_gap = ((hold_dr_all - any_hold_dr) * 100
                    if (hold_dr_all is not None and any_hold_dr is not None) else None)
        n_pass    = n_any >= _MIN_N_CELL
        pool_pass = pool_gap >= 5.0
        hold_pass = hold_gap is not None and hold_gap >= 5.0
        lines += [
            f"- Pool: n(bearish≥1) = {n_any}, "
            f"DR = {any_pool_dr*100:.1f}%, pool DR = {pool_dr_all*100:.1f}%, "
            f"gap = {pool_gap:+.1f}pp ({'✓' if pool_pass else '✗'} ≥ 5pp)",
            f"- Holdout: n(bearish≥1) = {len(any_hold)}, "
            f"DR = {(any_hold_dr*100 if any_hold_dr is not None else 0):.1f}%, "
            f"holdout DR = {(hold_dr_all*100 if hold_dr_all is not None else 0):.1f}%, "
            f"gap = {f'{hold_gap:+.1f}pp' if hold_gap is not None else '—'} "
            f"({'✓' if hold_pass else '✗'} ≥ 5pp)",
            f"- n(bearish≥1) ≥ {_MIN_N_CELL}: {'✓' if n_pass else '✗'}",
            "",
        ]
        if n_pass and pool_pass and hold_pass:
            verdict = "**PASS** — proceed to Stage 1 (confluence + bearish veto A/B)."
        elif pool_pass and not hold_pass:
            verdict = ("**PARTIAL** — pool shows effect but holdout does not.  "
                       "Same trap as 7 recent stage-0 PARTIALs.  Park.")
        elif n_pass:
            verdict = "**FAIL** — bearish co-active entries don't underperform pool."
        else:
            verdict = (f"**FAIL** — n(bearish≥1) = {n_any} < {_MIN_N_CELL}; "
                       "veto sub-population is too thin to use even if real.")
        lines.append(verdict)
    return "\n".join(lines)


def _fmt_row(label: str, s: dict) -> str:
    n = s["n"]
    if n == 0:
        return f"| {label} | 0 | — | — | — | — | — |"
    return (f"| {label} | {n} | "
            f"{s['dr']*100:.1f}% | "
            f"{s['mean_r']*100:+.2f}% | "
            f"{'—' if s['sharpe'] is None else f'{s["sharpe"]:+.2f}'} | "
            f"{s['avg_win']*100:+.2f}% | "
            f"{s['avg_loss']*100:+.2f}% |")


def main() -> None:
    all_results: list = []
    all_bear: dict = {}
    for cfg in RS_FY_CONFIGS:
        logger.info("── {} ──", cfg.label)
        results, bear_map = _run_fy(cfg)
        all_results.extend(results)
        all_bear.update(bear_map)
        logger.info("  {}: {} confluence trades, {} bearish_count entries",
                    cfg.label, len(results), len(bear_map))

    report = _format_report(all_results, all_bear)
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    _REPORT.write_text(report + "\n")
    logger.info("Wrote {}", _REPORT)
    # Print summary + verdict
    print(report)


if __name__ == "__main__":
    main()
