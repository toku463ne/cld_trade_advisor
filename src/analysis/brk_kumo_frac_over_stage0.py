"""Stage 0 — reject brk_kumo_hi when price was already over the kumo for n% of last 60d?

Operator (2026-05-30): brk_kumo_hi should mark a FRESH stage change from below to above
the cloud. If the stock has already spent most of the last 60 days above the kumo, the
"breakout" is chop on an already-above-cloud chart, not a transition → reject the sign.

Feature per K=1 fire = fraction of the prior 60 trading days (T-60..T-1) the price was
above the cloud TOP. Two definitions:
  frac_close_over : close > kumo_top   (price above the cloud)
  frac_low_over   : low   > kumo_top   (entire bar above the cloud, strict)

Hypothesis: HIGH frac → already-established above-cloud → WORSE forward return → reject.
Bucket forward +20-bar return (entry T+1 open, exit close) by the fraction. Pooled +
FY2025 OOS (walk-forward gate). Monotone-DOWN ⇒ a real reject gate; flat ⇒ no-op.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.brk_kumo_frac_over_stage0
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.brk_kumo_days_under_stage0 import _close_at, _stock_levels
from src.analysis.confluence_benchmark import _FYS
from src.data.db import get_session
from src.simulator.cache import DataCache

_H = 20      # forward-exit horizon (bars after T+1 open)
_LOOK = 60   # context window (trading days)
_DISPLACE = 26


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    # rows: (fy, frac_close_over, frac_low_over, fwd_ret)
    rows: list[tuple[str, float, float, float]] = []

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=260)   # ichimoku warmup + 60d context
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
            cmap: dict = {}
            for b in c.bars:
                cmap[b.dt.date()] = b.close          # last close per date
            cl = np.array([cmap[d] for d in dts])
            n = len(dts)
            for T in range(_DISPLACE + _LOOK + 1, n):
                if np.isnan(top[T]) or np.isnan(top[T - 1]) or top[T] <= 0:
                    continue
                if not (lo[T] > top[T] and lo[T - 1] <= top[T - 1]):   # K=1 transition
                    continue
                if not (cfg.start <= dts[T] <= cfg.end) or T + 1 >= n:
                    continue
                win = range(T - _LOOK, T)               # prior 60 days, excl. fire day
                valid = [i for i in win if not np.isnan(top[i]) and top[i] > 0]
                if len(valid) < _LOOK // 2:
                    continue
                fc = float(np.mean([cl[i] > top[i] for i in valid]))
                fl = float(np.mean([lo[i] > top[i] for i in valid]))
                exit_i = min(T + 1 + _H, n - 1)
                entry = o[T + 1]
                if not entry:
                    continue
                fwd = float(_close_at(c, dts[exit_i]) / entry - 1.0)
                rows.append((cfg.label, fc, fl, fwd))
        logger.info("  {} done ({} fires so far)", cfg.label, len(rows))

    _report("frac of prior 60d with CLOSE over kumo top", rows, idx=1)
    _report("frac of prior 60d with LOW over kumo top (strict)", rows, idx=2)


def _bucketed(rows, idx, edges):
    out = defaultdict(list)
    for r in rows:
        v = r[idx]
        lab = next((lab for lo, hi, lab in edges if lo <= v <= hi), None)
        if lab:
            out[lab].append(r[3])
    return out


def _report(title, rows, idx):
    edges = [(0.0, 0.2, "0-20%"), (0.2, 0.4, "20-40%"), (0.4, 0.6, "40-60%"),
             (0.6, 0.8, "60-80%"), (0.8, 1.0001, "80-100%")]
    print("\n" + "=" * 80)
    print(f"brk_kumo_hi forward +{_H}-bar return by {title}")
    print("=" * 80)
    print(f"{'bucket':<10}{'n':>7}{'DR(>0)':>9}{'mean ret':>11}  || FY2025 OOS  {'n':>5}{'DR':>7}{'mean':>9}")
    pooled = _bucketed(rows, idx, edges)
    oos = _bucketed([r for r in rows if r[0] == "FY2025"], idx, edges)
    for _, _, lab in edges:
        p = pooled.get(lab, []); q = oos.get(lab, [])
        if not p:
            continue
        dr = sum(1 for x in p if x > 0) / len(p)
        qs = (f"{len(q):>5}{sum(1 for x in q if x>0)/len(q)*100:>6.0f}%{np.mean(q)*100:>+8.2f}%"
              if q else f"{len(q):>5}{'—':>7}{'—':>9}")
        print(f"{lab:<10}{len(p):>7}{dr*100:>8.0f}%{np.mean(p)*100:>+10.2f}%  ||            {qs}")
    allp = [r[3] for r in rows]
    print(f"{'ALL':<10}{len(allp):>7}{sum(1 for x in allp if x>0)/len(allp)*100:>8.0f}%"
          f"{np.mean(allp)*100:>+10.2f}%")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
