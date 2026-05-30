"""SAMEDAY-priority ordering vs fill-order null: paired shuffles (read-only).

Operator question (2026-05-30): given the production WINDOWED candidate pool, when
more than 6 candidates compete for slots, should we PRIORITIZE the ones whose >=3
bullish signs fired TODAY ("SAMEDAY-qualifying") ahead of ones that only reach the
gate via carried (still-valid) past fires?

This is a SELECTION/ORDERING rule (same pool, reorder fills) — governed by the
fill-order-null root lesson (CLAUDE.md). Binding test: paired fill-order null.

Method (cf. confluence_drop_tenkan_null / confluence_slot_order):
  - ONE pool: production WINDOWED candidates.
  - Each candidate tagged sameday=True if >=3 distinct bullish signs fired on its
    exact entry_date (else it reached the gate only via carried fires).
  - run_simulation stable-sorts by entry_date, so input order = within-day fill
    tiebreak. For each seed k:
      arm A (null)     : shuffle(pool, k)                      -> random within-day
      arm B (priority) : stable_sort(shuffle(pool, k), by NOT-sameday)
                         -> sameday candidates promoted ahead within each day,
                            random tiebreak inside each tier (same seed)
  - Δ = B − A per seed removes fill-order luck.

REAL only if P(Δ Sharpe > 0) >= 0.95 AND 95% CI excludes 0. Else the priority key
doesn't beat random fill at this contention level.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_sameday_priority_null
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
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import (
    _closes, _fy_returns, _metrics, _sharpe,
)
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_LOW = 5


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


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    fires = _fires(_WINDOWED)
    exsim._MAX_LOW_CORR = _LOW

    # per-stock {date: set(signs fired that exact day)} for sameday tagging
    sameday_signs: dict[str, dict[datetime.date, set]] = defaultdict(lambda: defaultdict(set))
    for st, lst in fires.items():
        for sg, d in lst:
            sameday_signs[st][d].add(sg)

    st_ret = {"A": [[] for _ in range(_K)], "B": [[] for _ in range(_K)]}
    n_same = n_carry = 0

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
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        pool = []
        for code in caches:
            pool += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE)

        # tag sameday: >=N_GATE distinct signs fired on the candidate's entry_date
        def _is_sameday(c) -> bool:
            return len(sameday_signs.get(c.stock_code, {}).get(c.entry_date, set())) >= _N_GATE
        sd_flag = {id(c): _is_sameday(c) for c in pool}
        n_same += sum(sd_flag.values()); n_carry += len(pool) - sum(sd_flag.values())

        for k in range(_K):
            base = pool[:]
            random.Random(k).shuffle(base)
            # arm A: random within-day (shuffled order; run_simulation stable-sorts by date)
            st_ret["A"][k] += _fy_returns(base, caches, cfg, stock_dts, cal, _SLOTS)[1:]
            # arm B: promote sameday ahead within each day (stable on the shuffled order)
            prio = sorted(base, key=lambda c: 0 if sd_flag[id(c)] else 1)
            st_ret["B"][k] += _fy_returns(prio, caches, cfg, stock_dts, cal, _SLOTS)[1:]
        logger.info("  {} done ({} cands: {} sameday / {} carried, {} shuffles)",
                    cfg.label, len(pool), sum(sd_flag.values()),
                    len(pool) - sum(sd_flag.values()), _K)

    sh = {a: np.array([_sharpe(st_ret[a][k]) for k in range(_K)]) for a in ("A", "B")}
    rt = {a: np.array([_metrics(st_ret[a][k])[0] for k in range(_K)]) for a in ("A", "B")}
    dd = {a: np.array([_metrics(st_ret[a][k])[2] for k in range(_K)]) for a in ("A", "B")}

    print("\n" + "=" * 82)
    print(f"SAMEDAY-PRIORITY vs RANDOM FILL — {_K} paired shuffles, WINDOWED pool, 6-slot")
    print("=" * 82)
    print(f"\npool composition: {n_same} sameday-qualifying / {n_carry} carried-only "
          f"({100*n_same/(n_same+n_carry):.0f}% sameday)")
    print(f"\n{'arm':<24}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a, lbl in [("A", "random fill (null)"), ("B", "sameday-priority")]:
        s_ = sh[a]
        print(f"{lbl:<24}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.0f}%")

    d = sh["B"] - sh["A"]
    print(f"\n[paired Δ Sharpe = sameday-priority − random, same seed each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}]")
    print(f"  P(Δ > 0) = {(d > 0).mean():.3f}  ({int((d>0).sum())}/{_K} shuffles)")
    dr = rt["B"] - rt["A"]
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")

    verdict = ("REAL — sameday-priority beats random fill net of order luck"
               if (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
               else "NOT separated — priority key doesn't beat random fill (fill-order null holds)")
    print(f"\n  VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
