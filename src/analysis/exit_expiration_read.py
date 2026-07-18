"""B0 — Expiration-period read: is max_bars=40 right, and does the LIVE no-expiration book leak?

Operator question (2026-07-18): "I suspect one general tp/sl rule / expiration period."

FACTS this script pins down (grounded before any null):
  * The backtested rule is ZsTpSl(2/2/0.3) with a HARD max_bars=40 time-stop.
  * The LIVE book (`crud.evaluate_position_as_of`) enforces ONLY tp/sl — status is
    {tp_hit, sl_hit, hold}. There is NO expiration live.  So the operator runs the
    max_bars=inf variant while the benchmark that blessed ZsTpSl ran max_bars=40.
    This script measures the gap.

Design (mirrors confluence_exit_ab.py; exit is a STRUCTURAL lever → certifiable at
current n via a paired fill-order null, per project_confluence_exit_ab_reject):
  ARMS = ZsTpSl(2/2/0.3) with only max_bars swept: b15,b20,b26, b40 (CONTROL, the
  benchmarked value), b_inf (=live, no time-stop).  tp/sl geometry identical across
  arms → isolates the expiration lever alone.

  PART 1 — identical entries, NO slot cap (per-trade, deterministic):
    * exit-reason mix (%tp / %sl / %time) + mean_r/DR/mean-hold per arm.
    * TIME-COHORT autopsy on b40: split its trades by exit_reason and report
      mean_r/DR of the "time" exits — are the 40-bar cap trades dead money?
    * LIVE-GAP: on the subset where b40 exits on "time", what does b_inf earn by
      holding past 40?  (matches trades by (stock, entry_date)).  Quantifies exactly
      what the live no-expiration book is doing that the backtest never rewarded.

  PART 2 — the live 6-slot book (1 high + 5 low):
    (a) deterministic per-FY Sharpe per arm (Δ vs b40; bull/bear + FY2025 OOS).
    (b) paired fill-order null (SAME shuffled order to all arms, K shuffles) → this is
        the CAPITAL-RECYCLING test: a shorter cap frees slots sooner; only a book-level
        null (not per-trade) can tell whether that matters.

Gate (pre-registered, same bar as every exit study): a non-40 arm certifies only if
paired Δ Sharpe P(Δ>0) ≥ 0.95 AND 95% CI lower > 0 AND FY2025 OOS Δ > 0.  Otherwise
this is a DESCRIPTIVE read that answers the live/backtest mismatch, not a rule change.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.exit_expiration_read
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 6
_K = 200
_CTRL = "b40"
_MAX_BARS = {"b15": 15, "b20": 20, "b26": 26, "b40": 40, "b_inf": 10 ** 6}
_ARMS = {a: ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3, max_bars=mb)
         for a, mb in _MAX_BARS.items()}
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)
_BULL_FYS = {"FY2020", "FY2023", "FY2025"}


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


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(cands, rule, caches, cfg, stock_dts, cal):
    cal_set = set(cal)
    results = run_simulation(cands, rule, caches, cfg.end)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r / _SLOTS
    return [day.get(d, 0.0) for d in cal]


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
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

    # part 1 accumulators
    eq = {a: [] for a in _ARMS}                 # arm -> [ExitResult]  (no-cap, all FYs)
    per_fy = {a: {} for a in _ARMS}             # part 2a
    st = {a: [[] for _ in range(_K)] for a in _ARMS}  # part 2b stitched daily series

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
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        # PART 1 — no cap: every candidate fills; per-trade result deterministic
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10 ** 9, 10 ** 9
        for a, rule in _ARMS.items():
            eq[a].extend(run_simulation(cands, rule, caches, cfg.end))
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 1, 5   # restore 6-slot production

        # PART 2a — deterministic 6-slot per-FY Sharpe
        base = sorted(cands, key=lambda c: c.entry_date)
        for a, rule in _ARMS.items():
            per_fy[a][cfg.label] = _sharpe(_fy_returns(base, rule, caches, cfg, stock_dts, cal)[1:])

        # PART 2b — paired fill-order null: SAME shuffled order to all arms
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            for a, rule in _ARMS.items():
                st[a][k] += _fy_returns(pool, rule, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} candidates)", cfg.label, len(cands))

    # ── PART 1a: exit-reason mix + per-arm per-trade ──
    print("\n" + "=" * 84)
    print("PART 1a — identical entries, NO cap: exit-reason mix + per-trade (expiration lever only)")
    print("=" * 84)
    print(f"\n{'arm':<7}{'max_bars':>9}{'n':>7}{'mean_r%':>9}{'DR%':>7}{'hold':>7}   "
          f"{'%tp':>6}{'%sl':>6}{'%time':>7}{'%eod':>6}")
    for a in _ARMS:
        res = eq[a]
        n = len(res)
        r = np.array([p.return_pct for p in res])
        hold = np.array([p.hold_bars for p in res])
        rc = defaultdict(int)
        for p in res:
            rc[p.exit_reason] += 1
        print(f"{a:<7}{_MAX_BARS[a]:>9}{n:>7}{r.mean()*100:>9.2f}{(r>0).mean()*100:>7.1f}"
              f"{hold.mean():>7.1f}   {rc['tp']/n*100:>6.1f}{rc['sl']/n*100:>6.1f}"
              f"{rc['time']/n*100:>7.1f}{rc['end_of_data']/n*100:>6.1f}")

    # ── PART 1b: TIME-COHORT autopsy on b40 (are the capped trades dead money?) ──
    print("\n" + "=" * 84)
    print("PART 1b — b40 autopsy: per-trade quality split by exit_reason")
    print("=" * 84)
    b40 = eq["b40"]
    print(f"\n{'reason':<12}{'n':>7}{'mean_r%':>9}{'DR%':>7}{'median_r%':>11}{'hold':>7}")
    for reason in ["tp", "sl", "time", "end_of_data"]:
        sub = [p.return_pct for p in b40 if p.exit_reason == reason]
        if not sub:
            continue
        a = np.array(sub)
        hold = np.mean([p.hold_bars for p in b40 if p.exit_reason == reason])
        print(f"{reason:<12}{len(a):>7}{a.mean()*100:>9.2f}{(a>0).mean()*100:>7.1f}"
              f"{np.median(a)*100:>11.2f}{hold:>7.1f}")
    print("  (if 'time' exits are ~0 or negative with high hold → the 40-bar cohort is dead money)")

    # ── PART 1c: LIVE-GAP — what does no-expiration (b_inf) earn on the b40-'time' cohort? ──
    print("\n" + "=" * 84)
    print("PART 1c — LIVE-GAP: on trades where b40 hits its cap, what does b_inf (=live) do?")
    print("=" * 84)
    inf_by_key = {(p.stock_code, p.entry_date): p for p in eq["b_inf"]}
    capped = [p for p in b40 if p.exit_reason == "time"]
    b40_r, inf_r, extra_hold = [], [], []
    inf_reason = defaultdict(int)
    for p in capped:
        q = inf_by_key.get((p.stock_code, p.entry_date))
        if q is None:
            continue
        b40_r.append(p.return_pct)
        inf_r.append(q.return_pct)
        extra_hold.append(q.hold_bars - p.hold_bars)
        inf_reason[q.exit_reason] += 1
    if b40_r:
        b40_r = np.array(b40_r); inf_r = np.array(inf_r); extra_hold = np.array(extra_hold)
        n = len(b40_r)
        print(f"\n  matched capped trades: {n}")
        print(f"  b40 (exit at 40 bars):   mean_r {b40_r.mean()*100:+.2f}%  DR {(b40_r>0).mean()*100:.1f}%")
        print(f"  b_inf (hold past 40):    mean_r {inf_r.mean()*100:+.2f}%  DR {(inf_r>0).mean()*100:.1f}%"
              f"  (+{extra_hold.mean():.1f} extra bars held)")
        print(f"  Δ from removing the cap: {(inf_r.mean()-b40_r.mean())*100:+.2f}pp per capped trade")
        print(f"  b_inf eventual exit on this cohort: "
              + "  ".join(f"{k}={v/n*100:.0f}%" for k, v in sorted(inf_reason.items())))
        print("  (POSITIVE Δ → live no-expiration is HELPING vs the 40-cap; NEGATIVE → live leaks)")

    # ── PART 2a: per-FY deterministic Sharpe ──
    print("\n" + "=" * 84)
    print("PART 2a — deterministic 6-slot per-FY Sharpe (Δ = arm − b40)")
    print("=" * 84)
    print(f"\n{'FY':<9}" + "".join(f"{a:>8}" for a in _ARMS)
          + "".join(f"{'Δ'+a:>9}" for a in _ARMS if a != _CTRL))
    bull_d, bear_d = defaultdict(list), defaultdict(list)
    for cfg in _FYS:
        if cfg.label not in per_fy[_CTRL]:
            continue
        row = f"{cfg.label:<9}" + "".join(f"{per_fy[a][cfg.label]:>8.2f}" for a in _ARMS)
        for a in _ARMS:
            if a == _CTRL:
                continue
            dlt = per_fy[a][cfg.label] - per_fy[_CTRL][cfg.label]
            row += f"{dlt:>9.2f}"
            (bull_d if cfg.label in _BULL_FYS else bear_d)[a].append(dlt)
        if cfg.label == "FY2025":
            row += "  OOS"
        print(row)
    for a in _ARMS:
        if a == _CTRL:
            continue
        oos = per_fy[a]["FY2025"] - per_fy[_CTRL]["FY2025"]
        print(f"  {a}: FY2025 OOS Δ {oos:+.2f} | bull-mean Δ {np.mean(bull_d[a]):+.2f} "
              f"| bear-mean Δ {np.mean(bear_d[a]):+.2f}"
              f"  {'(sign-flip!)' if np.mean(bull_d[a])*np.mean(bear_d[a])<0 else ''}")

    # ── PART 2b: paired fill-order null ──
    print("\n" + "=" * 84)
    print(f"PART 2b — paired fill-order null, {_K} shuffles (6-slot book, CAPITAL-RECYCLING test)")
    print("=" * 84)
    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in _ARMS}
    print(f"\n{'arm':<7}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}")
    for a in _ARMS:
        s_ = sh[a]
        print(f"{a:<7}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}")
    for a in _ARMS:
        if a == _CTRL:
            continue
        d = sh[a] - sh[_CTRL]
        p = (d > 0).mean()
        lo, hi = np.percentile(d, [2.5, 97.5])
        cert = p >= 0.95 and lo > 0
        print(f"\n[paired Δ Sharpe {a} − b40]  mean {d.mean():+.3f} | 95% CI [{lo:+.3f}, {hi:+.3f}]"
              f" | P(Δ>0)={p:.3f}")
        print(f"  VERDICT({a}): " + ("CERTIFIED" if cert else "NOT separated — descriptive only"))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
