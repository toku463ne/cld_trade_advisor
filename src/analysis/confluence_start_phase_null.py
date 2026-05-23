"""Start-phase robustness: does WHEN you begin the book change the year?

Companion to confluence_slot_order.py. That script perturbs the WITHIN-DAY fill
order (which of several same-day candidates wins a contested slot). This script
perturbs a different, complementary axis the within-day shuffle does NOT span:
the START PHASE.

Operator's scenario: starting the 4-slot book on 4/1 vs 4/10 leaves *different*
stocks in the slots — and not just on day 1. Positions are sticky (~22-bar
holds, book ~96% invested), so the starting phase cascades: a slot filled early
is blocked for weeks, changing every downstream admit/skip decision. So the
realized trade set depends on the entry phase, which the same-day shuffle can't
reach.

Two questions:
  1. PHASE SENSITIVITY (operationally real — you won't adopt on a FY boundary):
     sweep the start offset (drop candidates before start+offset trading days),
     run the deterministic baseline at each, and report how much the stitched
     Sharpe/return moves with phase ALONE. Trade count per offset is printed so
     the "less time left" confound is visible.
  2. COMBINED phase+order null: layer K within-day shuffles on top of each
     offset → a richer null band. Compared against the order-only null
     (offset 0 shuffles = the confluence_slot_order null). The selection arms
     (corr-greedy, RS-high) are positioned against BOTH nulls — widening the
     band can only make a selection edge HARDER to certify, never easier.

Prior (stated before running): phase widens the band and confirms the selection
reject; but question (1) is worth knowing on its own before committing capital.

OUTCOME (2026-05-23, 8 offsets 0..35 trading days x 15 shuffles):
  1. PHASE SENSITIVITY IS LARGE AND REAL. Deterministic baseline Sharpe swings
     [+0.53, +1.01] (range 0.48, sd 0.15), return [+100%, +359%], on START DATE
     ALONE — and trade count barely moves (326→271), so it is NOT the "less time
     left" confound. The shipped offset-0 (+0.84/+257%) is mid-pack, not special.
     Operationally real: you won't adopt on a FY boundary, and a ~7-week shift in
     when you start swings the year by ~0.5 Sharpe. The wide null is therefore
     mostly REGIME-TIMING variance (sticky slots land over different regimes),
     not name-picking noise — the timing-side view of the 62%-beta decomposition.
  2. Combined phase+order null is WIDER than order-only (sd 0.16→0.19, p5-p95
     width 0.49→0.60): phase is a distinct variance axis the within-day shuffle
     does not span.
  3. Selection still REJECT, more clearly in the wider null: corr-greedy perm p
     0.20→0.23, RS-high 0.47→0.38 — both inside the band. Widening can only hurt
     a selection edge. See project_confluence_fill_order_null.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_start_phase_null
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
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
_RS_LOOKBACK = 60
_CORR_WIN = 20
_OFFSETS = list(range(0, 40, 5))   # 0,5,..,35 trading days (~7 weeks) of start phase
_K_INNER = 15                       # within-day shuffles layered on each offset
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


def _rs(stock_dts, stock_cmap, n_dts, n_cmap, d):
    def _ret(dts, cmap):
        if d not in cmap:
            return None
        i = dts.index(d)
        if i < _RS_LOOKBACK:
            return None
        p0 = cmap[dts[i - _RS_LOOKBACK]]
        return cmap[d] / p0 - 1.0 if p0 else None
    sr = _ret(stock_dts, stock_cmap)
    if sr is None:
        return 0.0
    return sr - (_ret(n_dts, n_cmap) or 0.0)


def _make_corr_selector(returns, didx, window=_CORR_WIN):
    def trailing(code, today):
        di = didx.get(code)
        if di is None:
            return None
        i = di.get(today)
        if i is None or i < window:
            return None
        return returns[code][i - window + 1:i + 1]

    def selector(today, cands, open_pos):
        held = list({p.candidate.stock_code for p in open_pos})
        held_r = [r for r in (trailing(h, today) for h in held) if r is not None]
        if not held_r:
            return cands

        def key(c):
            cr = trailing(c.stock_code, today)
            if cr is None:
                return 0.0
            best = 0.0
            for r in held_r:
                cc = np.corrcoef(cr, r)[0, 1]
                if not math.isnan(cc):
                    best = max(best, abs(cc))
            return best
        return sorted(cands, key=key)
    return selector


def _fy_returns(ordered_or_cands, exit_rule, caches, cfg, stock_dts, cal,
                day_selector=None):
    """Return (daily_returns_over_cal, n_trades)."""
    cal_set = set(cal)
    results = run_simulation(ordered_or_cands, exit_rule, caches, cfg.end,
                             day_selector=day_selector)
    day_contrib = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / _SLOTS
    return [day_contrib.get(d, 0.0) for d in cal], len(results)


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

    # stitched daily series, accumulated across FY
    st_phase = {o: [] for o in _OFFSETS}                 # deterministic per offset
    n_phase = {o: 0 for o in _OFFSETS}
    st_combined = {(o, k): [] for o in _OFFSETS for k in range(_K_INNER)}
    st_corr, st_rs = [], []                              # selection arms (offset 0)

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

        returns, didx = {}, {}
        for code, (dts, cmap) in stock_dts.items():
            cl = np.array([cmap[d] for d in dts])
            r = np.zeros_like(cl, dtype=float)
            if len(cl) > 1:
                r[1:] = cl[1:] / cl[:-1] - 1.0
            returns[code] = r
            didx[code] = {d: i for i, d in enumerate(dts)}

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        rs_of = {}
        for c in cands:
            sdts, scmap = stock_dts.get(c.stock_code, ([], {}))
            rs_of[id(c)] = _rs(sdts, scmap, n_dts, n_cmap, c.entry_date)

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        sel = _make_corr_selector(returns, didx)

        for o in _OFFSETS:
            eff_start = cal[o] if o < len(cal) else cal[-1]
            pool_o = [c for c in cands if c.entry_date >= eff_start]
            base = sorted(pool_o, key=lambda c: c.entry_date)
            rets, n = _fy_returns(base, cbt._EXIT_RULE, caches, cfg, stock_dts, cal)
            st_phase[o] += rets[1:]
            n_phase[o] += n
            for k in range(_K_INNER):
                rng = random.Random(o * 10_000 + k)
                shuf = pool_o[:]
                rng.shuffle(shuf)
                st_combined[(o, k)] += _fy_returns(
                    shuf, cbt._EXIT_RULE, caches, cfg, stock_dts, cal)[0][1:]

        # selection arms at offset 0 (full year), for re-positioning vs the nulls
        base0 = sorted(cands, key=lambda c: c.entry_date)
        rs_order = sorted(cands, key=lambda c: (c.entry_date, -rs_of[id(c)]))
        st_corr += _fy_returns(base0, cbt._EXIT_RULE, caches, cfg, stock_dts, cal, sel)[0][1:]
        st_rs += _fy_returns(rs_order, cbt._EXIT_RULE, caches, cfg, stock_dts, cal)[0][1:]
        logger.info("  {} done ({} candidates)", cfg.label, len(cands))

    # ── phase-sensitivity curve (deterministic baseline per offset) ──────────
    phase_sh = np.array([_sharpe(st_phase[o]) for o in _OFFSETS])
    phase_rt = np.array([_metrics(st_phase[o])[0] for o in _OFFSETS])
    phase_dd = np.array([_metrics(st_phase[o])[2] for o in _OFFSETS])

    print("\n" + "=" * 78)
    print(f"START-PHASE SENSITIVITY — deterministic baseline, offsets "
          f"{_OFFSETS[0]}..{_OFFSETS[-1]} trading days")
    print("=" * 78)
    print(f"\n{'offset(d)':>9}{'trades':>8}{'Sharpe':>9}{'total%':>9}{'maxDD%':>9}")
    for i, o in enumerate(_OFFSETS):
        print(f"{o:>9}{n_phase[o]:>8}{phase_sh[i]:>9.2f}{phase_rt[i]*100:>9.1f}"
              f"{phase_dd[i]*100:>9.1f}")
    print(f"\n  start phase ALONE moves: Sharpe [{phase_sh.min():+.2f}, "
          f"{phase_sh.max():+.2f}] (range {phase_sh.max()-phase_sh.min():.2f}, "
          f"sd {phase_sh.std():.2f}) | return [{phase_rt.min()*100:+.0f}%, "
          f"{phase_rt.max()*100:+.0f}%]")

    # ── order-only null (offset 0 shuffles) vs combined phase+order null ─────
    order_only = np.array([_sharpe(st_combined[(0, k)]) for k in range(_K_INNER)])
    combined = np.array([_sharpe(st_combined[(o, k)])
                         for o in _OFFSETS for k in range(_K_INNER)])

    def _band(d):
        return (f"mean {d.mean():+.2f} sd {d.std():.2f} | "
                f"p5 {np.percentile(d,5):+.2f} p50 {np.percentile(d,50):+.2f} "
                f"p95 {np.percentile(d,95):+.2f} | "
                f"width(p95-p5) {np.percentile(d,95)-np.percentile(d,5):.2f}")

    print("\n" + "=" * 78)
    print("NULL COMPARISON — order-only vs combined phase+order (stitched Sharpe)")
    print("=" * 78)
    print(f"\norder-only (offset 0, {_K_INNER} shuffles):   {_band(order_only)}")
    print(f"combined   ({len(_OFFSETS)}x{_K_INNER}={combined.size} draws):       {_band(combined)}")

    # selection arms vs both nulls
    cm, rm = _metrics(st_corr), _metrics(st_rs)
    print(f"\n{'arm':<14}{'Sharpe':>8}{'p in order-only':>17}{'p in combined':>15}")
    for name, m in (("corr-greedy", cm), ("RS-high", rm)):
        p_o = float((order_only >= m[1]).mean())
        p_c = float((combined >= m[1]).mean())
        print(f"{name:<14}{m[1]:>8.2f}{p_o:>17.3f}{p_c:>15.3f}")
    print("\n(perm p = P(null Sharpe >= arm). A wider combined null = larger p = "
          "harder to certify. Selection edge is real only if p stays small in BOTH.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
