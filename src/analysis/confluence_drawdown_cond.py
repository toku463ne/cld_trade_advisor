"""Drawdown-conditional cut — how does confluence behave on the market's worst days?

Reuses the capital-aware 4-slot equity curve from confluence_buyhold, but instead
of per-FY metrics it conditions on the N225's own daily move:

  1. Bin every trading day by N225 same-day return (deciles / tails). For each
     bin, mean confluence daily return vs N225 — does it lose less on down days?
  2. N225-drawdown regime: classify each day by how deep N225 is below its
     trailing 1y peak (0-5%, 5-10%, >10% = real bear). Confluence vs N225 mean
     daily return + capture ratios per regime.
  3. The market's 20 worst N225 days: confluence's return on exactly those days
     (downside capture on the tail that matters).

OUTCOME (2026-05-21): confluence is a SYMMETRIC ~0.55-0.6 beta book on the tails
(20 worst days down-capture 0.57, 20 best days up-capture 0.53) — NOT an
asymmetric hedge. In a real bear regime (N225 >10% below trailing 1y peak) it
still loses −19% annualized (vs −39% N225) — it stays long and bleeds, never goes
to cash. Combined with the FY2018 bear (conf −6.9%/DD−28% WORSE than N225
−0.9%/DD−21%) this shows it does NOT reliably avoid bears: 2-for-4 across tested
bear episodes (dampened COVID + won FY2024 via idiosyncratic selection; amplified
FY2018 + FY2021). See memory project_confluence_buyhold_win.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_drawdown_cond
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
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
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

    # global daily series: date -> (conf_ret, n225_ret), plus n225 close for dd regime
    conf_by_day, n225_close = {}, {}
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
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)

        n_dts, n_cmap = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        contrib = defaultdict(float)
        for p in results:
            dts, cmap = stock_dts.get(p.stock_code, ([], {}))
            for d, r in _pos_daily(p, dts, cmap).items():
                if d in cal_set:
                    contrib[d] += r / _SLOTS
        for d in n_dts:
            n225_close[d] = n_cmap[d]
        for i in range(1, len(cal)):
            d, dp = cal[i], cal[i - 1]
            conf_by_day[d] = (contrib.get(d, 0.0), n_cmap[d] / n_cmap[dp] - 1.0)
        logger.info("  {} processed", cfg.label)

    days = sorted(conf_by_day)
    conf = np.array([conf_by_day[d][0] for d in days])
    nk = np.array([conf_by_day[d][1] for d in days])
    print("\n" + "=" * 74)
    print(f"DRAWDOWN-CONDITIONAL CUT — {len(days)} trading days (FY2017+FY2019-2025)")
    print("=" * 74)

    # 1. N225 daily-return deciles
    print("\n[1] By N225 same-day return decile (mean daily %):")
    print(f"{'decile':<8}{'N225 range':>18}{'n':>5}{'N225 mean':>11}{'conf mean':>11}{'capture':>9}")
    edges = np.quantile(nk, np.linspace(0, 1, 11))
    for q in range(10):
        lo, hi = edges[q], edges[q + 1]
        m = (nk >= lo) & (nk <= hi) if q == 9 else (nk >= lo) & (nk < hi)
        if m.sum() == 0:
            continue
        nm, cm = nk[m].mean(), conf[m].mean()
        cap = cm / nm if abs(nm) > 1e-9 else float("nan")
        print(f"D{q+1:<7}{f'[{lo*100:+.2f},{hi*100:+.2f}]':>18}{int(m.sum()):>5}"
              f"{nm*100:>11.3f}{cm*100:>11.3f}{cap:>9.2f}")

    # 2. N225 drawdown regime (depth below trailing 252d peak)
    print("\n[2] By N225 drawdown regime (depth below trailing 1y peak):")
    n_days = sorted(n225_close)
    n_arr = np.array([n225_close[d] for d in n_days])
    didx = {d: i for i, d in enumerate(n_days)}
    def _dd(d):
        i = didx[d]
        lo = max(0, i - 252)
        peak = n_arr[lo:i + 1].max()
        return n_arr[i] / peak - 1.0
    regimes = {"0 to -5%": [], "-5 to -10%": [], "< -10% (bear)": []}
    reg_n = {k: [] for k in regimes}
    for d in days:
        dd = _dd(d)
        k = "0 to -5%" if dd > -0.05 else "-5 to -10%" if dd > -0.10 else "< -10% (bear)"
        regimes[k].append(conf_by_day[d][0]); reg_n[k].append(conf_by_day[d][1])
    print(f"{'regime':<16}{'days':>6}{'N225 mean':>11}{'conf mean':>11}{'N225 ann%':>11}{'conf ann%':>11}")
    for k in regimes:
        c, n = np.array(regimes[k]), np.array(reg_n[k])
        if len(c) == 0:
            continue
        print(f"{k:<16}{len(c):>6}{n.mean()*100:>11.3f}{c.mean()*100:>11.3f}"
              f"{n.mean()*252*100:>11.1f}{c.mean()*252*100:>11.1f}")

    # 3. The 20 worst N225 days
    print("\n[3] The 20 worst N225 days — confluence return on exactly those days:")
    order = np.argsort(nk)[:20]
    worst_n, worst_c = nk[order], conf[order]
    print(f"  N225 mean on its 20 worst days : {worst_n.mean()*100:+.2f}%")
    print(f"  confluence mean on those days  : {worst_c.mean()*100:+.2f}%")
    print(f"  downside capture (conf/N225)   : {worst_c.mean()/worst_n.mean():.2f}  "
          f"(<1 = loses less; <0 = up while mkt down)")
    print(f"  conf POSITIVE on {int((worst_c>0).sum())}/20 of the market's worst days")
    # symmetric: 20 best
    bo = np.argsort(nk)[-20:]
    print(f"\n  [upside ref] N225 20 best days mean {nk[bo].mean()*100:+.2f}%, "
          f"conf {conf[bo].mean()*100:+.2f}%, upside capture {conf[bo].mean()/nk[bo].mean():.2f}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
