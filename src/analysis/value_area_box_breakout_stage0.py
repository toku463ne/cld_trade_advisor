"""Stage 0 — value-area-box upside breakout event study (N225 daily).

Port of the `density_pullback` idea from cld_bittrade (BTC/JPY 1h, shipped) to JP daily
bars, tested under this project's rules.  See
docs/analysis/value_area_box_breakout_stage0_preregistration.md for the full pre-reg.

Primitive: a CAUSAL rolling volume-at-price profile over the trailing PROFILE_W bars yields
a value-area BOX [VAL, VAH] covering COVERAGE of traded mass (POC-centered).  Gate the box
TIGHT ((VAH-VAL)/close <= MAX_BAND_PCT = a consolidation) and require the prior BASE_MIN
closes to sit INSIDE it (a real base).  LONG trigger: close[T] crosses up through VAH
(close[T]>VAH and close[T-1]<=VAH).  Long-only (project is retail 6-slot, short sleeve
CLOSED).  Reuses the vap_node_sr_stage0 volume-profile machinery.

Two entry arms (two-bar fill; both exit at the SAME bar close[T+h] so only ENTRY differs):
  * market : entry open[T+1]                      -> ret_h = close[T+h]/open[T+1] - 1
  * retest : rest a limit at VAH, fill at VAH iff low touches VAH within LIMIT_WINDOW bars
             -> ret_h = close[T+h]/VAH - 1   (the density_pullback pullback fill)
             non-fills recorded with their would-be market return (adverse-selection probe).

Pre-registered gates (G1 freshness >=20% vs the 6 upside brk_* signs; G2 fresh-cohort edge
right-signed at h10 AND h20; G3 not-beta >=5/8 FY; G4 retest non-inferior + non-fills not
the winners).  Stage-0 PASS = G1 & G2 & G3.  Binding fill-order null is Stage-1, not here.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.value_area_box_breakout_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- params ---------------------------------------------------------------
N_BINS = 50
COVERAGE = 0.70
TURN_MIN = 30_000_000.0
HORIZONS = [1, 5, 10, 20]
WINSOR = 0.60
COOLDOWN = 20            # trading-day cooldown per stock
FRESH_DAYS = 2           # +/- trading-day window for "collides with an existing breakout"
_FY_START_MONTH = 4

# upside (bullish) breakout signs we must be ORTHOGONAL to (G1)
BRK_UP_SIGNS = ["brk_bol", "brk_floor", "brk_sma", "brk_wall",
                "brk_kumo_hi", "brk_tenkan_hi"]

# primary config + sweep
PRIMARY = dict(profile_w=60, max_band_pct=0.09, base_min=5, limit_window=6)
SWEEP_W = [40, 60, 90]
SWEEP_BAND = [0.06, 0.09, 0.12]


# ---- helpers (mirrors vap_node_sr_stage0) --------------------------------
def _codes() -> list[str]:
    """Canonical benchmarked universe — the ~222 codes the brk_* signs fire on.

    Restricting here (vs the 2,799-name expansion tier, which was REJECTED in
    universe_expansion_stage1) keeps G1 apples-to-apples: the new sign and the
    incumbent breakouts are measured on the same names the 6-slot book trades.
    """
    with get_session() as s:
        rows = s.execute(text(
            "SELECT DISTINCT stock_code FROM sign_benchmark_events ORDER BY stock_code"
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


def _value_area(tp: np.ndarray, vol: np.ndarray, coverage: float
                ) -> tuple[float, float, float]:
    """POC-centered value area [VAL, VAH] covering `coverage` of volume mass."""
    lo, hi = float(tp.min()), float(tp.max())
    if not (hi > lo):
        return (np.nan, np.nan, np.nan)
    edges = np.linspace(lo, hi, N_BINS + 1)
    idx = np.clip(np.digitize(tp, edges) - 1, 0, N_BINS - 1)
    w = np.bincount(idx, weights=vol, minlength=N_BINS)
    total = w.sum()
    if total <= 0:
        return (np.nan, np.nan, np.nan)
    centers = (edges[:-1] + edges[1:]) / 2.0
    poc = int(np.argmax(w))
    lo_i = hi_i = poc
    cum = w[poc]
    target = coverage * total
    while cum < target and (lo_i > 0 or hi_i < N_BINS - 1):
        left = w[lo_i - 1] if lo_i > 0 else -1.0
        right = w[hi_i + 1] if hi_i < N_BINS - 1 else -1.0
        if right >= left:
            hi_i += 1
            cum += w[hi_i]
        else:
            lo_i -= 1
            cum += w[lo_i]
    return centers[lo_i], centers[hi_i], centers[poc]


def _load_brk_events() -> dict[str, np.ndarray]:
    """{code: sorted np.array of fire-date ordinals} for the 6 upside breakout signs."""
    with get_session() as s:
        rows = s.execute(text(
            "SELECT e.stock_code, e.fired_at FROM sign_benchmark_events e "
            "JOIN sign_benchmark_runs r ON e.run_id = r.id "
            "WHERE r.sign_type = ANY(:sigs)"
        ), {"sigs": BRK_UP_SIGNS}).all()
    by_code: dict[str, list[int]] = {}
    for code, fired in rows:
        d = pd.Timestamp(fired).tz_localize(None).normalize()
        by_code.setdefault(code, []).append(d.toordinal())
    return {c: np.array(sorted(set(v))) for c, v in by_code.items()}


def _is_fresh(brk: dict[str, np.ndarray], code: str, ordinal: int) -> bool:
    arr = brk.get(code)
    if arr is None or arr.size == 0:
        return True
    j = np.searchsorted(arr, ordinal)
    for k in (j - 1, j):
        if 0 <= k < arr.size and abs(int(arr[k]) - ordinal) <= FRESH_DAYS * 7 / 5:
            return False
    return True


# ---- core fire extraction -------------------------------------------------
def _fires_for_stock(sub: pd.DataFrame, code: str, cfg: dict) -> list[dict]:
    o = sub["open"].to_numpy()
    h = sub["high"].to_numpy()
    lo = sub["low"].to_numpy()
    c = sub["close"].to_numpy()
    v = sub["vol"].to_numpy()
    dates = sub["date"].to_numpy()
    tp = (h + lo + c) / 3.0
    vavg = sub["vol"].rolling(20).mean().shift(1).to_numpy()
    turn_avg = c * np.where(vavg > 0, vavg, np.nan)
    N = len(sub)
    W = cfg["profile_w"]
    base_min = cfg["base_min"]
    band_pct = cfg["max_band_pct"]
    lw = cfg["limit_window"]
    hmax = max(HORIZONS)
    out: list[dict] = []
    for T in range(W, N - hmax - 1):
        if not (turn_avg[T] >= TURN_MIN):
            continue
        val, vah, _poc = _value_area(tp[T - W:T], v[T - W:T], COVERAGE)
        if not np.isfinite(vah):
            continue
        if (vah - val) / c[T] > band_pct:          # tightness gate
            continue
        base = c[T - base_min:T]                    # base gate: prior closes inside box
        if not np.all((base >= val) & (base <= vah)):
            continue
        if not (c[T] > vah and c[T - 1] <= vah):    # fresh upside cross of VAH
            continue
        rec = {"code": code, "date": pd.Timestamp(dates[T]),
               "ord": pd.Timestamp(dates[T]).toordinal()}
        # market arm: entry open[T+1], exit close[T+h]
        ent = o[T + 1]
        for hh in HORIZONS:
            rec[f"mkt{hh}"] = c[T + hh] / ent - 1.0 if ent > 0 else np.nan
        # retest arm: limit at VAH, fill iff low touches VAH within lw bars (T+1..T+lw)
        fill_j = None
        for j in range(T + 1, min(T + lw, N - hmax - 1) + 1):
            if lo[j] <= vah:
                fill_j = j
                break
        rec["filled"] = fill_j is not None
        for hh in HORIZONS:
            if fill_j is not None:
                rec[f"rt{hh}"] = c[T + hh] / vah - 1.0    # better price, same exit bar
            else:
                rec[f"rt{hh}"] = np.nan
        out.append(rec)
    return out


# ---- reporting ------------------------------------------------------------
def _panel(ev: pd.DataFrame, prefix: str, label: str, base: dict | None = None) -> dict:
    print(f"\n  --- {label}  (n={len(ev)}) ---")
    hdr = f"  {'horizon':>8} {'n':>7} {'mean%':>8} {'med%':>8} {'DR%':>7}"
    if base is not None:
        hdr += f" {'exMean':>8} {'exDR':>7}"
    print(hdr)
    out = {}
    for hh in HORIZONS:
        st = _stats(_winsor(ev[f"{prefix}{hh}"].to_numpy()))
        out[hh] = st
        line = (f"  {'h'+str(hh):>8} {st['n']:>7} {st['mean']:>8.2f} "
                f"{st['median']:>8.2f} {st['dr']:>7.1f}")
        if base is not None and base.get(hh, {}).get("n", 0):
            line += f" {st['mean']-base[hh]['mean']:>8.2f} {st['dr']-base[hh]['dr']:>7.1f}"
        print(line)
    return out


def _per_fy(ev: pd.DataFrame, prefix: str) -> None:
    ev = ev.copy()
    ev["fy"] = ev["date"].apply(_fy)
    parts = []
    pos = 0
    fys = sorted(ev["fy"].unique())
    for fy in fys:
        sf = _winsor(ev[ev["fy"] == fy][f"{prefix}10"].to_numpy())
        sf = sf[np.isfinite(sf)]
        m = float(np.mean(sf) * 100) if sf.size else 0.0
        if m > 0:
            pos += 1
        parts.append(f"FY{fy}:{m:+.1f}(n{sf.size})")
    print(f"    per-FY(h10 {prefix}): " + "  ".join(parts))
    print(f"    --> {pos}/{len(fys)} FY positive  (G3 needs >=5/8 and not up-years-only)")


def _report_primary(fires: pd.DataFrame, brk: dict) -> None:
    fires = _dedupe(fires).copy()
    fires["fresh"] = [_is_fresh(brk, c, o) for c, o in zip(fires["code"], fires["ord"])]
    n = len(fires)
    nfresh = int(fires["fresh"].sum())
    print("\n" + "=" * 78)
    print(f"PRIMARY CONFIG  {PRIMARY}")
    print("=" * 78)
    print(f"  total fires (deduped): {n}   stocks: {fires['code'].nunique()}")

    base = _panel(fires, "mkt", "BASELINE — all fires, MARKET arm")
    _per_fy(fires, "mkt")

    # ---- G1 orthogonality ----
    print("\n" + "-" * 78)
    print(f"G1 ORTHOGONALITY  (fresh = no upside brk_* within +/-{FRESH_DAYS} bars)")
    print("-" * 78)
    fr = 100.0 * nfresh / max(n, 1)
    print(f"  fresh: {nfresh}/{n} = {fr:.1f}%   "
          f"{'PASS' if fr >= 20 else 'FAIL'} (gate >=20%)")
    fresh_ev = fires[fires["fresh"]]
    co_ev = fires[~fires["fresh"]]

    # ---- G2 fresh-cohort edge ----
    print("\n" + "-" * 78)
    print("G2 FRESH-COHORT EDGE  (market arm; excess vs baseline)")
    print("-" * 78)
    fp = _panel(fresh_ev, "mkt", "FRESH cohort", base=base)
    _panel(co_ev, "mkt", "CO-FIRED cohort (collinear w/ existing breakouts)", base=base)
    g2 = (fp[10]["mean"] > 0 and fp[20]["mean"] > 0 and
          fp[10]["dr"] > base[10]["dr"] and fp[20]["dr"] > base[20]["dr"])
    print(f"\n  G2 {'PASS' if g2 else 'FAIL'}: fresh mean_r>0 @h10&h20 AND DR>baseline @h10&h20")
    print("  (orthogonality necessary NOT sufficient — accum_volume lesson)")
    _per_fy(fresh_ev, "mkt")

    # ---- G4 retest vs market ----
    print("\n" + "-" * 78)
    print("G4 RETEST vs MARKET  (does the density_pullback pullback fill earn its keep?)")
    print("-" * 78)
    filled = fires[fires["filled"]]
    nofill = fires[~fires["filled"]]
    print(f"  retest fill-rate: {len(filled)}/{len(fires)} = "
          f"{100*len(filled)/max(len(fires),1):.0f}%   "
          f"(non-fills: {len(nofill)})")
    _panel(filled, "rt", "RETEST arm (filled at VAH)")
    _panel(filled, "mkt", "  same trades, MARKET arm (entry open[T+1])")
    print("\n  Adverse-selection probe — would-be MARKET return of the NON-filled fires:")
    _panel(nofill, "mkt", "NON-FILL fires (skipped by the limit)")
    print("  (if NON-FILL >= FILLED market return -> retest skips winners = limit_entry reject)")


def _report_sweep(by_cfg: dict[tuple, pd.DataFrame], brk: dict) -> None:
    print("\n" + "=" * 78)
    print("SWEEP  (profile_w x max_band_pct; market arm @h10, deduped)")
    print("=" * 78)
    print(f"  {'profile_w':>9} {'band':>6} {'n':>6} {'fresh%':>7} "
          f"{'mkt_h10':>8} {'DR':>6} {'fresh_h10':>10} {'fDR':>6}")
    for (w, band), fires in sorted(by_cfg.items()):
        f = _dedupe(fires).copy()
        if f.empty:
            print(f"  {w:>9} {band:>6.2f} {0:>6}")
            continue
        f["fresh"] = [_is_fresh(brk, c, o) for c, o in zip(f["code"], f["ord"])]
        st = _stats(_winsor(f["mkt10"].to_numpy()))
        fe = f[f["fresh"]]
        fst = _stats(_winsor(fe["mkt10"].to_numpy()))
        fr = 100.0 * len(fe) / max(len(f), 1)
        print(f"  {w:>9} {band:>6.2f} {len(f):>6} {fr:>7.1f} "
              f"{st['mean']:>8.2f} {st['dr']:>6.1f} {fst['mean']:>10.2f} {fst['dr']:>6.1f}")


def main() -> None:
    codes = _codes()
    logger.info("loading upside-breakout fire events for G1 ...")
    brk = _load_brk_events()
    logger.info("  brk events for {} codes", len(brk))

    cfgs = {(w, b): dict(profile_w=w, max_band_pct=b,
                         base_min=PRIMARY["base_min"], limit_window=PRIMARY["limit_window"])
            for w in SWEEP_W for b in SWEEP_BAND}
    parts: dict[tuple, list] = {k: [] for k in cfgs}
    primary_key = (PRIMARY["profile_w"], PRIMARY["max_band_pct"])

    logger.info("streaming {} stocks ...", len(codes))
    min_len = max(SWEEP_W) + max(HORIZONS) + 5
    with get_session() as s:
        for n, code in enumerate(codes):
            if n % 300 == 0:
                logger.info("  {}/{}", n, len(codes))
            sub = _load_one(s, code)
            if len(sub) < min_len:
                continue
            for key, cfg in cfgs.items():
                fr = _fires_for_stock(sub, code, cfg)
                if fr:
                    parts[key].extend(fr)

    by_cfg = {k: (pd.DataFrame(v) if v else pd.DataFrame()) for k, v in parts.items()}
    logger.info("done. primary fires (raw): {}", len(by_cfg[primary_key]))

    _report_sweep(by_cfg, brk)
    prim = by_cfg[primary_key]
    if prim.empty:
        print("\nNO PRIMARY FIRES — gate too tight.")
        return
    _report_primary(prim, brk)


if __name__ == "__main__":
    main()
