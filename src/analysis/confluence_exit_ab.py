"""Confluence exit-rule A/B: adx_d8 (headline) vs ZsTpSl (control) vs time40 (diag).

The confluence book has ALWAYS used ZsTpSl(2/2/0.3); the exit rule has never been
A/B'd on confluence entries. Exit rule is a STRUCTURAL lever (changes the realized
return of every filled trade), so per evaluation_criteria §5.12 it can certify at
current n via a paired fill-order null — like the capacity bump, unlike the
exhausted selection family. /sign-debate (2026-05-23) judge verdict.

Design (judge spec, critic-modified):
  - ARMS: zs = ZsTpSl(2/2/0.3) CONTROL; adx_d8 = AdxTrail(8.0) HEADLINE
    (holds ~26.7 ≈ ZsTpSl 27.9 → minimal slot-occupancy confound); time40 =
    TimeStop(40) DIAGNOSTIC-ONLY (re-runs the closed project_timestop40_bootstrap
    _reject; its ~38-bar hold changes slot occupancy → composition confound; and a
    fixed-cohort null is BLIND to the regime non-stationarity that killed it —
    auto-reject if high-corr Δ flips sign).
  - PART 1 — exit quality on IDENTICAL entries (no slot cap → every candidate
    fills; per-trade return given an entry is deterministic & shuffle-independent).
    mean per-trade return + DR per arm, and per-corr-mode (the binding
    same-trade-set decomposition: separates exit-quality from occupancy/
    composition; high-corr split = the timestop40 sign-flip falsifier).
  - PART 2 — the live 4-slot book: (a) deterministic per-FY Sharpe per arm (bull/
    bear + FY2025 OOS sign check); (b) paired fill-order null (same shuffled order
    to all arms, K shuffles) → paired Δ Sharpe P(Δ>0) + 95% CI.

Gate (pre-registered): adx_d8 accept = part-1 high-corr Δ NOT negative AND paired
Δ Sharpe P(Δ>0) ≥ 0.90 AND 95% CI lower > 0 AND FY2025 OOS Δ > 0. lean-yes
(operator-call) = P(Δ>0) ≥ 0.75 but CI grazes 0 (matches the capacity precedent).
time40 = diagnostic; auto-reject if its high-corr part-1 Δ < 0 (sign-flip).

OUTCOME (2026-05-23, /sign-debate): REJECT, keep ZsTpSl (judge confidence H).
adx_d8 (headline) fails 3 of 4 gates. Part 1 (identical entries, no cap): adx_d8
mean_r +1.56% vs zs +1.25% (+0.31pp) but DR 54.0 vs 56.0 ("win bigger, lose more
often"); high-corr Δ +0.27 (no per-trade flip). Part 2b paired null: Δ Sharpe
+0.021, 95% CI [−0.480,+0.463], P(Δ>0)=0.535 = COIN FLIP. Part 2a: FY2025 OOS Δ
−0.24 (fails hard gate); bull-mean Δ +0.65 / bear-mean Δ −0.25 = SIGN-FLIP
(FY2024 −1.66 worst), replicating project_timestop40_bootstrap_reject on a fresh
arm. time40 worse (Δ Sharpe −0.124, P=0.265) despite highest per-trade mean_r
(+1.95%) — cleanest proof per-trade EV doesn't transfer. Both exit AND selection
axes now exhausted at ~36 trades/yr; the per-trade exit-quality edge washes out in
fill-order luck. Only re-open path: a REGIME-CONDITIONAL exit that survives a
held-out bull FY (else the n-thin trap). NOTE the build bug: AdxTrail needs
_add_adx() or it silently degenerates to TimeStop(40) (§5.3). Next lead = 6-slot
capacity (project_confluence_capacity_null). See project_confluence_exit_ab_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_exit_ab
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
from src.analysis.exit_benchmark import FyConfig, _add_adx
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.adx_trail import AdxTrail
from src.exit.exit_simulator import run_simulation
from src.exit.time_stop import TimeStop
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
_K = 200
_CTRL = "zs"
_ARMS = {"zs": ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3),
         "adx_d8": AdxTrail(drop_threshold=8.0, min_bars=5, max_bars=40),
         "time40": TimeStop(max_bars=40)}
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

    # part 1: per-trade exit quality on IDENTICAL entries (no cap), by corr_mode
    eq = {a: defaultdict(list) for a in _ARMS}      # arm -> corr_mode -> [returns]
    # part 2a: deterministic per-FY Sharpe per arm
    per_fy = {a: {} for a in _ARMS}
    # part 2b: paired fill-order null, stitched daily series per (arm, shuffle)
    st = {a: [[] for _ in range(_K)] for a in _ARMS}

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
                    _add_adx(c)   # populate ADX14 or AdxTrail degenerates to TimeStop
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

        # PART 1 — no cap: every candidate fills, per-trade return is deterministic
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10 ** 9, 10 ** 9
        for a, rule in _ARMS.items():
            for p in run_simulation(cands, rule, caches, cfg.end):
                eq[a][p.corr_mode].append(p.return_pct)
                eq[a]["all"].append(p.return_pct)
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 1, 3   # restore production

        # PART 2a — deterministic 4-slot per-FY Sharpe per arm
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

    # ── PART 1 report: exit quality on identical entries ──
    print("\n" + "=" * 80)
    print("PART 1 — exit quality on IDENTICAL entries (no cap, per-trade), by corr_mode")
    print("=" * 80)
    print(f"\n{'arm':<9}{'n':>7}{'mean_r%':>9}{'DR%':>7}   "
          f"{'hi mean_r%':>11}{'hi Δ vs zs':>11}{'lo mean_r%':>11}")
    for a in _ARMS:
        allr = np.array(eq[a]["all"]); hir = np.array(eq[a].get("high", []))
        lor = np.array(eq[a].get("low", []) + eq[a].get("mid", []))
        hi_d = (hir.mean() - np.array(eq[_CTRL].get("high", [0])).mean()) if hir.size else float("nan")
        print(f"{a:<9}{allr.size:>7}{allr.mean()*100:>9.2f}{(allr>0).mean()*100:>7.1f}   "
              f"{(hir.mean()*100 if hir.size else float('nan')):>11.2f}{hi_d*100:>11.2f}"
              f"{(lor.mean()*100 if lor.size else float('nan')):>11.2f}")
    print("  (binding: adx_d8 'all' & 'hi Δ vs zs' should be ≥0; time40 hi Δ<0 = "
          "sign-flip → diagnostic reject, replicates project_timestop40_bootstrap_reject)")

    # ── PART 2a report: per-FY deterministic Sharpe + Δ vs control ──
    print("\n" + "=" * 80)
    print("PART 2a — deterministic 4-slot per-FY Sharpe (Δ = arm − zs)")
    print("=" * 80)
    print(f"\n{'FY':<9}" + "".join(f"{a:>10}" for a in _ARMS)
          + "".join(f"{'Δ'+a:>10}" for a in _ARMS if a != _CTRL))
    bull_d, bear_d = defaultdict(list), defaultdict(list)
    for cfg in _FYS:
        if cfg.label not in per_fy[_CTRL]:
            continue
        row = f"{cfg.label:<9}" + "".join(f"{per_fy[a][cfg.label]:>10.2f}" for a in _ARMS)
        for a in _ARMS:
            if a == _CTRL:
                continue
            dlt = per_fy[a][cfg.label] - per_fy[_CTRL][cfg.label]
            row += f"{dlt:>10.2f}"
            (bull_d if cfg.label in _BULL_FYS else bear_d)[a].append(dlt)
        if cfg.label == "FY2025":
            row += "  OOS"
        print(row)
    for a in _ARMS:
        if a == _CTRL:
            continue
        oos = per_fy[a]["FY2025"] - per_fy[_CTRL]["FY2025"]
        print(f"  {a}: FY2025 OOS Δ {oos:+.2f} | bull-mean Δ "
              f"{np.mean(bull_d[a]):+.2f} | bear-mean Δ {np.mean(bear_d[a]):+.2f}"
              f"  {'(sign-flip!)' if np.mean(bull_d[a])*np.mean(bear_d[a])<0 else ''}")

    # ── PART 2b report: paired fill-order null ──
    print("\n" + "=" * 80)
    print(f"PART 2b — paired fill-order null, {_K} shuffles (4-slot book)")
    print("=" * 80)
    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in _ARMS}
    print(f"\n{'arm':<9}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}")
    for a in _ARMS:
        s_ = sh[a]
        print(f"{a:<9}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}")
    for a in _ARMS:
        if a == _CTRL:
            continue
        d = sh[a] - sh[_CTRL]
        p = (d > 0).mean()
        lo, hi = np.percentile(d, [2.5, 97.5])
        cert = p >= 0.90 and lo > 0
        lean = (not cert) and p >= 0.75
        print(f"\n[paired Δ Sharpe {a} − zs]  mean {d.mean():+.3f} | 95% CI [{lo:+.3f}, {hi:+.3f}]"
              f" | P(Δ>0)={p:.3f}")
        print(f"  VERDICT({a}): "
              + ("CERTIFIED" if cert else "lean-yes / operator-call" if lean
                 else "NOT separated — park"))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
