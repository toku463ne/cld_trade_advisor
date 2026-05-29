"""Blend Stage 0 — daily-return correlation between RegimeSign and Confluence.

Backlog item 3 (`docs/analysis/regime_sign_improvement_backlog.md`): we now have
two strategies that each score ~+1.0 fill-order-null portfolio Sharpe. Blending
them into one book only helps if their *daily-return* correlation is meaningfully
below 1 (two equal-Sharpe assets diversify as long as ρ<1).

This is the cheap, decisive Stage-0 gate:
  - build each strategy's deterministic 6-slot stitched daily portfolio returns
    over the SAME N225 calendar, FY2019–FY2025;
  - report pooled Pearson ρ of the two daily series;
  - report each book's active-day fraction + standalone stitched Sharpe;
  - report candidate-overlap and realized-trade-overlap (Jaccard on (stock,date)).

Decision rule (from the backlog):
  ρ ≳ 0.85  → near-redundant; blending adds ~nothing (and informs keep-both-in-UI:
              consider running only the stronger one).
  ρ ≲ 0.70  → real diversification to harvest → escalate to Stage 1 (merged-pool
              fill-order null).

Read-only — prints a table, writes nothing.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_confluence_blend_stage0
"""
from __future__ import annotations

import datetime
import math
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_slot_order import _BULLISH, _N_GATE
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import (
    EXIT_RULE,
    RS_FY_CONFIGS,
    _build_zs_map,
    build_fy_candidates,
)
from src.data.db import get_session
from src.exit.exit_simulator import _MAX_HIGH_CORR, _MAX_LOW_CORR, run_simulation
from src.simulator.cache import DataCache

_SLOTS = _MAX_HIGH_CORR + _MAX_LOW_CORR   # 6-slot equal-weight book


def _closes(cache: DataCache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _daily_returns(results, stock_dts, cal):
    """{calendar_date: portfolio return that day} for a closed-position list."""
    cal_set = set(cal)
    dc = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                dc[d] += r / _SLOTS
    return {d: dc.get(d, 0.0) for d in cal}


def _sharpe(rets):
    rets = [r for r in rets]
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _total_return(rets):
    return float(np.prod([1.0 + r for r in rets]) - 1.0)


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)

    # Confluence fires (all FYs, once) ────────────────────────────────────
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    reg_series: list[float] = []
    conf_series: list[float] = []
    cand_overlap = {"reg": 0, "conf": 0, "both": 0}
    trade_overlap = {"reg": 0, "conf": 0, "both": 0}

    for cfg in RS_FY_CONFIGS:
        # ── RegimeSign deterministic 6-slot book ──────────────────────────
        cs = build_fy_candidates(cfg)
        if not cs.candidates or cs.n225_cache is None:
            logger.info("  {} skipped (no regime candidates)", cfg.label)
            continue
        n_dts, _ = _closes(cs.n225_cache)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        if len(cal) < 2:
            continue
        reg_stock_dts = {code: _closes(c) for code, c in cs.stock_caches.items()}
        reg_base = sorted(cs.candidates, key=lambda c: c.entry_date)
        reg_results = run_simulation(reg_base, EXIT_RULE, cs.stock_caches, cfg.end)
        reg_daily = _daily_returns(reg_results, reg_stock_dts, cal)

        # ── Confluence deterministic 6-slot book (own caches/universe) ─────
        codes = cbt._stocks_for_fy(cfg.stock_set)
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s,
                      datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            conf_caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s,
                       datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    conf_caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in conf_caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in conf_caches.items()}
        conf_cands = []
        for code in conf_caches:
            conf_cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), conf_caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE))
        conf_base = sorted(conf_cands, key=lambda c: c.entry_date)
        conf_results = run_simulation(conf_base, cbt._EXIT_RULE, conf_caches, cfg.end)
        conf_stock_dts = {code: _closes(c) for code, c in conf_caches.items()}
        conf_daily = _daily_returns(conf_results, conf_stock_dts, cal)

        # ── Align on the FY calendar ──────────────────────────────────────
        for d in cal:
            reg_series.append(reg_daily[d])
            conf_series.append(conf_daily[d])

        # ── Overlap on (stock, entry_date) ───────────────────────────────
        reg_cset = {(c.stock_code, c.entry_date) for c in cs.candidates}
        conf_cset = {(c.stock_code, c.entry_date) for c in conf_cands}
        cand_overlap["reg"] += len(reg_cset)
        cand_overlap["conf"] += len(conf_cset)
        cand_overlap["both"] += len(reg_cset & conf_cset)
        reg_tset = {(p.stock_code, p.entry_date) for p in reg_results}
        conf_tset = {(p.stock_code, p.entry_date) for p in conf_results}
        trade_overlap["reg"] += len(reg_tset)
        trade_overlap["conf"] += len(conf_tset)
        trade_overlap["both"] += len(reg_tset & conf_tset)

        logger.info("  {} done — reg {} trades / conf {} trades / cal {} days",
                    cfg.label, len(reg_results), len(conf_results), len(cal))

    # ── Stats ─────────────────────────────────────────────────────────────
    reg_a = np.asarray(reg_series)
    conf_a = np.asarray(conf_series)
    rho = float(np.corrcoef(reg_a, conf_a)[0, 1])
    # ρ on days where at least one book is active (drops mutual-flat days)
    active = (reg_a != 0.0) | (conf_a != 0.0)
    rho_active = (float(np.corrcoef(reg_a[active], conf_a[active])[0, 1])
                  if active.sum() > 2 else float("nan"))
    reg_act = float((reg_a != 0.0).mean())
    conf_act = float((conf_a != 0.0).mean())

    cand_jacc = cand_overlap["both"] / (cand_overlap["reg"] + cand_overlap["conf"]
                                        - cand_overlap["both"] or 1)
    trade_jacc = trade_overlap["both"] / (trade_overlap["reg"] + trade_overlap["conf"]
                                          - trade_overlap["both"] or 1)

    print("\n" + "=" * 78)
    print(f"BLEND STAGE 0 — RegimeSign vs Confluence daily-return ρ "
          f"({RS_FY_CONFIGS[0].label}–{RS_FY_CONFIGS[-1].label}, {_SLOTS}-slot)")
    print("=" * 78)
    print(f"\nstitched days                : {len(reg_a)}")
    print(f"pooled ρ (all calendar days) : {rho:+.3f}")
    print(f"pooled ρ (≥1 book active)     : {rho_active:+.3f}  "
          f"(on {int(active.sum())} active days)")
    print(f"active-day fraction          : RegimeSign {reg_act:.1%} | "
          f"Confluence {conf_act:.1%}")
    print(f"standalone stitched Sharpe   : RegimeSign {_sharpe(reg_series):+.2f} | "
          f"Confluence {_sharpe(conf_series):+.2f}")
    print(f"standalone stitched return   : RegimeSign {_total_return(reg_series)*100:+.0f}% | "
          f"Confluence {_total_return(conf_series)*100:+.0f}%")
    print(f"\ncandidate (stock,date) overlap: reg {cand_overlap['reg']} | "
          f"conf {cand_overlap['conf']} | both {cand_overlap['both']} | "
          f"Jaccard {cand_jacc:.1%}")
    print(f"realized trade overlap        : reg {trade_overlap['reg']} | "
          f"conf {trade_overlap['conf']} | both {trade_overlap['both']} | "
          f"Jaccard {trade_jacc:.1%}")

    print("\n" + "-" * 78)
    if rho >= 0.85:
        verdict = ("ρ ≥ 0.85 → NEAR-REDUNDANT. Blending adds ~nothing; the books are "
                   "the same bet. Run the stronger one; no Stage 1.")
    elif rho <= 0.70:
        verdict = ("ρ ≤ 0.70 → DIVERSIFICATION AVAILABLE. Escalate to Stage 1 "
                   "(merged-pool fill-order null vs the better single book).")
    else:
        verdict = ("0.70 < ρ < 0.85 → AMBIGUOUS band. Modest diversification; Stage 1 "
                   "only if the standalone Sharpes are close and overlap is low.")
    print("VERDICT:", verdict)
    print("-" * 78)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
