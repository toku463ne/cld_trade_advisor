"""Stage 0 — VPA Volume-at-Price (VAP) high-volume-node support/resistance event study.

Idea (2026-06-27, from docs/books/dekidaka.md — Coulling VPA, Ch.9 "Volume At Price",
p.17/80-85): a price LEVEL where heavy volume has historically traded (a High-Volume Node /
POC) is the strongest future support/resistance — "much business done there, so it takes
substantial volume to break back through" — while thin nodes (LVN) get sliced through.

This is a DIFFERENT primitive from the two rejected VPA volume ideas
([[lowprice_volspike]] high-spike, [[no_supply]] dry-up, [[vol_breakout_confirm]] gate):
those keyed on the MAGNITUDE of a single bar's volume; VAP keys on WHERE in price volume
concentrated over history.  So it is not pre-falsified by the "loud up-thrust = exhaustion"
finding.

Causal rolling volume profile: as of bar T, bin the trailing PROFILE_W bars' volume by each
bar's typical price tp=(h+l+c)/3.  `vap_strength` at the current price = (volume that traded
within +/-BAND of close[T]) / (volume expected there if the window's volume were spread
uniformly across its price range).  >1 = HVN (heavy node), <1 = LVN (thin node).

Two event families (two-bar fill, enter open[T+1], exit close[T+h]):
  * SUPPORT  : close[T] < close[T-PB_LOOK]  (price pulled back DOWN into the level)
               VPA predicts HVN bounces -> HIGHER forward return than LVN.
  * RESIST   : close[T] > close[T-PB_LOOK]  (price rallied UP into the level)
               VPA predicts HVN caps   -> LOWER forward return than LVN.

Decisive numbers: the vap_strength QUARTILE monotonicity panel (h10) and the HVN-minus-LVN
spread.  Flat = VAP uninformative; right-signed monotone = real S/R; wrong-signed = inverted.
Baseline = all bars in the family (any node strength).  Per-FY h10 regime check.  Per-stock
COOLDOWN dedupe.  avg-turnover>=TURN_MIN liquidity floor.  Read-only.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.vap_node_sr_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- event params ---------------------------------------------------------
PROFILE_W = 120              # trailing bars in the volume profile (~6 months)
BAND = 0.025                 # +/- price band around current close = "the node"
PB_LOOK = 5                  # bars over which the approach (pullback/rally) is measured
VAP_HI_SWEEP = [1.5, 2.0, 3.0]   # HVN cohorts (heavy node)
VAP_LO = 0.7                 # LVN ceiling (thin node)
TURN_MIN = 30_000_000.0      # ¥ AVERAGE turnover floor (close*avg_vol)
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


def _vap_strength(tp: np.ndarray, vol: np.ndarray, px: float) -> float:
    """Volume within +/-BAND of px, relative to uniform-spread expectation (>1 = HVN)."""
    band_abs = BAND * px
    if band_abs <= 0:
        return np.nan
    rng = tp.max() - tp.min()
    if rng <= 2 * band_abs:            # window too tight to define nodes
        return np.nan
    local = vol[np.abs(tp - px) <= band_abs].sum()
    n_bands = rng / (2 * band_abs)
    expected = vol.sum() / n_bands
    if expected <= 0:
        return np.nan
    return float(local / expected)


def _analyze(fires: pd.DataFrame, family: str, support: bool) -> None:
    fires = _dedupe(fires).copy()
    fires["fy"] = fires["date"].apply(_fy)
    base = {h: _stats(_winsor(fires[f"fwd{h}"].to_numpy())) for h in HORIZONS}
    pred = ("HVN should BOUNCE -> higher fwd than LVN" if support
            else "HVN should CAP -> lower fwd than LVN")
    print(f"\n############ FAMILY: {family} ############")
    print(f"  VPA prediction: {pred}")
    print(f"  fires (deduped): {len(fires)}  stocks: {fires['code'].nunique()}")

    print("\n  --- BASELINE: all bars in family (any node strength) ---")
    print(f"  {'horizon':>8} {'n':>7} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    for h in HORIZONS:
        b = base[h]
        print(f"  {'h'+str(h):>8} {b['n']:>7} {b['mean']:>8.2f} "
              f"{b['median']:>8.2f} {b['dr']:>7.1f}")

    vm = fires["vap"].to_numpy()
    f10 = fires["fwd10"].to_numpy()
    ok = np.isfinite(vm) & np.isfinite(f10)
    vm, f10 = vm[ok], f10[ok]
    qs = np.quantile(vm, [0.25, 0.5, 0.75])
    print(f"\n  --- MONOTONICITY: bars by vap_strength quartile (h=10) ---")
    print(f"  vap cuts: {qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}")
    print(f"  {'bucket':>14} {'n':>7} {'mean%':>8} {'DR%':>7}")
    edges = [-np.inf, qs[0], qs[1], qs[2], np.inf]
    names = ["Q1 thin LVN", "Q2", "Q3", "Q4 heavy HVN"]
    for i, nm in enumerate(names):
        m = (vm > edges[i]) & (vm <= edges[i + 1])
        st = _stats(_winsor(f10[m]))
        print(f"  {nm:>14} {st['n']:>7} {st['mean']:>8.2f} {st['dr']:>7.1f}")

    def _cohort(mask: pd.Series, name: str) -> dict:
        ev = fires[mask]
        print(f"\n  --- COHORT: {name}  (n={len(ev)}, "
              f"{100*len(ev)/max(len(fires),1):.0f}%) ---")
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
    for k in VAP_HI_SWEEP:
        hi[k] = _cohort(fires["vap"] > k, f"HVN vap>{k:g}")
    lo = _cohort(fires["vap"] < VAP_LO, f"LVN vap<{VAP_LO:g}")

    print(f"\n  === NODE EDGE: HVN(>{VAP_HI_SWEEP[0]:g}) minus LVN(<{VAP_LO:g}) ===")
    print(f"  (support: positive = S/R real;  resistance: negative = S/R real)")
    print(f"  {'horizon':>8} {'dMean%':>8} {'dDR%':>7}")
    for h in HORIZONS:
        dM = hi[VAP_HI_SWEEP[0]][h]["mean"] - lo[h]["mean"]
        dDR = hi[VAP_HI_SWEEP[0]][h]["dr"] - lo[h]["dr"]
        print(f"  {'h'+str(h):>8} {dM:>8.2f} {dDR:>7.1f}")


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    sup_parts, res_parts = [], []
    min_len = PROFILE_W + max(HORIZONS) + PB_LOOK + 2
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
            h_ = sub["high"].to_numpy()
            lo_ = sub["low"].to_numpy()
            v = sub["vol"].to_numpy()
            tp = (h_ + lo_ + c) / 3.0
            vavg = sub["vol"].rolling(20).mean().shift(1).to_numpy()
            turn_avg = c * np.where(vavg > 0, vavg, np.nan)
            entry = np.concatenate([o[1:], [np.nan]])
            for hh in HORIZONS:
                exitc = np.concatenate([c[hh:], [np.nan] * hh])
                sub[f"fwd{hh}"] = exitc / entry - 1.0
            N = len(sub)
            vap = np.full(N, np.nan)
            for T in range(PROFILE_W, N - 1):       # need entry at T+1
                if not (turn_avg[T] >= TURN_MIN):
                    continue
                vap[T] = _vap_strength(tp[T - PROFILE_W:T], v[T - PROFILE_W:T], c[T])
            sub["vap"] = vap
            # approach direction over PB_LOOK bars
            prior = np.concatenate([[np.nan] * PB_LOOK, c[:-PB_LOOK]])
            valid = np.isfinite(vap)
            sup = sub[valid & (c < prior)]
            res = sub[valid & (c > prior)]
            if not sup.empty:
                sup_parts.append(sup)
            if not res.empty:
                res_parts.append(res)

    sup = pd.concat(sup_parts, ignore_index=True)
    res = pd.concat(res_parts, ignore_index=True)
    logger.info("support bars: {}  resistance bars: {}", len(sup), len(res))
    _analyze(sup, f"SUPPORT (pullback into node, close<close[-{PB_LOOK}])", support=True)
    _analyze(res, f"RESIST (rally into node, close>close[-{PB_LOOK}])", support=False)


if __name__ == "__main__":
    main()
