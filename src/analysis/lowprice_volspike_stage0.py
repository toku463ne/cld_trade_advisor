"""Stage 0 — low-price (<¥1000) abnormal-volume-spike + price-up event study.

Operator request (2026-06-15): a NEW idea outside the confluence/regime_sign book —
penny-tier names (close < ¥1000) that print an abnormally high-volume bar while the
price is GOING UP.  Hypothesis: a heavy-volume up-bar on a cheap stock marks the start
of a fundamentals-driven move that a human could then research and trade manually.  The
operator already expects the sample to be thin; the question is whether there is any
CLEAR forward behaviour at all (continuation? mean-reversion? noise?) before investing
in a screener UI.

This is a measurement-only Stage 0.  It does NOT touch the strategy book.  If a clear,
regime-robust, beyond-baseline behaviour exists, the follow-up is a read-only search
engine (Daily-tab screener) for manual fundamental trading — NOT an automated sign.

Event (bar T), several pre-registered variants:
  * price tier   : close[T] < PRICE_MAX (¥1000)
  * volume spike : vol[T] / median(vol[T-V_LOOKBACK : T]) >= K        (K swept)
                   strict "ever" variant: vol[T] == max(vol[..T])     (all-time-high bar)
  * price up     : ret1[T] = close[T]/close[T-1] - 1 >= UP            (UP swept)
  * liquidity    : turnover[T] = close[T]*vol[T] >= TURN_MIN  (tradeable, real fill)

Outcome (two-bar fill, per CLAUDE.md): enter at open[T+1], exit at close[T+h] for
h in HORIZONS.  Forward returns winsorized at +/-WINSOR (unadjusted-glitch guard, same
as universe_baseline.py).

Null / beta strip: the SAME forward-return statistics over EVERY low-price stock-day
(close < PRICE_MAX) — the "penny drift" baseline.  A real effect must beat this, not
just be positive.  Per-FY breakdown included (regime/beta check).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.lowprice_volspike_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- event params ---------------------------------------------------------
PRICE_MAX = 1000.0          # ¥ adjusted close tier
V_LOOKBACK = 60             # trailing bars for median volume
K_SWEEP = [3.0, 5.0, 10.0]  # volume / trailing-median multiples
UP_SWEEP = [0.03, 0.05]     # min same-bar close-to-close return
TURN_MIN = 30_000_000.0     # ¥ turnover floor (close*vol) on the spike bar
HORIZONS = [1, 5, 10, 20]   # forward holding bars
WINSOR = 0.60               # forward-return clip
COOLDOWN = 20               # bars suppressed per stock after a fire (dedupe clusters)
_FY_START_MONTH = 4         # JP fiscal year starts in April


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
        return {"label": label, "n": 0}
    return {"label": label, "n": int(fwd.size),
            "mean": float(np.mean(fwd) * 100),
            "median": float(np.median(fwd) * 100),
            "dr": float(np.mean(fwd > 0) * 100)}


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    ev_parts = []                       # low-price stock-days (candidate events)
    base_fwd = {h: [] for h in HORIZONS}  # baseline: ALL low-price stock-days
    min_len = V_LOOKBACK + max(HORIZONS) + 2
    with get_session() as s:
        for n, code in enumerate(codes):
            if n % 300 == 0:
                logger.info("  {}/{}", n, len(codes))
            sub = _load_one(s, code)
            if len(sub) < min_len:
                continue
            sub["code"] = code
            c = sub["close"].to_numpy()
            v = sub["vol"].to_numpy()
            o = sub["open"].to_numpy()
            sub["ret1"] = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            med = sub["vol"].rolling(V_LOOKBACK).median().shift(1).to_numpy()
            sub["vmult"] = v / np.where(med > 0, med, np.nan)
            sub["vmax_prev"] = sub["vol"].cummax().shift(1).to_numpy()
            sub["turn"] = c * v
            entry = np.concatenate([o[1:], [np.nan]])          # entry[i]=open[T+1]
            for h in HORIZONS:
                exitc = np.concatenate([c[h:], [np.nan] * h])  # exitc[i]=close[T+h]
                sub[f"fwd{h}"] = exitc / entry - 1.0
            low = sub[(sub["close"] < PRICE_MAX) & sub["close"].notna()].copy()
            if low.empty:
                continue
            for h in HORIZONS:
                base_fwd[h].append(low[f"fwd{h}"].to_numpy())
            ev_parts.append(low)
    lowp = pd.concat(ev_parts, ignore_index=True)
    lowp["fy"] = lowp["date"].apply(_fy)
    base_fwd = {h: np.concatenate(base_fwd[h]) for h in HORIZONS}
    logger.info("low-price stock-days: {}", len(lowp))

    # ---- baseline (null): all low-price stock-days ------------------------
    print("\n=== BASELINE: all low-price (<¥{:.0f}) stock-days ===".format(PRICE_MAX))
    print(f"{'horizon':>8} {'n':>9} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    base = {}
    for h in HORIZONS:
        st = _stats(_winsor(base_fwd[h]), f"h{h}")
        base[h] = st
        print(f"{'h'+str(h):>8} {st['n']:>9} {st.get('mean',0):>8.2f} "
              f"{st.get('median',0):>8.2f} {st.get('dr',0):>7.1f}")

    # ---- event variants ---------------------------------------------------
    def _events(mask: pd.Series) -> pd.DataFrame:
        ev = lowp[mask & (lowp["turn"] >= TURN_MIN)].copy()
        ev = ev.sort_values(["code", "date"])
        # greedy per-stock cooldown dedupe
        keep_idx = []
        last = {}
        for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
            prev = last.get(code)
            if prev is None or (d - prev).days > COOLDOWN * 7 / 5:
                keep_idx.append(idx)
                last[code] = d
        return ev.loc[keep_idx]

    def _report(ev: pd.DataFrame, name: str) -> None:
        print(f"\n=== EVENT: {name} ===")
        print(f"raw fires (after turnover floor): {len(ev)}  "
              f"stocks: {ev['code'].nunique()}")
        print(f"{'horizon':>8} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'excess_mean':>12} {'excess_DR':>10}")
        for h in HORIZONS:
            st = _stats(_winsor(ev[f"fwd{h}"].to_numpy()), name)
            b = base[h]
            if st["n"] == 0:
                continue
            print(f"{'h'+str(h):>8} {st['n']:>6} {st['mean']:>8.2f} "
                  f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                  f"{st['mean']-b['mean']:>12.2f} {st['dr']-b['dr']:>10.1f}")
        # per-FY at h=10 (regime check)
        print(f"  per-FY (h=10 mean% / n):")
        line = []
        for fy in sorted(ev["fy"].unique()):
            sub = ev[ev["fy"] == fy]
            m = np.mean(_winsor(sub["fwd10"].to_numpy()[
                np.isfinite(sub["fwd10"].to_numpy())])) * 100
            line.append(f"FY{fy}:{m:+.1f}(n{len(sub)})")
        print("    " + "  ".join(line))

    for K in K_SWEEP:
        for UP in UP_SWEEP:
            m = (lowp["vmult"] >= K) & (lowp["ret1"] >= UP)
            ev = _events(m)
            _report(ev, f"vmult>={K:g} & up>={UP:.0%}")

    # strict "abnormal volume EVER" (all-time-high volume bar)
    for UP in UP_SWEEP:
        m = (lowp["vol"] >= lowp["vmax_prev"]) & (lowp["ret1"] >= UP) \
            & lowp["vmax_prev"].notna()
        ev = _events(m)
        _report(ev, f"ALL-TIME-HIGH vol & up>={UP:.0%}")


if __name__ == "__main__":
    main()
