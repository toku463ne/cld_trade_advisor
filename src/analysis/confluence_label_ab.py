"""Confluence A/B — a-priori bullish set vs DATA-DERIVED bullish set, N>=3.

Arm A (a-priori, production confluence_sign._BULLISH_SIGNS, 10 signs):
  str_hold str_lead str_lag brk_sma brk_bol rev_lo rev_nlo
  brk_kumo_hi brk_tenkan_hi chiko_hi

Arm B (data-derived bullish, discover FY2010-16 excess>+0.1pp, 12 signs):
  brk_wall brk_sma rev_lo corr_shift str_hold brk_kumo_lo brk_bol
  corr_flip chiko_lo brk_kumo_hi div_peer rev_nhold

Same everything else as confluence_strategy_backtest (ZsTpSl 2/2/0.3, portfolio
caps, 2-bar fill, 10-bar cooldown), FY2019-FY2025. Δ = B − A (positive = data
labels win). Binding gates: trade-level bootstrap CI excl 0 AND FY-level CI excl
0 AND per-FY sign-consistent AND FY2025 (OOS) positive.

OUTCOME (2026-05-21): REJECT — data-derived labels are net WORSE. Pool Sharpe
a-priori +3.28 (245 trades) vs data +2.87 (224), Δ −0.41; FY-eq avg +3.30 vs
+2.93. 3/4 gates FAIL (trade CI [−3.32,+2.46] p=0.614; FY CI [−3.10,+2.00];
per-FY 5/7; only FY2025 OOS +1.14 PASS). FY2024 collapse +6.48→−1.24 dominates.
Lesson: unconditional per-sign directionality is the WRONG membership criterion
for a confluence gate — what matters is behavior IN confluence. Keep the
production set; re-open via per-sign leave-one-out, not a wholesale label swap.
See memory project_confluence_label_swap_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_label_ab
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
from src.analysis.exit_benchmark import _metrics
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_N_GATE = 3
_NBOOT = 5000
_SEED = 20260521

_APRIORI = {
    "str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
    "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5,
}
_DATA = {
    "brk_wall": 5, "brk_sma": 5, "rev_lo": 5, "corr_shift": 5, "str_hold": 3,
    "brk_kumo_lo": 5, "brk_bol": 3, "corr_flip": 5, "chiko_lo": 5,
    "brk_kumo_hi": 5, "div_peer": 5, "rev_nhold": 5,
}
_ARMS = {"apriori": _APRIORI, "data": _DATA}


def _load_fires(signs) -> dict[str, list[tuple[str, datetime.date]]]:
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(signs)))
        ).all()
    by_stock: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sign, stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        by_stock[stock].append((sign, d))
    return by_stock


def _sharpe(rets) -> float:
    if len(rets) < 2:
        return float("nan")
    mr = statistics.mean(rets)
    sd = statistics.stdev(rets)
    return (mr / sd * math.sqrt(252)) if sd > 0 else float("nan")


def _boot_sharpe_diff(b_rets, a_rets, rng):
    a = np.asarray(a_rets); b = np.asarray(b_rets)
    diffs = np.empty(_NBOOT)
    for i in range(_NBOOT):
        ra = rng.choice(a, len(a), replace=True)
        rb = rng.choice(b, len(b), replace=True)
        sa = ra.mean() / ra.std(ddof=1) * math.sqrt(252) if ra.std(ddof=1) > 0 else 0
        sb = rb.mean() / rb.std(ddof=1) * math.sqrt(252) if rb.std(ddof=1) > 0 else 0
        diffs[i] = sb - sa
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)), float((diffs <= 0).mean())


def run() -> None:
    fires = {arm: _load_fires(signs) for arm, signs in _ARMS.items()}
    rng = np.random.default_rng(_SEED)

    # per FY, per arm: list of trade returns
    per = {arm: {} for arm in _ARMS}
    pooled = {arm: [] for arm in _ARMS}

    for cfg in RS_FY_CONFIGS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        span_start = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        span_end = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(span_end, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches: dict[str, DataCache] = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(span_end, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}

        for arm, vb in _ARMS.items():
            cbt._VALID_BARS = vb               # _candidates_for_stock reads this global
            all_cands = []
            for code in caches:
                all_cands.extend(cbt._candidates_for_stock(
                    code, fires[arm].get(code, []), caches[code],
                    corr_maps.get(code, {}), zs_maps.get(code, {}),
                    cfg.start, cfg.end, _N_GATE))
            results = run_simulation(all_cands, cbt._EXIT_RULE, caches, cfg.end)
            rets = [r.return_pct for r in results]
            per[arm][cfg.label] = rets
            pooled[arm].extend(rets)
            m = _metrics(results)
            logger.info("  {} {}: n={} sharpe={:.2f} mean_r={:+.2%}", cfg.label, arm,
                        m.n, m.sharpe if not math.isnan(m.sharpe) else float('nan'), m.mean_r)

    # ── report ───────────────────────────────────────────────────────────────
    fys = [c.label for c in RS_FY_CONFIGS]
    print("\n" + "=" * 84)
    print(f"CONFLUENCE LABEL A/B — N>={_N_GATE}   Δ = data-derived − a-priori (Sharpe)")
    print("=" * 84)
    print(f"{'FY':<8}{'n_apr':>6}{'Sh_apr':>9}{'mr_apr':>9}{'n_dat':>7}{'Sh_dat':>9}{'mr_dat':>9}{'ΔSharpe':>9}")
    fy_deltas = []
    for fy in fys:
        ra, rb = per["apriori"].get(fy, []), per["data"].get(fy, [])
        sa, sb = _sharpe(ra), _sharpe(rb)
        ma = statistics.mean(ra) if ra else float('nan')
        mb = statistics.mean(rb) if rb else float('nan')
        d = (sb - sa) if (not math.isnan(sa) and not math.isnan(sb)) else float('nan')
        if not math.isnan(d):
            fy_deltas.append(d)
        print(f"{fy:<8}{len(ra):>6}{sa:>9.2f}{ma*100:>9.2f}{len(rb):>7}{sb:>9.2f}{mb*100:>9.2f}{d:>9.2f}")

    sa_all = _sharpe(pooled["apriori"]); sb_all = _sharpe(pooled["data"])
    print("-" * 84)
    print(f"{'POOL':<8}{len(pooled['apriori']):>6}{sa_all:>9.2f}"
          f"{statistics.mean(pooled['apriori'])*100:>9.2f}"
          f"{len(pooled['data']):>7}{sb_all:>9.2f}"
          f"{statistics.mean(pooled['data'])*100:>9.2f}{sb_all-sa_all:>9.2f}")

    # FY-equal-weighted avg Sharpe
    avg_a = statistics.mean([_sharpe(per['apriori'][f]) for f in fys if _sharpe(per['apriori'].get(f, []))==_sharpe(per['apriori'].get(f, []))])
    avg_b = statistics.mean([_sharpe(per['data'][f]) for f in fys if _sharpe(per['data'].get(f, []))==_sharpe(per['data'].get(f, []))])
    print(f"\nFY-equal-weighted avg Sharpe: a-priori {avg_a:+.2f}  |  data-derived {avg_b:+.2f}  |  Δ {avg_b-avg_a:+.2f}")

    # gates
    lo_t, hi_t, p_t = _boot_sharpe_diff(pooled["data"], pooled["apriori"], rng)
    pos = sum(1 for d in fy_deltas if d > 0)
    fy_arr = np.array(fy_deltas)
    bl = rng.choice(fy_arr, (_NBOOT, len(fy_arr)), replace=True).mean(axis=1)
    lo_f, hi_f = np.percentile(bl, [2.5, 97.5])
    fy25 = per["data"].get("FY2025", []), per["apriori"].get("FY2025", [])
    d25 = _sharpe(fy25[0]) - _sharpe(fy25[1])

    print("\n── BINDING GATES (Δ = data − a-priori) ──")
    print(f"  trade-level bootstrap ΔSharpe 95% CI: [{lo_t:+.2f}, {hi_t:+.2f}]  p(Δ≤0)={p_t:.3f}  "
          f"{'PASS' if lo_t>0 else 'FAIL'}")
    print(f"  FY-level bootstrap ΔSharpe 95% CI:    [{lo_f:+.2f}, {hi_f:+.2f}]  "
          f"{'PASS' if lo_f>0 else 'FAIL'}")
    print(f"  per-FY positive: {pos}/{len(fy_deltas)}  {'PASS' if pos>=len(fy_deltas)-1 else 'FAIL'}")
    print(f"  FY2025 (OOS) ΔSharpe: {d25:+.2f}  {'PASS' if d25>0 else 'FAIL'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
