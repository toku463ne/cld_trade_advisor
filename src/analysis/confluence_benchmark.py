"""Canonical per-FY benchmark of ConfluenceSignStrategy (current rules, 6-slot book).

Regenerated 2026-05-23 on the rebuilt data (OHLCV 2017-2026, 8 FY clusters, full
sign_benchmark) to replace the stale 2026-05-17 per-FY table in
docs/analysis/confluence_strategy.md, which reported PER-TRADE Sharpe only
(FY2019-2025) and predated the 6-slot ship + the capital-aware reframing.

Reports, per FY (FY2018-FY2025), TWO metric families so the magnitudes aren't
confused:
  - PER-TRADE (from exit_benchmark._metrics, the original v3 column): n, mean_r,
    win%, per-trade Sharpe, hold_bars. These are large (e.g. +8) — they are NOT
    annualized book Sharpe.
  - CAPITAL-AWARE BOOK (the real metric): each filled position marked daily /
    n_slots (6), stitched to a daily equity curve → annualized Sharpe (×√252),
    total return, maxDD. This is the ~0.6-1.2 band the fill-order null established;
    the deterministic (sorted entry_date) order is one draw of that band.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_benchmark
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
from src.analysis.exit_benchmark import FyConfig, _metrics
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 6   # production after the 6-slot ship (_MAX_LOW_CORR=5)
# Real target account: ¥2,000,000 traded in 単元株 (100-share lots). A name whose one
# lot won't fit a budget/_SLOTS slot (price > ~¥3,333) is unaffordable and never
# consumes a slot; affordable names are weighted by *deployed* capital (integer lots →
# cash drag), not an idealized 1/_SLOTS. See src.portfolio.sizing.
_BUDGET = 2_000_000
_FYS = [FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


def _closes(cache):
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


def _book(rets):
    """capital-aware annualized Sharpe, total return, maxDD from a daily series."""
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    return sh, float(eq[-1] - 1.0), float((eq / runmax - 1.0).min())


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0   # DB holds only fresh post-rebuild runs
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    per_fy = {}
    stitched_ew: list[float] = []
    stitched_bw: list[float] = []

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
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

        cands = []
        for code in caches:
            cands += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code], corr_maps[code], zs_maps[code],
                cfg.start, cfg.end, _N_GATE)
        cands.sort(key=lambda c: c.entry_date)   # deterministic canonical order

        # Affordability pre-filter: a name whose ~entry-day price can't fit one 100-sh
        # lot in a budget/_SLOTS slot can't be held at _BUDGET, so it must NOT consume a
        # slot (a cheaper name takes it). Price ≈ close at entry_date — the small
        # open/next-open difference never flips affordability near the ~¥3,333 line.
        def _affordable(c) -> bool:
            _, cmap = stock_dts.get(c.stock_code, ([], {}))
            px = cmap.get(c.entry_date)
            return px is not None and recommended_lots(_BUDGET, float(px), _SLOTS) > 0
        cands_aff = [c for c in cands if _affordable(c)]

        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
        results_bw = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end)

        m = _metrics(results)   # per-trade metrics (matches the original v3 column)
        cal_set = set(cal)

        # equal-weight book (idealized r/_SLOTS — the historical reference column)
        day_ew: defaultdict[datetime.date, float] = defaultdict(float)
        for p in results:
            sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
            for d, r in _pos_daily(p, sdts, scmap).items():
                if d in cal_set:
                    day_ew[d] += r / _SLOTS

        # budget-constrained book: deployed-capital weight (integer lots → cash drag)
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
        per_fy[cfg.label] = (m, _book(rets_ew), _book(rets_bw), len(results_bw))
        stitched_ew += rets_ew[1:]
        stitched_bw += rets_bw[1:]
        logger.info("  {} done ({} trades, {} affordable @ ¥{:,})",
                    cfg.label, m.n, len(results_bw), _BUDGET)

    print("\n" + "=" * 104)
    print("CONFLUENCE BENCHMARK — current rules, N>=3, 6-slot book, rebuilt data (2026-05-23)")
    print("=" * 104)
    print(f"\n{'FY':<8}{'n':>5}{'mean_r':>8}{'win%':>6}{'perSh':>7}{'hold':>5}"
          f"  || {'ewSh':>6}{'ewTot':>7}{'ewDD':>6}"
          f"  || {'affN':>5}{'bwSh':>6}{'bwTot':>7}{'bwDD':>6}")
    print(f"  per-trade (not annualized)        || equal-wt r/{_SLOTS} book"
          f"   || budget ¥{_BUDGET:,} ({_SLOTS}-slot, 100-sh lots)")
    for cfg in _FYS:
        if cfg.label not in per_fy:
            continue
        m, (esh, etot, edd), (bsh, btot, bdd), naff = per_fy[cfg.label]
        oos = " OOS" if cfg.label == "FY2025" else ""
        mr = f"{m.mean_r*100:+.2f}%" if m.mean_r is not None else "—"
        wr = f"{m.win_rate*100:.0f}%" if m.win_rate is not None else "—"
        hb = f"{m.hold_bars:.0f}" if m.hold_bars is not None else "—"
        psh = f"{m.sharpe:+.2f}" if (m.sharpe is not None and not math.isnan(m.sharpe)) else "—"
        print(f"{cfg.label:<8}{m.n:>5}{mr:>8}{wr:>6}{psh:>7}{hb:>5}  || "
              f"{esh:>6.2f}{etot*100:>6.0f}%{edd*100:>5.0f}%  || "
              f"{naff:>5}{bsh:>6.2f}{btot*100:>6.0f}%{bdd*100:>5.0f}%{oos}")

    esh, etot, edd = _book(stitched_ew)
    bsh, btot, bdd = _book(stitched_bw)
    pos = sum(1 for cfg in _FYS if cfg.label in per_fy and per_fy[cfg.label][2][0] > 0)
    nfy = len(per_fy)
    print(f"\n  STITCHED equal-wt  book: Sharpe {esh:+.2f} | total {etot*100:+.0f}% | maxDD {edd*100:.0f}%")
    print(f"  STITCHED budget    book: Sharpe {bsh:+.2f} | total {btot*100:+.0f}% | maxDD {bdd*100:.0f}% "
          f"| budget-Sharpe positive {pos}/{nfy} FYs")
    print(f"  NOTE: budget book skips names unaffordable at ¥{_BUDGET:,}/{_SLOTS}-slot "
          f"(price > ~¥{_BUDGET/_SLOTS/100:,.0f}) and weights by deployed capital (integer "
          "100-sh lots → cash drag) — the realistic live book. Equal-wt is the idealized "
          "reference. Both are ONE deterministic (sorted entry_date) fill-order draw "
          "(null band ~+0.6..+1.2). Per-trade Sharpe is NOT annualized.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
