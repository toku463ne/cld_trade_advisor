"""Confluence persistence A/B: does requiring a level-breakout to STILL HOLD help?

/sign-debate 2026-05-23, operator-authorized after the diagnostic showed it BITES
(27.8% of N>=3 stock-days would drop below quorum). This decides whether it HELPS.

Arm A (current): a sign counts toward the N>=3 quorum if it fired within valid_bars.
Arm B (persistence): same, EXCEPT brk_tenkan_hi / brk_kumo_hi count on an in-window
day only if their level STILL HOLDS that day (low > tenkan / low > kumo_top).
(brk_sma/brk_bol already self-trim; chiko out of scope — judge.)

Both arms feed the production 6-slot book (run_simulation, _MAX_LOW_CORR=5) +
ZsTpSl. Binding test = paired fill-order null (same seed to both arms) at the
~36-trade/yr book, plus per-FY + FY2025 OOS + the §3 guardrail (n drop must be
justified by a Sharpe/EV lift). Accept if P(Δ>0) >= 0.85 AND no bull/bear sign-flip
AND FY2025 OOS Δ >= 0; lean-yes if P in [0.75,0.85); else park.

OUTCOME (2026-05-23, 8 FYs FY2018-2025, 200 paired shuffles, full rebuilt
sign_benchmark): PARK — persistence does NOT help the portfolio. Deterministic
per-FY LOOKED strong (bull-mean Δ +0.56, bear +0.26, FY2025 OOS +1.11, 5/8 FYs
up) — but the PAIRED fill-order null kills it: Δ Sharpe −0.062, P(Δ>0)=0.395, CI
[−0.528,+0.329] (A mean +0.91 vs B +0.85). The per-FY table was a single lucky
fill order — the SAME trap as ADX-priority (single-arm strong → paired coin flip).
Crucially B drops only ~2% of TRADES (409→401), NOT the 27.8% the diagnostic
implied: the diagnostic counted stock-DAYS, but the realized book emits 1 candidate
per burst + skips most at the slot cap, so per-day lapses rarely change a trade.
KEEP the current windowed validity; the brk_sma/brk_bol-vs-kumo/tenkan code split
is cosmetic at the portfolio level (resolve by fiat/doc if desired, no perf impact).
See project_confluence_level_persistence.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_persistence_ab
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
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.indicators.ichimoku import calc_ichimoku
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 6                # production after the 6-slot ship (1 high + 5 low)
_K = 200
_PERSIST = ("brk_tenkan_hi", "brk_kumo_hi")
_BULL_FYS = {"FY2020", "FY2023", "FY2025"}
_FYS = [FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
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


def _level_info(cache):
    """Per-stock ichimoku for the persistence re-check: date->idx, low, tenkan, kumo_top."""
    seen, dts, H, L, C = set(), [], [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); H.append(b.high); L.append(b.low); C.append(b.close)
    order = np.argsort(dts)
    dts = [dts[i] for i in order]; H = [H[i] for i in order]
    L = [L[i] for i in order]; C = [C[i] for i in order]
    ichi = calc_ichimoku(H, L, C, tenkan_period=9)
    tk = np.asarray(ichi["tenkan"], dtype=float)
    sa = np.asarray(ichi["senkou_a"], dtype=float); sb = np.asarray(ichi["senkou_b"], dtype=float)
    disp = ichi["displacement"]; n = len(dts)
    kt = np.full(n, np.nan)
    for i in range(disp, n):
        kt[i] = max(sa[i - disp], sb[i - disp])
    return {d: i for i, d in enumerate(dts)}, np.asarray(L, dtype=float), tk, kt


def _holds(sign, lo, tk, kt, i):
    if i is None:
        return False
    if sign == "brk_tenkan_hi":
        return tk[i] == tk[i] and lo[i] > tk[i]
    return kt[i] == kt[i] and lo[i] > kt[i]


def _candidates(stock, fires, cache, corr_map, zs_map, lvl, start, end, persistence):
    """Mirror of cbt._candidates_for_stock with an optional level-persistence gate."""
    if not cache.bars:
        return []
    by_date, tdates, seen = {}, [], set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); by_date[d] = b.close; tdates.append(d)
    tdates.sort()
    d2i = {d: i for i, d in enumerate(tdates)}
    lidx, lo, tk, kt = lvl

    valid: dict[int, set] = defaultdict(set)
    for sign, fd in fires:
        if fd not in d2i:
            continue
        fi = d2i[fd]; vb = _BULLISH.get(sign, 5)
        for j in range(fi, min(fi + vb + 1, len(tdates))):
            if persistence and sign in _PERSIST:
                if not _holds(sign, lo, tk, kt, lidx.get(tdates[j])):
                    continue
            valid[j].add(sign)

    cands, last = [], -10_000
    for i, d in enumerate(tdates):
        if d < start or d > end or len(valid.get(i, set())) < _N_GATE:
            continue
        if i - last < cbt._COOLDOWN_BARS:
            continue
        cands.append(EntryCandidate(
            stock_code=stock, entry_date=d, entry_price=by_date[d],
            corr_mode=corr_map.get(d, "mid"), corr_n225=0.0, zs_history=zs_map.get(d, ())))
        last = i
    return cands


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


def _sharpe(r):
    if len(r) < 2:
        return float("nan")
    sd = statistics.stdev(r)
    return statistics.mean(r) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(cands, caches, cfg, stock_dts, cal):
    cal_set = set(cal)
    res = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
    day = defaultdict(float)
    for p in res:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r / _SLOTS
    return [day.get(d, 0.0) for d in cal][1:], len(res)


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0   # DB holds only fresh post-rebuild runs
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    per_fy = {}
    stA = [[] for _ in range(_K)]
    stB = [[] for _ in range(_K)]
    n_seen = [0, 0]

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            logger.warning("no universe for {} ({}) — skip", cfg.label, cfg.stock_set)
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
        if not caches:
            logger.warning("no caches for {} — skip", cfg.label)
            continue
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        lvl = {code: _level_info(c) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        candsA, candsB = [], []
        for code in caches:
            fr = fires.get(code, [])
            candsA += _candidates(code, fr, caches[code], corr_maps[code], zs_maps[code],
                                  lvl[code], cfg.start, cfg.end, False)
            candsB += _candidates(code, fr, caches[code], corr_maps[code], zs_maps[code],
                                  lvl[code], cfg.start, cfg.end, True)

        rA, nA = _fy_returns(sorted(candsA, key=lambda c: c.entry_date), caches, cfg, stock_dts, cal)
        rB, nB = _fy_returns(sorted(candsB, key=lambda c: c.entry_date), caches, cfg, stock_dts, cal)
        per_fy[cfg.label] = (_sharpe(rA), _sharpe(rB), nA, nB)
        n_seen[0] += nA; n_seen[1] += nB

        for k in range(_K):
            ra = candsA[:]; rb = candsB[:]
            random.Random(k).shuffle(ra); random.Random(k).shuffle(rb)
            stA[k] += _fy_returns(ra, caches, cfg, stock_dts, cal)[0]
            stB[k] += _fy_returns(rb, caches, cfg, stock_dts, cal)[0]
        logger.info("  {} done (A {} / B {} trades)", cfg.label, nA, nB)

    print("\n" + "=" * 78)
    print(f"CONFLUENCE PERSISTENCE A/B — 6-slot, {_K} paired shuffles")
    print("A = current (fired-within-window) | B = + brk_tenkan_hi/brk_kumo_hi must STILL HOLD")
    print("=" * 78)
    print(f"\n{'FY':<9}{'A Sh':>8}{'B Sh':>8}{'ΔSh':>8}{'A tr':>7}{'B tr':>7}   note")
    bull, bear = [], []
    for cfg in _FYS:
        if cfg.label not in per_fy:
            continue
        a, b, na, nb = per_fy[cfg.label]
        dlt = b - a
        (bull if cfg.label in _BULL_FYS else bear).append(dlt)
        note = "OOS" if cfg.label == "FY2025" else ""
        print(f"{cfg.label:<9}{a:>8.2f}{b:>8.2f}{dlt:>8.2f}{na:>7}{nb:>7}   {note}")
    if "FY2025" in per_fy:
        oos = per_fy["FY2025"][1] - per_fy["FY2025"][0]
        print(f"\n  bull-mean Δ {np.mean(bull) if bull else float('nan'):+.2f} | "
              f"bear-mean Δ {np.mean(bear) if bear else float('nan'):+.2f} | FY2025 OOS Δ {oos:+.2f}"
              f"  {'(sign-flip!)' if bull and bear and np.mean(bull)*np.mean(bear)<0 else ''}")
    print(f"  trades total: A {n_seen[0]}  B {n_seen[1]}  (B drops {n_seen[0]-n_seen[1]}, "
          f"{100*(n_seen[0]-n_seen[1])/max(1,n_seen[0]):.0f}%)")

    shA = np.array([_sharpe(stA[k]) for k in range(_K)])
    shB = np.array([_sharpe(stB[k]) for k in range(_K)])
    d = shB - shA
    print(f"\n[paired fill-order null] A Sharpe mean {shA.mean():+.2f} | B {shB.mean():+.2f}")
    lo, hi = np.percentile(d, [2.5, 97.5])
    p = (d > 0).mean()
    print(f"  paired Δ Sharpe (B−A) mean {d.mean():+.3f} | 95% CI [{lo:+.3f}, {hi:+.3f}] | P(Δ>0)={p:.3f}")
    cert = p >= 0.85 and lo > -0.05
    print(f"  VERDICT: " + ("ACCEPT-lean — persistence helps" if p >= 0.85 else
          "lean-yes / operator-call" if p >= 0.75 else "PARK — no portfolio benefit"))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
