"""Stage 0 (follow-up) — low-price vol-spike *stage-confirmation* event study.

Operator request (2026-06-16): the previous study ([[project_lowprice_volspike_stage0_reject]])
rejected "buy AT the spike" — a cheap-name volume-up bar marks the move ENDING (pump-and-fade).
The NEW idea is different: do NOT buy at the spike.  Instead WAIT N days and only act on the
spikes that *really went to the next stage*, defined by two post-spike confirmations:

  1. Volume STAYED elevated   : mean(vol over the N days AFTER the spike)
                                  >  mean(vol over the N days BEFORE the spike)
  2. Price HELD the level      : min(low over the N days after the spike) >= low[T] (spike-day low)
                                  (price never gave back below the spike-day support)

Then ENTER at open[T+N+1] (strictly after the confirmation window — no look-ahead) and ask:
does this *confirmed* cohort actually continue (worth a long), or does the fade just arrive late?

Controls (the whole point — confirmation timing must be held constant):
  * BASELINE        : all low-price stock-days, entry open[T+1] (penny-drift null, as before).
  * SPIKE_ALL       : every spike, entered at the SAME late point open[T+N+1] (no confirmation).
                      Isolates: does the confirmation FILTER add anything over just waiting N days?
  * CONFIRM_ONLY    : low-price days passing the two confirmations with NO spike requirement.
                      Isolates: is any edge from the SPIKE, or just from "rising-vol + held level"?
  * SPIKE_CONFIRMED : spike AND both confirmations (the operator's cohort).
  * vol-only / price-only confirmation legs, to see which gate carries the effect.

A real "next-stage continuation" edge requires SPIKE_CONFIRMED to beat BOTH the baseline AND
the SPIKE_ALL / CONFIRM_ONLY controls, and to be regime-robust (per-FY), not a one-year beta.

Read-only Stage 0.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.lowprice_volspike_stage_confirm
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- params ---------------------------------------------------------------
PRICE_MAX = 1000.0           # ¥ adjusted close tier
V_LOOKBACK = 60              # trailing bars for spike vmult median
TURN_MIN = 30_000_000.0      # ¥ turnover floor on the spike bar
N_CONFIRM = [5, 10]          # post-spike confirmation window (bars)
HORIZONS = [5, 10, 20]       # forward holding bars, measured FROM the confirmed entry
WINSOR = 0.60                # forward-return clip
COOLDOWN = 20                # bars suppressed per stock after a spike (dedupe clusters)
UP_MIN = 0.03                # spike-bar min close-to-close return
VMULT_K = 5.0                # spike-bar vol / trailing-median multiple
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
    fwd = _winsor(fwd[np.isfinite(fwd)])
    if fwd.size == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "dr": 0.0}
    return {"n": int(fwd.size), "mean": float(np.mean(fwd) * 100),
            "median": float(np.median(fwd) * 100),
            "dr": float(np.mean(fwd > 0) * 100)}


def _dedupe(ev: pd.DataFrame) -> pd.DataFrame:
    """Greedy per-stock cooldown dedupe on spike date (cluster suppression)."""
    ev = ev.sort_values(["code", "date"])
    keep, last = [], {}
    for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
        prev = last.get(code)
        if prev is None or (d - prev).days > COOLDOWN * 7 / 5:
            keep.append(idx)
            last[code] = d
    return ev.loc[keep]


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    parts = []
    base_fwd = {h: [] for h in HORIZONS}
    min_len = V_LOOKBACK + max(N_CONFIRM) + max(HORIZONS) + 3
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
            lo = sub["low"].to_numpy()
            sub["ret1"] = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
            med = sub["vol"].rolling(V_LOOKBACK).median().shift(1).to_numpy()
            sub["vmult"] = v / np.where(med > 0, med, np.nan)
            sub["vmax_prev"] = sub["vol"].cummax().shift(1).to_numpy()
            sub["turn"] = c * v

            # baseline penny-drift forward returns (entry open[T+1])
            entry0 = np.concatenate([o[1:], [np.nan]])
            low_mask = (sub["close"] < PRICE_MAX) & sub["close"].notna()
            for h in HORIZONS:
                exitc = np.concatenate([c[h:], [np.nan] * h])
                fwd0 = exitc / entry0 - 1.0
                base_fwd[h].append(fwd0[low_mask.to_numpy()])

            # per-N confirmation columns + confirmed-entry forward returns
            vol_s = sub["vol"]
            low_s = sub["low"]
            for N in N_CONFIRM:
                pre_v = vol_s.rolling(N).mean().shift(1).to_numpy()           # mean vol [T-N..T-1]
                post_v = vol_s.rolling(N).mean().shift(-N).to_numpy()         # mean vol [T+1..T+N]
                post_min = low_s.rolling(N).min().shift(-N).to_numpy()        # min low  [T+1..T+N]
                sub[f"cv{N}"] = post_v > pre_v                               # volume stayed up
                sub[f"cp{N}"] = post_min >= lo                              # price held spike-day low
                entryN = np.concatenate([o[N + 1:], [np.nan] * (N + 1)])     # open[T+N+1]
                for h in HORIZONS:
                    shift = N + h
                    exitN = np.concatenate([c[shift:], [np.nan] * shift])    # close[T+N+h]
                    sub[f"f{N}_{h}"] = exitN / entryN - 1.0

            keep_cols = (["date", "code", "ret1", "vmult", "vmax_prev", "turn"]
                         + [f"cv{N}" for N in N_CONFIRM] + [f"cp{N}" for N in N_CONFIRM]
                         + [f"f{N}_{h}" for N in N_CONFIRM for h in HORIZONS])
            parts.append(sub.loc[low_mask, keep_cols].copy())

    lowp = pd.concat(parts, ignore_index=True)
    lowp["fy"] = lowp["date"].apply(_fy)
    base_fwd = {h: np.concatenate(base_fwd[h]) for h in HORIZONS}
    logger.info("low-price stock-days: {}", len(lowp))

    # ---- baseline ---------------------------------------------------------
    print(f"\n=== BASELINE: all low-price (<¥{PRICE_MAX:.0f}) stock-days, entry T+1 ===")
    print(f"{'horizon':>8} {'n':>9} {'mean%':>8} {'med%':>8} {'DR%':>7}")
    base = {}
    for h in HORIZONS:
        st = _stats(base_fwd[h])
        base[h] = st
        print(f"{'h'+str(h):>8} {st['n']:>9} {st['mean']:>8.2f} {st['median']:>8.2f} {st['dr']:>7.1f}")

    spike_v = (lowp["vmult"] >= VMULT_K) & (lowp["ret1"] >= UP_MIN)
    tradeable = lowp["turn"] >= TURN_MIN

    def _report(mask: pd.Series, N: int, name: str, ref: dict | None = None) -> dict:
        ev = _dedupe(lowp[mask].copy())
        print(f"\n--- {name}  (N={N}) ---")
        print(f"fires: {len(ev)}  stocks: {ev['code'].nunique()}")
        hdr = f"{'h':>4} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} {'exc_mean':>9} {'exc_DR':>7}"
        if ref is not None:
            hdr += f" {'vs_ref':>8}"
        print(hdr)
        out = {}
        for h in HORIZONS:
            st = _stats(ev[f"f{N}_{h}"].to_numpy())
            out[h] = st
            if st["n"] == 0:
                continue
            b = base[h]
            line = (f"{'h'+str(h):>4} {st['n']:>6} {st['mean']:>8.2f} {st['median']:>8.2f} "
                    f"{st['dr']:>7.1f} {st['mean']-b['mean']:>9.2f} {st['dr']-b['dr']:>7.1f}")
            if ref is not None:
                line += f" {st['mean']-ref[h]['mean']:>8.2f}"
            print(line)
        # per-FY at h=10
        col = f"f{N}_10"
        line = []
        for fy in sorted(ev["fy"].unique()):
            d = ev[ev["fy"] == fy][col].to_numpy()
            d = _winsor(d[np.isfinite(d)])
            if d.size:
                line.append(f"FY{fy}:{np.mean(d)*100:+.1f}(n{d.size})")
        print("  per-FY h10:  " + "  ".join(line))
        return out

    for N in N_CONFIRM:
        cv, cp = lowp[f"cv{N}"], lowp[f"cp{N}"]
        spike = spike_v & tradeable
        # confirmation rate among spikes
        sp = _dedupe(lowp[spike].copy())
        conf = sp[(sp[f"cv{N}"]) & (sp[f"cp{N}"])]
        print(f"\n========== N={N}  spike=vmult>={VMULT_K:g}&up>={UP_MIN:.0%} ==========")
        print(f"spikes(deduped)={len(sp)}  confirmed(vol&price)={len(conf)} "
              f"({100*len(conf)/max(len(sp),1):.0f}%)  "
              f"vol-only={int(sp[f'cv{N}'].sum())}  price-only={int(sp[f'cp{N}'].sum())}")
        spike_all = _report(spike, N, "SPIKE_ALL (entry T+N+1, no confirm)")
        _report(spike & cv & cp, N, "SPIKE_CONFIRMED (vol&price held)", ref=spike_all)
        _report(spike & cv, N, "spike + vol-only confirm", ref=spike_all)
        _report(spike & cp, N, "spike + price-only confirm", ref=spike_all)
        _report(tradeable & cv & cp & ~spike_v, N, "CONFIRM_ONLY (no spike)")


if __name__ == "__main__":
    main()
