"""A2 — PER-STOCK zigzag-reachability persistence (premise test for a per-stock TP/SL weight).

Operator idea (2026-07-18): instead of one global ZsTpSl(2/2/0.3), learn a PER-STOCK
"weight to the zigzag" from the last ~year — analogous to per_stock_sign_quality.

Framing (see the CLAUDE.md TP/SL note): ZsTpSl's band is ALREADY per-stock &
per-time — it's an EWA of that stock's recent zigzag legs.  So per-stock VOLATILITY
is captured.  The ONLY thing a per-stock multiplier can add is per-stock GEOMETRY /
REACHABILITY: does THIS stock tend to travel far in band-units before reversing
(deserves a wider tp_mult), or does it reverse near 1x (deserves a tighter one)?

This is the per-stock analog of [[project_per_stock_sign_quality_reject]] — whose
PREMISE passed (rare) yet still REJECT'd at the fill-order null.  So before spending
any null budget, test the PREMISE the same cheap way:

    Does a stock's TRAILING reachability predict its FORWARD reachability, ABOVE the
    global/calendar effect the EWA band already captures?

Per fire (every bullish-sign fire, pooled — TP/SL is sign-agnostic), fill open[F+1]:
  band  = EWA(alpha=0.3) of the stock's trailing zigzag legs CONFIRMED by fire day
          (>= _ZZ_SIZE bars before F → causal; mirrors crud._build_zs_legs), >=3 legs.
  Over the next H bars (H=40 = the exit window; also 20):
    r_fav   = MFE / band   (max favorable excursion, band units)  -> TP side
    r_adv   = MAE / band   (max adverse excursion,  band units)  -> SL side
    tp_first= 1 if price reaches +2*band (TP) strictly BEFORE -2*band (SL), else 0
              (NaN if neither within H) -> the ACTUAL ZsTpSl(2/2) outcome, path-based.

Look-ahead-safe persistence (per stock, event-level OOS):
  * residualize each fire's value within its year-month across ALL stocks -> strips
    the calendar/market-vol regime the band already rides; what's left is the
    stock-specific component (the only thing a per-stock weight can harvest).
  * walk each stock in time order; from the (M+1)-th fire on, trail = mean residual
    of PRIOR fires; pair (trail, this fire).  Spearman + Q1..Q4 table + per-FY spread.
  * RAW (non-residual) shown for contrast: raw>0 but residual flat => the apparent
    per-stock signal is just the global/calendar effect (nothing to harvest).

Decisive: residualized Spearman > 0 with a monotone Q1<Q4 forward table holding across
FYs, on r_fav and/or tp_first.  Flat/inverted => the EWA band already captures per-stock
scale; a per-stock tp_mult tilt has no persistent target => DOA before the fill-order null.

Per-fire only; read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.per_stock_reachability_stage0
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats as sstats
from sqlalchemy import text

import src.analysis.confluence_strategy_backtest as cbt
from src.data.db import get_session
from src.indicators.zigzag import detect_peaks

HORIZONS = [20, 40]
ALPHA = 0.3
TP_MULT = 2.0
SL_MULT = 2.0
ZZ_SIZE = 5           # matches crud._ZZ_SIZE
ZZ_MIDDLE = 2         # matches crud._ZZ_MIDDLE
ZS_LOOKBACK = 16      # matches crud._ZS_LOOKBACK
MIN_LEGS = 3          # ZsTpSl.min_legs
MIN_PRIOR = 3         # >= this many prior fires on a stock before forming a trail
MIN_YM_GROUP = 8      # min events in a year-month cell to trust the residual mean
WINSOR_FAV = 8.0      # clip band-unit excursions (fat right tail)
_FY_START_MONTH = 4

_CACHE = ("/tmp/claude-1000/-home-ubuntu-cld-trade-advisor/"
          "c170f00b-8659-4fa0-aafe-8f053b6ee1a3/scratchpad/per_stock_reach_events.pkl")


def _load_one(s, code: str) -> pd.DataFrame:
    rows = s.execute(text(
        "SELECT ts, open_price::float8, high_price::float8, low_price::float8, "
        "close_price::float8 FROM ohlcv_1d WHERE stock_code=:c ORDER BY ts"
    ), {"c": code}).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["ts"]).dt.tz_localize(None).dt.normalize()
    g = df.groupby("date", sort=True)
    return g.agg(open=("open", "first"), high=("high", "max"),
                 low=("low", "min"), close=("close", "last")).reset_index()


def _fy(ts: pd.Timestamp) -> int:
    return ts.year if ts.month >= _FY_START_MONTH else ts.year - 1


def _ewa(legs: list[float]) -> float:
    ewa = legs[0]
    for leg in legs[1:]:
        ewa = ALPHA * leg + (1.0 - ALPHA) * ewa
    return ewa


def _leg_series(highs: np.ndarray, lows: np.ndarray) -> list[tuple[int, float]]:
    """Return (confirm_bar_index, leg_size) list; confirm_bar_index = the LATER pivot's bar."""
    peaks = detect_peaks(list(highs), list(lows), size=ZZ_SIZE, middle_size=ZZ_MIDDLE)
    peaks = sorted(peaks, key=lambda p: p.bar_index)
    legs: list[tuple[int, float]] = []
    for a, b in zip(peaks[:-1], peaks[1:]):
        legs.append((b.bar_index, abs(b.price - a.price)))
    return legs


def _band_at(legs: list[tuple[int, float]], fi: int) -> float | None:
    """EWA band from legs CONFIRMED >= ZZ_SIZE bars before fire fi (causal)."""
    avail = [sz for (ci, sz) in legs if ci <= fi - ZZ_SIZE and sz > 0]
    if len(avail) < MIN_LEGS:
        return None
    return _ewa(avail[-ZS_LOOKBACK:])


def _build_events() -> pd.DataFrame:
    fires_by_stock = cbt._load_bullish_fires_by_stock()
    logger.info("stocks with bullish fires: {}", len(fires_by_stock))
    recs = []
    with get_session() as s:
        for n, (code, fires) in enumerate(fires_by_stock.items()):
            if n % 50 == 0:
                logger.info("  {}/{}", n, len(fires_by_stock))
            sub = _load_one(s, code)
            if len(sub) < max(HORIZONS) + ZS_LOOKBACK + ZZ_SIZE + 3:
                continue
            dates = list(sub["date"])
            didx = {d: i for i, d in enumerate(dates)}
            o = sub["open"].to_numpy(); h = sub["high"].to_numpy(); low = sub["low"].to_numpy()
            N = len(sub)
            legs = _leg_series(h, low)
            seen: set[pd.Timestamp] = set()
            for _sign, fd in fires:            # pooled across signs (TP/SL is sign-agnostic)
                key = pd.Timestamp(fd).normalize()
                if key in seen:
                    continue
                seen.add(key)
                fi = didx.get(key)
                if fi is None or fi + 1 + max(HORIZONS) >= N:
                    continue
                band = _band_at(legs, fi)
                if band is None or band <= 0:
                    continue
                entry = o[fi + 1]
                rec = {"code": code, "date": key, "fy": _fy(key),
                       "ym": f"{key.year}-{key.month:02d}", "band_pct": band / entry}
                for hh in HORIZONS:
                    win_h = h[fi + 1: fi + 1 + hh + 1]
                    win_l = low[fi + 1: fi + 1 + hh + 1]
                    mfe = float(win_h.max() - entry)
                    mae = float(entry - win_l.min())
                    rec[f"rfav{hh}"] = min(max(mfe / band, 0.0), WINSOR_FAV)
                    rec[f"radv{hh}"] = min(max(mae / band, 0.0), WINSOR_FAV)
                    # path-based tp_first: which 2*band level is touched first
                    tp_lvl = entry + TP_MULT * band
                    sl_lvl = entry - SL_MULT * band
                    tp_first = np.nan
                    for hi_, lo_ in zip(win_h, win_l):
                        hit_tp = hi_ >= tp_lvl
                        hit_sl = lo_ <= sl_lvl
                        if hit_tp and hit_sl:
                            tp_first = 0.0   # both same bar -> conservative (SL-first, matches sim risk)
                            break
                        if hit_tp:
                            tp_first = 1.0; break
                        if hit_sl:
                            tp_first = 0.0; break
                    rec[f"tpf{hh}"] = tp_first
                recs.append(rec)
    df = pd.DataFrame(recs)
    # residualize within year-month across ALL stocks
    for hh in HORIZONS:
        for base in [f"rfav{hh}", f"radv{hh}", f"tpf{hh}"]:
            grp = df.groupby("ym")[base]
            gmean = grp.transform("mean")
            gsize = grp.transform("size")
            df[f"res_{base}"] = (df[base] - gmean).where(gsize >= MIN_YM_GROUP, np.nan)
    return df


def _persistence(df: pd.DataFrame, col: str, label: str, pct: bool = True) -> None:
    """Walk each stock in time order; trail mean of `col` (prior fires) vs this fire."""
    pairs = []
    for code, g in df.sort_values("date").groupby("code"):
        vals = g[col].to_numpy(); fys = g["fy"].to_numpy()
        run_sum = 0.0; run_n = 0
        for k in range(len(vals)):
            if run_n >= MIN_PRIOR and np.isfinite(vals[k]):
                pairs.append((run_sum / run_n, vals[k], fys[k]))
            if np.isfinite(vals[k]):
                run_sum += vals[k]; run_n += 1
    if len(pairs) < 50:
        print(f"\n[{label}] too few paired events ({len(pairs)})")
        return
    P = pd.DataFrame(pairs, columns=["trail", "fwd", "fy"])
    rho, p = sstats.spearmanr(P["trail"], P["fwd"])
    scale = 100.0 if pct else 1.0
    unit = "%" if pct else ""
    print(f"\n=== PERSISTENCE [{label}]  paired events={len(P)} ===")
    print(f"  Spearman(trail, fwd) = {rho:+.4f}  (p={p:.3f})")
    P["q"] = pd.qcut(P["trail"].rank(method="first"), 4, labels=[1, 2, 3, 4])
    print(f"  {'Q(trail)':>9} {'n':>6} {'trail_mean'+unit:>12} {'fwd_mean'+unit:>11}")
    for q in [1, 2, 3, 4]:
        sub = P[P["q"] == q]
        print(f"  {q:>9} {len(sub):>6} {sub['trail'].mean()*scale:>12.3f} {sub['fwd'].mean()*scale:>11.3f}")
    q4 = P[P["q"] == 4]["fwd"].mean(); q1 = P[P["q"] == 1]["fwd"].mean()
    print(f"  Q4-Q1 forward spread = {(q4-q1)*scale:+.3f}{unit}  (want > 0 for the idea to live)")
    line = []
    for fy in sorted(P["fy"].unique()):
        s = P[P["fy"] == fy]
        if s["q"].nunique() < 4:
            line.append(f"FY{fy}:na"); continue
        d = s[s["q"] == 4]["fwd"].mean() - s[s["q"] == 1]["fwd"].mean()
        line.append(f"FY{fy}:{d*scale:+.2f}(n{len(s)})")
    print("  per-FY Q4-Q1 spread: " + "  ".join(line))


def main() -> None:
    if os.path.exists(_CACHE):
        logger.info("loading cached events from {}", _CACHE)
        df = pd.read_pickle(_CACHE)
    else:
        df = _build_events()
        df.to_pickle(_CACHE)
        logger.info("cached events to {}", _CACHE)

    print(f"\n=== EVENTS: {len(df)} fires  stocks={df['code'].nunique()} ===")
    cell = df.groupby("code").size()
    print(f"per-stock lifetime fires: median={cell.median():.0f} mean={cell.mean():.1f} "
          f"p25={cell.quantile(.25):.0f} p75={cell.quantile(.75):.0f}")

    # cross-sectional spread: is there ANY per-stock reachability dispersion to harvest?
    for hh in HORIZONS:
        ps = df.groupby("code").agg(n=("code", "size"),
                                    rfav=(f"rfav{hh}", "mean"),
                                    tpf=(f"tpf{hh}", "mean"))
        ps = ps[ps["n"] >= 5]
        print(f"\n[h{hh}] pooled mean r_fav={df[f'rfav{hh}'].mean():.2f}  "
              f"r_adv={df[f'radv{hh}'].mean():.2f}  tp_first_rate={df[f'tpf{hh}'].mean():.3f}")
        print(f"[h{hh}] per-stock (n>=5) r_fav: p10={ps['rfav'].quantile(.1):.2f} "
              f"med={ps['rfav'].median():.2f} p90={ps['rfav'].quantile(.9):.2f} | "
              f"tp_first: p10={ps['tpf'].quantile(.1):.2f} med={ps['tpf'].median():.2f} "
              f"p90={ps['tpf'].quantile(.9):.2f}")

    for hh in HORIZONS:
        print(f"\n################  HORIZON h{hh}  ################")
        print("---- r_fav (TP-side reachability, band units) ----")
        _persistence(df, f"rfav{hh}", f"RAW r_fav h{hh} (calendar NOT stripped)", pct=False)
        _persistence(df, f"res_rfav{hh}", f"RESID r_fav h{hh} (stock-specific)", pct=False)
        print("\n---- tp_first (ACTUAL ZsTpSl 2/2 outcome, path-based) ----")
        _persistence(df, f"tpf{hh}", f"RAW tp_first h{hh}", pct=True)
        _persistence(df, f"res_tpf{hh}", f"RESID tp_first h{hh} (stock-specific)", pct=True)
        print("\n---- r_adv (SL-side reachability) ----")
        _persistence(df, f"res_radv{hh}", f"RESID r_adv h{hh} (stock-specific)", pct=False)


if __name__ == "__main__":
    logger.remove()
    import sys
    logger.add(sys.stderr, level="INFO")
    main()
