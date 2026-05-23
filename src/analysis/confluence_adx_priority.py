"""ADX-priority selection vs the fill-order null: high > low > mid ADX.

Operator: the stock-chop cut (confluence_regime_pooling.py) showed per-trade EV
is non-monotonic in the stock's own ADX14 at entry — trending (high ADX) best
(alpha +0.98%), choppy (low) 2nd (+0.67%), mid worst (+0.45%). So when several
candidates compete for a freeing slot on a trade day, prioritise them
high-ADX > low-ADX > mid-ADX. Does that ordering beat the slot-fill luck?

This is a SELECTION rule (it only changes which same-day competitor wins a slot),
so the correct benchmark is the within-day fill-order permutation null — the same
one that rejected RS (p=0.41), corr-greedy (p=0.27) and prefer_b0 (p~0.11). The
ADX priority is encoded as a static per-candidate scalar (trending=2, choppy=1,
mid=0 from per-FY ADX terciles) and applied as a pre-sort within each entry_date
(equivalent to a holdings-independent day_selector). Positioned against the
200-shuffle null by percentile + one-sided permutation p.

NOTE: ZsTpSl exit does not populate ADX14, so _add_adx() is called on each cache.

OUTCOME (2026-05-23): single-arm LOOKED like the session's best lead, but the
paired null (confluence_adx_priority_null.py) REJECTED it — the single-arm score
was order luck. This script's deterministic arm: Sharpe 1.15 / +564% / DD −23.5
/ pctile 92 / perm p=0.080 (vs baseline 0.84 / p44), per-FY 6/9, FY2025 OOS
Δ +1.22. By single-arm gates that's the strongest selector ever (beats RS p0.41,
corr-greedy p0.27, prefer_b0 p~0.11) and clears per-FY + OOS. BUT the paired
test (same fill order, with vs without ADX tiebreak) shows the isolated ADX
effect is +0.029 Sharpe (P(Δ>0)=0.545, coin flip): the deterministic 1.15 was a
favorable ORDER draw mis-attributed to ADX. Lesson: single-arm-vs-null percentile
conflates the rule with its lucky draw; the PAIRED null is decisive (same trap
the capacity test avoided). Per-fire EV gap is real (α +0.98/+0.67/+0.45%) but
does NOT translate to a portfolio picking edge. REJECT as gate AND as UI hint
(§5.11: a hint implies a non-existent edge). See project_confluence_phase_regime.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_adx_priority
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
from src.analysis.exit_benchmark import FyConfig, _add_adx
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
_K = 200
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
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    return eq[-1] - 1.0, sh, float((eq / runmax - 1.0).min())


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(cands, caches, cfg, stock_dts, cal):
    cal_set = set(cal)
    results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / _SLOTS
    return [day_contrib.get(d, 0.0) for d in cal]


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

    st_base, st_adx = [], []
    per_fy = {}            # label -> (base_sharpe, adx_sharpe)
    st_shuffle = [[] for _ in range(_K)]

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
                    _add_adx(c)
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, n_cmap = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}

        # stock ADX14 at each date
        stock_adx = {}
        for code, c in caches.items():
            m = {}
            for b in c.bars:
                a = b.indicators.get("ADX14")
                if a and a == a:
                    m[b.dt.date()] = a
            stock_adx[code] = m

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))

        # per-candidate ADX + per-FY tercile cuts → priority (trending2>choppy1>mid0)
        adx_of = {id(c): stock_adx.get(c.stock_code, {}).get(c.entry_date) for c in cands}
        vals = np.array([a for a in adx_of.values() if a is not None])
        lo, hi = (np.percentile(vals, [33.33, 66.67]) if vals.size else (0.0, 0.0))

        def _prio(c):
            a = adx_of[id(c)]
            if a is None:
                return 0          # unknown → treat as mid
            if a > hi:
                return 2          # trending — best
            if a <= lo:
                return 1          # choppy — 2nd
            return 0              # mid — worst

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        base = sorted(cands, key=lambda c: c.entry_date)
        adx_order = sorted(cands, key=lambda c: (c.entry_date, -_prio(c)))

        rb = _fy_returns(base, caches, cfg, stock_dts, cal)[1:]
        ra = _fy_returns(adx_order, caches, cfg, stock_dts, cal)[1:]
        st_base += rb
        st_adx += ra
        per_fy[cfg.label] = (_sharpe(rb), _sharpe(ra))
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            st_shuffle[k] += _fy_returns(pool, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} candidates, ADX cuts {:.1f}/{:.1f})",
                    cfg.label, len(cands), lo, hi)

    sh_dist = np.array([_sharpe(s) for s in st_shuffle])
    bm, am = _metrics(st_base), _metrics(st_adx)

    def _pct(v):
        return 100.0 * (sh_dist < v).mean()
    def _pval(v):
        return float((sh_dist >= v).mean())

    print("\n" + "=" * 78)
    print(f"ADX-PRIORITY (high>low>mid) vs FILL-ORDER NULL — {_K} shuffles, 4-slot")
    print("=" * 78)
    print(f"\n[shuffle null] Sharpe mean {sh_dist.mean():+.2f} sd {sh_dist.std():.2f} | "
          f"p5 {np.percentile(sh_dist,5):+.2f} p50 {np.percentile(sh_dist,50):+.2f} "
          f"p95 {np.percentile(sh_dist,95):+.2f}")
    print(f"\n{'arm':<16}{'Sharpe':>8}{'total%':>9}{'maxDD%':>8}{'pctile':>8}{'perm p':>9}")
    for name, m in (("baseline", bm), ("adx-priority", am)):
        print(f"{name:<16}{m[1]:>8.2f}{m[0]*100:>9.1f}{m[2]*100:>8.1f}"
              f"{_pct(m[1]):>7.0f}%{_pval(m[1]):>9.3f}")
    print("\n(perm p = P(random order >= arm). ADX-priority is real only if perm p "
          "is small AND it beats the null p95 — same bar RS/corr-greedy/prefer_b0 failed.)")

    print(f"\n{'FY':<9}{'base Sh':>10}{'adx Sh':>10}{'Δ':>9}   note")
    wins = 0
    for cfg in _FYS:
        if cfg.label not in per_fy:
            continue
        b, a = per_fy[cfg.label]
        wins += a > b
        note = "OOS" if cfg.label == "FY2025" else ""
        print(f"{cfg.label:<9}{b:>10.2f}{a:>10.2f}{a-b:>+9.2f}   {note}")
    n_fy = len(per_fy)
    print(f"\n  per-FY: ADX-priority wins {wins}/{n_fy} FYs"
          f"  (selection-cert bar = >=6/9 + FY2025 OOS positive Δ).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
