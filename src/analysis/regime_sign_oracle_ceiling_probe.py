"""RegimeSign oracle-ceiling probe — perfect-foresight headroom per axis (read-only, diagnostic).

Backlog item 1 (`docs/analysis/regime_sign_improvement_backlog.md`): quantify the THEORETICAL ceiling of
the shipped RegimeSignStrategy on the capital-aware ¥2M 6-slot book, so we know which axis (selection vs
exit) has room IN PRINCIPLE before spending a pre-registration. The sister probe for Confluence is
`confluence_oracle_ceiling_probe.py`; this uses the same capital-aware weighting (cb._BUDGET / cb._SLOTS /
recommended_lots / position_weight) so the two oracle tables are directly comparable.

Books (all capital-aware, deployed-capital weighted, same shipped candidate pool / same FYs):
  baseline           — production: ZsTpSl exit, chronological fill-order, 6-slot caps.
  oracle-SELECTION   — same exit, but each day the 6 slots are filled with the candidates whose STANDALONE
                       (cap-free) ZsTpSl return turns out best (perfect foresight on selection).
  oracle-EXIT(hold)  — same entries, each trade exits at its MAX-CLOSE bar WITHIN its baseline hold window
                       (perfect exit timing, no extra holding → conservative).
  oracle-EXIT(+60)   — same entries, exit at max close within [fill, fill+60] (allowed to hold longer →
                       optimistic upper bound; ignores extra slot occupancy).
  oracle-BOTH        — oracle selection + oracle-exit(hold).

Candidate POOL is the exact shipped RegimeSign set (`build_fy_candidates`); caches are reloaded with a
+90-day post-FY window so the +60 oracle-exit lookahead is accurate (the production `_load_cache` stops at
FY-end). Interpretation: the gap (oracle − baseline) per axis = perfect-foresight headroom. The REALIZABLE
selection ceiling is the fill-order null band (RegimeSign: Sharpe mean +1.03, p5 +0.90, p95 +1.19, baseline
p40 — `regime_sign_fill_order_null.py`); a big oracle-SELECTION gap is room IN PRINCIPLE that the tight
null says is NOT exploitable. Read-only, no gate.
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_oracle_ceiling_probe
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

from loguru import logger

import src.analysis.confluence_benchmark as cb
import src.exit.exit_simulator as exsim
from src.analysis.confluence_oracle_ceiling_probe import _EXT, _bw_daily, _mfe, _stats
from src.analysis.regime_sign_backtest import (
    EXIT_RULE,
    RS_FY_CONFIGS,
    build_fy_candidates,
)
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import recommended_lots
from src.simulator.cache import DataCache


def _reload_caches(codes, start, end):
    """Reload OHLCV caches with a +90-day post-FY window for accurate oracle-exit lookahead."""
    ss = start - datetime.timedelta(days=cb._LOOKBACK if hasattr(cb, "_LOOKBACK") else 200)
    se = end + datetime.timedelta(days=90)
    caches = {}
    with get_session() as s:
        for code in codes:
            c = DataCache(code, "1d")
            c.load(s,
                   datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                   datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            if c.bars:
                caches[code] = c
    return caches


def run() -> None:
    orig_hi, orig_lo = exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR
    books = {k: [] for k in ("base", "sel", "exh", "exe", "both")}
    ntr = {k: 0 for k in books}

    for cfg in RS_FY_CONFIGS:
        cs = build_fy_candidates(cfg)
        if not cs.candidates or cs.n225_cache is None:
            logger.info("  {} skipped (no candidates)", cfg.label)
            continue

        codes = {c.stock_code for c in cs.candidates}
        caches = _reload_caches(codes, cfg.start, cfg.end)
        n225 = _reload_caches({"^N225"}, cfg.start, cfg.end).get("^N225") or cs.n225_cache

        n_dts, _ = cb._closes(n225)
        stock_dts = {code: cb._closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        cands = sorted((c for c in cs.candidates if c.stock_code in caches),
                       key=lambda c: c.entry_date)

        def _affordable(c) -> bool:
            _, cmap = stock_dts.get(c.stock_code, ([], {}))
            px = cmap.get(c.entry_date)
            return px is not None and recommended_lots(cb._BUDGET, float(px), cb._SLOTS) > 0
        cands_aff = [c for c in cands if _affordable(c)]

        # standalone (cap-free) return per candidate → oracle selection key
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10**9, 10**9
        res_all = run_simulation(cands_aff, EXIT_RULE, caches, cfg.end)
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = orig_hi, orig_lo
        ret_of = {(r.stock_code, r.entry_date): r.exit_price / r.entry_price - 1.0
                  for r in res_all}

        def _oracle_sel(today, todays, open_pos):
            return sorted(todays, key=lambda c: ret_of.get((c.stock_code, c.entry_date), -9e9),
                          reverse=True)

        res_base = run_simulation(cands_aff, EXIT_RULE, caches, cfg.end)
        res_sel = run_simulation(cands_aff, EXIT_RULE, caches, cfg.end, day_selector=_oracle_sel)

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
    print("REGIMESIGN ORACLE-CEILING — capital-aware ¥2M 6-slot book, FY2019–2025 (perfect foresight)")
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

    print(f"\n  realizable reference (fill-order null, same strategy): Sharpe mean +1.03, "
          f"p5 +0.90, p95 +1.19; shipped baseline p40 (regime_sign_fill_order_null.py).")
    print("  → the selection null band is TIGHT (sd 0.09) — little realizable selection headroom.")
    print("\nHOW TO READ:")
    print("• oracle gap = perfect-foresight headroom on that axis. A big oracle-SELECTION gap is room IN\n"
          "  PRINCIPLE, but the tight fill-order null says selection is not exploitable by an ex-ante rule\n"
          "  at this contention. A big oracle-EXIT gap is the more actionable signal IF a tractable causal\n"
          "  rule can capture a fraction (cf. backlog items 2/5).\n"
          "• exit(within-hold) is conservative (exits ≤ baseline date, frees slots earlier but freed slots\n"
          "  are NOT re-filled here); exit(+60) is optimistic (ignores extra occupancy). True exit ceiling\n"
          "  sits between them. DIAGNOSTIC ONLY — no deployment decision.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
