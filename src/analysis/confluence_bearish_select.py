"""Selection A/B: prefer 'N bullish + 1 bearish' candidates when filling slots.

Operator hypothesis (informed by the bearish-veto Stage-0 bowl: per-fire DR
bearish=0 58% < bearish=1 64.2% > bearish>=2 50.7%): when ordering the
candidate pool that competes for the 4 slots, prefer candidates with exactly 1
valid bearish sign.  This is a SELECTION preference (not the rejected veto).

Method = the corrected RS-rerank harness: order the full ~1200/FY candidate pool
by a bearish-preference key (stable sort keeps the order within each entry_date,
so run_simulation fills free slots in that order), mark the capital-aware 4-slot
equity curve.  Bearish count at a candidate's entry_date = distinct bearish sign
types valid (windowed 5 bars), same as Stage-0.

Arms:
  baseline   — entry_date only (production / arbitrary within-day)
  prefer_b1  — bearish==1 first   (the hypothesis)
  prefer_b0  — bearish==0 first   (falsification)
  prefer_b2  — bearish>=2 first   (falsification; bowl predicts WORST)
Bowl prediction at selection level: prefer_b1 > baseline > prefer_b2.

Also: per-bearish-bucket DR on baseline filled trades (does the bowl replicate
on the actual filled book?), and block-bootstrap certification of prefer_b1 /
prefer_b0 / veto_b2.

OUTCOME (2026-05-22): the per-fire "bearish=1 bowl" did NOT replicate on the
filled book — bearish 0 and 1 tie (DR 62.2% / 61.5%, mean_r +2.89% / +2.23%),
≥2 is the clear loser (DR 45.2%, mean_r −0.11%). So the operator's "prefer 1
bearish" is real but suboptimal: the gradient is monotone "fewer bearish =
better". Stitched Sharpe baseline 0.84 < prefer_b2 0.85 < veto_b2 0.96 (+0.12)
< prefer_b1 1.03 (+0.20) < prefer_b0 1.12 (+0.29, maxDD −20.8 vs −29.9).
prefer_b0 is the strongest selection lead of the session (per-FY 8/9 PASS,
FY2025 OOS +0.52 PASS) but bootstrap G1 just misses (CI [−0.13,+0.81] p=0.078).
veto_b2 (hard skip ≥2) is weaker than prefer_b0 (soft ordering) — ORDERING beats
FILTERING (keeps ≥2 as last-resort fills, stays invested). Actionable: prefer
the FEWEST valid bearish signs (0 best), deprioritize ≥2. Uncertified. See
project_confluence_bearish_select.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_bearish_select
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
_BEARISH = ("rev_nhi", "rev_hi", "brk_kumo_lo", "brk_tenkan_lo", "chiko_lo")
_BEARISH_VB = 5
_N_GATE = 3
_SLOTS = 4
_HOLDOUT = "FY2025"
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
    return total, sh, float((eq / runmax - 1.0).min())


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _bearish_count_map(bear_fires, dates):
    """date -> #distinct bearish sign types valid (windowed _BEARISH_VB bars)."""
    d2i = {d: i for i, d in enumerate(dates)}
    per_idx = defaultdict(set)
    for sign, fd in bear_fires:
        if fd not in d2i:
            continue
        fi = d2i[fd]
        for j in range(fi, min(fi + _BEARISH_VB + 1, len(dates))):
            per_idx[j].add(sign)
    return {dates[i]: len(per_idx[i]) for i in range(len(dates))}


def _block_boot(a, b, block=21, iters=5000, seed=0):
    rng = np.random.default_rng(seed)
    a = np.asarray(a); b = np.asarray(b)
    n = len(a); nb = math.ceil(n / block)
    deltas = []
    for _ in range(iters):
        starts = rng.integers(0, max(1, n - block), size=nb)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        deltas.append(_sharpe(b[idx]) - _sharpe(a[idx]))
    deltas = np.array(deltas)
    return float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5)), float((deltas <= 0).mean())


def _pref_key(b, mode):
    if mode == "b1":
        return 0 if b == 1 else (1 if b == 0 else 2)
    if mode == "b0":
        return 0 if b == 0 else (1 if b == 1 else 2)
    if mode == "b2":
        return 0 if b >= 2 else (1 if b == 1 else 2)
    return 0


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        brows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
        bear_rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(_BEARISH))).all()
    fires = defaultdict(list)
    for sg, st, fa in brows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    bear_by_stock = defaultdict(list)
    for sg, st, fa in bear_rows:
        bear_by_stock[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    arms = ["baseline", "prefer_b1", "prefer_b0", "prefer_b2", "veto_b2"]
    stitched = {a: [] for a in arms}
    per_fy = {a: {} for a in arms}
    # baseline per-bucket trade returns (does the bowl replicate on filled book?)
    bucket_rets = {0: [], 1: [], 2: []}

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
        n_dts, n_cmap = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        bear_maps = {code: _bearish_count_map(bear_by_stock.get(code, []), dts)
                     for code, (dts, _cm) in stock_dts.items()}

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        bcount = {id(c): bear_maps.get(c.stock_code, {}).get(c.entry_date, 0) for c in cands}

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)

        for a in arms:
            if a == "veto_b2":
                # hard filter: drop candidates carrying >=2 bearish signs
                pool = [c for c in cands if bcount[id(c)] < 2]
                ordered = sorted(pool, key=lambda c: c.entry_date)
            else:
                mode = a.split("_")[-1] if "_" in a else ""
                ordered = sorted(cands, key=lambda c: (c.entry_date, _pref_key(bcount[id(c)], mode)))
            results = run_simulation(ordered, cbt._EXIT_RULE, caches, cfg.end)
            day_contrib = defaultdict(float)
            for p in results:
                sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
                for d, r in _pos_daily(p, sdts, scmap).items():
                    if d in cal_set:
                        day_contrib[d] += r / _SLOTS
            fy_r = [day_contrib.get(d, 0.0) for d in cal]
            per_fy[a][cfg.label] = _metrics(fy_r)
            stitched[a] += fy_r[1:]
            if a == "baseline":
                for p in results:
                    b = bear_maps.get(p.stock_code, {}).get(p.entry_date, 0)
                    bucket_rets[min(b, 2)].append(p.exit_price / p.entry_price - 1.0)
        logger.info("  {} done ({} candidates)", cfg.label, len(cands))

    # ── per-bucket DR on baseline filled trades ────────────────────────────────
    print("\n" + "=" * 78)
    print("BEARISH-SELECT A/B — does 'prefer bearish==1' help when filling slots?")
    print("=" * 78)
    print("\n[bowl check] baseline FILLED trades by bearish-count at entry:")
    print(f"{'bearish':>8}{'n':>6}{'DR%':>8}{'mean_r%':>10}")
    for b in (0, 1, 2):
        rs = bucket_rets[b]
        if not rs:
            continue
        dr = 100.0 * sum(1 for r in rs if r > 0) / len(rs)
        lbl = ">=2" if b == 2 else str(b)
        print(f"{lbl:>8}{len(rs):>6}{dr:>8.1f}{statistics.mean(rs)*100:>10.2f}")

    # ── arm comparison ──────────────────────────────────────────────────────────
    sm = {a: _metrics(stitched[a]) for a in arms}
    print(f"\n[arms] stitched (FY2017-2025):")
    print(f"{'arm':<11}{'total%':>9}{'Sharpe':>8}{'maxDD%':>8}")
    for a in arms:
        print(f"{a:<11}{sm[a][0]*100:>9.1f}{sm[a][1]:>8.2f}{sm[a][2]*100:>8.1f}")
    base_sh = sm["baseline"][1]
    print(f"\nbowl prediction: prefer_b1 > baseline > prefer_b2")
    print(f"  prefer_b1 Δ {sm['prefer_b1'][1]-base_sh:+.2f} | "
          f"prefer_b0 Δ {sm['prefer_b0'][1]-base_sh:+.2f} | "
          f"prefer_b2 Δ {sm['prefer_b2'][1]-base_sh:+.2f}")

    # ── certify each candidate arm vs baseline ───────────────────────────────────
    base = np.asarray(stitched["baseline"])
    testable = sum(1 for c in _FYS if c.label in per_fy["baseline"])
    for arm in ("prefer_b1", "prefer_b0", "veto_b2"):
        lo, hi, p0 = _block_boot(base, np.asarray(stitched[arm]))
        wins = sum(1 for c in _FYS if c.label in per_fy["baseline"]
                   and per_fy[arm][c.label][1] > per_fy["baseline"][c.label][1])
        oos = (per_fy[arm].get(_HOLDOUT, (0, float("nan"), 0))[1]
               - per_fy["baseline"].get(_HOLDOUT, (0, float("nan"), 0))[1])
        g1 = "PASS" if lo > 0 else "FAIL"
        g2 = "PASS" if wins >= 6 else "FAIL"
        g3 = "PASS" if oos > 0 else "FAIL"
        print(f"\n[certify {arm} vs baseline]")
        print(f"  G1 block-bootstrap ΔSharpe CI [{lo:+.2f}, {hi:+.2f}] p(Δ≤0)={p0:.3f} -> {g1}")
        print(f"  G2 per-FY wins {wins}/{testable} -> {g2}")
        print(f"  G3 {_HOLDOUT} OOS ΔSharpe {oos:+.2f} -> {g3}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
