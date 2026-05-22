"""Step 2: does relative-strength re-ranking of confluence candidates win?

Stage-0 (confluence_selection_pressure.py) showed a real choice exists on ~25%
of fills (≥2 live candidates competing for a freed slot), but only ~8/yr — so
this is an EVIDENCE run, expected to be n-thin, not a ship decision.

Design: both arms run the SAME hard 4-slot book (≤1 high-corr seat, K=7-cal-day
proposal shelf-life). They differ only in which live candidate fills a freed
slot when ≥2 compete:
  FIFO    — earliest signal first (neutral "no ranking" baseline)
  RS-high — highest trailing-60-bar relative strength (stock_ret60 − N225_ret60)
  RS-low  — lowest RS (falsification arm; should LOSE if RS carries signal)
Each arm's taken trades are marked on the capital-aware 4-slot equity curve
(same model as confluence_buyhold.py). Monotone RS-high ≥ FIFO ≥ RS-low across
Sharpe/return is the signal we're looking for.

OUTCOME (2026-05-22): REJECT. Stitched Sharpe FIFO +0.91 > RS-high +0.86
(Δ −0.05) > RS-low +0.81 (Δ −0.10); RS-high return +219.9% < FIFO +249.2%.
RS-high beats FIFO in only 2/9 FYs. The falsification arm shows a faint
within-RS gradient (high > low by +0.05 Sh, the "expected" direction) so RS is
not anti-signal — but BOTH lose to neutral take-earliest FIFO, and only 28
trades differ from FIFO (~3/FY = noise-scale). No usable cross-sectional edge.
Consistent with trend_score REJECT: the confluence/regime gate already selects
momentum context, so re-ranking the gated set adds nothing. Binding constraint
is capacity, not name choice. See memory project_confluence_xsec_ranking_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_rs_rerank
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
_K_DAYS = 7        # proposal shelf-life (calendar days)
_RS_LOOKBACK = 60  # trailing bars for relative strength
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


def _pos_daily(entry_date, exit_date, entry_price, exit_price, dts, cmap):
    try:
        ie, ix = dts.index(entry_date), dts.index(exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[entry_date] = exit_price / entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / entry_price - 1.0
        elif d == exit_date:
            out[d] = exit_price / cmap[span[k - 1]] - 1.0
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


def _rs_as_of(stock_dts, stock_cmap, n_dts, n_cmap, d):
    """Trailing-60-bar relative strength: stock_ret60 − N225_ret60 as of date d."""
    def _ret(dts, cmap):
        if d not in cmap:
            return None
        i = dts.index(d)
        if i < _RS_LOOKBACK:
            return None
        p0 = cmap[dts[i - _RS_LOOKBACK]]
        return cmap[d] / p0 - 1.0 if p0 else None
    sr = _ret(stock_dts, stock_cmap)
    nr = _ret(n_dts, n_cmap)
    if sr is None:
        return 0.0
    return sr - (nr or 0.0)


def _select(trades, policy):
    """4-slot event-driven selection. trades: list of dicts with
    entry_date, exit_date, corr, rs. Returns set of taken trade ids."""
    cand = sorted(trades, key=lambda t: t["entry_date"])
    for i, t in enumerate(cand):
        t["_id"] = i
    live = []
    ci = 0
    free_after = [datetime.date.min] * _SLOTS
    high_busy_until = datetime.date.min
    taken = set()
    evt_days = sorted({t["entry_date"] for t in trades} | {t["exit_date"] for t in trades})
    for d in evt_days:
        while ci < len(cand) and cand[ci]["entry_date"] <= d:
            t = cand[ci]
            live.append((t["entry_date"] + datetime.timedelta(days=_K_DAYS), t))
            ci += 1
        live = [(exp, t) for (exp, t) in live if exp >= d]
        free_idx = [i for i, fa in enumerate(free_after) if fa < d]
        hi_free = high_busy_until < d
        while free_idx:
            elig = [(exp, t) for (exp, t) in live if not (t["corr"] == "high" and not hi_free)]
            if not elig:
                break
            if policy == "fifo":
                pick = min(elig, key=lambda et: et[1]["entry_date"])
            elif policy == "rs_high":
                pick = max(elig, key=lambda et: et[1]["rs"])
            else:  # rs_low
                pick = min(elig, key=lambda et: et[1]["rs"])
            live.remove(pick)
            t = pick[1]
            i = free_idx.pop(0)
            free_after[i] = t["exit_date"]
            if t["corr"] == "high":
                high_busy_until = t["exit_date"]
                hi_free = False
            taken.add(t["_id"])
    return taken, {t["_id"]: t for t in cand}


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

    policies = ["fifo", "rs_high", "rs_low"]
    stitched = {p: [] for p in policies}
    per_fy = {p: {} for p in policies}
    diff_trades = {"rs_high": 0, "rs_low": 0}  # trades differing from fifo
    n_total = 0

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 100)
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
        n_total += len(results)

        n_dts, n_cmap = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}

        trades = []
        for p in results:
            sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
            rs = _rs_as_of(sdts, scmap, n_dts, n_cmap, p.entry_date)
            trades.append({
                "entry_date": p.entry_date, "exit_date": p.exit_date,
                "entry_price": p.entry_price, "exit_price": p.exit_price,
                "corr": p.corr_mode, "rs": rs, "stock": p.stock_code,
            })

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)

        # baseline fifo taken set (for diff counting)
        taken_fifo, _ = _select([dict(t) for t in trades], "fifo")

        for pol in policies:
            taken, by_id = _select([dict(t) for t in trades], pol)
            if pol in diff_trades:
                diff_trades[pol] += len(taken.symmetric_difference(taken_fifo))
            day_contrib = defaultdict(float)
            for tid in taken:
                t = by_id[tid]
                sdts, scmap = stock_dts.get(t["stock"], ([], {}))
                for d, r in _pos_daily(t["entry_date"], t["exit_date"],
                                       t["entry_price"], t["exit_price"], sdts, scmap).items():
                    if d in cal_set:
                        day_contrib[d] += r / _SLOTS
            fy_r = [day_contrib.get(d, 0.0) for d in cal]
            per_fy[pol][cfg.label] = (_metrics(fy_r), len(taken))
            stitched[pol] += fy_r[1:]
        logger.info("  {} processed ({} candidates)", cfg.label, len(results))

    # ── report ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 88)
    print(f"RS RE-RANK A/B — 4-slot capital-aware book, {n_total} candidates, "
          f"K={_K_DAYS}d shelf, RS=trail{_RS_LOOKBACK} rel N225")
    print("=" * 88)
    print(f"{'FY':<8} | {'FIFO tot/Sh/DD':>22} | {'RS-high tot/Sh/DD':>22} | "
          f"{'RS-low tot/Sh/DD':>22}")
    def _fmt(m):
        t, s, dd = m
        return f"{t*100:+6.1f}/{s:+5.2f}/{dd*100:6.1f}"
    for cfg in _FYS:
        if cfg.label not in per_fy["fifo"]:
            continue
        print(f"{cfg.label:<8} | "
              f"{_fmt(per_fy['fifo'][cfg.label][0]):>22} | "
              f"{_fmt(per_fy['rs_high'][cfg.label][0]):>22} | "
              f"{_fmt(per_fy['rs_low'][cfg.label][0]):>22}")
    print("-" * 88)
    sm = {p: _metrics(stitched[p]) for p in policies}
    print(f"{'STITCH':<8} | {_fmt(sm['fifo']):>22} | {_fmt(sm['rs_high']):>22} | "
          f"{_fmt(sm['rs_low']):>22}")
    print("\n(tot=total return %, Sh=daily Sharpe ×√252, DD=max drawdown %)")

    dh = sm["rs_high"][1] - sm["fifo"][1]
    dl = sm["rs_low"][1] - sm["fifo"][1]
    print(f"\nStitched Sharpe:  FIFO {sm['fifo'][1]:+.2f} | "
          f"RS-high {sm['rs_high'][1]:+.2f} (Δ {dh:+.2f}) | "
          f"RS-low {sm['rs_low'][1]:+.2f} (Δ {dl:+.2f})")
    print(f"Trades differing from FIFO:  RS-high {diff_trades['rs_high']}, "
          f"RS-low {diff_trades['rs_low']}  (of {n_total})")

    # per-FY sign consistency of RS-high vs FIFO
    wins = sum(1 for c in _FYS if c.label in per_fy["fifo"]
               and per_fy["rs_high"][c.label][0][1] > per_fy["fifo"][c.label][0][1])
    testable = sum(1 for c in _FYS if c.label in per_fy["fifo"])
    print(f"RS-high beats FIFO on Sharpe in {wins}/{testable} FYs")

    mono = sm["rs_high"][1] >= sm["fifo"][1] >= sm["rs_low"][1]
    print("\n" + "-" * 88)
    print(f"MONOTONE (RS-high ≥ FIFO ≥ RS-low on stitched Sharpe)? {mono}")
    if mono and dh > 0.05:
        print("  -> RS carries selection signal (weak/strong TBD by magnitude + n).")
    else:
        print("  -> No clean monotone RS signal; re-ranking does not separate from FIFO.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
