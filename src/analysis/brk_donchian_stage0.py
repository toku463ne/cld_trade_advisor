"""Stage 0 — Turtle Donchian N-day channel breakout forward-return study.

Idea (2026-06-28, from docs/books/kojiro.md — 小次郎講師 真・トレーダーズバイブル, Turtle rules):
go long when price closes above the prior N-day high (新高値ブレイク).  Turtle Entry Rule 1
uses N=20 (mid trend), Rule 2 uses N=55 (long trend).  Mechanism claimed: trapped sellers'
limit orders at the old high get exhausted, overhead supply vanishes, price runs ("スルスル").
The "Donchian system" variant gates entries on a long-term trend filter SMA(50)>SMA(300).

This is the cleanest net-new sign vs the existing repo `brk_*` family (which are SMA-cross /
Bollinger, NOT an N-day high/low channel).  But the prior is LOW: breakout-momentum on JP
daily has inverted twice (`vol_breakout_confirm`, `lowprice_volspike` — a high-vol up-thrust
marks the move ENDING).  So per kojiro.md we test the UN-volume-gated channel and ask:

  Q1 (does it beat baseline?) — fresh Donchian breakout fwd return vs all tradeable bars.
  Q2 (BETA strip) — THE decisive control.  Subtract same-date equal-weight universe mean fwd
                    from every bar (market-neutral).  A breakout in a rising market is just
                    beta; MN tells us if the breakout out-SELECTS within the cross-section.
  Q3 (dose-response) — bucket fresh breakouts by breakout STRENGTH (close-above-channel in
                    ATR units).  Monotone-up = continuation works; INVERTED (strongest worst)
                    = the volspike/vol_breakout exhaustion pattern again.
  Q4 (trend filter) — does the SMA(50)>SMA(300) gate (Donchian system) help, and does any
                    edge survive per-FY (or is it just bull-year beta)?

Measurement-only.  Two-bar fill (enter open[T+1], exit close[T+h]).  Tradeable bars only
(avg-turnover floor).  Forward returns winsorized.  Only a beyond-baseline, BETA-STRIPPED,
regime-robust, NON-inverted result would justify a `brk_donchian` sign + the paired
fill-order null (CLAUDE.md Methodology).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.brk_donchian_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- params ---------------------------------------------------------------
CHANNELS = [20, 55]          # Donchian lookbacks (Turtle Rule 1 / Rule 2)
ATR_N = 20                   # ATR window for breakout-strength normalization
TREND_FAST = 50              # Donchian-system trend filter fast SMA
TREND_SLOW = 300             # Donchian-system trend filter slow SMA
TURN_MIN = 30_000_000.0      # ¥ AVERAGE turnover floor (close*avg_vol) — tradeable names
TURN_LOOKBACK = 20
HORIZONS = [1, 5, 10, 20]    # forward holding bars
WINSOR = 0.60
COOLDOWN = 20                # bars suppressed per stock after a fresh fire (dedupe runs)
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
    parts = []
    min_len = max(CHANNELS) + max(HORIZONS) + 2
    keep_cols = ["code", "date", "trend_ok"]
    for N in CHANNELS:
        keep_cols += [f"brk{N}", f"fresh{N}", f"str{N}"]
    keep_cols += [f"fwd{h}" for h in HORIZONS]
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
            # True Range / ATR for breakout-strength normalization
            prev_c = np.concatenate([[np.nan], c[:-1]])
            tr = np.maximum.reduce([h - lo, np.abs(h - prev_c), np.abs(prev_c - lo)])
            atr = pd.Series(tr).rolling(ATR_N).mean().shift(1).to_numpy()
            # trend filter SMA50 > SMA300
            sma_f = sub["close"].rolling(TREND_FAST).mean().to_numpy()
            sma_s = sub["close"].rolling(TREND_SLOW).mean().to_numpy()
            sub["trend_ok"] = sma_f > sma_s
            # Donchian channels: prior-N-day highest HIGH (shifted, no look-ahead)
            for N in CHANNELS:
                chan = sub["high"].rolling(N).max().shift(1).to_numpy()  # max of T-N..T-1
                brk = c > chan                                           # decisive close break
                prev_brk = np.concatenate([[False], brk[:-1]])
                sub[f"brk{N}"] = brk
                sub[f"fresh{N}"] = brk & (~prev_brk)                     # first day of run
                with np.errstate(invalid="ignore"):
                    sub[f"str{N}"] = (c - chan) / atr                    # break size in ATR
            # forward returns (two-bar fill)
            entry = np.concatenate([o[1:], [np.nan]])
            for hh in HORIZONS:
                exitc = np.concatenate([c[hh:], [np.nan] * hh])
                sub[f"fwd{hh}"] = exitc / entry - 1.0
            vavg = sub["vol"].rolling(TURN_LOOKBACK).mean().shift(1).to_numpy()
            turn_avg = c * np.where(vavg > 0, vavg, np.nan)
            keep = sub[turn_avg >= TURN_MIN].copy()
            if keep.empty:
                continue
            parts.append(keep[keep_cols])
    pop = pd.concat(parts, ignore_index=True)
    pop["fy"] = pop["date"].apply(_fy)
    logger.info("tradeable bars (pop): {}", len(pop))

    # market-neutral forward returns (BETA strip)
    for hh in HORIZONS:
        col = f"fwd{hh}"
        pop[f"mn{hh}"] = pop[col] - pop.groupby("date")[col].transform("mean")

    # ---- baseline ---------------------------------------------------------
    print("\n=== BASELINE: all tradeable bars ===")
    print(f"n={len(pop)}  stocks={pop['code'].nunique()}  "
          f"FY{pop['fy'].min()}..{pop['fy'].max()}")
    print(f"{'horizon':>8} {'n':>9} {'mean%':>8} {'med%':>8} {'DR%':>7} "
          f"{'MNmean%':>8} {'MN_DR%':>7}")
    base = {}
    for hh in HORIZONS:
        st = _stats(_winsor(pop[f"fwd{hh}"].to_numpy()), f"h{hh}")
        mn = _stats(_winsor(pop[f"mn{hh}"].to_numpy()), f"h{hh}")
        base[hh] = st
        base[("mn", hh)] = mn
        print(f"{'h'+str(hh):>8} {st['n']:>9} {st['mean']:>8.2f} "
              f"{st['median']:>8.2f} {st['dr']:>7.1f} "
              f"{mn['mean']:>8.2f} {mn['dr']:>7.1f}")

    # ---- event reporter ---------------------------------------------------
    def _dedupe(ev: pd.DataFrame) -> pd.DataFrame:
        ev = ev.sort_values(["code", "date"])
        keep_idx, last = [], {}
        for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
            prev = last.get(code)
            if prev is None or (d - prev).days > COOLDOWN * 7 / 5:
                keep_idx.append(idx)
                last[code] = d
        return ev.loc[keep_idx]

    def _report(ev: pd.DataFrame, name: str, dedupe: bool = True) -> None:
        if dedupe:
            ev = _dedupe(ev)
        print(f"\n=== EVENT: {name} ===")
        print(f"fires: {len(ev)}  stocks: {ev['code'].nunique()}  "
              f"({100*len(ev)/max(len(pop),1):.2f}% of pop)")
        print(f"{'horizon':>8} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'exc':>6} {'MNmean%':>8} {'MN_exc':>7}")
        for hh in HORIZONS:
            st = _stats(_winsor(ev[f"fwd{hh}"].to_numpy()), name)
            mn = _stats(_winsor(ev[f"mn{hh}"].to_numpy()), name)
            b, bm = base[hh], base[("mn", hh)]
            if st["n"] == 0:
                continue
            print(f"{'h'+str(hh):>8} {st['n']:>6} {st['mean']:>8.2f} "
                  f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                  f"{st['mean']-b['mean']:>6.2f} {mn['mean']:>8.2f} "
                  f"{mn['mean']-bm['mean']:>7.2f}")
        print("  per-FY (h=10 raw mean% / MN mean% / n):")
        line = []
        for fy in sorted(ev["fy"].unique()):
            sf = _winsor(ev[ev["fy"] == fy]["fwd10"].to_numpy())
            sfm = _winsor(ev[ev["fy"] == fy]["mn10"].to_numpy())
            sf, sfm = sf[np.isfinite(sf)], sfm[np.isfinite(sfm)]
            m = np.mean(sf) * 100 if sf.size else 0.0
            mm = np.mean(sfm) * 100 if sfm.size else 0.0
            line.append(f"FY{fy}:{m:+.1f}/{mm:+.1f}(n{sf.size})")
        print("    " + "  ".join(line))

    # ---- Q1/Q2/Q4: fresh breakout per channel, +/- trend filter -----------
    for N in CHANNELS:
        fresh = pop[pop[f"fresh{N}"]]
        _report(fresh, f"Q1 fresh Donchian-{N} breakout (no filter)")
        fresh_tf = pop[pop[f"fresh{N}"] & pop["trend_ok"]]
        _report(fresh_tf, f"Q4 fresh Donchian-{N} + SMA{TREND_FAST}>SMA{TREND_SLOW}")

    # ---- Q3: dose-response by breakout strength (ATR units), h10 ----------
    for N in CHANNELS:
        print(f"\n=== Q3 DOSE: fresh Donchian-{N} by breakout strength (ATR), h=10 ===")
        print("  (continuation→monotone up; exhaustion→strongest worst, like volspike)")
        fr = _dedupe(pop[pop[f"fresh{N}"]])
        strv = fr[f"str{N}"].to_numpy()
        raw = fr["fwd10"].to_numpy()
        mnv = fr["mn10"].to_numpy()
        ok = np.isfinite(strv) & np.isfinite(raw)
        strv, raw, mnv = strv[ok], raw[ok], mnv[ok]
        qs = np.quantile(strv, [0.25, 0.5, 0.75])
        print(f"  strength(ATR) quartile cuts: "
              f"{qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}")
        print(f"{'bucket':>16} {'n':>7} {'raw_mean%':>10} {'MN_mean%':>9} {'MN_DR%':>7}")
        edges = [-np.inf, qs[0], qs[1], qs[2], np.inf]
        names = ["Q1 smallest", "Q2", "Q3", "Q4 largest"]
        for i, nm in enumerate(names):
            m = (strv > edges[i]) & (strv <= edges[i + 1])
            r = _stats(_winsor(raw[m]), nm)
            mn = _stats(_winsor(mnv[m]), nm)
            print(f"{nm:>16} {r['n']:>7} {r['mean']:>10.2f} "
                  f"{mn['mean']:>9.2f} {mn['dr']:>7.1f}")


if __name__ == "__main__":
    main()
