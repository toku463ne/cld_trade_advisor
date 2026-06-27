"""Stage 0 — use the CONFLUENCE fire as the "uptrend" context, then time the entry on a
VPA no_supply (low-volume dry-up) pullback instead of the canonical two-bar-open fill.

Operator idea (2026-06-27): the no_supply Stage-0 rejects defined "uptrend" mechanically
(SMA stack / ADX) and found pullback-bar volume uninformative.  Reframe: let the repo's
VALIDATED bullish detector — a >=3-bullish-sign confluence fire — BE the uptrend context,
and use no_supply only to pick a better ENTRY within that confirmed-bullish window.

This is an entry-TIMING modifier on the confluence book.  Two prior rejects bound it:
  * Limit/stop-entry REJECT — waiting for a cheaper/pullback entry SKIPS WINNERS (the names
    that pull back to fill you are adversely selected; non-fills +3.94%).
  * Adverse-move Stage-0 — late/adverse entries within the confluence window are NOT worse
    (≈ neutral), and a big down bar at entry is BEST not worst.
So the binding question is ADVERSE SELECTION: are the fires that do NOT print a no_supply
pullback the winners we'd skip/delay?  Plus: does entering at the no_supply bar actually
beat entering at the fire (same holding length)?

Reconstructs confluence fires from the dev-DB benchmark events (validity-windowed >=3 of
_BULLISH_SIGNS, 10-bar cooldown) exactly like confluence_strategy_backtest._candidates_for_stock,
then on raw daily bars: enter idx E => fill open[E+1], exit close[E+1+H].
  * baseline (canonical): E = F (fire)
  * delay policy        : E = first no_supply bar in (F, F+K], else F (never skip)
  * skip  policy        : only fires WITH a no_supply pullback, E = that bar
no_supply bar = down bar & vmult<=V_DRY  (variant B also: close_pos>=0.5 & lwick>=0.25).

Decisive: delay/skip vs baseline (mean/DR/median, per-FY) AND the WITH-minus-WITHOUT
canonical split (adverse selection).  Per-fire only; if it shows an edge, the binding test
is the paired fill-order null on the 6-slot book.  Read-only.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_no_supply_entry_stage0
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.bullish_confluence_v2_probe import _BULLISH_SIGNS, _VALID_BARS
from src.data.db import get_session

_N_GATE = 3
_COOLDOWN = 10               # confluence re-entry cooldown (cbt._COOLDOWN_BARS)
K = 5                        # bars after the fire to look for a no_supply pullback
V_DRY = 0.7                  # dry-up ceiling (vol / SMA(vol,20))
V_LOOKBACK = 20
CLOSE_POS = 0.5
LWICK = 0.25
HORIZONS = [5, 10, 20]       # forward holding bars from each entry
WINSOR = 0.60
_FY_START_MONTH = 4


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


def _fy(d) -> int:
    return d.year if d.month >= _FY_START_MONTH else d.year - 1


def _winsor(a: np.ndarray) -> np.ndarray:
    return np.clip(a, -WINSOR, WINSOR)


def _stats(a: np.ndarray) -> dict:
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "dr": 0.0}
    return {"n": int(a.size), "mean": float(np.mean(a) * 100),
            "median": float(np.median(a) * 100), "dr": float(np.mean(a > 0) * 100)}


def main() -> None:
    logger.info("loading bullish fires from dev DB ...")
    fires_by_stock = cbt._load_bullish_fires_by_stock()
    logger.info("stocks with bullish fires: {}", len(fires_by_stock))

    rows = []  # one per confluence fire
    with get_session() as s:
        for n, (code, fires) in enumerate(fires_by_stock.items()):
            if n % 50 == 0:
                logger.info("  {}/{}", n, len(fires_by_stock))
            sub = _load_one(s, code)
            if len(sub) < V_LOOKBACK + max(HORIZONS) + K + 3:
                continue
            dates = list(sub["date"])
            didx = {d: i for i, d in enumerate(dates)}
            o = sub["open"].to_numpy(); h = sub["high"].to_numpy()
            lo = sub["low"].to_numpy(); c = sub["close"].to_numpy()
            v = sub["vol"].to_numpy()
            N = len(sub)
            rng = h - lo
            with np.errstate(divide="ignore", invalid="ignore"):
                close_pos = np.where(rng > 0, (c - lo) / rng, np.nan)
                lwick = np.where(rng > 0, (np.minimum(o, c) - lo) / rng, np.nan)
            ret1 = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            vavg = pd.Series(v).rolling(V_LOOKBACK).mean().shift(1).to_numpy()
            vmult = v / np.where(vavg > 0, vavg, np.nan)
            is_nosup_A = (ret1 < 0) & (vmult <= V_DRY)
            is_nosup_B = is_nosup_A & (close_pos >= CLOSE_POS) & (lwick >= LWICK)

            # validity-windowed confluence count per trading-day index
            valid = [set() for _ in range(N)]
            for sign, fd in fires:
                fd = fd if not hasattr(fd, "date") else fd
                # normalize to midnight Timestamp to match `dates`
                key = pd.Timestamp(fd).normalize()
                fi = didx.get(key)
                if fi is None:
                    continue
                vb = _VALID_BARS.get(sign, 5)
                for j in range(fi, min(fi + vb + 1, N)):
                    valid[j].add(sign)

            def fwd(E: int, hh: int) -> float:
                if E + 1 + hh >= N or E + 1 >= N:
                    return np.nan
                return c[E + 1 + hh] / o[E + 1] - 1.0

            last_fire = -10_000
            for i in range(N):
                if len(valid[i]) < _N_GATE:
                    continue
                if i - last_fire < _COOLDOWN:
                    continue
                last_fire = i
                if i + 1 + max(HORIZONS) >= N:
                    continue
                # find first no_supply bar in (i, i+K]
                nsA = nsB = -1
                for j in range(i + 1, min(i + K + 1, N)):
                    if nsA < 0 and is_nosup_A[j]:
                        nsA = j
                    if nsB < 0 and is_nosup_B[j]:
                        nsB = j
                rec = {"code": code, "fy": _fy(dates[i]), "fire": i,
                       "nsA": nsA, "nsB": nsB}
                for hh in HORIZONS:
                    rec[f"fire_h{hh}"] = fwd(i, hh)
                    rec[f"nsA_h{hh}"] = fwd(nsA, hh) if nsA >= 0 else np.nan
                    rec[f"nsB_h{hh}"] = fwd(nsB, hh) if nsB >= 0 else np.nan
                rows.append(rec)

    df = pd.DataFrame(rows)
    logger.info("confluence fires: {}  stocks: {}", len(df), df["code"].nunique())
    covA = (df["nsA"] >= 0).mean() * 100
    covB = (df["nsB"] >= 0).mean() * 100
    print(f"\n=== CONFLUENCE FIRES: {len(df)}  stocks: {df['code'].nunique()} ===")
    print(f"coverage: no_supply(A dry) within {K} bars = {covA:.1f}%  |  "
          f"no_supply(B dry+shape) = {covB:.1f}%")

    # ---- baseline: canonical fire entry --------------------------------
    print("\n=== BASELINE: canonical fire entry (enter open[F+1]) ===")
    print(f"{'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    base = {}
    for hh in HORIZONS:
        st = _stats(_winsor(df[f"fire_h{hh}"].to_numpy()))
        base[hh] = st
        print(f"{hh:>4} {st['n']:>6} {st['mean']:>8.2f} {st['median']:>8.2f} {st['dr']:>7.1f}")

    # ---- adverse selection: WITH vs WITHOUT a no_supply pullback --------
    for tag, col in [("A dry", "nsA"), ("B dry+shape", "nsB")]:
        withm = df[col] >= 0
        print(f"\n=== ADVERSE SELECTION [{tag}]: canonical fire return, "
              f"fires WITH vs WITHOUT a no_supply pullback ===")
        print(f"{'cohort':>16} {'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7}")
        for name, mask in [("WITH pullback", withm), ("WITHOUT (skip)", ~withm)]:
            for hh in HORIZONS:
                st = _stats(_winsor(df.loc[mask, f"fire_h{hh}"].to_numpy()))
                print(f"{name:>16} {hh:>4} {st['n']:>6} {st['mean']:>8.2f} "
                      f"{st['median']:>8.2f} {st['dr']:>7.1f}")

    # ---- policy comparisons vs baseline --------------------------------
    def _policy(col: str, tag: str) -> None:
        print(f"\n=== POLICY [{tag}] ===")
        print(f"{'policy':>22} {'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'vs base mean':>12} {'vs base DR':>10}")
        for hh in HORIZONS:
            # delay: use no_supply entry if present else fire (never skip)
            delay = np.where(df[col] >= 0, df[f"{col}_h{hh}"], df[f"fire_h{hh}"])
            # skip: only fires with a pullback, entered at the no_supply bar
            skip = df.loc[df[col] >= 0, f"{col}_h{hh}"].to_numpy()
            for pname, arr in [("delay (NS or fire)", delay), ("skip (NS-only)", skip)]:
                st = _stats(_winsor(np.asarray(arr, dtype=float)))
                print(f"{pname:>22} {hh:>4} {st['n']:>6} {st['mean']:>8.2f} "
                      f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                      f"{st['mean']-base[hh]['mean']:>12.2f} {st['dr']-base[hh]['dr']:>10.1f}")
        # per-FY for the delay policy at H10
        print(f"  per-FY (delay, H10 mean% / n):")
        line = []
        for fy in sorted(df["fy"].unique()):
            sub = df[df["fy"] == fy]
            d = np.where(sub[col] >= 0, sub[f"{col}_h10"], sub["fire_h10"])
            d = _winsor(np.asarray(d, dtype=float))
            d = d[np.isfinite(d)]
            line.append(f"FY{fy}:{np.mean(d)*100:+.1f}(n{len(sub)})" if d.size else f"FY{fy}:na")
        print("    " + "  ".join(line))

    _policy("nsA", "A dry vmult<=0.7")
    _policy("nsB", "B dry+shape")


if __name__ == "__main__":
    main()
