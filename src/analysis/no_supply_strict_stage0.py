"""Stage 0 (STRICT) — VPA `no_supply` re-test with a faithful strong-uptrend +
pullback-to-the-MA specification.

Follow-up to project_no_supply_stage0_reject.md (2026-06-27).  The first cut used a LOOSE
trend gate (close>rising SMA20) and a one-down-bar "pullback", and found volume dry-up
uninformative.  Operator's fair critique: no_supply is meant to fire only in an ESTABLISHED
STRONG uptrend on a genuine pullback BACK TO THE MOVING AVERAGE — that narrow setup was
never isolated.  This script tightens BOTH:

  STRONG UPTREND (all required):
    * close > SMA20 > SMA50                     (stacked MAs)
    * SMA50 rising over TREND_SLOPE bars        (longer trend up)
    * close > close[-UPLEG]                      (real net advance over ~1 month)
    * ADX(14) >= ADX_MIN                         (trend strength, Wilder)
  PULLBACK TO THE MA:
    * close < max(high[T-PB_LOOK : T])           (came off a recent high)
    * low[T] <= SMA20[T] * (1 + TOUCH)           (the bar reached DOWN to the SMA20)
    * ret1[T] < 0                                (the bar itself is a down probe)

Event (the volume axis, two-bar fill enter open[T+1], exit close[T+h]):
    A: + dry-up  vmult <= V_DRY                  (V_DRY swept)
    B: A + close_pos >= CLOSE_POS & lwick >= LWICK   (holding bar)

Baseline/null = ALL strong-uptrend pullback-to-MA bars (any volume) — isolates the dry-up
effect WITHIN the faithful setup.  Decisive: the vmult quartile monotonicity panel (h10)
and the dry-minus-heavy spread.  Per-FY h10 regime check; per-stock COOLDOWN dedupe.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.no_supply_strict_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- trend params ---------------------------------------------------------
SMA_FAST = 20
SMA_SLOW = 50
TREND_SLOPE = 20             # SMA50 must be rising over this many bars
UPLEG = 20                   # close must exceed close[-UPLEG] (net advance)
ADX_N = 14
ADX_MIN = 20.0               # Wilder ADX trend-strength floor
# ---- pullback params ------------------------------------------------------
PB_LOOK = 5                  # "came off a recent high" window
TOUCH = 0.01                 # bar low must reach within +1% of SMA20 (pullback to MA)
# ---- event (volume) params ------------------------------------------------
V_LOOKBACK = 20
V_DRY_SWEEP = [0.7, 0.5]
CLOSE_POS = 0.5
LWICK = 0.25
# ---- shared --------------------------------------------------------------
TURN_MIN = 30_000_000.0
HORIZONS = [1, 5, 10, 20]
WINSOR = 0.60
COOLDOWN = 20
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


def _adx(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = ADX_N) -> np.ndarray:
    """Wilder ADX (RMA via ewm alpha=1/n)."""
    prev_c = np.concatenate([[np.nan], c[:-1]])
    tr = np.maximum.reduce([h - lo, np.abs(h - prev_c), np.abs(lo - prev_c)])
    up = h - np.concatenate([[np.nan], h[:-1]])
    dn = np.concatenate([[np.nan], lo[:-1]]) - lo
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    rma = lambda a: pd.Series(a).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    atr = rma(tr)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * rma(plus_dm) / atr
        mdi = 100.0 * rma(minus_dm) / atr
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    return rma(dx)


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


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    pop_parts = []
    min_len = SMA_SLOW + TREND_SLOPE + max(HORIZONS) + 5
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
            h = sub["high"].to_numpy()
            lo = sub["low"].to_numpy()
            v = sub["vol"].to_numpy()
            rng = h - lo
            with np.errstate(divide="ignore", invalid="ignore"):
                sub["close_pos"] = np.where(rng > 0, (c - lo) / rng, np.nan)
                sub["lwick"] = np.where(rng > 0, (np.minimum(o, c) - lo) / rng, np.nan)
            sub["ret1"] = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            sma20 = sub["close"].rolling(SMA_FAST).mean()
            sma50 = sub["close"].rolling(SMA_SLOW).mean()
            sub["sma20"] = sma20.to_numpy()
            adx = _adx(h, lo, c)
            vavg = sub["vol"].rolling(V_LOOKBACK).mean().shift(1).to_numpy()
            sub["vmult"] = v / np.where(vavg > 0, vavg, np.nan)
            sub["turn_avg"] = c * np.where(vavg > 0, vavg, np.nan)
            recent_hi = sub["high"].rolling(PB_LOOK).max().shift(1).to_numpy()
            entry = np.concatenate([o[1:], [np.nan]])
            for hh in HORIZONS:
                exitc = np.concatenate([c[hh:], [np.nan] * hh])
                sub[f"fwd{hh}"] = exitc / entry - 1.0
            strong = ((c > sma20.to_numpy()) & (sma20.to_numpy() > sma50.to_numpy())
                      & (sma50.to_numpy() > np.concatenate(
                          [[np.nan] * TREND_SLOPE, sma50.to_numpy()[:-TREND_SLOPE]]))
                      & (c > np.concatenate([[np.nan] * UPLEG, c[:-UPLEG]]))
                      & (adx >= ADX_MIN))
            pullback = ((c < recent_hi)
                        & (lo <= sma20.to_numpy() * (1.0 + TOUCH))
                        & (sub["ret1"].to_numpy() < 0))
            pop = sub[strong & pullback & (sub["turn_avg"] >= TURN_MIN)
                      & sub["vmult"].notna()].copy()
            if not pop.empty:
                pop_parts.append(pop)
    pop = pd.concat(pop_parts, ignore_index=True)
    pop["fy"] = pop["date"].apply(_fy)
    logger.info("STRICT strong-uptrend pullback-to-MA bars: {}", len(pop))

    print("\n=== STRICT BASELINE: strong-uptrend (close>SMA20>SMA50, rising SMA50, "
          f"+{UPLEG}-bar advance, ADX>={ADX_MIN:.0f}) pullback-to-MA down bars ===")
    print(f"n={len(pop)}  stocks={pop['code'].nunique()}")
    print(f"{'horizon':>8} {'n':>8} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    base = {}
    for hh in HORIZONS:
        st = _stats(_winsor(pop[f"fwd{hh}"].to_numpy()))
        base[hh] = st
        print(f"{'h'+str(hh):>8} {st['n']:>8} {st['mean']:>8.2f} "
              f"{st['median']:>8.2f} {st['dr']:>7.1f}")

    print("\n=== MONOTONICITY: strict pop by vmult quartile (h=10) ===")
    vm = pop["vmult"].to_numpy()
    f10 = pop["fwd10"].to_numpy()
    ok = np.isfinite(vm) & np.isfinite(f10)
    vm, f10 = vm[ok], f10[ok]
    qs = np.quantile(vm, [0.25, 0.5, 0.75])
    print(f"  vmult cuts: {qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}")
    print(f"{'bucket':>14} {'n':>7} {'mean%':>8} {'DR%':>7}")
    edges = [-np.inf, qs[0], qs[1], qs[2], np.inf]
    names = ["Q1 driest", "Q2", "Q3", "Q4 heaviest"]
    for i, nm in enumerate(names):
        m = (vm > edges[i]) & (vm <= edges[i + 1])
        st = _stats(_winsor(f10[m]))
        print(f"{nm:>14} {st['n']:>7} {st['mean']:>8.2f} {st['dr']:>7.1f}")

    def _report(ev: pd.DataFrame, name: str) -> dict:
        ev = _dedupe(ev)
        print(f"\n=== EVENT: {name} ===")
        print(f"fires (deduped): {len(ev)}  stocks: {ev['code'].nunique()}  "
              f"({100*len(ev)/max(len(pop),1):.1f}% of strict pop)")
        print(f"{'horizon':>8} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'exc_mean':>9} {'exc_DR':>8}")
        out = {}
        for hh in HORIZONS:
            st = _stats(_winsor(ev[f"fwd{hh}"].to_numpy()))
            out[hh] = st
            if st["n"] == 0:
                continue
            print(f"{'h'+str(hh):>8} {st['n']:>6} {st['mean']:>8.2f} "
                  f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                  f"{st['mean']-base[hh]['mean']:>9.2f} {st['dr']-base[hh]['dr']:>8.1f}")
        line = []
        for fy in sorted(ev["fy"].unique()):
            sf = ev[ev["fy"] == fy]["fwd10"].to_numpy()
            sf = _winsor(sf[np.isfinite(sf)])
            m = np.mean(sf) * 100 if sf.size else 0.0
            line.append(f"FY{fy}:{m:+.1f}(n{len(ev[ev['fy']==fy])})")
        print("  per-FY(h10): " + "  ".join(line))
        return out

    for V_DRY in V_DRY_SWEEP:
        a = _report(pop[pop["vmult"] <= V_DRY], f"A dry vmult<={V_DRY:g}")
        _report(pop[(pop["vmult"] <= V_DRY) & (pop["close_pos"] >= CLOSE_POS)
                    & (pop["lwick"] >= LWICK)],
                f"B no_supply vmult<={V_DRY:g} & close_pos>={CLOSE_POS} & lwick>={LWICK}")
        heavy = _report(pop[pop["vmult"] > 1.0], f"(ref) heavy vmult>1.0 [V_DRY={V_DRY:g}]")
        print(f"\n  === DRY EDGE: dry(<={V_DRY:g}) minus heavy(>1.0) ===")
        print(f"  {'horizon':>8} {'dMean%':>8} {'dDR%':>7}")
        for hh in HORIZONS:
            print(f"  {'h'+str(hh):>8} {a[hh]['mean']-heavy[hh]['mean']:>8.2f} "
                  f"{a[hh]['dr']-heavy[hh]['dr']:>7.1f}")


if __name__ == "__main__":
    main()
