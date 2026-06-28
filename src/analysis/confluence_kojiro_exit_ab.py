"""Confluence exit-rule A/B: Kojiro ATR-stop + half-rise trail vs ZsTpSl (control).

Tests the exit rule from docs/books/kojiro.md (小次郎講師 真・トレーダーズバイブル, Turtle
chassis): NO fixed take-profit; an initial stop at k·ATR ("k·N", book range 2.0-3.0N,
default ~2.5N) placed at entry, then a TRAILING stop that on each new high rises by HALF
the price advance (the author's signature variant, p.62-64/p.94):

    stop_0 = entry - k * ATR_entry
    on new high H>prev_high:  stop += 0.5 * (H - prev_high)   # half-rise (kj_half)
                              stop += 1.0 * (H - prev_high)   # full-rise comparator (kj_full)
    exit when low <= stop

This is the same class as the repo's `atr_trail`/`adx_trail` (a volatility trailing stop).
PRIOR IS LOW: every exit A/B at ~36 trades/yr has washed out at the paired fill-order null
— adx_d8 (the benchmark BEST) coin-flipped vs ZsTpSl (Δ Sharpe +0.021, P=0.535), time40,
asym, peak-anchored and milestone-trail all REJECT, and the FY2020-24 bootstrap found NO
significant separation among top exit rules (project_confluence_exit_ab_reject,
project_timestop40_bootstrap_reject, src/exit/benchmark.md). So the binding test is the
SAME harness: Part 1 per-trade quality on identical entries; Part 2a deterministic per-FY
Sharpe (bull/bear + FY2025 OOS); Part 2b paired fill-order null on the 6-slot book.

Gate (pre-registered, same as the adx_d8 A/B): a Kojiro arm CERTIFIES only if part-1 'all'
mean_r ≥ control AND paired Δ Sharpe P(Δ>0) ≥ 0.90 AND 95% CI lower > 0 AND FY2025 OOS Δ > 0.
lean-yes (operator-call) = P ≥ 0.75 with CI grazing 0. Anything else = NOT separated → keep
ZsTpSl. A bull/bear per-FY SIGN-FLIP is the non-stationarity tell that sank timestop40/adx_d8.

CAVEAT (documented): ATR here is TRADE-LOCAL Wilder (seeded from the entry bar's TR and
updated bar-by-bar over the trade), NOT a pre-entry ATR(20) snapshot — this is the exact
modeling choice the repo's existing `AtrTrail` makes (it has no access to pre-entry bars in
the exit context). It makes the initial stop modestly tighter than a true ATR(20)-at-entry
stop; the k∈{2.5,3.0} sweep brackets that. If a Kojiro arm were to PASS, the precise
entry-ATR(20) implementation would be built and re-confirmed before believing it.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_kojiro_exit_ab
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
import src.exit.exit_simulator as exsim
from src.analysis.exit_benchmark import FyConfig, _add_adx
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.adx_trail import AdxTrail
from src.exit.base import ExitContext, ExitRule
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache


# ---------------------------------------------------------------------------
# Kojiro ATR-stop + half/full-rise trailing exit (research arm; not yet in src/exit)
# ---------------------------------------------------------------------------
class KojiroTrail(ExitRule):
    """Initial k·ATR stop + rise-fraction trailing stop (no take-profit).

    Args:
        k:            Initial stop distance = k × ATR (book "k·N", 2.0-3.0).
        rise_frac:    Stop raised by rise_frac × (new_high − prev_high) on each new
                      high. 0.5 = book's half-rise (default); 1.0 = full-rise (Turtle).
        atr_period:   Wilder ATR period (book uses 20). Trade-local (see module caveat).
        max_bars:     Hard time-stop safety net (book has none; bound the tail).
    """

    def __init__(self, k: float = 2.5, rise_frac: float = 0.5,
                 atr_period: int = 20, max_bars: int = 60) -> None:
        self._k = k
        self._rise = rise_frac
        self._period = atr_period
        self._alpha = 1.0 / atr_period
        self._max = max_bars
        self.reset()

    @property
    def name(self) -> str:
        return f"kojiro_k{self._k}_r{self._rise}"

    def reset(self) -> None:
        self._prev_close: float | None = None
        self._atr: float | None = None
        self._running_high: float | None = None
        self._stop: float | None = None

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        # True Range (entry bar has no prev close → use bar range)
        if self._prev_close is None:
            tr = ctx.high - ctx.low
        else:
            tr = max(ctx.high - ctx.low, abs(ctx.high - self._prev_close),
                     abs(ctx.low - self._prev_close))
        self._prev_close = ctx.close
        # Wilder ATR seeded from the entry bar, updated each bar (trade-local)
        self._atr = tr if self._atr is None else self._alpha * tr + (1 - self._alpha) * self._atr

        # Initialise stop + running high on the entry bar
        if self._stop is None:
            self._stop = ctx.entry_price - self._k * self._atr
            self._running_high = ctx.high
        # Half/full-rise trail: raise stop by rise_frac of each new-high advance
        elif ctx.high > self._running_high:
            self._stop += self._rise * (ctx.high - self._running_high)
            self._running_high = ctx.high

        if ctx.bar_index >= self._max:
            return True, "time"
        if ctx.low <= self._stop:
            return True, "kojiro_trail"
        return False, ""


_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 6                     # production 6-slot book (1 high + 5 low)
_K = 150                       # shuffles (4 arms × 150 × 9 FY ≈ original 3×200×9 cost)
_CTRL = "zs"
_ARMS = {
    "zs":        ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3),          # CONTROL
    "adx_d8":    AdxTrail(drop_threshold=8.0, min_bars=5, max_bars=40),  # reference coin-flip anchor
    "kj_h2.5":   KojiroTrail(k=2.5, rise_frac=0.5),                    # book half-rise 2.5N
    "kj_h3.0":   KojiroTrail(k=3.0, rise_frac=0.5),                    # book half-rise 3.0N
}
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)
_BULL_FYS = {"FY2020", "FY2023", "FY2025"}


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


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(cands, rule, caches, cfg, stock_dts, cal):
    cal_set = set(cal)
    results = run_simulation(cands, rule, caches, cfg.end)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r / _SLOTS
    return [day.get(d, 0.0) for d in cal]


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

    eq = {a: defaultdict(list) for a in _ARMS}      # arm -> corr_mode -> [returns]
    hold = {a: [] for a in _ARMS}                    # arm -> [hold_bars]
    per_fy = {a: {} for a in _ARMS}
    st = {a: [[] for _ in range(_K)] for a in _ARMS}

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
                    _add_adx(c)   # populate ADX14 for the adx_d8 reference arm
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        # PART 1 — no cap: every candidate fills; per-trade return deterministic
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10 ** 9, 10 ** 9
        for a, rule in _ARMS.items():
            for p in run_simulation(cands, rule, caches, cfg.end):
                eq[a][p.corr_mode].append(p.return_pct)
                eq[a]["all"].append(p.return_pct)
                hold[a].append(p.hold_bars)
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 1, 5   # restore production 6-slot

        # PART 2a — deterministic 6-slot per-FY Sharpe
        base = sorted(cands, key=lambda c: c.entry_date)
        for a, rule in _ARMS.items():
            per_fy[a][cfg.label] = _sharpe(_fy_returns(base, rule, caches, cfg, stock_dts, cal)[1:])

        # PART 2b — paired fill-order null: SAME shuffled order to all arms
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            for a, rule in _ARMS.items():
                st[a][k] += _fy_returns(pool, rule, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} candidates)", cfg.label, len(cands))

    # ── PART 1 ──
    print("\n" + "=" * 86)
    print("PART 1 — exit quality on IDENTICAL entries (no cap, per-trade), by corr_mode")
    print("=" * 86)
    print(f"\n{'arm':<9}{'n':>7}{'mean_r%':>9}{'DR%':>7}{'hold':>7}   "
          f"{'hi mean_r%':>11}{'hi Δ vs zs':>11}{'lo mean_r%':>11}")
    for a in _ARMS:
        allr = np.array(eq[a]["all"]); hir = np.array(eq[a].get("high", []))
        lor = np.array(eq[a].get("low", []) + eq[a].get("mid", []))
        hi_d = (hir.mean() - np.array(eq[_CTRL].get("high", [0])).mean()) if hir.size else float("nan")
        mh = float(np.mean(hold[a])) if hold[a] else float("nan")
        print(f"{a:<9}{allr.size:>7}{allr.mean()*100:>9.2f}{(allr>0).mean()*100:>7.1f}{mh:>7.1f}   "
              f"{(hir.mean()*100 if hir.size else float('nan')):>11.2f}{hi_d*100:>11.2f}"
              f"{(lor.mean()*100 if lor.size else float('nan')):>11.2f}")
    print("  (binding: a Kojiro arm's 'all' mean_r should be ≥ zs; watch hold = occupancy "
          "confound; hi Δ<0 = high-corr per-trade flip)")

    # ── PART 2a ──
    print("\n" + "=" * 86)
    print("PART 2a — deterministic 6-slot per-FY Sharpe (Δ = arm − zs)")
    print("=" * 86)
    print(f"\n{'FY':<9}" + "".join(f"{a:>9}" for a in _ARMS)
          + "".join(f"{'Δ'+a:>9}" for a in _ARMS if a != _CTRL))
    bull_d, bear_d = defaultdict(list), defaultdict(list)
    for cfg in _FYS:
        if cfg.label not in per_fy[_CTRL]:
            continue
        row = f"{cfg.label:<9}" + "".join(f"{per_fy[a][cfg.label]:>9.2f}" for a in _ARMS)
        for a in _ARMS:
            if a == _CTRL:
                continue
            dlt = per_fy[a][cfg.label] - per_fy[_CTRL][cfg.label]
            row += f"{dlt:>9.2f}"
            (bull_d if cfg.label in _BULL_FYS else bear_d)[a].append(dlt)
        if cfg.label == "FY2025":
            row += "  OOS"
        print(row)
    for a in _ARMS:
        if a == _CTRL:
            continue
        oos = per_fy[a]["FY2025"] - per_fy[_CTRL]["FY2025"]
        print(f"  {a}: FY2025 OOS Δ {oos:+.2f} | bull-mean Δ "
              f"{np.mean(bull_d[a]):+.2f} | bear-mean Δ {np.mean(bear_d[a]):+.2f}"
              f"  {'(sign-flip!)' if np.mean(bull_d[a])*np.mean(bear_d[a])<0 else ''}")

    # ── PART 2b ──
    print("\n" + "=" * 86)
    print(f"PART 2b — paired fill-order null, {_K} shuffles (6-slot book)")
    print("=" * 86)
    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in _ARMS}
    print(f"\n{'arm':<9}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}")
    for a in _ARMS:
        s_ = sh[a]
        print(f"{a:<9}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}")
    for a in _ARMS:
        if a == _CTRL:
            continue
        d = sh[a] - sh[_CTRL]
        p = (d > 0).mean()
        lo, hi = np.percentile(d, [2.5, 97.5])
        cert = p >= 0.90 and lo > 0
        lean = (not cert) and p >= 0.75
        print(f"\n[paired Δ Sharpe {a} − zs]  mean {d.mean():+.3f} | 95% CI [{lo:+.3f}, {hi:+.3f}]"
              f" | P(Δ>0)={p:.3f}")
        print(f"  VERDICT({a}): "
              + ("CERTIFIED" if cert else "lean-yes / operator-call" if lean
                 else "NOT separated — keep ZsTpSl"))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
