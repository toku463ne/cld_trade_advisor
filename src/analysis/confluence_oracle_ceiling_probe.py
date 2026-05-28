"""Confluence oracle-ceiling probe — perfect-foresight headroom per axis (read-only, diagnostic).

Backlog item 1 (docs/analysis/confluence_improvement_backlog.md): quantify the THEORETICAL ceiling of
the shipped ConfluenceSignStrategy on the capital-aware ¥2M 6-slot book, so we know which axis has room
IN PRINCIPLE before spending effort on a pre-reg. Read-only, no gate (diagnostic).

Books (all capital-aware, deployed-capital weighted, same fires / same FYs):
  baseline           — production: ZsTpSl exit, chronological fill-order, 6-slot caps. (≈ shipped +0.88)
  oracle-SELECTION   — same exit, but each day the 6 slots are filled with the candidates whose
                       STANDALONE return turns out best (perfect foresight on selection). day_selector
                       ordered by each candidate's cap-free ZsTpSl return.
  oracle-EXIT(hold)  — same entries as baseline, but each trade exits at its MAX-CLOSE bar WITHIN its
                       baseline hold window (perfect exit TIMING, no extra holding → conservative).
  oracle-EXIT(+60)   — same entries, exit at max close within [fill, fill+60] (allowed to hold longer →
                       optimistic upper bound; ignores the extra slot occupancy).
  oracle-BOTH        — oracle selection + oracle-exit(hold).

Interpretation: the gap (oracle − baseline) per axis = the perfect-foresight headroom. The REALIZABLE
ceiling for "pick better entries from this pool" is the fill-order null band (Sharpe median 0.89, p5
0.60, p95 1.20 — every ex-ante rule landed inside it; shipped +0.88 is a median draw). A large
oracle-SELECTION gap means selection has room in principle but the null says it's not exploitable at
this contention; a large oracle-EXIT gap with a tractable rule is the more promising axis. Read-only.
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_oracle_ceiling_probe
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_benchmark as cb
import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

_EXT = 60   # oracle-exit extended look-ahead cap (bars)


def _bw_daily(results, stock_dts, cal):
    cal_set = set(cal)
    day: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        lots = recommended_lots(cb._BUDGET, float(p.entry_price), cb._SLOTS)
        w = position_weight(lots, float(p.entry_price), cb._BUDGET)
        for d, r in cb._pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r * w
    return [day.get(d, 0.0) for d in cal]


def _mfe(results, stock_dts, ext=None):
    """Rewrite each trade's exit to the max-close bar in its window. ext=None → within the baseline
    hold [fill .. baseline_exit]; ext=N → [fill .. fill+N] (may extend past baseline exit)."""
    out = []
    for r in results:
        dts, cmap = stock_dts.get(r.stock_code, ([], {}))
        if r.entry_date not in cmap or not dts:
            out.append(r); continue
        ie = dts.index(r.entry_date)
        try:
            ix = dts.index(r.exit_date)
        except ValueError:
            out.append(r); continue
        hi = ix if ext is None else min(ie + ext, len(dts) - 1)
        lo = ie + 1                                  # earliest exit = fill bar (next bar)
        if hi <= lo:
            out.append(r); continue
        win = dts[lo:hi + 1]
        best = max(win, key=lambda d: cmap[d])
        out.append(r._replace(exit_date=best, exit_price=cmap[best]))
    return out


def _stats(rets):
    sh, tot, dd = cb._book(rets)
    n = len(rets)
    cagr = (1.0 + tot) ** (252.0 / n) - 1.0 if n > 1 and (1.0 + tot) > 0 else float("nan")
    return sh, cagr, tot, dd


def run() -> None:
    cbt._VALID_BARS = dict(cb._BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(cb._BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    orig_hi, orig_lo = exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR
    books = {k: [] for k in ("base", "sel", "exh", "exe", "both")}
    ntr = {k: 0 for k in books}

    for cfg in cb._FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=90)
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
        n_dts, _ = cb._closes(n225)
        stock_dts = {code: cb._closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        cands = []
        for code in caches:
            cands += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code], corr_maps[code], zs_maps[code],
                cfg.start, cfg.end, cb._N_GATE)
        cands.sort(key=lambda c: c.entry_date)

        def _affordable(c) -> bool:
            _, cmap = stock_dts.get(c.stock_code, ([], {}))
            px = cmap.get(c.entry_date)
            return px is not None and recommended_lots(cb._BUDGET, float(px), cb._SLOTS) > 0
        cands_aff = [c for c in cands if _affordable(c)]

        # standalone (cap-free) return per candidate → oracle selection key
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10**9, 10**9
        res_all = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end)
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = orig_hi, orig_lo
        ret_of = {}
        for r in res_all:
            ret_of[(r.stock_code, r.entry_date)] = r.exit_price / r.entry_price - 1.0

        def _oracle_sel(today, todays, open_pos):
            return sorted(todays, key=lambda c: ret_of.get((c.stock_code, c.entry_date), -9e9),
                          reverse=True)

        res_base = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end)
        res_sel = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end, day_selector=_oracle_sel)

        variants = {
            "base": res_base,
            "sel": res_sel,
            "exh": _mfe(res_base, stock_dts, None),
            "exe": _mfe(res_base, stock_dts, _EXT),
            "both": _mfe(res_sel, stock_dts, None),
        }
        for k, res in variants.items():
            books[k] += _bw_daily(res, stock_dts, cal)[1:]
            ntr[k] += len(res)
        logger.info("  {} done ({} base trades)", cfg.label, len(res_base))

    print("\n" + "=" * 96)
    print("CONFLUENCE ORACLE-CEILING — capital-aware ¥2M 6-slot book, FY2018–2025 (perfect foresight)")
    print("=" * 96)
    print(f"  {'book':<28}{'Sharpe':>8}{'CAGR':>8}{'total':>9}{'maxDD':>8}{'trades':>8}  Δ Sharpe")
    base_sh = _stats(books["base"])[0]
    labels = {
        "base": "baseline (ZsTpSl, prod)",
        "sel": "oracle SELECTION",
        "exh": "oracle EXIT (within hold)",
        "exe": f"oracle EXIT (+{_EXT}-bar)",
        "both": "oracle BOTH (sel+exit-hold)",
    }
    for k in ("base", "sel", "exh", "exe", "both"):
        sh, cagr, tot, dd = _stats(books[k])
        dlt = "" if k == "base" else f"  {sh - base_sh:+.2f}"
        print(f"  {labels[k]:<28}{sh:>8.2f}{cagr * 100:>7.1f}%{tot * 100:>8.1f}%"
              f"{dd * 100:>7.1f}%{ntr[k]:>8}{dlt}")

    print(f"\n  realizable reference (fill-order null, same strategy): Sharpe median 0.89, "
          f"p5 0.60, p95 1.20")
    print("  → shipped baseline is a median draw; p95 is luck not edge.")
    print("\nHOW TO READ:")
    print("• oracle gap = perfect-foresight headroom on that axis. A big oracle-SELECTION gap means\n"
          "  selection has room IN PRINCIPLE — but the fill-order null already showed it is NOT\n"
          "  exploitable by any ex-ante rule at this contention (selection is exhausted). A big\n"
          "  oracle-EXIT gap is the more actionable signal IF a tractable (causal) rule can capture a\n"
          "  fraction of it — regime-conditional exit (backlog item 3) is the candidate.\n"
          "• exit(within-hold) is conservative (exits ≤ baseline date, frees slots earlier but the\n"
          "  freed slots are NOT re-filled here); exit(+60) is optimistic (ignores extra occupancy).\n"
          "  The true exit ceiling sits between them. DIAGNOSTIC ONLY — no deployment decision.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
