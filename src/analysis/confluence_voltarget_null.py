"""Vol-target / risk-parity slot sizing vs equal-weight — paired fill-order null.

Backlog item 4 (docs/analysis/confluence_improvement_backlog.md). The 6-slot
confluence book is equal-weight across slots (each held name contributes r/6 to the
daily book). Re-weighting slots inverse to recent realized vol (risk-parity) should
cut portfolio variance / maxDD with little return cost — a RISK-adjusted win, not a
return win. Sizing is the WEIGHTS axis, distinct from the (exhausted) selection /
ordering axis and from exit timing, and it does NOT condition on the market regime
(so it does not fight the regime-inverse alpha that killed items 3 + the TSMOM gate).

METHOD (cleaner pairing than the capacity null): for each shuffle seed the simulation
is run ONCE at the production 6-slot book (_MAX_LOW_CORR=5). The resulting fills are
IDENTICAL across arms; the arms differ ONLY in how each held name's daily return is
weighted into the book. So Δ = Sharpe(weighted) − Sharpe(equal) on the SAME fills is
the pure sizing effect net of fill-order luck.

Arms (see docs/analysis/confluence_voltarget_sizing_preregistration.md):
  - EW    : w_p = 1/6 each held day (shipped baseline).
  - IV-RP : PRIMARY. inverse-vol risk-parity, SAME gross — among held set H,
            w_p = (|H|/6) * iv_p / Σ iv, iv_p = 1/vol_p. Gross == EW each day; only
            the split among held names changes. Carries the verdict.
  - VT    : SECONDARY diagnostic. vol-target, gross-scaled —
            w_p = (1/6) * clip(vt/vol_p, 0.5, 2.0), vt = per-FY median entry vol.
            Gross floats (deleverages). A leverage decision, awkward on a manual
            integer-lot book → does NOT carry the verdict.

vol_p = trailing 20-bar stdev of daily pct returns, measured strictly BEFORE
entry_date (no lookahead), frozen for the hold; FY-median fallback if short history.

Binding gate (frozen): IV-RP vs EW, K=200 paired shuffles —
  P(Δ Sharpe > 0) >= 0.95 AND 95% CI lower bound > 0, maxDD not worsened, OOS stable.

OUTCOME (2026-05-29, K=200, FY2018–2025): REJECT — gate failed hard. EW Sharpe 0.911 / maxDD −27.3%;
IV-RP 0.868 (Δ −0.044, P(Δ>0)=0.205, CI [−0.134,+0.067]); VT 0.846. Inverse-vol does not just tie EW —
it mildly HURTS, cutting return −41pp (P(Δret>0)=0.075) with maxDD flat (−0.09pp). MECHANISM: on a
high-β (~0.7) positively-correlated long book the higher-vol breakouts carry the return, and because the
names co-move, down-weighting them by single-name vol loses return without a portfolio-variance offset →
equal-weight is already ≈ risk-parity. VT (gross-scaled) shaves maxDD +0.75pp but loses Sharpe = a pure
leverage trade. OOS FY2025 alone leaned + (IV-RP +0.13) but the pooled verdict binds. See
docs/analysis/confluence_improvement_backlog.md item 4 + confluence_voltarget_sizing_preregistration.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_voltarget_null
"""
from __future__ import annotations

import bisect
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
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_VOL_WINDOW = 20      # frozen — matches the project's rho(20) convention
_VT_LO, _VT_HI = 0.5, 2.0   # vol-target per-name leverage clip
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
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


def _vol_series(cache):
    """Per-stock {date: trailing-20-bar daily-return stdev measured BEFORE that date}.

    No lookahead: vol[d] uses only returns of bars strictly before d. Returns None
    for dates without >=2 prior returns (caller falls back to FY-median).
    """
    dts, cmap = _closes(cache)
    rets, rdates = [], []
    for i in range(1, len(dts)):
        prev, cur = cmap[dts[i - 1]], cmap[dts[i]]
        if prev and cur:
            rets.append(cur / prev - 1.0)
            rdates.append(dts[i])
    # vol[d] = stdev of the trailing _VOL_WINDOW returns whose date < d (rdates sorted)
    vol: dict[datetime.date, float] = {}
    for d in dts:
        k = bisect.bisect_left(rdates, d)   # count of returns strictly before d
        window = rets[max(0, k - _VOL_WINDOW):k]
        if len(window) >= 2:
            sd = statistics.stdev(window)
            if sd > 0:
                vol[d] = sd
    return vol


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _ret_dd(rets):
    if len(rets) < 2:
        return float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), float((eq / runmax - 1.0).min())


def _fy_weighted_returns(results, caches, stock_dts, vol_maps, cal, fy_med_vol):
    """Return three daily-return series for the FY on calendar `cal`:
    (equal-weight, inverse-vol risk-parity same-gross, vol-target gross-scaled).

    Each is a dict date->return, built from the SAME `results` (fills) — the only
    difference is the per-position daily weight.
    """
    cal_set = set(cal)
    # per day: list of (daily_return, vol) for each held position
    day_items: defaultdict[datetime.date, list] = defaultdict(list)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        v = vol_maps.get(p.stock_code, {}).get(p.entry_date) or fy_med_vol
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_items[d].append((r, v))

    ew, iv, vt = {}, {}, {}
    for d, items in day_items.items():
        n = len(items)
        ew[d] = sum(r for r, _ in items) / _SLOTS
        # inverse-vol risk parity, SAME gross (= n/_SLOTS)
        inv = [1.0 / v for _, v in items]
        sinv = sum(inv)
        gross = n / _SLOTS
        iv[d] = gross * sum(r * (ip / sinv) for (r, _), ip in zip(items, inv))
        # vol-target gross-scaled
        vt[d] = sum(r * (1.0 / _SLOTS) * min(_VT_HI, max(_VT_LO, fy_med_vol / v))
                    for r, v in items)
    return ew, iv, vt


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0   # DB holds only fresh post-rebuild runs (default 47 drops early FYs)
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

    # stitched daily series per (arm, shuffle k); also FY2025-only for OOS
    arms = ("ew", "iv", "vt")
    st = {a: [[] for _ in range(_K)] for a in arms}
    oos = {a: [[] for _ in range(_K)] for a in arms}

    exsim._MAX_LOW_CORR = 5   # production 6-slot book, fixed for all arms
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
        vol_maps = {code: _vol_series(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        # per-FY median entry vol (fallback + vol-target reference), over candidate
        # entry dates so it reflects the names actually competing for slots
        cand_vols = [vol_maps[c.stock_code].get(c.entry_date)
                     for c in cands if c.stock_code in vol_maps]
        cand_vols = [v for v in cand_vols if v]
        fy_med_vol = statistics.median(cand_vols) if cand_vols else 0.02

        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)           # within-day fill order; run_simulation stable-sorts by date
            results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
            ew, iv, vt = _fy_weighted_returns(results, caches, stock_dts, vol_maps, cal, fy_med_vol)
            series = {"ew": ew, "iv": iv, "vt": vt}
            for a in arms:
                seq = [series[a].get(d, 0.0) for d in cal]
                st[a][k] += seq[1:]
                if cfg.label == "FY2025":
                    oos[a][k] += seq[1:]
        logger.info("  {} done ({} candidates, med vol {:.3f}, {} shuffles)",
                    cfg.label, len(cands), fy_med_vol, _K)

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in arms}
    rt = {a: np.array([_ret_dd(st[a][k])[0] for k in range(_K)]) for a in arms}
    dd = {a: np.array([_ret_dd(st[a][k])[1] for k in range(_K)]) for a in arms}
    oos_sh = {a: np.array([_sharpe(oos[a][k]) for k in range(_K)]) for a in arms}

    label = {"ew": "EW (baseline)", "iv": "IV-RP (primary)", "vt": "VT (diag)"}
    print("\n" + "=" * 86)
    print(f"VOL-TARGET / RISK-PARITY SIZING vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot")
    print("=" * 86)
    print(f"\n{'arm':<18}{'Sharpe mean':>12}{'sd':>7}{'p5':>7}{'p50':>7}{'p95':>7}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a in arms:
        s_ = sh[a]
        print(f"{label[a]:<18}{s_.mean():>12.3f}{s_.std():>7.3f}"
              f"{np.percentile(s_,5):>7.2f}{np.percentile(s_,50):>7.2f}{np.percentile(s_,95):>7.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.1f}%")

    def report(name, a):
        d = sh[a] - sh["ew"]                 # paired Δ Sharpe vs equal-weight
        ddd = dd[a] - dd["ew"]               # paired Δ maxDD (positive = shallower DD)
        dr = rt[a] - rt["ew"]
        do = oos_sh[a] - oos_sh["ew"]
        print(f"\n[{name}: paired Δ vs EW, same fills each draw]")
        print(f"  Δ Sharpe  mean {d.mean():+.3f} | sd {d.std():.3f} | "
              f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
              f"P(Δ>0) {(d>0).mean():.3f} ({int((d>0).sum())}/{_K})")
        print(f"  Δ maxDD   mean {ddd.mean()*100:+.2f}pp (positive = shallower) | "
              f"P(shallower) {(ddd>0).mean():.3f}")
        print(f"  Δ return  mean {dr.mean()*100:+.1f}pp | P(Δ>0) {(dr>0).mean():.3f}")
        print(f"  OOS FY2025 Δ Sharpe mean {do.mean():+.3f} | P(Δ>0) {(do>0).mean():.3f}")
        passes = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
        dd_ok = ddd.mean() >= 0
        return passes, dd_ok

    p_iv, dd_iv = report("IV-RP (PRIMARY — risk parity, same gross)", "iv")
    report("VT (SECONDARY — vol-target, gross-scaled, leverage decision)", "vt")

    print("\n" + "-" * 86)
    if p_iv and dd_iv:
        verdict = "ACCEPT — IV-RP band sits above EW net of fill-order luck, maxDD not worsened"
    elif p_iv:
        verdict = "PARTIAL — Sharpe gate passes but maxDD worsened (a leverage win, not risk-parity)"
    else:
        verdict = ("REJECT — IV-RP Δ Sharpe within fill-order noise (CI straddles / P<0.95); "
                   "re-weighting a ~5-name held set is second-order")
    print(f"  PRIMARY VERDICT: {verdict}")
    print("  (Gate: P(ΔSharpe>0)>=0.95 AND 95% CI-lo>0 AND maxDD not worsened. "
          "VT is a diagnostic only — gross-scaling is a leverage decision.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
