"""Cost-inclusive confluence vs Nikkei-ETF — does it WIN net of frictions?

The buy-and-hold comparison (confluence_buyhold.py) is GROSS: confluence beats the
^N225 index gross (+256.9%/Sharpe +0.84/DD−29.9% vs +180.6%/+0.68/−32.8%) but the
edge is thin (+0.16 Sharpe) and confluence turns over ~140 trades/yr while the index
never trades. This script settles the open thread: deduct realistic round-trip
transaction costs from every confluence trade and compare against the REAL buyable
passive alternative — a Nikkei 225 ETF (e.g. 1321) carrying an expense ratio — not
the frictionless index.

Cost model:
  - Confluence: round-trip cost of C bps deducted once per trade on its exit day.
    Each position is 1/4 of capital, so the book-level hit on exit day d is
    (C/10000)/SLOTS per position exiting that day. Linear in C, so we sweep
    C ∈ {0,10,15,20} bps cheaply on one gross pass.
  - Nikkei ETF: ^N225 buy-and-hold minus a 0.15%/yr expense ratio (1321-class),
    accrued daily. One entry/exit cost is negligible over 9 FYs and ignored.
  - Universe equal-weight BH: gross reference only (no buyable instrument exists).

Same 4-slot capital-aware daily-marked equity curve and same trading days as
confluence_buyhold.py, FY2017 + FY2018 + FY2019-2025 (9 FYs).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_buyhold_costs
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
_COST_BPS = [0.0, 10.0, 15.0, 20.0]   # round-trip per trade
_ETF_EXPENSE = 0.0015                  # 0.15%/yr Nikkei-225 ETF (1321-class)
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


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    total = eq[-1] - 1.0
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    maxdd = float((eq / runmax - 1.0).min())
    return total, sh, maxdd


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

    # stitched daily axes (i>=1 within each FY, day0 dropped for stitching)
    st_gross, st_costfrac, st_n225, st_univ = [], [], [], []
    n_trades_total = 0
    n_fy = 0

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
        n_trades_total += len(results); n_fy += 1

        n_dts, n_cmap = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)
        stock_dts = {code: _closes(c) for code, c in caches.items()}

        day_contrib = defaultdict(float)
        day_costfrac = defaultdict(float)   # (#positions exiting that day)/SLOTS
        for p in results:
            dts, cmap = stock_dts.get(p.stock_code, ([], {}))
            for d, r in _pos_daily(p, dts, cmap).items():
                if d in cal_set:
                    day_contrib[d] += r / _SLOTS
            if p.exit_date in cal_set:
                day_costfrac[p.exit_date] += 1.0 / _SLOTS

        conf_r = [day_contrib.get(d, 0.0) for d in cal]
        costf = [day_costfrac.get(d, 0.0) for d in cal]
        n225_r = [n_cmap[cal[i]] / n_cmap[cal[i - 1]] - 1.0 for i in range(1, len(cal))]
        univ_r = []
        for i in range(1, len(cal)):
            d, dp = cal[i], cal[i - 1]
            rs = [cmap[d] / cmap[dp] - 1.0 for code, (dts, cmap) in stock_dts.items()
                  if d in cmap and dp in cmap and cmap[dp] > 0]
            univ_r.append(statistics.mean(rs) if rs else 0.0)

        st_gross += conf_r[1:]
        st_costfrac += costf[1:]
        st_n225 += n225_r
        st_univ += univ_r
        logger.info("  {} processed ({} trades)", cfg.label, len(results))

    gross = np.asarray(st_gross)
    costf = np.asarray(st_costfrac)
    n225 = np.asarray(st_n225)
    univ = np.asarray(st_univ)
    n_days = len(gross)
    yrs = n_days / 252.0
    etf_daily_drag = _ETF_EXPENSE / 252.0
    etf = n225 - etf_daily_drag

    print("\n" + "=" * 84)
    print("COST-INCLUSIVE: confluence (net of round-trip bps) vs buyable Nikkei ETF")
    print("=" * 84)
    print(f"{n_fy} FYs, {n_days} trading days (~{yrs:.1f}y), {n_trades_total} confluence "
          f"trades (~{n_trades_total / yrs:.0f}/yr)\n")

    print(f"{'series':<34}{'total':>9}{'Sharpe':>8}{'maxDD':>8}{'ann.cost':>9}")
    print("-" * 84)
    # passive benchmarks
    nm = _metrics(list(n225))
    print(f"{'^N225 index (frictionless)':<34}{nm[0]*100:+8.1f}%{nm[1]:+8.2f}{nm[2]*100:+7.1f}%{'—':>9}")
    em = _metrics(list(etf))
    print(f"{'Nikkei ETF (0.15%/yr expense)':<34}{em[0]*100:+8.1f}%{em[1]:+8.2f}{em[2]*100:+7.1f}%"
          f"{_ETF_EXPENSE*100:>8.2f}%")
    um = _metrics(list(univ))
    print(f"{'univ equal-wt BH (uninvestable)':<34}{um[0]*100:+8.1f}%{um[1]:+8.2f}{um[2]*100:+7.1f}%{'—':>9}")
    print("-" * 84)
    # confluence net at each cost level
    for c in _COST_BPS:
        net = gross - (c / 10000.0) * costf
        m = _metrics(list(net))
        ann_cost = ((c / 10000.0) * costf.sum()) / yrs
        tag = f"confluence net @{c:.0f}bps" + (" (gross)" if c == 0 else "")
        print(f"{tag:<34}{m[0]*100:+8.1f}%{m[1]:+8.2f}{m[2]*100:+7.1f}%{ann_cost*100:>8.2f}%")
    print("-" * 84)

    # verdict line: confluence net Sharpe vs ETF Sharpe at each cost
    print("\nNet Sharpe edge over the buyable Nikkei ETF (+ = confluence wins):")
    for c in _COST_BPS:
        net = gross - (c / 10000.0) * costf
        m = _metrics(list(net))
        edge = m[1] - em[1]
        verdict = "WIN " if edge > 0 else "LOSE"
        print(f"  @{c:>2.0f}bps round-trip:  confluence {m[1]:+.2f}  vs ETF {em[1]:+.2f}  "
              f"=> {edge:+.2f}  {verdict}")
    # break-even cost
    if costf.sum() > 0:
        # solve gross - (c/1e4)*costf  Sharpe == etf Sharpe by linear scan
        be = None
        for cb in np.arange(0, 200, 0.5):
            net = gross - (cb / 10000.0) * costf
            if _metrics(list(net))[1] <= em[1]:
                be = cb; break
        print(f"\nBreak-even round-trip cost (confluence Sharpe == ETF Sharpe): "
              f"{'%.0f bps' % be if be is not None else '>200 bps'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
