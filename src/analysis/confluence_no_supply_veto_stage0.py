"""Stage 0 — post-trigger no_supply / low-volume VETO (delayed confirmation) on the
confluence book.

Operator idea (2026-06-27, follow-up to project_confluence_no_supply_entry_reject): the
prior test used a no_supply pullback to TIME the entry (enter AT the dip) — adversely
selective.  This is the INVERSE: a confluence trigger that is FOLLOWED by no_supply / low
volume (no demand follow-through) in its validity days is a WEAK setup → SKIP it.

The prior study already showed the signal exists: fires followed by a no_supply bar within
5 bars were the duds (h10 −0.05/DR47.5) vs not-followed winners (+1.52/DR57.2).  But under
the two-bar fill we enter at open[F+1] BEFORE we can see the post-trigger bars, so a pure
veto is not realizable.  The realizable form is DELAYED CONFIRMATION:

    at fire F, do NOT enter; wait W bars; if a no_supply (or low-volume) bar appeared in
    (F, F+W], SKIP the trigger; else ENTER at open[F+W+1] and hold H.

This is fully causal (decision at F+W uses only data through F+W).  Its COST is that the
winners — which never pull back — are entered W bars LATE.  Net verdict = does dropping the
duds outweigh entering the winners late?

Arms reported per veto-trigger × W:
  * baseline      : ALL fires entered at fire (open[F+1])           — production
  * survive@fire  : survivors entered at fire (LOOK-AHEAD upper bound, not realizable)
  * survive@F+W   : survivors entered at open[F+W+1] (REALIZABLE confirmation entry)
Veto triggers: no_supply (down & vmult<=V_DRY) | low_vol (any bar vmult<=V_DRY).

Per-fire only; if survive@F+W beats baseline with decent retention, the binding test is the
paired fill-order null on the 6-slot book.  Read-only.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_no_supply_veto_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.bullish_confluence_v2_probe import _BULLISH_SIGNS, _VALID_BARS
from src.data.db import get_session

_N_GATE = 3
_COOLDOWN = 10
W_SWEEP = [2, 3, 5]          # confirmation-wait windows
V_DRY = 0.7
V_LOOKBACK = 20
HORIZONS = [5, 10, 20]
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
    rows = []
    maxW = max(W_SWEEP)
    with get_session() as s:
        for n, (code, fires) in enumerate(fires_by_stock.items()):
            if n % 50 == 0:
                logger.info("  {}/{}", n, len(fires_by_stock))
            sub = _load_one(s, code)
            if len(sub) < V_LOOKBACK + max(HORIZONS) + maxW + 3:
                continue
            dates = list(sub["date"])
            didx = {d: i for i, d in enumerate(dates)}
            o = sub["open"].to_numpy(); h = sub["high"].to_numpy()
            lo = sub["low"].to_numpy(); c = sub["close"].to_numpy()
            v = sub["vol"].to_numpy()
            N = len(sub)
            ret1 = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            vavg = pd.Series(v).rolling(V_LOOKBACK).mean().shift(1).to_numpy()
            vmult = v / np.where(vavg > 0, vavg, np.nan)
            low_vol = vmult <= V_DRY
            no_sup = low_vol & (ret1 < 0)

            valid = [set() for _ in range(N)]
            for sign, fd in fires:
                fi = didx.get(pd.Timestamp(fd).normalize())
                if fi is None:
                    continue
                vb = _VALID_BARS.get(sign, 5)
                for j in range(fi, min(fi + vb + 1, N)):
                    valid[j].add(sign)

            def fwd(E: int, hh: int) -> float:
                if E < 0 or E + 1 + hh >= N or E + 1 >= N:
                    return np.nan
                return c[E + 1 + hh] / o[E + 1] - 1.0

            last_fire = -10_000
            for i in range(N):
                if len(valid[i]) < _N_GATE or i - last_fire < _COOLDOWN:
                    continue
                last_fire = i
                if i + maxW + 1 + max(HORIZONS) >= N:
                    continue
                rec = {"code": code, "fy": _fy(dates[i]), "fire": i}
                for hh in HORIZONS:
                    rec[f"fire_h{hh}"] = fwd(i, hh)
                for W in W_SWEEP:
                    win = range(i + 1, min(i + W + 1, N))
                    rec[f"nosup_W{W}"] = bool(any(no_sup[j] for j in win))
                    rec[f"lowvol_W{W}"] = bool(any(low_vol[j] for j in win))
                    for hh in HORIZONS:
                        rec[f"confW{W}_h{hh}"] = fwd(i + W, hh)   # enter open[F+W+1]
                rows.append(rec)

    df = pd.DataFrame(rows)
    logger.info("confluence fires: {}  stocks: {}", len(df), df["code"].nunique())
    print(f"\n=== CONFLUENCE FIRES: {len(df)}  stocks: {df['code'].nunique()} ===")

    print("\n=== BASELINE: ALL fires entered at fire (open[F+1]) ===")
    base = {}
    print(f"{'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    for hh in HORIZONS:
        st = _stats(_winsor(df[f"fire_h{hh}"].to_numpy()))
        base[hh] = st
        print(f"{hh:>4} {st['n']:>6} {st['mean']:>8.2f} {st['median']:>8.2f} {st['dr']:>7.1f}")

    for trig in ["nosup", "lowvol"]:
        for W in W_SWEEP:
            col = f"{trig}_W{W}"
            survive = ~df[col]                      # no veto trigger in window -> keep
            ret = df[col].mean() * 100
            print(f"\n=== VETO [{trig}, W={W}]  veto-rate={ret:.1f}%  "
                  f"retention={100-ret:.1f}% (n_survive={int(survive.sum())}) ===")
            print(f"{'arm':>22} {'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
                  f"{'vs base mean':>12} {'vs base DR':>10}")
            for hh in HORIZONS:
                # survivors entered at fire (look-ahead upper bound)
                sa = _stats(_winsor(df.loc[survive, f"fire_h{hh}"].to_numpy()))
                # survivors entered at F+W (realizable confirmation entry)
                sb = _stats(_winsor(df.loc[survive, f"confW{W}_h{hh}"].to_numpy()))
                print(f"{'survive@fire(LA)':>22} {hh:>4} {sa['n']:>6} {sa['mean']:>8.2f} "
                      f"{sa['median']:>8.2f} {sa['dr']:>7.1f} "
                      f"{sa['mean']-base[hh]['mean']:>12.2f} {sa['dr']-base[hh]['dr']:>10.1f}")
                print(f"{'survive@F+W(real)':>22} {hh:>4} {sb['n']:>6} {sb['mean']:>8.2f} "
                      f"{sb['median']:>8.2f} {sb['dr']:>7.1f} "
                      f"{sb['mean']-base[hh]['mean']:>12.2f} {sb['dr']-base[hh]['dr']:>10.1f}")
            # per-FY realizable survive@F+W at H10
            line = []
            for fy in sorted(df["fy"].unique()):
                sub = df[(df["fy"] == fy) & survive]
                a = _winsor(sub[f"confW{W}_h10"].to_numpy())
                a = a[np.isfinite(a)]
                line.append(f"FY{fy}:{np.mean(a)*100:+.1f}(n{len(sub)})" if a.size else f"FY{fy}:na")
            print("  per-FY(survive@F+W, H10): " + "  ".join(line))


if __name__ == "__main__":
    main()
