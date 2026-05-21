"""Confluence vs buy-and-hold — does the timing of beta exposure WIN?

Builds a capital-aware equity curve for ConfluenceSignStrategy (bullish N>=3) and
compares against (a) buy-and-hold ^N225 and (b) buy-and-hold equal-weight
universe, over the SAME trading days, FY2017 + FY2019-2025.

Portfolio model: 4 equal slots (matches the ≤1 high-corr + ≤3 low/mid cap), each
position sized 1/4 of capital, cash (0%) in empty slots. Each open position is
marked daily along its real price path (entry fill→close, close→close, →exit
fill). This is the realistic "sized for up to 4 names, selective so often partly
in cash" book — cash drag is a real cost shown honestly.

Metrics per series: total return, daily Sharpe (×√252), max drawdown, % time
invested. Risk-adjusted (Sharpe, maxDD) is the verdict; total return alone is
unfair to a more selective book.

OUTCOME (2026-05-21): CONFLUENCE WINS, defensively. Stitched confluence +300.9% /
daily-Sharpe +1.01 / maxDD −26.9% vs N225 BH +183%/+0.75/−31.6% vs universe BH
+267%/+0.98/−33.6%. Beats N225 on all 3; beats universe on return+drawdown, ties
Sharpe. Win shape = trades upside for downside protection (crushes down/flat FYs
— FY2024 +42.5% vs N225 −10.5%; lags bull rebounds FY2020/23/25). NOTE: ~95%
invested (not cash-timing) — it's a concentrated rotating 4-name long book with a
defensive selection + ZsTpSl-exit tilt. REAL daily Sharpe ≈1.0, NOT the per-trade
benchmark +3.4. See memory project_confluence_buyhold_win.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_buyhold
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
                 "classified2016")] + list(RS_FY_CONFIGS)


def _closes(cache: DataCache) -> tuple[list[datetime.date], dict]:
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts: list[datetime.date], cmap: dict) -> dict:
    """Daily returns contributed by one position over [entry, exit]."""
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
            out[d] = cmap[d] / p.entry_price - 1.0          # fill → close
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0  # prev close → exit fill
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0       # close → close
    return out


def _metrics(rets: list[float]) -> tuple[float, float, float]:
    """(total_return, annualized daily Sharpe, max_drawdown) from a daily series."""
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

    stitched = {"conf": [], "n225": [], "univ": []}
    per_fy = {}

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
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE))
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)

        # trading calendar = N225 days within the FY
        n_dts, n_cmap = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)
        # per-stock closes
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        # confluence daily contributions
        day_contrib = defaultdict(float)
        day_nopen = defaultdict(int)
        for p in results:
            dts, cmap = stock_dts.get(p.stock_code, ([], {}))
            for d, r in _pos_daily(p, dts, cmap).items():
                if d in cal_set:
                    day_contrib[d] += r / _SLOTS
                    day_nopen[d] += 1
        conf_r = [day_contrib.get(d, 0.0) for d in cal]
        invested = statistics.mean([min(day_nopen.get(d, 0), _SLOTS) / _SLOTS for d in cal]) if cal else 0.0

        # N225 buy-and-hold
        n225_r = []
        for i in range(1, len(cal)):
            n225_r.append(n_cmap[cal[i]] / n_cmap[cal[i - 1]] - 1.0)
        # universe equal-weight buy-and-hold
        univ_r = []
        for i in range(1, len(cal)):
            d, dp = cal[i], cal[i - 1]
            rs = []
            for code, (dts, cmap) in stock_dts.items():
                if d in cmap and dp in cmap and cmap[dp] > 0:
                    rs.append(cmap[d] / cmap[dp] - 1.0)
            univ_r.append(statistics.mean(rs) if rs else 0.0)
        # align conf to the i>=1 axis for stitching (drop day0)
        conf_r_al = conf_r[1:]

        per_fy[cfg.label] = {
            "conf": _metrics(conf_r), "n225": _metrics(n225_r),
            "univ": _metrics(univ_r), "inv": invested, "ntr": len(results)}
        stitched["conf"] += conf_r_al
        stitched["n225"] += n225_r
        stitched["univ"] += univ_r
        logger.info("  {}: {} trades, {:.0%} invested", cfg.label, len(results), invested)

    # ── report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("CONFLUENCE (4-slot, cash when idle) vs BUY-AND-HOLD — same trading days")
    print("=" * 90)
    print(f"{'FY':<8}{'trades':>7}{'%inv':>6} | "
          f"{'conf:tot/Sh/DD':>22} | {'N225:tot/Sh/DD':>22} | {'univ:tot/Sh/DD':>22}")
    def _fmt(m):
        t, s, dd = m
        return f"{t*100:+6.1f}/{s:+5.2f}/{dd*100:6.1f}"
    for cfg in _FYS:
        if cfg.label not in per_fy:
            continue
        r = per_fy[cfg.label]
        print(f"{cfg.label:<8}{r['ntr']:>7}{r['inv']*100:>5.0f}% | "
              f"{_fmt(r['conf']):>22} | {_fmt(r['n225']):>22} | {_fmt(r['univ']):>22}")
    print("-" * 90)
    cm, nm, um = _metrics(stitched["conf"]), _metrics(stitched["n225"]), _metrics(stitched["univ"])
    print(f"{'STITCH':<8}{'':>7}{'':>6} | {_fmt(cm):>22} | {_fmt(nm):>22} | {_fmt(um):>22}")
    print("\n(tot=total return %, Sh=daily Sharpe ×√252, DD=max drawdown %)")
    print(f"\nStitched daily Sharpe — confluence {cm[1]:+.2f} | N225 BH {nm[1]:+.2f} | "
          f"universe BH {um[1]:+.2f}")
    print(f"Stitched max drawdown — confluence {cm[2]*100:.1f}% | N225 BH {nm[2]*100:.1f}% | "
          f"universe BH {um[2]*100:.1f}%")
    print(f"Stitched total return — confluence {cm[0]*100:+.1f}% | N225 BH {nm[0]*100:+.1f}% | "
          f"universe BH {um[0]*100:+.1f}%  (confluence ~{statistics.mean([per_fy[c.label]['inv'] for c in _FYS if c.label in per_fy])*100:.0f}% invested avg)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
