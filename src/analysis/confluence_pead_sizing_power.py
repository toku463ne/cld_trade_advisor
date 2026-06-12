"""PEAD-conditional SIZING tilt — Stage-0 power-check (in-sample upper bound).

The untried sanctioned path for the deployable PEAD edge (project_jquants_pead_universe):
PEAD as a candidate-level SELECTION key is exhausted — vote (confluence_pead_null),
score-booster (confluence_pead_boost_null: Δ +0.016 P=0.495), universe expansion, sleeve:
all REJECT vs the fill-order null. The decisive booster finding was that its FILLED-book
cohort genuinely outperformed (+2.40% vs +1.45% mean_r) yet selection couldn't capture it
because reordering rarely changes WHICH 6 names fill. SIZING captures that surviving cohort
edge directly: keep the same fills, OVERWEIGHT the PEAD-positive names. This is the WEIGHTS
axis (like item-2 EV tilt / item-4 vol-target), NOT pre-killed by the fill-order null.

This is the cheap power-check BEFORE the full paired null (mirrors sizing_decorator_power_probe):
build the canonical 6-slot book ONCE, tag each fill PEAD-boosted (up-revision in the trailing
_PEAD_WIN trading bars before entry), and compute the IN-SAMPLE (β chosen to maximize Δ =
look-ahead upper bound) Δ Sharpe of a same-gross redistribution tilt vs equal-weight. Block
bootstrap CI.

  in-sample Δ Sharpe < +0.05  → walk-forward will be worse → REJECT, do not build the null.
  in-sample Δ Sharpe > +0.10  → room to clear +0.05 walk-forward → ESCALATE to the paired null.

Also reports the booster cohort diagnostic (does +2.40/+1.45 replicate on the canonical book?)
and boosted-names-per-held-day (coarseness: a tilt over ~6 names with usually 0-1 boosted is
weak by construction — the item-2 integer-lot lesson).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_pead_sizing_power
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_evtilt_null import (
    _closes, _pos_daily, _ret_dd, _sharpe,
)
from src.analysis.confluence_pead_inclusion_ab import (
    _build_pead_up_fires, _load_pead_statements,
)
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS

_SLOTS = 6
_N_GATE = 3
_PEAD_WIN = 60          # trailing trading bars for "recent up-revision" (booster spec)
_BETAS = [1.5, 2.0, 3.0]   # boosted relative-weight sweep (in-sample upper bound = best)
_BLOCK = 20
_BOOT = 2000
_SEED = 0


def _boosted_dates(cache, pead_fires) -> set[datetime.date]:
    """Trading dates where a confluence entry would count as PEAD-boosted:
    an up-revision entry occurred within the trailing _PEAD_WIN bars (inclusive)."""
    cal = sorted({b.dt.date() for b in cache.bars})
    idx = {d: i for i, d in enumerate(cal)}
    rev_idx = sorted({idx[e] for _, e in pead_fires if e in idx})
    out: set[datetime.date] = set()
    if not rev_idx:
        return out
    for i, d in enumerate(cal):
        # any revision in (i-_PEAD_WIN, i]
        lo = i - _PEAD_WIN
        # binary-ish scan (rev_idx small per stock)
        if any(lo < r <= i for r in rev_idx):
            out.add(d)
    return out


def _day_items_pead(results, stock_dts, boosted_of, cal):
    cal_set = set(cal)
    di: defaultdict[datetime.date, list] = defaultdict(list)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        b = boosted_of.get((p.stock_code, p.entry_date), False)
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                di[d].append((r, b))
    return di


def _ew(di, d):
    items = di.get(d, [])
    return sum(r for r, _ in items) / _SLOTS


def _rd(di, d, beta):
    """Same-gross redistribution: boosted relative weight beta, plain 1; gross = |H|/SLOTS."""
    items = di.get(d, [])
    if not items:
        return 0.0
    wi = [beta if b else 1.0 for _, b in items]
    sw = sum(wi)
    gross = len(items) / _SLOTS
    return gross * sum(r * (w / sw) for (r, _), w in zip(items, wi)) if sw > 0 else 0.0


def _block_boot_dsharpe(ew, rd):
    """Moving-block bootstrap CI of Δ Sharpe(rd) − Sharpe(ew) on paired daily series."""
    n = len(ew)
    ew = np.asarray(ew); rd = np.asarray(rd)
    nb = int(math.ceil(n / _BLOCK))
    rng = np.random.default_rng(12345)
    out = np.empty(_BOOT)
    starts_pool = np.arange(0, n - _BLOCK + 1)
    for j in range(_BOOT):
        starts = rng.choice(starts_pool, size=nb)
        idx = np.concatenate([np.arange(s, s + _BLOCK) for s in starts])[:n]
        out[j] = _sharpe(rd[idx].tolist()) - _sharpe(ew[idx].tolist())
    return float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 50)), float(np.nanpercentile(out, 97.5))


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    exsim._MAX_LOW_CORR = 5     # production 6-slot
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    stmts = _load_pead_statements()

    pooled = {"ew": [], **{b: [] for b in _BETAS}}
    oos = {"ew": [], **{b: [] for b in _BETAS}}
    # cohort: per-position total return by boosted flag
    coh = {True: [], False: []}
    boosted_per_day: list[int] = []
    held_per_day: list[int] = []

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
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

        pead = _build_pead_up_fires(caches, stmts)
        boosted_dates = {code: _boosted_dates(c, pead.get(code, [])) for code, c in caches.items()}

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        boosted_of = {(c.stock_code, c.entry_date): (c.entry_date in boosted_dates.get(c.stock_code, set()))
                      for c in cands}

        rng = random.Random(_SEED)
        pool = cands[:]
        rng.shuffle(pool)
        results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
        di = _day_items_pead(results, stock_dts, boosted_of, cal)

        # cohort: per-position total return
        for p in results:
            b = boosted_of.get((p.stock_code, p.entry_date), False)
            if p.entry_price:
                coh[b].append(p.exit_price / p.entry_price - 1.0)
        for d in cal:
            items = di.get(d, [])
            if items:
                held_per_day.append(len(items))
                boosted_per_day.append(sum(1 for _, b in items if b))

        ew_s = [_ew(di, d) for d in cal][1:]
        pooled["ew"] += ew_s
        if cfg.label == "FY2025":
            oos["ew"] += ew_s
        for beta in _BETAS:
            rd_s = [_rd(di, d, beta) for d in cal][1:]
            pooled[beta] += rd_s
            if cfg.label == "FY2025":
                oos[beta] += rd_s
        logger.info("  {} done ({} cands, {} fills)", cfg.label, len(cands), len(results))

    _report(pooled, oos, coh, boosted_per_day, held_per_day)


def _cohort_line(name, rets):
    if not rets:
        return f"  {name:>8}: n=0"
    a = np.array(rets)
    return f"  {name:>8}: n={len(a):>4}  mean_r={a.mean()*100:+.2f}%  DR={float((a>0).mean()*100):.1f}%"


def _report(pooled, oos, coh, bpd, hpd):
    print("\n=== PEAD-conditional SIZING tilt — Stage-0 power-check ===")
    print(f"params: PEAD_WIN={_PEAD_WIN}bars  betas={_BETAS}  seed={_SEED}  6-slot capital-aware\n")

    print("(0) BOOSTER COHORT replication on the canonical book (per-position total return):")
    print(_cohort_line("boosted", coh[True]))
    print(_cohort_line("plain", coh[False]))
    if coh[True] and coh[False]:
        spread = (np.mean(coh[True]) - np.mean(coh[False])) * 100
        print(f"   spread (boosted − plain) = {spread:+.2f}pp")
    bpd_a = np.array(bpd); hpd_a = np.array(hpd)
    if len(bpd_a):
        print(f"\n(1) COARSENESS: held/day mean {hpd_a.mean():.2f}; boosted/day mean {bpd_a.mean():.2f}; "
              f"days with >=1 boosted = {float((bpd_a>0).mean()*100):.1f}%")

    ew_sh = _sharpe(pooled["ew"])
    ew_ret, ew_dd = _ret_dd(pooled["ew"])
    print(f"\n(2) IN-SAMPLE (look-ahead, β=best = UPPER BOUND) — pooled FY2018-2025")
    print(f"   EW       Sharpe {ew_sh:+.3f}  ret {ew_ret*100:+.0f}%  maxDD {ew_dd*100:.0f}%")
    best = None
    for beta in _BETAS:
        sh = _sharpe(pooled[beta]); rt, dd = _ret_dd(pooled[beta])
        d = sh - ew_sh
        print(f"   RD β={beta:<4} Sharpe {sh:+.3f}  ret {rt*100:+.0f}%  maxDD {dd*100:.0f}%  ΔSharpe {d:+.3f}")
        if best is None or d > best[1]:
            best = (beta, d)
    bbeta, bd = best
    lo, med, hi = _block_boot_dsharpe(pooled["ew"], pooled[bbeta])
    print(f"\n   best β={bbeta}: in-sample ΔSharpe {bd:+.3f}  "
          f"block-boot CI [{lo:+.3f}, {hi:+.3f}] (median {med:+.3f})")

    oo_ew = _sharpe(oos["ew"]); oo_rd = _sharpe(oos[bbeta])
    print(f"   OOS FY2025: EW {oo_ew:+.3f}  RD β={bbeta} {oo_rd:+.3f}  Δ {oo_rd-oo_ew:+.3f}")

    print("\n(3) VERDICT")
    if bd < 0.05:
        print(f"   in-sample upper bound ΔSharpe {bd:+.3f} < +0.05 → REJECT (walk-forward worse). Do NOT build the null.")
    elif bd < 0.10:
        print(f"   in-sample ΔSharpe {bd:+.3f} in [0.05,0.10) → MARGINAL. Walk-forward likely < +0.05; escalate only if cohort+coarseness strong.")
    else:
        print(f"   in-sample ΔSharpe {bd:+.3f} >= +0.10 → ESCALATE to paired fill-order null (clone confluence_evtilt_null with PEAD weights).")
    print()


if __name__ == "__main__":
    run()
