"""confluence_sameday_ab — A/B: validity-windowed confluence vs SAME-DAY-ONLY confluence.

Question (operator, 2026-05-30): does the confluence benchmark change if a sign
counts toward the ≥N gate ONLY on the exact day it fired, instead of for its full
`valid_bars` window?

This script reuses the canonical capital-aware 6-slot book machinery from
`confluence_benchmark.py` and runs it twice per FY:

  - WINDOWED   : production rule — each fire counts for `_VALID_BARS[sign]`
                 trading days (str_hold/brk_bol=3, others=5).
  - SAME-DAY   : every sign's valid_bars forced to 0 — a fire counts only on its
                 fire date.  This is the variant the operator asked about.

Both arms share the SAME caches / corr maps / zs maps / affordability filter /
deterministic (sorted entry_date) fill order, so the only difference is the
confluence-count construction.  Reports per-FY and stitched book Sharpe / total /
maxDD for both the equal-weight r/6 book and the budget ¥2M 6-slot book.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_sameday_ab
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import (
    _BUDGET,
    _BULLISH,
    _FYS,
    _N_GATE,
    _SLOTS,
    _book,
    _closes,
    _pos_daily,
)
from src.analysis.exit_benchmark import _metrics
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

_WINDOWED = dict(_BULLISH)                      # production windows
_SAMEDAY = {s: 0 for s in _BULLISH}             # fire counts only on fire day


def _build_books(cands, caches, stock_dts, cal, cfg_end):
    """Run the simulation for one candidate list → (per-trade m, ew book, bw book, naff)."""
    def _affordable(c) -> bool:
        _, cmap = stock_dts.get(c.stock_code, ([], {}))
        px = cmap.get(c.entry_date)
        return px is not None and recommended_lots(_BUDGET, float(px), _SLOTS) > 0

    cands_aff = [c for c in cands if _affordable(c)]
    results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg_end)
    results_bw = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg_end)
    m = _metrics(results)
    cal_set = set(cal)

    day_ew: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_ew[d] += r / _SLOTS

    day_bw: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results_bw:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        lots = recommended_lots(_BUDGET, float(p.entry_price), _SLOTS)
        w = position_weight(lots, float(p.entry_price), _BUDGET)
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_bw[d] += r * w

    rets_ew = [day_ew.get(d, 0.0) for d in cal]
    rets_bw = [day_bw.get(d, 0.0) for d in cal]
    return m, rets_ew, rets_bw, len(results_bw)


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    arms = {"WINDOWED": _WINDOWED, "SAMEDAY": _SAMEDAY}
    per_fy: dict[str, dict] = {a: {} for a in arms}
    stitched_ew: dict[str, list[float]] = {a: [] for a in arms}
    stitched_bw: dict[str, list[float]] = {a: [] for a in arms}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s,
                      datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s,
                       datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        if not caches:
            continue
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        for arm, vb_map in arms.items():
            cbt._VALID_BARS = vb_map
            cands = []
            for code in caches:
                cands += cbt._candidates_for_stock(
                    code, fires.get(code, []), caches[code], corr_maps[code],
                    zs_maps[code], cfg.start, cfg.end, _N_GATE)
            cands.sort(key=lambda c: c.entry_date)
            m, rets_ew, rets_bw, naff = _build_books(cands, caches, stock_dts, cal, cfg.end)
            per_fy[arm][cfg.label] = (m, _book(rets_ew), _book(rets_bw), naff)
            stitched_ew[arm] += rets_ew[1:]
            stitched_bw[arm] += rets_bw[1:]
            logger.info("  {} {} — {} trades ({} affordable)", cfg.label, arm, m.n, naff)

    # ── report ──────────────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("CONFLUENCE SAME-DAY-ONLY vs WINDOWED — N>=3, 6-slot book, budget ¥{:,}".format(_BUDGET))
    print("=" * 96)
    print(f"\n{'FY':<8}{'arm':<10}{'n':>5}{'mean_r':>8}{'win%':>6}"
          f"  || {'ewSh':>6}{'ewTot':>7}{'ewDD':>6}"
          f"  || {'affN':>5}{'bwSh':>6}{'bwTot':>7}{'bwDD':>6}")
    for cfg in _FYS:
        if cfg.label not in per_fy["WINDOWED"]:
            continue
        for arm in ("WINDOWED", "SAMEDAY"):
            m, (esh, etot, edd), (bsh, btot, bdd), naff = per_fy[arm][cfg.label]
            mr = f"{m.mean_r*100:+.2f}%" if m.mean_r is not None else "—"
            wr = f"{m.win_rate*100:.0f}%" if m.win_rate is not None else "—"
            oos = " OOS" if cfg.label == "FY2025" else ""
            print(f"{cfg.label:<8}{arm:<10}{m.n:>5}{mr:>8}{wr:>6}  || "
                  f"{esh:>6.2f}{etot*100:>6.0f}%{edd*100:>5.0f}%  || "
                  f"{naff:>5}{bsh:>6.2f}{btot*100:>6.0f}%{bdd*100:>5.0f}%{oos}")
        print()

    print("-" * 96)
    print("STITCHED (all FYs):")
    for arm in ("WINDOWED", "SAMEDAY"):
        esh, etot, edd = _book(stitched_ew[arm])
        bsh, btot, bdd = _book(stitched_bw[arm])
        ntot = sum(per_fy[arm][f][0].n for f in per_fy[arm])
        print(f"  {arm:<10} n={ntot:<4}  ew Sharpe {esh:+.2f} tot {etot*100:+.0f}% DD {edd*100:.0f}%"
              f"   ||  bw Sharpe {bsh:+.2f} tot {btot*100:+.0f}% DD {bdd*100:.0f}%")
    print("\nNOTE: WINDOWED is production (fire valid for valid_bars days). SAMEDAY forces "
          "valid_bars=0 (fire counts only on fire date). Both share caches / fill order / "
          "affordability filter. One deterministic draw each (fill-order null band ~+0.6..+1.2).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
