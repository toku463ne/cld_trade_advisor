"""Prefer-high-deployed-capital slot ordering vs fill-order null (budget book).

Operator (2026-05-30): when candidates compete for the 6 slots, prefer the "most
expensive" stock by total position value (lots × 100 × price) — i.e. the candidate
that deploys the MOST capital into its slot (least integer-lot cash drag, most fully
invested). Does it improve the portfolio?

This is a capital-efficiency / EXPOSURE key, not a predicted-return key — and it only
bites on the BUDGET book (¥2M, integer 100-share lots, deployed-capital weights). On
the idealized equal-weight r/6 book it is a no-op, so the existing nulls (which use the
equal-weight book) can't see it. This harness weights daily returns by the realized
deployed capital, exactly like confluence_benchmark's budget book.

Method (paired, cf. confluence_sameday_priority_null): one pool = production WINDOWED
*affordable* candidates. Priority key = lots(signal price)×100×signal_price. run_simulation
stable-sorts by entry_date, so input order = within-day fill tiebreak. Per seed k:
  arm A (null)     : shuffle(pool, k)                         -> random within-day
  arm B (deployed) : stable_sort(shuffle(pool,k), -deployed)  -> richest-slot first
Δ = B − A per seed on the BUDGET-book stitched Sharpe / return / maxDD.

REAL only if P(Δ Sharpe>0) >= 0.95 AND 95% CI excludes 0. (Watch return & DD too — an
exposure rule can buy return at proportional risk = Sharpe wash + worse DD.)

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_deployed_capital_null
"""
from __future__ import annotations

import datetime
import random
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _BUDGET, _SLOTS
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import _closes, _metrics, _pos_daily, _sharpe
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_notional, position_weight, recommended_lots
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200


def _fires(signs):
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(signs)))).all()
    f = defaultdict(list)
    for sg, st, fa in rows:
        f[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    return f


def _fy_budget_returns(pool, caches, cfg, stock_dts, cal):
    """Daily returns weighted by realized deployed capital (the ¥2M budget book)."""
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        lots = recommended_lots(_BUDGET, float(p.entry_price), _SLOTS)
        w = position_weight(lots, float(p.entry_price), _BUDGET)
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r * w
    return [day.get(d, 0.0) for d in cal]


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    fires = _fires(_WINDOWED)
    exsim._MAX_LOW_CORR = _SLOTS - 1

    st = {"A": [[] for _ in range(_K)], "B": [[] for _ in range(_K)]}
    depl_all: list[float] = []

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
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        pool = []
        for code in caches:
            pool += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE)

        # deployed capital at SIGNAL price (decision-time info); drop unaffordable (lots=0)
        depl: dict[int, float] = {}
        pool_aff = []
        for c in pool:
            lots = recommended_lots(_BUDGET, float(c.entry_price), _SLOTS)
            if lots <= 0:
                continue
            depl[id(c)] = position_notional(lots, float(c.entry_price))
            pool_aff.append(c)
        depl_all += list(depl.values())

        for k in range(_K):
            base = pool_aff[:]
            random.Random(k).shuffle(base)
            st["A"][k] += _fy_budget_returns(base, caches, cfg, stock_dts, cal)[1:]
            prio = sorted(base, key=lambda c: -depl[id(c)])   # richest slot first
            st["B"][k] += _fy_budget_returns(prio, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} affordable cands, {} shuffles)", cfg.label, len(pool_aff), _K)

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in ("A", "B")}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in ("A", "B")}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in ("A", "B")}

    print("\n" + "=" * 84)
    print(f"PREFER-HIGH-DEPLOYED-CAPITAL vs RANDOM FILL — {_K} paired shuffles, ¥{_BUDGET:,} budget book")
    print("=" * 84)
    d_arr = np.array(depl_all)
    print(f"\ndeployed-capital per affordable slot: mean ¥{d_arr.mean():,.0f}  "
          f"p10 ¥{np.percentile(d_arr,10):,.0f}  p90 ¥{np.percentile(d_arr,90):,.0f}  "
          f"(slot budget ¥{_BUDGET//_SLOTS:,})")
    print(f"\n{'arm':<26}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a, lbl in [("A", "random fill (null)"), ("B", "prefer-deployed-capital")]:
        s_ = sh[a]
        print(f"{lbl:<26}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.0f}%")

    d = sh["B"] - sh["A"]
    print(f"\n[paired Δ = prefer-deployed − random, same seed each draw]")
    print(f"  Δ Sharpe mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
          f"P(Δ>0)={(d>0).mean():.3f}")
    dr = rt["B"] - rt["A"]; ddd = dd["B"] - dd["A"]
    print(f"  Δ return mean {dr.mean()*100:+.0f}pp (P(Δ>0)={(dr>0).mean():.3f}) | "
          f"Δ maxDD mean {ddd.mean()*100:+.1f}pp (negative = DEEPER DD)")

    verdict = ("REAL — deployed-capital ordering beats random fill net of order luck"
               if (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
               else "NOT separated — capital-efficiency ordering doesn't beat random fill")
    print(f"\n  VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
