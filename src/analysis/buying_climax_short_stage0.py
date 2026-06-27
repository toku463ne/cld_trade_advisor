"""Stage 0 — VPA buying-climax / topping-out SHORT event study (the short side).

The dekidaka arc established that on daily JP a loud up-thrust on high volume = EXHAUSTION
(lowprice_volspike inverted, vol_breakout_confirm inverted, VAP node support inverted). On
the LONG side that sign is fatal; on the SHORT side it is the RIGHT sign. This tests the
canonical VPA short: a BUYING CLIMAX / topping-out bar — extended uptrend, AT a recent high,
climactic high volume, but a WEAK close (upper wick, close in lower half = effort with no
result / distribution) — as a SHORT signal.

Two priors built in:
  * Shorts fight positive drift → the cohort must be ABSOLUTELY negative forward (not just
    below baseline), net of a borrow assumption, and FY-robust (a short dies in bull years).
  * The factor short sleeve closed on BORROWABILITY (alpha anti-located with borrowable
    large-caps). So the key test is a TURNOVER-TIER split: does the short signal survive in
    the LIQUID / borrowable tier, or only in (unborrowable) illiquid names?

Event (bar T), pre-registered:
  * extended up + at a high : close[T] > SMA(20) AND close[T] >= NEAR*max(high[T-NHIGH:T+1])
  * climactic volume        : vmult[T] = vol[T]/SMA(vol,20) >= K            (K swept)
  * up bar                  : close[T] > open[T]
  * weak close (topping)    : close_pos[T] = (close-low)/(high-low) <= CP   (CP swept; upper wick)
  * borrowable proxy        : avg turnover close*SMA(vol) >= TURN_MIN       (tier swept)

Outcome (two-bar): SHORT at open[T+1], cover at close[T+h].  short_ret = -(close[T+h]/open[T+1]-1).
Forward long returns winsorized +/-WINSOR; short stats derived from the negated array.
Baseline/null = ALL uptrend-at-high bars (any vol/close) — the natural drift you short against.
Monotonicity by vmult quartile (louder = more negative?) and a close_pos split.  Per-FY h10.
Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.buying_climax_short_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

SMA_N = 20
NHIGH = 20
NEAR = 0.97
V_LOOKBACK = 20
K_SWEEP = [2.0, 3.0]
CP_SWEEP = [0.5, 0.4]
TURN_TIERS = [30_000_000.0, 100_000_000.0]     # ¥ avg turnover; 100M+ = borrowable proxy
HORIZONS = [5, 10, 20]
WINSOR = 0.60
COOLDOWN = 20
BORROW_BP_PER_BAR = 0.0                          # set >0 to net a borrow cost per bar
_FY_START_MONTH = 4


def _codes():
    with get_session() as s:
        rows = s.execute(text(
            "SELECT DISTINCT stock_code FROM ohlcv_1d ORDER BY stock_code")).all()
    return [r[0] for r in rows if not r[0].startswith("^")]


def _load_one(s, code):
    rows = s.execute(text(
        "SELECT ts, open_price::float8, high_price::float8, low_price::float8, "
        "close_price::float8, volume::float8 FROM ohlcv_1d "
        "WHERE stock_code=:c ORDER BY ts"), {"c": code}).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df["date"] = pd.to_datetime(df["ts"]).dt.tz_localize(None).dt.normalize()
    g = df.groupby("date", sort=True)
    return g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                 close=("close", "last"), vol=("vol", "sum")).reset_index()


def _fy(d):
    return d.year if d.month >= _FY_START_MONTH else d.year - 1


def _w(a):
    return np.clip(a, -WINSOR, WINSOR)


def _short(longfwd):
    """Short stats from a long-forward array: mean%/median%/win%(=P(long<0))."""
    a = -_w(longfwd[np.isfinite(longfwd)])
    if BORROW_BP_PER_BAR:
        a = a - BORROW_BP_PER_BAR
    if a.size == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "win": 0.0}
    return {"n": int(a.size), "mean": float(np.mean(a) * 100),
            "median": float(np.median(a) * 100), "win": float(np.mean(a > 0) * 100)}


def main():
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    pop_parts = []
    min_len = max(SMA_N, NHIGH, V_LOOKBACK) + max(HORIZONS) + 2
    with get_session() as s:
        for n, code in enumerate(codes):
            if n % 300 == 0:
                logger.info("  {}/{}", n, len(codes))
            sub = _load_one(s, code)
            if len(sub) < min_len:
                continue
            sub["code"] = code
            o = sub["open"].to_numpy(); h = sub["high"].to_numpy()
            lo = sub["low"].to_numpy(); c = sub["close"].to_numpy(); v = sub["vol"].to_numpy()
            rng = h - lo
            with np.errstate(divide="ignore", invalid="ignore"):
                sub["close_pos"] = np.where(rng > 0, (c - lo) / rng, np.nan)
            sub["upbar"] = c > o
            sma = sub["close"].rolling(SMA_N).mean().to_numpy()
            rollhi = sub["high"].rolling(NHIGH).max().to_numpy()
            vavg = sub["vol"].rolling(V_LOOKBACK).mean().shift(1).to_numpy()
            sub["vmult"] = v / np.where(vavg > 0, vavg, np.nan)
            sub["turn_avg"] = c * np.where(vavg > 0, vavg, np.nan)
            entry = np.concatenate([o[1:], [np.nan]])
            for hh in HORIZONS:
                exitc = np.concatenate([c[hh:], [np.nan] * hh])
                sub[f"fwd{hh}"] = exitc / entry - 1.0
            athigh = (c > sma) & (c >= NEAR * rollhi)
            pop = sub[athigh & (sub["turn_avg"] >= TURN_TIERS[0]) & sub["vmult"].notna()].copy()
            if not pop.empty:
                pop_parts.append(pop)
    pop = pd.concat(pop_parts, ignore_index=True)
    pop["fy"] = pop["date"].apply(_fy)
    logger.info("uptrend-at-high bars: {}", len(pop))

    print(f"\n=== BASELINE pop: uptrend & within {100*(1-NEAR):.0f}% of {NHIGH}d high, "
          f"turnover>=¥{TURN_TIERS[0]/1e6:.0f}M (n={len(pop)}) ===")
    print("  (short baseline = shorting a random uptrend-at-high bar)")
    print(f"{'H':>4} {'longMean%':>10} {'shortMean%':>11} {'shortWin%':>10}")
    base = {}
    for hh in HORIZONS:
        s_ = _short(pop[f"fwd{hh}"].to_numpy()); base[hh] = s_
        lm = -s_["mean"]
        print(f"{hh:>4} {lm:>10.2f} {s_['mean']:>11.2f} {s_['win']:>10.1f}")

    # monotonicity by vmult quartile (within up bars at high) — louder = more negative?
    up = pop[pop["upbar"]]
    vm = up["vmult"].to_numpy(); f10 = up["fwd10"].to_numpy()
    ok = np.isfinite(vm) & np.isfinite(f10); vm, f10 = vm[ok], f10[ok]
    qs = np.quantile(vm, [0.25, 0.5, 0.75])
    print(f"\n=== MONOTONICITY (up bars at high) by vmult quartile — SHORT h10 ===")
    print(f"  vmult cuts: {qs[0]:.2f}/{qs[1]:.2f}/{qs[2]:.2f}")
    print(f"  {'bucket':>14} {'n':>7} {'shortMean%':>11} {'shortWin%':>10}")
    edges = [-np.inf, qs[0], qs[1], qs[2], np.inf]
    for i, nm in enumerate(["Q1 quiet", "Q2", "Q3", "Q4 loudest"]):
        m = (vm > edges[i]) & (vm <= edges[i + 1])
        s_ = _short(f10[m])
        print(f"  {nm:>14} {s_['n']:>7} {s_['mean']:>11.2f} {s_['win']:>10.1f}")

    # event cohorts: climax short, swept, with borrowability tiers
    def _report(mask, name):
        ev = pop[mask].sort_values(["code", "date"])
        keep, last = [], {}
        for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
            p = last.get(code)
            if p is None or (d - p).days > COOLDOWN * 7 / 5:
                keep.append(idx); last[code] = d
        ev = ev.loc[keep]
        print(f"\n=== CLIMAX SHORT: {name} ===")
        print(f"  fires {len(ev)}  stocks {ev['code'].nunique()}")
        print(f"  {'H':>4} {'n':>6} {'shortMean%':>11} {'shortMed%':>10} {'shortWin%':>10} "
              f"{'vs base':>8}")
        for hh in HORIZONS:
            s_ = _short(ev[f"fwd{hh}"].to_numpy())
            print(f"  {hh:>4} {s_['n']:>6} {s_['mean']:>11.2f} {s_['median']:>10.2f} "
                  f"{s_['win']:>10.1f} {s_['mean']-base[hh]['mean']:>8.2f}")
        line = []
        for fy in sorted(ev["fy"].unique()):
            a = -_w(ev.loc[ev["fy"] == fy, "fwd10"].to_numpy())
            a = a[np.isfinite(a)]
            line.append(f"FY{fy}:{np.mean(a)*100:+.1f}(n{len(ev[ev['fy']==fy])})" if a.size else f"FY{fy}:na")
        print("    per-FY(short h10): " + "  ".join(line))

    for K in K_SWEEP:
        for CP in CP_SWEEP:
            for TT in TURN_TIERS:
                m = (pop["upbar"] & (pop["vmult"] >= K) & (pop["close_pos"] <= CP)
                     & (pop["turn_avg"] >= TT))
                tier = "borrowable≥¥100M" if TT >= 100_000_000 else "all≥¥30M"
                _report(m, f"vmult>={K:g} close_pos<={CP:g} [{tier}]")


if __name__ == "__main__":
    main()
