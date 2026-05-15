"""asym_exit_ab — follow-up A/B for project_asym_exit.

Tests whether routing the exit rule by the most-recent confirmed zigzag pivot
direction at entry beats the production default (ZsTpSl universal).

Routing logic at entry bar:
  - Last confirmed pivot within `_PIVOT_LOOKBACK` bars is HIGH (dir=+2):
      → use `AdxTrail(d=8.0)` ("trend continues" wisdom from asym probe)
  - Last confirmed pivot within `_PIVOT_LOOKBACK` bars is LOW  (dir=-2):
      → use `ZsTpSl(2.0, 2.0, 0.3)` ("counter-trend, lock profit" wisdom)
  - No confirmed pivot within `_PIVOT_LOOKBACK` bars:
      → use default `ZsTpSl(2.0, 2.0, 0.3)`

Pre-registered falsifier (all must hold to ACCEPT):
  G1  Variant Sharpe − baseline ≥ +0.10 on FY2019-FY2024 aggregate.
  G2  Variant Sharpe − baseline ≥ +0.05 on FY2025 OOS.
  G3  No FY in FY2019-FY2024 has variant Sharpe < baseline by > 0.20 (regime-fragility).
  G4  n ≥ 50 positions per route arm (HIGH-routed, LOW-routed) on FY2025 OOS.

This is a probe — discovery of effect direction, not production wiring.
If ACCEPT, the next step is user-authorized integration into
src/strategy/regime_sign.py + src/analysis/regime_sign_backtest.py.

CLI: uv run --env-file devenv python -m src.analysis.asym_exit_ab
"""

from __future__ import annotations

import copy
import datetime
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.exit_benchmark import FyConfig, Metrics, _load_cache, _metrics
from src.analysis.models import SignBenchmarkRun
from src.analysis.regime_sign_backtest import (
    PRIOR_BENCH_SETS, RS_FY_CONFIGS, _build_zs_map,
)
from src.data.db import get_session
from src.exit.adx_trail import AdxTrail
from src.exit.base import EntryCandidate, ExitContext, ExitResult, ExitRule
from src.exit.exit_simulator import _daily_bars_with_adx
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache
from src.strategy.proposal import SignalProposal
from src.strategy.regime_sign import RegimeSignStrategy

_N225          = "^N225"
_PIVOT_LOOKBACK = 20
_ZZ_SIZE       = 5
_ZZ_MID        = 2
_LOOKBACK_DAYS = 200
_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "asym_exit_ab"
_MIN_DR = 0.52

_DEFAULT_RULE = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
_HIGH_PIVOT_RULE = AdxTrail(drop_threshold=8.0)
_LOW_PIVOT_RULE  = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
_MAX_HIGH_CORR = 1
_MAX_LOW_CORR  = 3


def _recent_pivot_dir(cache: DataCache, on_date: datetime.date) -> int:
    """Return +1 if last confirmed pivot within lookback is HIGH (dir=+2),
    -1 if LOW (dir=-2), 0 if none within window.
    """
    daily = sorted({b.dt.date(): b for b in cache.bars}.items())
    if not daily:
        return 0
    dates = [d for d, _ in daily]
    try:
        on_idx = dates.index(on_date)
    except ValueError:
        # find closest earlier date
        on_idx = next((i for i in range(len(dates) - 1, -1, -1)
                       if dates[i] <= on_date), -1)
        if on_idx < 0:
            return 0

    start = max(0, on_idx - 200)
    end_excl = on_idx + 1
    highs = [b.high for _, b in daily[start:end_excl]]
    lows  = [b.low  for _, b in daily[start:end_excl]]
    if len(highs) < _ZZ_SIZE * 2 + 1:
        return 0
    peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MID)
    confirmed = [p for p in peaks if abs(p.direction) == 2]
    if not confirmed:
        return 0
    last = confirmed[-1]
    # Was the last confirmed pivot within _PIVOT_LOOKBACK bars before on_idx?
    last_global_idx = start + last.bar_index
    if (on_idx - last_global_idx) > _PIVOT_LOOKBACK:
        return 0
    return 1 if last.direction > 0 else -1


@dataclass
class _OpenPosition:
    candidate:  EntryCandidate
    fill_price: float
    fill_date:  datetime.date
    bars:       list
    peak_adx:   float
    rule:       ExitRule
    route:      str   # "HIGH" / "LOW" / "DEFAULT"


def _isnan(x: float) -> bool:
    return x != x


def _run_routed(
    candidates: list[EntryCandidate],
    route_map: dict[tuple[str, datetime.date], str],
    rule_map:  dict[str, ExitRule],
    stock_caches: dict[str, DataCache],
    end_date: datetime.date,
) -> tuple[list[ExitResult], dict[str, int]]:
    """Mirror of run_simulation but routes exit rule per candidate."""
    sorted_cands = sorted(candidates, key=lambda c: c.entry_date)
    bar_index = {c: _daily_bars_with_adx(cache) for c, cache in stock_caches.items()}
    date_to_idx = {c: {b.date: i for i, b in enumerate(bars)}
                   for c, bars in bar_index.items()}

    results: list[ExitResult] = []
    open_pos: list[_OpenPosition] = []
    route_counts = {"HIGH": 0, "LOW": 0, "DEFAULT": 0}

    cand_idx = 0
    n_cands  = len(sorted_cands)

    all_dates: set[datetime.date] = set()
    for bars in bar_index.values():
        all_dates.update(b.date for b in bars)
    sorted_dates = sorted(all_dates)

    for today in sorted_dates:
        if today > end_date:
            break

        # Advance open positions
        still_open: list[_OpenPosition] = []
        for pos in open_pos:
            code = pos.candidate.stock_code
            bar_i = date_to_idx.get(code, {}).get(today)
            if bar_i is None:
                still_open.append(pos); continue
            bar = bar_index[code][bar_i]
            pos.bars.append(bar)
            adx_val = bar.adx if not _isnan(bar.adx) else pos.peak_adx
            pos.peak_adx = max(pos.peak_adx, adx_val)
            still_open.append(pos)
        open_pos = still_open

        # Check exits
        closed_now: list[_OpenPosition] = []
        remaining: list[_OpenPosition] = []
        for pos in open_pos:
            if not pos.bars:
                remaining.append(pos); continue
            bar = pos.bars[-1]
            bar_n = len(pos.bars) - 1
            ctx = ExitContext(
                bar_index=bar_n, entry_price=pos.fill_price,
                high=bar.high, low=bar.low, close=bar.close,
                adx=bar.adx if not _isnan(bar.adx) else 0.0,
                adx_pos=bar.adx_p if not _isnan(bar.adx_p) else 0.0,
                adx_neg=bar.adx_n if not _isnan(bar.adx_n) else 0.0,
                peak_adx=pos.peak_adx,
                zs_history=pos.candidate.zs_history,
            )
            exit_now, reason = pos.rule.should_exit(ctx)
            force = today >= end_date
            if exit_now or force:
                reason = reason if (exit_now and not force) else "end_of_data"
                results.append(ExitResult(
                    stock_code=pos.candidate.stock_code,
                    entry_date=pos.candidate.entry_date,
                    exit_date=today, entry_price=pos.fill_price,
                    exit_price=bar.close, hold_bars=bar_n,
                    exit_reason=reason, corr_mode=pos.candidate.corr_mode,
                ))
                closed_now.append(pos)
            else:
                remaining.append(pos)
        open_pos = remaining

        # Accept new candidates entering today
        while cand_idx < n_cands and sorted_cands[cand_idx].entry_date <= today:
            cand = sorted_cands[cand_idx]
            cand_idx += 1
            if cand.entry_date != today:
                continue

            high_open = sum(1 for p in open_pos if p.candidate.corr_mode == "high")
            low_open  = sum(1 for p in open_pos if p.candidate.corr_mode != "high")
            if cand.corr_mode == "high" and high_open >= _MAX_HIGH_CORR:
                continue
            if cand.corr_mode != "high" and low_open >= _MAX_LOW_CORR:
                continue

            code = cand.stock_code
            bar_i = date_to_idx.get(code, {}).get(today)
            if bar_i is None or bar_i + 1 >= len(bar_index.get(code, [])):
                continue
            fill_bar = bar_index[code][bar_i + 1]

            route = route_map.get((cand.stock_code, cand.entry_date), "DEFAULT")
            route_counts[route] += 1
            base_rule = rule_map[route]
            pos_rule = copy.deepcopy(base_rule)
            pos_rule.reset()

            open_pos.append(_OpenPosition(
                candidate=cand, fill_price=fill_bar.open, fill_date=fill_bar.date,
                bars=[], peak_adx=0.0, rule=pos_rule, route=route,
            ))

    # Force-close anything still open
    for pos in open_pos:
        if not pos.bars:
            continue
        bar = pos.bars[-1]
        results.append(ExitResult(
            stock_code=pos.candidate.stock_code,
            entry_date=pos.candidate.entry_date,
            exit_date=bar.date, entry_price=pos.fill_price,
            exit_price=bar.close, hold_bars=len(pos.bars) - 1,
            exit_reason="end_of_data", corr_mode=pos.candidate.corr_mode,
        ))

    return results, route_counts


def _load_run_ids(prior_sets: list[str]) -> list[int]:
    with get_session() as session:
        rows = session.execute(
            select(SignBenchmarkRun.id)
            .where(SignBenchmarkRun.stock_set.in_(prior_sets))
        ).scalars().all()
    return list(rows)


def _run_fy(config: FyConfig) -> tuple[Metrics, Metrics, dict, dict]:
    prior_sets = PRIOR_BENCH_SETS[config.stock_set]
    run_ids = _load_run_ids(prior_sets)
    tz = datetime.timezone.utc
    lookback_start = (
        datetime.datetime(config.start.year, config.start.month, config.start.day, tzinfo=tz)
        - datetime.timedelta(days=_LOOKBACK_DAYS)
    )
    fy_start = datetime.datetime(config.start.year, config.start.month, config.start.day, tzinfo=tz)
    fy_end = datetime.datetime(config.end.year, config.end.month, config.end.day,
                                23, 59, 59, tzinfo=tz)

    strategy = RegimeSignStrategy.from_config(
        stock_set=config.stock_set, run_ids=run_ids,
        start=lookback_start, end=fy_end, mode="backtest", min_dr=_MIN_DR,
    )
    proposals_by_date = strategy.propose_range(fy_start, fy_end)
    all_proposals: list[SignalProposal] = [
        p for ps in proposals_by_date.values() for p in ps
    ]
    if not all_proposals:
        logger.warning("  {}: no proposals", config.label)
        empty = _metrics([])
        return empty, empty, {"HIGH": 0, "LOW": 0, "DEFAULT": 0}, {"HIGH": 0, "LOW": 0, "DEFAULT": 0}

    n225_cache = _load_cache(_N225, config.start, config.end)
    stock_codes = {p.stock_code for p in all_proposals}
    stock_caches: dict[str, DataCache] = {}
    for code in stock_codes:
        c = _load_cache(code, config.start, config.end)
        if c:
            stock_caches[code] = c

    zs_maps = {c: _build_zs_map(cache, n225_cache) for c, cache in stock_caches.items()}
    close_by_date = {c: {b.dt.date(): b.close for b in cache.bars} for c, cache in stock_caches.items()}

    candidates: list[EntryCandidate] = []
    seen: set = set()
    for p in sorted(all_proposals, key=lambda x: x.fired_at):
        d = p.fired_at.date()
        key = (p.stock_code, d)
        if key in seen:
            continue
        seen.add(key)
        if p.stock_code not in stock_caches:
            continue
        close = close_by_date[p.stock_code].get(d)
        if close is None:
            continue
        candidates.append(EntryCandidate(
            stock_code=p.stock_code, entry_date=d, entry_price=close,
            corr_mode=p.corr_mode, corr_n225=p.corr_n225,
            zs_history=zs_maps.get(p.stock_code, {}).get(d, ()),
        ))

    # ── Build route map ──────────────────────────────────────────────────────
    route_map: dict[tuple[str, datetime.date], str] = {}
    for cand in candidates:
        d = _recent_pivot_dir(stock_caches[cand.stock_code], cand.entry_date)
        if d > 0:
            route_map[(cand.stock_code, cand.entry_date)] = "HIGH"
        elif d < 0:
            route_map[(cand.stock_code, cand.entry_date)] = "LOW"
        else:
            route_map[(cand.stock_code, cand.entry_date)] = "DEFAULT"

    # Baseline: all DEFAULT
    baseline_map = {k: "DEFAULT" for k in route_map}
    baseline_rules = {"HIGH": _DEFAULT_RULE, "LOW": _DEFAULT_RULE, "DEFAULT": _DEFAULT_RULE}
    variant_rules  = {"HIGH": _HIGH_PIVOT_RULE, "LOW": _LOW_PIVOT_RULE, "DEFAULT": _DEFAULT_RULE}

    logger.info("  {}: {} candidates", config.label, len(candidates))
    baseline_res, baseline_counts = _run_routed(candidates, baseline_map, baseline_rules,
                                                 stock_caches, config.end)
    variant_res,  variant_counts  = _run_routed(candidates, route_map,    variant_rules,
                                                 stock_caches, config.end)
    bm = _metrics(baseline_res)
    vm = _metrics(variant_res)
    logger.info("  {}: baseline n={} mean_r={:+.3%} sharpe={:.2f} | variant n={} mean_r={:+.3%} sharpe={:.2f}",
                config.label, bm.n, bm.mean_r, bm.sharpe, vm.n, vm.mean_r, vm.sharpe)
    logger.info("  {}: variant route counts {}", config.label, variant_counts)
    return bm, vm, baseline_counts, variant_counts


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.date.today().isoformat()

    per_fy: list[tuple[FyConfig, Metrics, Metrics, dict]] = []
    for cfg in RS_FY_CONFIGS:
        bm, vm, _, vcounts = _run_fy(cfg)
        per_fy.append((cfg, bm, vm, vcounts))

    md: list[str] = [
        "# asym_exit_ab — pivot-direction-routed exit-rule A/B",
        "",
        f"Generated: {today_str}",
        "",
        "Baseline: `ZsTpSl(2.0,2.0,0.3)` universal (current regime_sign_backtest default).  ",
        "Variant: `AdxTrail(d=8.0)` if last confirmed pivot within 20 bars is HIGH; "
        "`ZsTpSl(2.0,2.0,0.3)` if LOW; `ZsTpSl(2.0,2.0,0.3)` default otherwise.",
        "",
        "## Per-FY",
        "",
        "| FY | n | baseline mean_r | baseline sharpe | variant mean_r | variant sharpe | ΔSharpe | route counts (H/L/D) |",
        "|----|---|-----------------|-----------------|----------------|----------------|---------|----------------------|",
    ]
    fy_deltas: list[tuple[str, float]] = []
    for cfg, bm, vm, vcounts in per_fy:
        delta = (vm.sharpe - bm.sharpe) if (not math.isnan(bm.sharpe) and not math.isnan(vm.sharpe)) else float("nan")
        fy_deltas.append((cfg.label, delta))
        md.append(
            f"| {cfg.label} | {bm.n} "
            f"| {bm.mean_r*100:+.2f}% | {bm.sharpe:.3f} "
            f"| {vm.mean_r*100:+.2f}% | {vm.sharpe:.3f} "
            f"| **{delta:+.3f}** "
            f"| {vcounts['HIGH']}/{vcounts['LOW']}/{vcounts['DEFAULT']} |"
        )

    train_deltas = [d for fy, d in fy_deltas if fy != "FY2025" and not math.isnan(d)]
    oos_deltas   = [d for fy, d in fy_deltas if fy == "FY2025" and not math.isnan(d)]
    train_mean_delta = statistics.mean(train_deltas) if train_deltas else float("nan")
    oos_delta = oos_deltas[0] if oos_deltas else float("nan")
    worst_train = min(train_deltas) if train_deltas else float("nan")

    # Aggregate Sharpe across train FYs (n-weighted mean of returns, then re-sharpe)
    # Simplification: report mean-of-FY-Sharpes as the aggregate.
    g1_pass = (not math.isnan(train_mean_delta)) and train_mean_delta >= 0.10
    g2_pass = (not math.isnan(oos_delta)) and oos_delta >= 0.05
    g3_pass = (not math.isnan(worst_train)) and worst_train >= -0.20
    # G4: variant route counts on OOS
    if per_fy and per_fy[-1][0].label == "FY2025":
        oos_counts = per_fy[-1][3]
        g4_pass = oos_counts["HIGH"] >= 50 and oos_counts["LOW"] >= 50
    else:
        g4_pass = False

    all_pass = g1_pass and g2_pass and g3_pass and g4_pass
    verdict = "ACCEPT" if all_pass else "REJECT"

    md += [
        "",
        "## Pre-registered gates",
        "",
        "| Gate | Observed | Threshold | Pass? |",
        "|------|----------|-----------|-------|",
        f"| G1 mean ΔSharpe FY2019-FY2024 | {train_mean_delta:+.3f} | ≥ +0.10 | {'✓' if g1_pass else '✗'} |",
        f"| G2 ΔSharpe FY2025 OOS | {oos_delta:+.3f} | ≥ +0.05 | {'✓' if g2_pass else '✗'} |",
        f"| G3 worst FY ΔSharpe (FY2019-FY2024) | {worst_train:+.3f} | ≥ −0.20 | {'✓' if g3_pass else '✗'} |",
    ]
    if per_fy and per_fy[-1][0].label == "FY2025":
        oos_counts = per_fy[-1][3]
        md.append(
            f"| G4 n_HIGH ≥ 50 AND n_LOW ≥ 50 on FY2025 OOS | "
            f"H={oos_counts['HIGH']}, L={oos_counts['LOW']} | ≥ 50 each | {'✓' if g4_pass else '✗'} |")
    else:
        md.append("| G4 OOS route counts | (no FY2025 data) | ≥ 50 each | ✗ |")

    md += [
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## Notes",
        "- ΔSharpe computed per-FY then averaged; not pooled-Sharpe of concatenated returns.",
        "- Routing uses `detect_peaks(size=5, middle_size=2)` on daily bars; same as project default.",
        "- Recent-pivot lookback = 20 bars at entry date.",
        "- This A/B uses the production `regime_sign` proposals + portfolio constraints (≤1 high-corr, ≤3 low-corr).",
        "- Exit rules are the REAL production rules (`AdxTrail(d=8.0)`, `ZsTpSl(2.0,2.0,0.3)`), not proxies.",
        "",
    ]

    out = _OUT_DIR / f"asym_exit_ab_{today_str}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", out)
    print("\n".join(md))


if __name__ == "__main__":
    main()
