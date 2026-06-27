"""Stage 0 — VPA `no_supply` low-volume "supply dry-up" continuation event study.

Idea (2026-06-27, from docs/books/dekidaka.md — Anna Coulling VPA): in an established
uptrend, a pullback bar whose VOLUME DRIES UP (well below its own trailing average) while
the close holds in the upper half of the bar = "no supply" = no selling pressure left =
bullish continuation.  This is the LOW-volume mirror of every volume sign the repo has
tried (obv_div / vol_absorb / lowprice_volspike all keyed on HIGH/abnormal volume and all
REJECTED — the spike marks the move ENDING).  The dry-up axis has never been benchmarked
here, so this Stage 0 asks the only question that matters first: among uptrend pullback
bars, does the LOW-volume subset show better forward behaviour than the generic uptrend
pullback — and is the effect MONOTONE in dryness (drier = better), or inverted like the
volspike was?

Measurement-only.  Does NOT touch the strategy book.  If a clear, regime-robust,
beyond-baseline, correctly-signed effect exists, the follow-up is a `no_supply` sign +
the fresh-vs-co-fired sufficiency test (accum_volume reject lesson) + the paired
fill-order null (CLAUDE.md Methodology) before any selection claim.

Event (bar T):
  * uptrend     : close[T] > SMA(close, TREND_N)[T]  AND  SMA rising over TREND_SLOPE bars
  * pullback    : close[T] < close[T-1]              (a down-probe inside the uptrend)
  * supply dry  : vmult[T] = vol[T] / SMA(vol, V_LOOKBACK)[T-1] <= V_DRY   (V_DRY swept)
  * holding bar : close_pos[T] >= CLOSE_POS  AND  lower_wick_frac[T] >= LWICK   (variant B)
  * liquidity   : avg turnover close[T]*SMA(vol)[T] >= TURN_MIN  (tradeable; floor on the
                  AVERAGE not the dried-up bar, so low bar-volume itself is not screened out)

Outcome (two-bar fill, per CLAUDE.md): enter at open[T+1], exit at close[T+h] for h in
HORIZONS.  Forward returns winsorized at +/-WINSOR.

Null / baseline: the SAME forward-return statistics over EVERY uptrend pullback bar
(regardless of volume) — isolates the volume-dry-up effect, not the uptrend-pullback
effect.  Plus a monotonicity panel: uptrend-pullback bars bucketed by volume quartile
(the inversion test).  Per-FY breakdown included (regime/beta check).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.no_supply_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- event params ---------------------------------------------------------
TREND_N = 20                 # SMA window for uptrend gate
TREND_SLOPE = 10             # SMA must be rising over this many bars
V_LOOKBACK = 20              # trailing bars for average volume (VPA "recent baseline")
V_DRY_SWEEP = [0.7, 0.5]     # vol / trailing-avg ceilings (dry-up thresholds)
CLOSE_POS = 0.5              # close in upper half of the bar (buyers defended)
LWICK = 0.25                 # lower-wick fraction (rejection of lower prices)
TURN_MIN = 30_000_000.0      # ¥ AVERAGE turnover floor (close*avg_vol) — tradeable names
HORIZONS = [1, 5, 10, 20]    # forward holding bars
WINSOR = 0.60                # forward-return clip
COOLDOWN = 20                # bars suppressed per stock after a fire (dedupe clusters)
_FY_START_MONTH = 4          # JP fiscal year starts in April


def _codes() -> list[str]:
    with get_session() as s:
        rows = s.execute(text(
            "SELECT DISTINCT stock_code FROM ohlcv_1d ORDER BY stock_code"
        )).all()
    return [r[0] for r in rows if not r[0].startswith("^")]


def _load_one(s, code: str) -> pd.DataFrame:
    """Daily bars for one stock (intraday rows collapsed to one daily bar)."""
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


def _stats(fwd: np.ndarray, label: str) -> dict:
    fwd = fwd[np.isfinite(fwd)]
    if fwd.size == 0:
        return {"label": label, "n": 0, "mean": 0.0, "median": 0.0, "dr": 0.0}
    return {"label": label, "n": int(fwd.size),
            "mean": float(np.mean(fwd) * 100),
            "median": float(np.median(fwd) * 100),
            "dr": float(np.mean(fwd > 0) * 100)}


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    pb_parts = []                          # all uptrend pullback bars (the baseline)
    min_len = max(TREND_N, V_LOOKBACK) + TREND_SLOPE + max(HORIZONS) + 2
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
            sma = sub["close"].rolling(TREND_N).mean()
            sub["sma"] = sma.to_numpy()
            sub["sma_rise"] = (sma > sma.shift(TREND_SLOPE)).to_numpy()
            vavg = sub["vol"].rolling(V_LOOKBACK).mean().shift(1).to_numpy()
            sub["vmult"] = v / np.where(vavg > 0, vavg, np.nan)
            sub["turn_avg"] = c * np.where(vavg > 0, vavg, np.nan)
            entry = np.concatenate([o[1:], [np.nan]])          # entry[i]=open[T+1]
            for hh in HORIZONS:
                exitc = np.concatenate([c[hh:], [np.nan] * hh])  # exitc[i]=close[T+hh]
                sub[f"fwd{hh}"] = exitc / entry - 1.0
            # uptrend pullback bars (the population we condition within)
            pb = sub[(sub["close"] > sub["sma"]) & sub["sma_rise"]
                     & (sub["ret1"] < 0) & (sub["turn_avg"] >= TURN_MIN)
                     & sub["vmult"].notna()].copy()
            if pb.empty:
                continue
            pb_parts.append(pb)
    pop = pd.concat(pb_parts, ignore_index=True)
    pop["fy"] = pop["date"].apply(_fy)
    logger.info("uptrend pullback bars (baseline pop): {}", len(pop))

    # ---- baseline (null): all uptrend pullback bars -----------------------
    print("\n=== BASELINE: all uptrend pullback bars (any volume) ===")
    print(f"n={len(pop)}  stocks={pop['code'].nunique()}")
    print(f"{'horizon':>8} {'n':>9} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    base = {}
    for hh in HORIZONS:
        st = _stats(_winsor(pop[f"fwd{hh}"].to_numpy()), f"h{hh}")
        base[hh] = st
        print(f"{'h'+str(hh):>8} {st['n']:>9} {st['mean']:>8.2f} "
              f"{st['median']:>8.2f} {st['dr']:>7.1f}")

    # ---- monotonicity panel: pullback bars by volume quartile (h10) -------
    # THE inversion test. If "drier = better" the lowest-vol quartile wins and the
    # trend is monotone; if non-monotone/inverted, the dry-up axis is not the edge.
    print("\n=== MONOTONICITY: uptrend pullback bars by vmult quartile (h=10) ===")
    vm = pop["vmult"].to_numpy()
    f10 = pop["fwd10"].to_numpy()
    ok = np.isfinite(vm) & np.isfinite(f10)
    vm, f10 = vm[ok], f10[ok]
    qs = np.quantile(vm, [0.25, 0.5, 0.75])
    print(f"  vmult quartile cuts: {qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}")
    print(f"{'bucket':>14} {'n':>8} {'mean%':>8} {'DR%':>7}")
    edges = [-np.inf, qs[0], qs[1], qs[2], np.inf]
    names = ["Q1 driest", "Q2", "Q3", "Q4 heaviest"]
    for i, nm in enumerate(names):
        m = (vm > edges[i]) & (vm <= edges[i + 1])
        st = _stats(_winsor(f10[m]), nm)
        print(f"{nm:>14} {st['n']:>8} {st['mean']:>8.2f} {st['dr']:>7.1f}")

    # ---- event variants ---------------------------------------------------
    def _dedupe(ev: pd.DataFrame) -> pd.DataFrame:
        ev = ev.sort_values(["code", "date"])
        keep_idx, last = [], {}
        for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
            prev = last.get(code)
            if prev is None or (d - prev).days > COOLDOWN * 7 / 5:
                keep_idx.append(idx)
                last[code] = d
        return ev.loc[keep_idx]

    def _report(ev: pd.DataFrame, name: str) -> None:
        ev = _dedupe(ev)
        print(f"\n=== EVENT: {name} ===")
        print(f"fires (deduped): {len(ev)}  stocks: {ev['code'].nunique()}  "
              f"({100*len(ev)/max(len(pop),1):.1f}% of baseline pop)")
        print(f"{'horizon':>8} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'exc_mean':>9} {'exc_DR':>8}")
        for hh in HORIZONS:
            st = _stats(_winsor(ev[f"fwd{hh}"].to_numpy()), name)
            b = base[hh]
            if st["n"] == 0:
                continue
            print(f"{'h'+str(hh):>8} {st['n']:>6} {st['mean']:>8.2f} "
                  f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                  f"{st['mean']-b['mean']:>9.2f} {st['dr']-b['dr']:>8.1f}")
        print("  per-FY (h=10 mean% / n):")
        line = []
        for fy in sorted(ev["fy"].unique()):
            sf = ev[ev["fy"] == fy]["fwd10"].to_numpy()
            sf = _winsor(sf[np.isfinite(sf)])
            m = np.mean(sf) * 100 if sf.size else 0.0
            line.append(f"FY{fy}:{m:+.1f}(n{len(ev[ev['fy']==fy])})")
        print("    " + "  ".join(line))

    for V_DRY in V_DRY_SWEEP:
        # variant A: dry-up only (no candle-shape filter) — isolates the volume axis
        a = pop[pop["vmult"] <= V_DRY]
        _report(a, f"A dry-up vmult<={V_DRY:g} (no shape filter)")
        # variant B: full no_supply (dry-up + holding bar: close upper-half + lower wick)
        b = pop[(pop["vmult"] <= V_DRY) & (pop["close_pos"] >= CLOSE_POS)
                & (pop["lwick"] >= LWICK)]
        _report(b, f"B no_supply vmult<={V_DRY:g} & close_pos>={CLOSE_POS} "
                   f"& lwick>={LWICK}")


if __name__ == "__main__":
    main()
