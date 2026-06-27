"""Stage 0 — VPA `vol_breakout_confirm`: does breakout-bar VOLUME separate real
breakouts from fakeouts?

Idea (2026-06-27, from docs/books/dekidaka.md — Coulling VPA "valid-breakout rule",
p.55/68-69/84-85/100): leaving a congestion zone takes effort, so a GENUINE breakout
closes decisively beyond the level on ABOVE-AVERAGE and RISING volume, while a break on
LOW/below-average volume is an insider trap (ダマシ) that reverses.  In this repo that is a
proposed VOLUME GATE on brk_sma / brk_bol.  The only question Stage 0 asks: among breakout
bars, do HIGH-volume breakouts drift better than LOW-volume breakouts (the gate's edge =
the high-minus-low spread), and is the effect MONOTONE in volume — or flat like no_supply?

Measurement-only, read-only.  Does NOT touch the strategy book.  A green light needs the
high-volume cohort to beat the all-breakout baseline AND the low-volume cohort to
underperform it (a real fakeout signature), monotone in vmult, regime-robust (FY).  Only
then does an A/B volume gate on brk_sma/brk_bol earn a run, and any selection value still
faces the paired fill-order null (CLAUDE.md Methodology).

Breakout families (all = "decisive close beyond a level", two-bar fill):
  * DON20 / DON40 : close[T] > max(close[T-L : T])         (new L-day closing high)
  * SMA20x        : close crosses above SMA(close,20)        (brk_sma-style)
Volume axis: vmult[T] = vol[T] / SMA(vol, V_LOOKBACK)[T-1]  (relative, per-stock).
Cohorts: ALL (baseline) / >1.0 / >1.5 / >2.0 / <1.0 (fakeout candidate) / >1.0 & rising.

Outcome: enter open[T+1], exit close[T+h], h in HORIZONS, winsorized +/-WINSOR.
Per-stock COOLDOWN dedupe (breakouts cluster in trends).  Per-FY h10 regime check.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.vol_breakout_confirm_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- event params ---------------------------------------------------------
DON_SWEEP = [20, 40]         # Donchian closing-high lookbacks
SMA_N = 20                   # SMA-cross breakout window
V_LOOKBACK = 20              # trailing bars for average volume
HI_SWEEP = [1.0, 1.5, 2.0]   # above-average vmult cohorts
TURN_MIN = 30_000_000.0      # ¥ AVERAGE turnover floor (close*avg_vol) — tradeable names
HORIZONS = [1, 5, 10, 20]    # forward holding bars
WINSOR = 0.60                # forward-return clip
COOLDOWN = 20                # bars suppressed per stock after a fire (dedupe clusters)
_FY_START_MONTH = 4


def _codes() -> list[str]:
    with get_session() as s:
        rows = s.execute(text(
            "SELECT DISTINCT stock_code FROM ohlcv_1d ORDER BY stock_code"
        )).all()
    return [r[0] for r in rows if not r[0].startswith("^")]


def _load_one(s, code: str) -> pd.DataFrame:
    rows = s.execute(text(
        "SELECT ts, open_price::float8, high_price::float8, low_price::float8, "
        "close_price::float8, volume::float8 FROM ohlcv_1d "
        "WHERE stock_code=:c ORDER BY ts"
    ), {"c": code}).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df["date"] = pd.to_datetime(df["ts"]).dt.tz_localize(None).dt.normalize()
    g = df.groupby("date", sort=True)
    return g.agg(open=("open", "first"), high=("high", "max"),
                 low=("low", "min"), close=("close", "last"),
                 vol=("vol", "sum")).reset_index()


def _fy(d: pd.Timestamp) -> int:
    return d.year if d.month >= _FY_START_MONTH else d.year - 1


def _winsor(a: np.ndarray) -> np.ndarray:
    return np.clip(a, -WINSOR, WINSOR)


def _stats(fwd: np.ndarray) -> dict:
    fwd = fwd[np.isfinite(fwd)]
    if fwd.size == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "dr": 0.0}
    return {"n": int(fwd.size), "mean": float(np.mean(fwd) * 100),
            "median": float(np.median(fwd) * 100), "dr": float(np.mean(fwd > 0) * 100)}


def _dedupe(ev: pd.DataFrame) -> pd.DataFrame:
    ev = ev.sort_values(["code", "date"])
    keep_idx, last = [], {}
    for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
        prev = last.get(code)
        if prev is None or (d - prev).days > COOLDOWN * 7 / 5:
            keep_idx.append(idx)
            last[code] = d
    return ev.loc[keep_idx]


def _analyze(fires: pd.DataFrame, family: str) -> None:
    fires = _dedupe(fires)
    fires = fires.copy()
    fires["fy"] = fires["date"].apply(_fy)
    base = {h: _stats(_winsor(fires[f"fwd{h}"].to_numpy())) for h in HORIZONS}
    print(f"\n############ FAMILY: {family} ############")
    print(f"breakout fires (deduped): {len(fires)}  stocks: {fires['code'].nunique()}")

    print("\n  --- BASELINE: all breakouts (any volume) ---")
    print(f"  {'horizon':>8} {'n':>7} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    for h in HORIZONS:
        b = base[h]
        print(f"  {'h'+str(h):>8} {b['n']:>7} {b['mean']:>8.2f} "
              f"{b['median']:>8.2f} {b['dr']:>7.1f}")

    # monotonicity panel (h10): breakout fires by vmult quartile
    vm = fires["vmult"].to_numpy()
    f10 = fires["fwd10"].to_numpy()
    ok = np.isfinite(vm) & np.isfinite(f10)
    vm, f10 = vm[ok], f10[ok]
    qs = np.quantile(vm, [0.25, 0.5, 0.75])
    print(f"\n  --- MONOTONICITY: breakouts by vmult quartile (h=10) ---")
    print(f"  vmult cuts: {qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}")
    print(f"  {'bucket':>14} {'n':>7} {'mean%':>8} {'DR%':>7}")
    edges = [-np.inf, qs[0], qs[1], qs[2], np.inf]
    names = ["Q1 lightest", "Q2", "Q3", "Q4 heaviest"]
    for i, nm in enumerate(names):
        m = (vm > edges[i]) & (vm <= edges[i + 1])
        st = _stats(_winsor(f10[m]))
        print(f"  {nm:>14} {st['n']:>7} {st['mean']:>8.2f} {st['dr']:>7.1f}")

    # cohorts
    def _cohort(mask: pd.Series, name: str) -> dict:
        ev = fires[mask]
        print(f"\n  --- COHORT: {name}  (n={len(ev)}, "
              f"{100*len(ev)/max(len(fires),1):.0f}% of breakouts) ---")
        print(f"  {'horizon':>8} {'n':>7} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'exc_mean':>9} {'exc_DR':>8}")
        out = {}
        for h in HORIZONS:
            st = _stats(_winsor(ev[f"fwd{h}"].to_numpy()))
            out[h] = st
            if st["n"] == 0:
                continue
            print(f"  {'h'+str(h):>8} {st['n']:>7} {st['mean']:>8.2f} "
                  f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                  f"{st['mean']-base[h]['mean']:>9.2f} {st['dr']-base[h]['dr']:>8.1f}")
        line = []
        for fy in sorted(ev["fy"].unique()):
            sf = ev[ev["fy"] == fy]["fwd10"].to_numpy()
            sf = _winsor(sf[np.isfinite(sf)])
            m = np.mean(sf) * 100 if sf.size else 0.0
            line.append(f"FY{fy}:{m:+.1f}(n{len(ev[ev['fy']==fy])})")
        print("    per-FY(h10): " + "  ".join(line))
        return out

    hi = {}
    for k in HI_SWEEP:
        hi[k] = _cohort(fires["vmult"] > k, f"vmult>{k:g} (confirmed)")
    lo = _cohort(fires["vmult"] < 1.0, "vmult<1.0 (LOW-vol / fakeout candidate)")
    _cohort((fires["vmult"] > 1.0) & fires["vol_rising"],
            "vmult>1.0 & volume rising vs prior bar")

    # THE decisive number: confirmed(>1) minus fakeout(<1) spread
    print("\n  === GATE EDGE: high-vol(>1.0) minus low-vol(<1.0) ===")
    print(f"  {'horizon':>8} {'dMean%':>8} {'dDR%':>7}")
    for h in HORIZONS:
        dM = hi[1.0][h]["mean"] - lo[h]["mean"]
        dDR = hi[1.0][h]["dr"] - lo[h]["dr"]
        print(f"  {'h'+str(h):>8} {dM:>8.2f} {dDR:>7.1f}")


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    don_parts = {L: [] for L in DON_SWEEP}
    sma_parts = []
    min_len = max(max(DON_SWEEP), SMA_N, V_LOOKBACK) + max(HORIZONS) + 2
    with get_session() as s:
        for n, code in enumerate(codes):
            if n % 300 == 0:
                logger.info("  {}/{}", n, len(codes))
            sub = _load_one(s, code)
            if len(sub) < min_len:
                continue
            sub["code"] = code
            c = sub["close"].to_numpy()
            o = sub["open"].to_numpy()
            v = sub["vol"].to_numpy()
            vavg = sub["vol"].rolling(V_LOOKBACK).mean().shift(1).to_numpy()
            sub["vmult"] = v / np.where(vavg > 0, vavg, np.nan)
            sub["vol_rising"] = np.concatenate([[False], v[1:] > v[:-1]])
            sub["turn_avg"] = c * np.where(vavg > 0, vavg, np.nan)
            entry = np.concatenate([o[1:], [np.nan]])
            for h in HORIZONS:
                exitc = np.concatenate([c[h:], [np.nan] * h])
                sub[f"fwd{h}"] = exitc / entry - 1.0
            liq = (sub["turn_avg"] >= TURN_MIN) & sub["vmult"].notna()
            # Donchian closing-high breakouts
            for L in DON_SWEEP:
                prior_hi = sub["close"].rolling(L).max().shift(1)
                fire = (sub["close"] > prior_hi) & liq & prior_hi.notna()
                if fire.any():
                    don_parts[L].append(sub[fire])
            # SMA-cross breakout
            sma = sub["close"].rolling(SMA_N).mean()
            cross = (sub["close"] > sma) & (sub["close"].shift(1) <= sma.shift(1))
            fire = cross & liq & sma.notna()
            if fire.any():
                sma_parts.append(sub[fire])

    for L in DON_SWEEP:
        fires = pd.concat(don_parts[L], ignore_index=True)
        _analyze(fires, f"DON{L} (new {L}-day closing high)")
    fires = pd.concat(sma_parts, ignore_index=True)
    _analyze(fires, f"SMA{SMA_N}x (close crosses above SMA{SMA_N})")


if __name__ == "__main__":
    main()
