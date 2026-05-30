"""Stage 0 — does "N days under the kumo before the breakout" improve brk_kumo_hi?

Operator (2026-05-30): brk_kumo_hi fires on a 1-bar transition (yesterday low <= cloud
top, today low > cloud top). Many fires are a price that only briefly poked under the
cloud and popped back (the screenshot). Hypothesis: require the price to have been
genuinely UNDER the cloud for N+ days before the upside break → fewer, higher-quality
fires, easing the 6-slot glut and lifting EV.

The detector already exposes this as `gate_lookback=K` (K consecutive prior bars with
low <= cloud TOP). This Stage 0 measures, for every K=1 fire, the run-length of prior
days under the cloud as a CONTINUOUS feature and buckets forward outcomes by it. Two
definitions:
  days_under_top : consecutive prior bars with low  <= kumo_top  (= gate_lookback)
  days_under_bot : consecutive prior bars with high <= kumo_bot  (entire bar BELOW the
                   whole cloud — the stricter "genuinely under the cloud" of the picture)

Forward outcome = production-consistent entry (T+1 open) held H=20 bars (exit close).
Monotone-up DR/mean across buckets ⇒ real gate (proceed to confluence A/B). Flat ⇒
no-op (trend_score-floor pattern). Pooled AND FY2025-OOS reported (walk-forward gate).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.brk_kumo_days_under_stage0
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.simulator.cache import DataCache

_TENKAN_P, _KIJUN_P, _SENKOU_B_P, _DISPLACE = 9, 26, 52, 26
_H = 20   # forward-exit horizon (bars after T+1 open)


def _stock_levels(cache):
    """Return (dates, open, low, kumo_top, kumo_bot) deduped by date."""
    seen, dts, o, hi, lo, cl = set(), [], [], [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            if b.high > hi[-1]:
                hi[-1] = b.high
            if b.low < lo[-1]:
                lo[-1] = b.low
            cl[-1] = b.close
            continue
        seen.add(d); dts.append(d)
        o.append(b.open); hi.append(b.high); lo.append(b.low); cl.append(b.close)
    n = len(dts)
    if n < _SENKOU_B_P + _DISPLACE + 2:
        return None
    ichi = calc_ichimoku(hi, lo, cl, tenkan_period=_TENKAN_P, kijun_period=_KIJUN_P,
                         senkou_b_period=_SENKOU_B_P, displacement=_DISPLACE)
    ssa = np.array(ichi["senkou_a"], float); ssb = np.array(ichi["senkou_b"], float)
    d = int(ichi["displacement"])
    top = np.full(n, np.nan); bot = np.full(n, np.nan)
    top[d:] = np.maximum(ssa[: n - d], ssb[: n - d])
    bot[d:] = np.minimum(ssa[: n - d], ssb[: n - d])
    return dts, np.array(o), np.array(hi), np.array(lo), top, bot


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    # rows: (fy, days_under_top, days_under_bot, fwd_ret)
    rows: list[tuple[str, int, int, float]] = []

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=200)   # ichimoku warmup + lookback
        se = cfg.end + datetime.timedelta(days=90)
        with get_session() as s:
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        for code, c in caches.items():
            lv = _stock_levels(c)
            if lv is None:
                continue
            dts, o, hi, lo, top, bot = lv
            n = len(dts)
            for T in range(_DISPLACE + 1, n):
                if np.isnan(top[T]) or np.isnan(top[T - 1]) or top[T] <= 0:
                    continue
                # K=1 brk_kumo_hi transition: low[T] > top[T], low[T-1] <= top[T-1]
                if not (lo[T] > top[T] and lo[T - 1] <= top[T - 1]):
                    continue
                if not (cfg.start <= dts[T] <= cfg.end):
                    continue
                if T + 1 >= n:
                    continue
                # run-length of prior days under cloud top / under cloud bottom
                du_top = 0
                for i in range(1, T + 1):
                    if np.isnan(top[T - i]) or top[T - i] <= 0 or not (lo[T - i] <= top[T - i]):
                        break
                    du_top += 1
                du_bot = 0
                for i in range(1, T + 1):
                    if np.isnan(bot[T - i]) or bot[T - i] <= 0 or not (hi[T - i] <= bot[T - i]):
                        break
                    du_bot += 1
                exit_i = min(T + 1 + _H, n - 1)
                entry = o[T + 1]
                if not entry:
                    continue
                fwd = float(_close_at(c, dts[exit_i]) / entry - 1.0)
                rows.append((cfg.label, du_top, du_bot, fwd))
        logger.info("  {} done ({} fires so far)", cfg.label, len(rows))

    _report("days_under_top (low<=cloud TOP)", rows, idx=1)
    _report("days_under_bot (high<=cloud BOTTOM, fully under)", rows, idx=2)


def _close_at(cache, d):
    for b in cache.bars:
        if b.dt.date() == d:
            return b.close
    return cache.bars[-1].close


def _bucketed(rows, idx, edges):
    out = defaultdict(list)
    for r in rows:
        v = r[idx]
        lab = next((lab for lo, hi, lab in edges if lo <= v <= hi), None)
        if lab:
            out[lab].append(r[3])
    return out


def _report(title, rows, idx):
    edges = [(0, 0, "0"), (1, 1, "1"), (2, 3, "2-3"), (4, 7, "4-7"), (8, 10_000, "8+")]
    print("\n" + "=" * 78)
    print(f"brk_kumo_hi forward +{_H}-bar return by {title}")
    print("=" * 78)
    print(f"{'bucket':<8}{'n':>7}{'DR(>0)':>9}{'mean ret':>11}  || FY2025 OOS  {'n':>5}{'DR':>7}{'mean':>9}")
    pooled = _bucketed(rows, idx, edges)
    oos = _bucketed([r for r in rows if r[0] == "FY2025"], idx, edges)
    for _, _, lab in edges:
        p = pooled.get(lab, []); q = oos.get(lab, [])
        if not p:
            continue
        dr = sum(1 for x in p if x > 0) / len(p)
        mr = float(np.mean(p))
        qs = (f"{len(q):>5}{sum(1 for x in q if x>0)/len(q)*100:>6.0f}%{np.mean(q)*100:>+8.2f}%"
              if q else f"{len(q):>5}{'—':>7}{'—':>9}")
        print(f"{lab:<8}{len(p):>7}{dr*100:>8.0f}%{mr*100:>+10.2f}%  ||            {qs}")
    allp = [r[3] for r in rows]
    print(f"{'ALL':<8}{len(allp):>7}{sum(1 for x in allp if x>0)/len(allp)*100:>8.0f}%"
          f"{np.mean(allp)*100:>+10.2f}%")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
