"""Stage-0 follow-up — hunt for a NON-FADING sub-cohort of the low-price vol-spike event.

The headline event (cheap <¥1000 + abnormal volume + up-day) FADES, monotone in spike/up
size (see project_lowprice_volspike_stage0_reject). Operator (2026-06-15): before deciding
on a UI, check whether any structural slice CONTINUES up instead.

Base event (loosest, largest n): close<¥1000, vmult=vol/median(60)>=3, ret1>=3%, turnover>=¥30M.
Within that population, bucket along candidate axes and ask: is any bucket's forward return
POSITIVE in EXCESS of the SAME-bucket low-price baseline (beta-stripped, per house rule —
residualize vs universe baseline, not the global mean), AND does the sign hold across FYs?

Axes:
  up      ret1 magnitude buckets               (mild accumulation vs blow-off chase)
  cloc    close location in bar (c-l)/(h-l)    (closes-strong vs distribution/reversal bar)
  gap     gap share = (open/prevclose-1)/ret1  (gap-and-go vs intraday chase)
  ext     close/SMA25-1                         (fresh-from-base vs already-extended)
  base60  close > prior 60d high ?              (genuine breakout vs pop inside range)
  mom20   close[T-1]/close[T-21]-1              (reversal-volume vs uptrend-continuation)
  vmult   spike multiple buckets               (re-confirm dose-response)

Outcome = enter open[T+1], hold h, exit close[T+h], winsor ±60%. Read-only. Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.lowprice_volspike_subcohort
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session
from src.analysis.lowprice_volspike_stage0 import (
    PRICE_MAX, V_LOOKBACK, TURN_MIN, HORIZONS, WINSOR, _codes, _load_one, _fy,
)

K_BASE = 3.0
UP_BASE = 0.03
H_FY = 10  # horizon for per-FY robustness


def _winsor(a):
    return np.clip(a, -WINSOR, WINSOR)


def _bucket_up(r):
    if r < 0.05: return "1:3-5%"
    if r < 0.10: return "2:5-10%"
    if r < 0.20: return "3:10-20%"
    return "4:>20%"


def _bucket_cloc(x):
    if not np.isfinite(x): return None
    if x < 0.33: return "1:near-low"
    if x < 0.67: return "2:mid"
    return "3:near-high"


def _bucket_gap(x):
    if not np.isfinite(x): return None
    if x < 0.25: return "1:intraday"
    if x < 0.75: return "2:mixed"
    return "3:gap"


def _bucket_ext(x):
    if not np.isfinite(x): return None
    if x < 0.0: return "1:below-sma"
    if x < 0.10: return "2:0-10%"
    if x < 0.25: return "3:10-25%"
    return "4:>25%"


def _bucket_mom(x):
    if not np.isfinite(x): return None
    if x < -0.10: return "1:dn>10%"
    if x < 0.0: return "2:dn0-10%"
    if x < 0.10: return "3:up0-10%"
    return "4:up>10%"


def _bucket_vmult(x):
    if x < 5: return "1:3-5x"
    if x < 10: return "2:5-10x"
    if x < 20: return "3:10-20x"
    return "4:>20x"


AXES = {
    "up": _bucket_up, "cloc": _bucket_cloc, "gap": _bucket_gap,
    "ext": _bucket_ext, "base60": None, "mom20": _bucket_mom,
    "vmult": _bucket_vmult,
}


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    # acc[axis][bucket][h] -> [n, sum, npos]  for baseline & event
    base = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0])))
    evt = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0])))
    # event per-FY (h=H_FY): evtfy[axis][bucket][fy] -> [n, sum]
    evtfy = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0.0])))
    min_len = V_LOOKBACK + max(HORIZONS) + 2

    with get_session() as s:
        for ni, code in enumerate(codes):
            if ni % 300 == 0:
                logger.info("  {}/{}", ni, len(codes))
            sub = _load_one(s, code)
            if len(sub) < min_len:
                continue
            c = sub["close"].to_numpy(); v = sub["vol"].to_numpy()
            o = sub["open"].to_numpy(); h_ = sub["high"].to_numpy()
            lo = sub["low"].to_numpy()
            ret1 = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            med = sub["vol"].rolling(V_LOOKBACK).median().shift(1).to_numpy()
            vmult = v / np.where(med > 0, med, np.nan)
            turn = c * v
            sma25 = sub["close"].rolling(25).mean().shift(1).to_numpy()
            hi60 = sub["high"].rolling(60).max().shift(1).to_numpy()
            prevc = np.concatenate([[np.nan], c[:-1]])
            cloc = np.where(h_ > lo, (c - lo) / (h_ - lo), np.nan)
            gap = np.where(ret1 != 0, (o / prevc - 1.0) / ret1, np.nan)
            ext = c / sma25 - 1.0
            mom20 = np.concatenate([[np.nan] * 21, c[21:] / c[:-21] - 1.0])
            fwd = {}
            entry = np.concatenate([o[1:], [np.nan]])
            for h in HORIZONS:
                exitc = np.concatenate([c[h:], [np.nan] * h])
                fwd[h] = _winsor(exitc / entry - 1.0)
            dts = sub["date"]

            lowmask = (c < PRICE_MAX) & np.isfinite(c)
            evmask = lowmask & (vmult >= K_BASE) & (ret1 >= UP_BASE) & (turn >= TURN_MIN)

            idxs = np.where(lowmask)[0]
            for i in idxs:
                labels = {
                    "up": _bucket_up(ret1[i]) if np.isfinite(ret1[i]) else None,
                    "cloc": _bucket_cloc(cloc[i]),
                    "gap": _bucket_gap(gap[i]),
                    "ext": _bucket_ext(ext[i]),
                    "base60": ("1:breakout" if np.isfinite(hi60[i]) and c[i] > hi60[i]
                               else "0:in-range" if np.isfinite(hi60[i]) else None),
                    "mom20": _bucket_mom(mom20[i]),
                    "vmult": _bucket_vmult(vmult[i]) if np.isfinite(vmult[i]) else None,
                }
                is_ev = bool(evmask[i])
                fy = _fy(dts.iloc[i])
                for ax, lab in labels.items():
                    if lab is None:
                        continue
                    for h in HORIZONS:
                        fv = fwd[h][i]
                        if not np.isfinite(fv):
                            continue
                        b = base[ax][lab][h]
                        b[0] += 1; b[1] += fv; b[2] += int(fv > 0)
                        if is_ev:
                            e = evt[ax][lab][h]
                            e[0] += 1; e[1] += fv; e[2] += int(fv > 0)
                    if is_ev:
                        fv = fwd[H_FY][i]
                        if np.isfinite(fv):
                            ef = evtfy[ax][lab][fy]
                            ef[0] += 1; ef[1] += fv

    # ---- report -----------------------------------------------------------
    def m(acc):  # mean% / DR%
        n, sm, npos = acc
        return (sm / n * 100 if n else float("nan"),
                npos / n * 100 if n else float("nan"))

    for ax in AXES:
        print(f"\n=== AXIS: {ax}  (event vs SAME-bucket low-price baseline) ===")
        print(f"{'bucket':>12} {'n_ev':>7} | "
              f"{'ev_h10':>7} {'base_h10':>8} {'exc_h10':>8} {'exc_h20':>8} "
              f"{'ev_DR10':>7} {'exc_DR':>7} | FY+ (h10)")
        for lab in sorted(evt[ax]):
            ne = evt[ax][lab][H_FY][0]
            if ne < 50:
                continue
            ev10, evdr = m(evt[ax][lab][H_FY])
            bs10, bsdr = m(base[ax][lab][H_FY])
            ev20, _ = m(evt[ax][lab][20])
            bs20, _ = m(base[ax][lab][20])
            # per-FY sign consistency (FYs with n>=40)
            pos = tot = 0
            for fy, (fn, fsm) in evtfy[ax][lab].items():
                if fn >= 40:
                    tot += 1
                    if fsm / fn > bs10 / 100:  # beat baseline that FY
                        pos += 1
            print(f"{lab:>12} {ne:>7} | {ev10:>7.2f} {bs10:>8.2f} "
                  f"{ev10-bs10:>8.2f} {ev20-bs20:>8.2f} {evdr:>7.1f} "
                  f"{evdr-bsdr:>7.1f} | {pos}/{tot}")


if __name__ == "__main__":
    main()
