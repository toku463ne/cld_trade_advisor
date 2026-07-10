"""Per-stock sign-quality SWAP / mid-trade EVICTION rule vs the fill-order null.

Operator idea (2026-07-10): on a confluence trigger day, if a NEW candidate C fires with a
GOOD trailing per-(stock,sign) track record, and a currently-HELD position P is
  (a) highly correlated (20d daily-return corr >= 0.6) to C's stock, AND
  (b) entered on a NOT-good sign (bad trailing quality),
then EVICT P and admit C into the freed slot.  Two co-moving stocks are ONE logical bet
(CLAUDE.md correlation philosophy), so this is a 1-for-1 representative swap that keeps
exposure fixed while upgrading the entry sign quality.

This is DISTINCT from the rejected per_stock_sign_quality arc (good-first priority,
veto-neg, contention tiebreaker) — those all decided EMPTY-slot fills; reordering rarely
changes which 6 fill.  This EVICTS an occupied slot mid-trade, the one mechanism those
tests never covered (see project_per_stock_sign_quality_reject.md).

Arms (K paired shuffles, same Random(k) fill order fed to every arm per seed):
  A   6-slot, no swap    (canonical book — the honest baseline; capital fixed)
  B   6-slot, swap on    (1-for-1 eviction, capital held fixed) <- the honest swap test
  A7  7-slot, no swap    (isolates the +1-slot EXPOSURE lever, A7 vs A)
  B7  7-slot, swap on    (the operator's "add budget for a 7th" framing, B7 vs A7)

Costs: a round-trip cost (bps) is charged uniformly on EVERY trade in EVERY arm, allocated
to the exit day, so the swap's extra churn (an early close + a fresh open) is penalised
fairly.  Run at COST_BPS in {0, 30}.

Binding gate (B vs A): P(Δ Sharpe > 0) >= 0.95 AND 95% CI lower bound > 0.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.per_stock_sign_quality_swap_null
"""
from __future__ import annotations

import copy
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
from src.analysis.per_stock_sign_quality_null import (
    _BULLISH, _N_GATE, _build_cands, _candidate_quality, _closes, _load_qmap,
)
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.base import ExitResult
from src.exit.exit_simulator import _DayBar, _daily_bars_with_adx, _isnan
from src.simulator.cache import DataCache

_K = 200
_H = 10
_CORR_GATE = 0.60        # pairwise 20d daily-return corr => "same logical bet"
_CORR_WIN = 20
_COST_LIST = (0.0, 0.0030)   # round-trip cost per trade, allocated to exit day


# ─────────────────────── pairwise stock-stock correlation ──────────────────────

def _build_ret_arrays(caches, cal):
    """Per stock, a return array aligned to the FY calendar `cal` (NaN where missing)."""
    idx = {d: i for i, d in enumerate(cal)}
    ret: dict[str, np.ndarray] = {}
    for code, c in caches.items():
        dts, cmap = _closes(c)
        arr = np.full(len(cal), np.nan)
        prev = None
        for d in dts:
            if prev is not None and d in idx and prev in cmap and cmap[prev]:
                arr[idx[d]] = cmap[d] / cmap[prev] - 1.0
            prev = d
        ret[code] = arr
    return ret, idx


class _CorrFn:
    """corr(a, b, today) = 20d trailing daily-return correlation, cached, look-ahead safe."""

    def __init__(self, ret_arr, date_idx):
        self.ret = ret_arr
        self.didx = date_idx
        self.cache: dict[tuple[str, str, int], float] = {}

    def __call__(self, a, b, today):
        ti = self.didx.get(today)
        if ti is None or ti < _CORR_WIN:
            return None
        key = (a, b, ti) if a < b else (b, a, ti)
        if key in self.cache:
            return self.cache[key]
        ra = self.ret.get(a); rb = self.ret.get(b)
        if ra is None or rb is None:
            self.cache[key] = None
            return None
        wa = ra[ti - _CORR_WIN + 1:ti + 1]
        wb = rb[ti - _CORR_WIN + 1:ti + 1]
        m = np.isfinite(wa) & np.isfinite(wb)
        if m.sum() < 15 or np.std(wa[m]) == 0 or np.std(wb[m]) == 0:
            self.cache[key] = None
            return None
        val = float(np.corrcoef(wa[m], wb[m])[0, 1])
        self.cache[key] = val
        return val


# ─────────────────────── engine with mid-trade eviction ────────────────────────

def run_swap_simulation(candidates, rule, stock_caches, end_date, *, swap, qual, med,
                        corr_fn, max_low):
    """run_simulation clone with an optional GOOD-candidate → BAD-correlated-held swap.

    Identical to exit_simulator.run_simulation when swap=False (so A vs B differ only by
    the eviction).  Returns (results, n_swaps).
    """
    max_high = 1
    sorted_cands = sorted(candidates, key=lambda c: c.entry_date)
    bar_index = {code: _daily_bars_with_adx(c) for code, c in stock_caches.items()}
    date_to_idx = {code: {b.date: i for i, b in enumerate(bars)}
                   for code, bars in bar_index.items()}
    results: list[ExitResult] = []
    open_pos: list = []
    n_swaps = 0
    cand_idx, n_cands = 0, len(sorted_cands)
    all_dates: set = set()
    for bars in bar_index.values():
        all_dates.update(b.date for b in bars)
    sorted_dates = sorted(all_dates)

    def _q(p):
        return qual.get((p.candidate.stock_code, p.candidate.entry_date))

    def _close_pos(p, today, reason):
        bar = p.bars[-1]
        results.append(ExitResult(
            stock_code=p.candidate.stock_code, entry_date=p.candidate.entry_date,
            exit_date=today, entry_price=p.fill_price, exit_price=bar.close,
            hold_bars=len(p.bars) - 1, exit_reason=reason,
            corr_mode=p.candidate.corr_mode))

    for today in sorted_dates:
        if today > end_date:
            break
        # 1. advance + exit-check open positions
        for pos in open_pos:
            bar_i = date_to_idx.get(pos.candidate.stock_code, {}).get(today)
            if bar_i is None:
                continue
            bar = bar_index[pos.candidate.stock_code][bar_i]
            pos.bars.append(bar)
            pos.peak_adx = max(pos.peak_adx, bar.adx if not _isnan(bar.adx) else pos.peak_adx)
        remaining = []
        for pos in open_pos:
            if not pos.bars:
                remaining.append(pos); continue
            bar = pos.bars[-1]; bar_n = len(pos.bars) - 1
            ctx = exsim.ExitContext(
                bar_index=bar_n, entry_price=pos.fill_price, high=bar.high, low=bar.low,
                close=bar.close, adx=bar.adx if not _isnan(bar.adx) else 0.0,
                adx_pos=bar.adx_p if not _isnan(bar.adx_p) else 0.0,
                adx_neg=bar.adx_n if not _isnan(bar.adx_n) else 0.0,
                peak_adx=pos.peak_adx, zs_history=pos.candidate.zs_history)
            exit_now, reason = pos._rule.should_exit(ctx)
            force = today >= end_date
            if exit_now or force:
                _close_pos(pos, today, reason if (exit_now and not force) else "end_of_data")
            else:
                remaining.append(pos)
        open_pos = remaining

        # 2. today's candidates
        todays = []
        while cand_idx < n_cands and sorted_cands[cand_idx].entry_date <= today:
            cand = sorted_cands[cand_idx]; cand_idx += 1
            if cand.entry_date == today:
                todays.append(cand)

        for cand in todays:
            cand_high = cand.corr_mode == "high"
            # ── SWAP: if this is a GOOD candidate whose bucket is full, try to evict a
            #    BAD, highly-correlated held position in the same bucket ──
            if swap:
                cq = qual.get((cand.stock_code, cand.entry_date))
                if cq is not None and cq >= med:
                    high_open = sum(1 for p in open_pos if p.candidate.corr_mode == "high")
                    low_open = sum(1 for p in open_pos if p.candidate.corr_mode != "high")
                    full = ((cand_high and high_open >= max_high)
                            or (not cand_high and low_open >= max_low))
                    if full:
                        best, best_c = None, _CORR_GATE
                        for p in open_pos:
                            if (p.candidate.corr_mode == "high") != cand_high:
                                continue
                            if p.candidate.stock_code == cand.stock_code or not p.bars:
                                continue
                            pq = _q(p)
                            if pq is None or pq >= med:   # only evict clearly-bad holds
                                continue
                            cc = corr_fn(cand.stock_code, p.candidate.stock_code, today)
                            if cc is not None and cc >= best_c:
                                best_c, best = cc, p
                        if best is not None:
                            _close_pos(best, today, "swap")
                            open_pos.remove(best)
                            n_swaps += 1

            high_open = sum(1 for p in open_pos if p.candidate.corr_mode == "high")
            low_open = sum(1 for p in open_pos if p.candidate.corr_mode != "high")
            if cand_high and high_open >= max_high:
                continue
            if not cand_high and low_open >= max_low:
                continue
            bar_i = date_to_idx.get(cand.stock_code, {}).get(today)
            bars_c = bar_index.get(cand.stock_code, [])
            if bar_i is None or bar_i + 1 >= len(bars_c):
                continue
            fill_bar = bars_c[bar_i + 1]
            pr = copy.deepcopy(rule); pr.reset()
            pos = exsim._OpenPosition(candidate=cand, fill_price=fill_bar.open,
                                      fill_date=fill_bar.date, bars=[], peak_adx=0.0)
            pos._rule = pr
            open_pos.append(pos)

    for pos in open_pos:
        if pos.bars:
            _close_pos(pos, pos.bars[-1].date, "end_of_data")
    return results, n_swaps


# ─────────────────────── portfolio metrics (cost-aware) ────────────────────────

def _pos_daily(p, dts, cmap, cost):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0 - cost
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0 - cost   # charge cost at exit
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), float((eq / runmax - 1.0).min())


def _fy_daily(results, stock_dts, cal, n_slots, cost):
    cal_set = set(cal)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap, cost).items():
            if d in cal_set:
                day[d] += r / n_slots
    return [day.get(d, 0.0) for d in cal]


# ───────────────────────────────── driver ──────────────────────────────────────

def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    base_fires = defaultdict(list)
    for sg, stk, fa in rows:
        base_fires[stk].append((sg, fa.date() if hasattr(fa, "date") else fa))
    qmap = _load_qmap()

    fys = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                    "classified2016"),
           FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                    "classified2017")] + list(RS_FY_CONFIGS)

    ARMS = ["A", "B", "A7", "B7"]
    SLOTS = {"A": 5, "B": 5, "A7": 6, "B7": 6}      # low slots; +1 high = 6/6/7/7 book
    SWAPON = {"A": False, "B": True, "A7": False, "B7": True}
    # accumulators per cost: daily-return streams concatenated across FYs
    daily = {cost: {a: [[] for _ in range(_K)] for a in ARMS} for cost in _COST_LIST}
    fy_sh = {cost: defaultdict(dict) for cost in _COST_LIST}   # [cost][fy][arm] = sharpe array
    swap_total = {"B": 0, "B7": 0}
    n_filled = {a: 0 for a in ARMS}

    for cfg in fys:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 180)
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
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        cands = _build_cands(base_fires, caches, corr_maps, zs_maps, cfg)
        qual = _candidate_quality(cands, base_fires, stock_dts, qmap)
        known = [v for v in (qual.get((c.stock_code, c.entry_date)) for c in cands)
                 if v is not None]
        med = float(np.median(known)) if known else 0.0

        full_cal = sorted({d for code in caches for d in stock_dts[code][0]})
        ret_arr, date_idx = _build_ret_arrays(caches, full_cal)
        corr_fn = _CorrFn(ret_arr, date_idx)

        per_fy_rets = {cost: {a: [] for a in ARMS} for cost in _COST_LIST}
        for k in range(_K):
            shuf = cands[:]; random.Random(k).shuffle(shuf)
            for a in ARMS:
                res, ns = run_swap_simulation(
                    shuf, cbt._EXIT_RULE, caches, cfg.end, swap=SWAPON[a], qual=qual,
                    med=med, corr_fn=corr_fn, max_low=SLOTS[a])
                if k == 0:
                    n_filled[a] += len(res)
                    if a in swap_total:
                        swap_total[a] += ns
                n_slots = SLOTS[a] + 1
                for cost in _COST_LIST:
                    dr = _fy_daily(res, stock_dts, cal, n_slots, cost)
                    daily[cost][a][k] += dr[1:]
                    per_fy_rets[cost][a].append(dr)
        for cost in _COST_LIST:
            for a in ARMS:
                fy_sh[cost][cfg.label][a] = np.array([_sharpe(r) for r in per_fy_rets[cost][a]])
        logger.info("  {} done ({} cands, med q={:.4f}, swaps B={} B7={})",
                    cfg.label, len(cands), med, swap_total["B"], swap_total["B7"])

    # ── report per cost ──
    for cost in _COST_LIST:
        print("\n" + "=" * 92)
        print(f"SWAP / MID-TRADE EVICTION vs FILL-ORDER NULL — K={_K} paired, "
              f"cost={cost*1e4:.0f}bps round-trip")
        print("=" * 92)
        sh = {a: np.array([_sharpe(daily[cost][a][k]) for k in range(_K)]) for a in ARMS}
        rt = {a: np.array([_metrics(daily[cost][a][k])[0] for k in range(_K)]) for a in ARMS}
        dd = {a: np.array([_metrics(daily[cost][a][k])[1] for k in range(_K)]) for a in ARMS}
        names = {"A": "6-slot no-swap", "B": "6-slot SWAP",
                 "A7": "7-slot no-swap", "B7": "7-slot SWAP"}
        print(f"\n{'arm':<18}{'Sharpe':>9}{'sd':>7}{'p5':>7}{'p50':>7}{'p95':>7}"
              f"{'ret':>9}{'DD':>8}")
        for a in ARMS:
            print(f"{names[a]:<18}{sh[a].mean():>9.2f}{sh[a].std():>7.2f}"
                  f"{np.percentile(sh[a],5):>7.2f}{np.percentile(sh[a],50):>7.2f}"
                  f"{np.percentile(sh[a],95):>7.2f}{rt[a].mean()*100:>8.0f}%{dd[a].mean()*100:>7.0f}%")

        def _paired(x, y, label):
            d = sh[x] - sh[y]
            p = float((d > 0).mean())
            lo, hi = float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))
            drt = (rt[x] - rt[y]).mean() * 100
            gate = p >= 0.95 and lo > 0
            print(f"\n[{label}]  Δ Sharpe {d.mean():+.3f} | sd {d.std():.3f} | "
                  f"95% CI [{lo:+.3f},{hi:+.3f}] | P(Δ>0)={p:.3f} | Δret {drt:+.0f}pp"
                  f"  → {'PASS' if gate else 'FAIL'}")

        _paired("B", "A", "SWAP effect, capital fixed:  6-slot SWAP − 6-slot no-swap  (BINDING)")
        _paired("A7", "A", "SLOT exposure lever:        7-slot no-swap − 6-slot no-swap")
        _paired("B7", "A7", "SWAP effect under +1 slot:  7-slot SWAP − 7-slot no-swap")
        _paired("B7", "A", "combined (swap+slot) vs canonical book")

        # per-FY for the binding B−A
        print("\n  PER-FY Δ Sharpe (6-slot SWAP − 6-slot no-swap):")
        pos = tot = 0
        for cfg in fys:
            if cfg.label not in fy_sh[cost]:
                continue
            d = fy_sh[cost][cfg.label]["B"] - fy_sh[cost][cfg.label]["A"]
            d = d[~np.isnan(d)]
            if not len(d):
                continue
            tot += 1; pos += d.mean() > 0
            tag = "  ← OOS" if cfg.label == "FY2025" else ""
            print(f"    {cfg.label}  Δ {d.mean():+.3f}  P(Δ>0)={(d>0).mean():.2f}{tag}")
        print(f"    ({pos}/{tot} FYs Δ>0)")

    print("\n" + "-" * 92)
    print(f"SWAP ACTIVITY (seed-0): total swaps  B(6-slot)={swap_total['B']}  "
          f"B7(7-slot)={swap_total['B7']}   |  filled trades " +
          "  ".join(f"{a}={n_filled[a]}" for a in ARMS))
    print("  If swaps ≈ 0 the mechanism is inert (thin) — that alone answers the question.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
