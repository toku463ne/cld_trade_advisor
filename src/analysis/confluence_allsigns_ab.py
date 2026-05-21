"""Confluence A/B — bullish-only count (production) vs DIRECTION-AGNOSTIC count.

Arm A (production): count valid signs from the 10-sign bullish set, fire N>=3.
Arm B (agnostic):   count ALL valid catalogue signs (any direction), sweep N.

Question: does raw signal DENSITY (valid_n, direction-agnostic) predict up-moves
as well as the bullish-filtered count?  Counting ~22 signs makes a given N far
weaker, so we sweep N for arm B and compare at MATCHED selectivity (trade count
closest to arm-A pool). Same ZsTpSl/portfolio/cooldown/2-bar fill, FY2019-25.

Δ = agnostic(N*) − bullish(N=3). Binding gates: trade-level boot CI excl 0 AND
FY-level CI excl 0 AND per-FY sign-consistent AND FY2025 (OOS) positive.

OUTCOME (2026-05-21): REJECT, but closest contender yet. Filter & threshold are
substitutes — agnostic needs N>=4 for ~245 trades. Point estimates straddle
production (bull3 pool Sharpe +3.28; all4 +3.60, all3 +2.22). No near-matched arm
(all3-all6) clears the gates: every trade+FY CI spans 0, per-FY tops 5/7. KEY:
agnostic (N>=4) wins 5/7 FYs AND OOS FY2025 (+2.2..+2.9) but COLLAPSES FY2024
(−5.5..−8.4). The bullish label isn't the edge — it's regime insurance vs
bearish-heavy years. Keep production. Follow-up: agnostic count + bearish-activity
veto (targets FY2024 directly) — but a binary bearish veto already rejected (bowl
shape, not monotone). See memory project_confluence_agnostic_count_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_allsigns_ab
"""
from __future__ import annotations

import datetime
import math
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select, text

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import _metrics
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_NBOOT = 5000
_SEED = 20260521
_BULLISH = ("str_hold", "str_lead", "str_lag", "brk_sma", "brk_bol",
            "rev_lo", "rev_nlo", "brk_kumo_hi", "brk_tenkan_hi", "chiko_hi")
_BULL_N = 3
_AGNOSTIC_NS = [3, 4, 5, 6, 7, 8]
_VB3 = {"str_hold", "brk_bol"}   # detector-default 3-bar validity; else 5


def _all_signs() -> list[str]:
    with get_session() as s:
        return [r[0] for r in s.execute(text(
            "select distinct r.sign_type from sign_benchmark_events e "
            "join sign_benchmark_runs r on r.id=e.run_id where r.id>=:m "
            "order by 1"), {"m": cbt._MULTIYEAR_MIN_RUN_ID}).all()]


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
    sd = statistics.stdev(rets)
    return (statistics.mean(rets) / sd * math.sqrt(252)) if sd > 0 else float("nan")


def _boot_diff(b_rets, a_rets, rng):
    a, b = np.asarray(a_rets), np.asarray(b_rets)
    d = np.empty(_NBOOT)
    for i in range(_NBOOT):
        ra, rb = rng.choice(a, len(a), True), rng.choice(b, len(b), True)
        sa = ra.mean() / ra.std(ddof=1) * math.sqrt(252) if ra.std(ddof=1) > 0 else 0
        sb = rb.mean() / rb.std(ddof=1) * math.sqrt(252) if rb.std(ddof=1) > 0 else 0
        d[i] = sb - sa
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)), float((d <= 0).mean())


def run() -> None:
    all_signs = _all_signs()
    logger.info("all-signs catalogue ({}): {}", len(all_signs), all_signs)
    fires_all = _load_fires(all_signs)
    fires_bull = {k: [(s, d) for s, d in v if s in _BULLISH] for k, v in fires_all.items()}
    rng = np.random.default_rng(_SEED)

    # arm key -> fy -> [returns]
    arms = ["bull3"] + [f"all{n}" for n in _AGNOSTIC_NS]
    per = {a: {} for a in arms}
    pooled = {a: [] for a in arms}

    for cfg in RS_FY_CONFIGS:
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
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}

        def _run(fires_by, n_gate):
            cands = []
            for code in caches:
                cands.extend(cbt._candidates_for_stock(
                    code, fires_by.get(code, []), caches[code],
                    corr_maps.get(code, {}), zs_maps.get(code, {}),
                    cfg.start, cfg.end, n_gate))
            res = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
            return [r.return_pct for r in res]

        cbt._VALID_BARS = {s: (3 if s in _VB3 else 5) for s in _BULLISH}
        per["bull3"][cfg.label] = _run(fires_bull, _BULL_N)
        pooled["bull3"].extend(per["bull3"][cfg.label])

        cbt._VALID_BARS = {s: (3 if s in _VB3 else 5) for s in all_signs}
        for n in _AGNOSTIC_NS:
            r = _run(fires_all, n)
            per[f"all{n}"][cfg.label] = r
            pooled[f"all{n}"].extend(r)
        logger.info("  {} done (bull3 n={})", cfg.label, len(per["bull3"][cfg.label]))

    fys = [c.label for c in RS_FY_CONFIGS]
    print("\n" + "=" * 78)
    print("CONFLUENCE: bullish-only (N=3) vs DIRECTION-AGNOSTIC count (sweep N)")
    print("=" * 78)
    print(f"{'arm':<10}{'pool_n':>8}{'pool_Sh':>9}{'pool_mr':>9}{'FYeq_Sh':>9}  per-FY trades")
    bn = len(pooled["bull3"])
    for a in arms:
        pn = len(pooled[a])
        psh = _sharpe(pooled[a])
        pmr = statistics.mean(pooled[a]) * 100 if pooled[a] else float("nan")
        fysh = [_sharpe(per[a][f]) for f in fys if len(per[a].get(f, [])) >= 2]
        fyeq = statistics.mean([x for x in fysh if not math.isnan(x)]) if fysh else float("nan")
        tcounts = "/".join(str(len(per[a].get(f, []))) for f in fys)
        tag = "  <- production" if a == "bull3" else ""
        print(f"{a:<10}{pn:>8}{psh:>9.2f}{pmr:>9.2f}{fyeq:>9.2f}  {tcounts}{tag}")

    # Gate every near-matched agnostic arm (within ±15% of bull3 pool count)
    near = [a for a in arms if a != "bull3" and abs(len(pooled[a]) - bn) <= 0.15 * bn]
    print(f"\nNear-matched agnostic arms (±15% of bull3 n={bn}): {near}")
    for star in near:
        fy_d = [(_sharpe(per[star][f]) - _sharpe(per['bull3'][f]))
                for f in fys if len(per[star].get(f, [])) >= 2 and len(per['bull3'].get(f, [])) >= 2]
        lo_t, hi_t, p_t = _boot_diff(pooled[star], pooled["bull3"], rng)
        fa = np.array(fy_d)
        bl = rng.choice(fa, (_NBOOT, len(fa)), True).mean(axis=1)
        lo_f, hi_f = np.percentile(bl, [2.5, 97.5])
        d25 = _sharpe(per[star].get("FY2025", [])) - _sharpe(per["bull3"].get("FY2025", []))
        pos = sum(1 for d in fy_d if d > 0)
        gates = (lo_t > 0) + (lo_f > 0) + (pos >= len(fy_d) - 1) + (d25 > 0)
        print(f"\n── {star} (n={len(pooled[star])}) vs bull3 — Δ=agnostic−bullish — {gates}/4 gates ──")
        print(f"  per-FY ΔSharpe: " +
              " ".join(f"{f[2:]}:{_sharpe(per[star].get(f,[]))-_sharpe(per['bull3'].get(f,[])):+.2f}" for f in fys))
        print(f"  trade-level boot CI [{lo_t:+.2f},{hi_t:+.2f}] p(Δ≤0)={p_t:.3f}  {'PASS' if lo_t>0 else 'FAIL'}")
        print(f"  FY-level boot CI    [{lo_f:+.2f},{hi_f:+.2f}]  {'PASS' if lo_f>0 else 'FAIL'}")
        print(f"  per-FY positive {pos}/{len(fy_d)}  {'PASS' if pos>=len(fy_d)-1 else 'FAIL'}")
        print(f"  FY2025 OOS ΔSharpe {d25:+.2f}  {'PASS' if d25>0 else 'FAIL'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
