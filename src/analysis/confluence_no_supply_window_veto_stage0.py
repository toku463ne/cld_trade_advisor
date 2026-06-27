"""Stage 0 — VALIDITY-WINDOW no_supply CANCELLATION on the confluence book.

Operator model (2026-06-27, refinement of project_confluence_no_supply_veto_nearmiss):
a confluence trigger has a validity window during which we WAIT for an entry.  If — while
still waiting, BEFORE any position is taken — a no_supply bar appears in that window, CANCEL
the trigger entirely (regardless of remaining validity days).  Survivors (no no_supply in the
window) are traded.  Compare surviving fires vs the FULL baseline.

TIMING CONSTRAINT (two-bar fill: decide on close, fill next open).  For a bar at F+k to cancel
BEFORE entry, you must not have entered by F+k.  So "cancel anywhere in the window" REQUIRES
deferring entry to after the window.  Two self-consistent reads, both reported:
  * surv@F+1  : survivors keep canonical entry (open[F+1]); veto scans whole window.
                NOT realizable (future bars keep an already-taken entry) — upper bound on selection.
  * surv@WE+1 : survivors enter at the open AFTER the window closes (open[WE+1]).
                Realizable / non-look-ahead (every veto bar is observed before this fill).

Validity window per trigger:
  * burst : consecutive days the confluence count stays >=3 from F (the natural "still valid" span)
  * L3/L5 : fixed (F, F+L]  windows (sensitivity)
Veto trigger in (F, WE]:  nosup = down bar & vmult<=0.7   |   lowvol = any bar vmult<=0.7.

Entry idx E => fill open[E+1], hold H.  Cohorts: BASELINE(all@F+1), VETOED(@F+1, the duds),
SURVIVORS@F+1 (UB), SURVIVORS@WE+1 (realizable).  Per-FY h10.  Read-only.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_no_supply_window_veto_stage0
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
_L_FIXED = [3, 5]
_V_DRY = 0.7
_VLOOK = 20
HORIZONS = [5, 10, 20]
WINSOR = 0.60
_FY_START_MONTH = 4


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


def _stats(a):
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "dr": 0.0}
    return {"n": int(a.size), "mean": float(np.mean(a) * 100),
            "median": float(np.median(a) * 100), "dr": float(np.mean(a > 0) * 100)}


def main():
    fires_by_stock = cbt._load_bullish_fires_by_stock()
    rows = []
    maxL = max(_L_FIXED) + 5
    with get_session() as s:
        for n, (code, fires) in enumerate(fires_by_stock.items()):
            if n % 50 == 0:
                logger.info("  {}/{}", n, len(fires_by_stock))
            sub = _load_one(s, code)
            if len(sub) < _VLOOK + max(HORIZONS) + maxL + 3:
                continue
            dates = list(sub["date"]); didx = {d: i for i, d in enumerate(dates)}
            o = sub["open"].to_numpy(); c = sub["close"].to_numpy()
            v = sub["vol"].to_numpy(); N = len(sub)
            ret1 = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            vavg = pd.Series(v).rolling(_VLOOK).mean().shift(1).to_numpy()
            vmult = v / np.where(vavg > 0, vavg, np.nan)
            nosup = (ret1 < 0) & (vmult <= _V_DRY)
            lowvol = (vmult <= _V_DRY)

            valid = [set() for _ in range(N)]
            for sign, fd in fires:
                fi = didx.get(pd.Timestamp(fd).normalize())
                if fi is None:
                    continue
                vb = _VALID_BARS.get(sign, 5)
                for j in range(fi, min(fi + vb + 1, N)):
                    valid[j].add(sign)
            cnt = np.array([len(s_) for s_ in valid])

            def fwd(E, hh):
                if E < 0 or E + 1 + hh >= N or E + 1 >= N:
                    return np.nan
                return c[E + 1 + hh] / o[E + 1] - 1.0

            last = -10_000
            for i in range(N):
                if cnt[i] < _N_GATE or i - last < _COOLDOWN:
                    continue
                last = i
                if i + maxL + 1 + max(HORIZONS) >= N:
                    continue
                # burst end = last consecutive day count>=3 from i
                be = i
                while be + 1 < N and cnt[be + 1] >= _N_GATE:
                    be += 1
                rec = {"code": code, "fy": _fy(dates[i]), "i": i}
                for hh in HORIZONS:
                    rec[f"fire_h{hh}"] = fwd(i, hh)
                windows = {"burst": be, "L3": min(i + 3, N - 1), "L5": min(i + 5, N - 1)}
                for wn, we in windows.items():
                    rec[f"{wn}_nosup"] = bool(nosup[i + 1:we + 1].any())
                    rec[f"{wn}_lowvol"] = bool(lowvol[i + 1:we + 1].any())
                    for hh in HORIZONS:
                        rec[f"{wn}_we_h{hh}"] = fwd(we, hh)   # deferred entry open[WE+1]
                rows.append(rec)
    df = pd.DataFrame(rows)
    logger.info("confluence triggers: {}  stocks: {}", len(df), df["code"].nunique())

    print(f"\n=== CONFLUENCE TRIGGERS: {len(df)}  stocks: {df['code'].nunique()} ===")
    print("\n--- BASELINE: all triggers @ canonical fire entry (open[F+1]) ---")
    base = {}
    print(f"{'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    for hh in HORIZONS:
        st = _stats(_w(df[f"fire_h{hh}"].to_numpy())); base[hh] = st
        print(f"{hh:>4} {st['n']:>6} {st['mean']:>8.2f} {st['median']:>8.2f} {st['dr']:>7.1f}")

    def _line(name, st, hh):
        return (f"{name:>22} {hh:>4} {st['n']:>6} {st['mean']:>8.2f} {st['median']:>8.2f} "
                f"{st['dr']:>7.1f} {st['mean']-base[hh]['mean']:>9.2f} {st['dr']-base[hh]['dr']:>8.1f}")

    for wn in ["burst", "L3", "L5"]:
        for trig in ["nosup", "lowvol"]:
            col = f"{wn}_{trig}"
            surv = ~df[col]
            vr = df[col].mean() * 100
            print(f"\n=== WINDOW={wn}  VETO={trig}  veto-rate={vr:.1f}%  "
                  f"retention={100-vr:.1f}% (n_surv={int(surv.sum())}) ===")
            print(f"{'cohort':>22} {'H':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
                  f"{'vs base m':>9} {'vs base DR':>8}")
            for hh in HORIZONS:
                # vetoed duds @ fire
                print(_line("VETOED@F+1", _stats(_w(df.loc[~surv, f"fire_h{hh}"].to_numpy())), hh))
                # survivors @ fire (look-ahead upper bound)
                print(_line("SURV@F+1(UB)", _stats(_w(df.loc[surv, f"fire_h{hh}"].to_numpy())), hh))
                # survivors @ window-end (realizable, non-look-ahead)
                print(_line("SURV@WE+1(real)", _stats(_w(df.loc[surv, f"{wn}_we_h{hh}"].to_numpy())), hh))
            # per-FY realizable @ H10
            line = []
            for fy in sorted(df["fy"].unique()):
                a = _w(df.loc[surv & (df["fy"] == fy), f"{wn}_we_h10"].to_numpy())
                a = a[np.isfinite(a)]
                line.append(f"FY{fy}:{np.mean(a)*100:+.1f}(n{int((surv&(df['fy']==fy)).sum())})"
                            if a.size else f"FY{fy}:na")
            print("  per-FY(SURV@WE+1, H10): " + "  ".join(line))


if __name__ == "__main__":
    main()
