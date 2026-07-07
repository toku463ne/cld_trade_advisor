"""Stage 0 — PER-STOCK sign-quality persistence (premise test).

Operator idea (2026-06-28): for each stock, scan the last ~12 months and learn which
signs appeared frequently AND performed well ON THAT STOCK.  At confluence-trigger time,
look at the individual signs valid for the stock and only enter if those signs have been
"good" on this stock; otherwise move to another candidate.

This is the PER-STOCK version of [[project_monthly_sign_winner_list_reject]] (2026-06-01),
which tested a GLOBAL trailing winner/loser sign list and REJECTED it: per-sign alpha
labels flip year-to-year, trailing winners mean-REVERT at the holding horizon (Spearman
+0.10@21d -> negative @63d+), and density was ~2 trades per (sign,month) cell.  Conditioning
per-STOCK is genuinely new, but it makes density strictly WORSE (~few fires per (stock,sign)
per 12mo).  Before building any selection/veto rule (which then still has to clear the
fill-order null that has killed every selection rule), test the PREMISE cheaply:

    Does a stock's TRAILING per-sign quality predict its FORWARD per-sign quality,
    ABOVE the global per-sign effect we already rejected?

Design (look-ahead-safe, event-level out-of-sample):
  * Load every individual bullish-sign fire per stock from the dev-DB benchmark events.
  * fwd return per fire: fill open[F+1], exit close[F+1+H] (two-bar), winsorized.
  * RESIDUALIZE each fire's return within its (sign, year-month) group across ALL stocks
    -> strips the global sign effect AND market/calendar regime in one step.  What remains
    is the STOCK-SPECIFIC component (the only thing per-stock conditioning can harvest).
  * Persistence: walk each (stock,sign) cell in time order; from the (M+1)-th fire on,
    trail_edge = mean residual of PRIOR fires in the same cell.  Pair (trail_edge, this
    fire's residual).  Report Spearman + quartile table (Q1..Q4 trail_edge -> fwd resid),
    per-FY spread, plus the FREQUENCY angle (trail fire-count -> fwd resid).
  * Also report the RAW (non-residualized) persistence for contrast — if raw is positive
    but residual is flat, the apparent signal is just the global sign effect (already dead).

Decisive: residualized Spearman > 0 with a monotone Q1<Q4 forward-resid table that holds
across FYs.  If flat/inverted, the per-stock selection idea is DOA before the fill-order null.
Per-fire only; read-only.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.per_stock_sign_quality_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats as sstats
from sqlalchemy import text

import src.analysis.confluence_strategy_backtest as cbt
from src.data.db import get_session

HORIZONS = [10, 20]
WINSOR = 0.60
MIN_PRIOR = 3            # need >= this many prior fires in a (stock,sign) cell to form trail_edge
MIN_YM_GROUP = 8         # min events in a (sign, year-month) cell to trust the global-residual mean
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


def _fy(ts: pd.Timestamp) -> int:
    return ts.year if ts.month >= _FY_START_MONTH else ts.year - 1


def _winsor(a: np.ndarray) -> np.ndarray:
    return np.clip(a, -WINSOR, WINSOR)


def _build_events() -> pd.DataFrame:
    """One row per individual bullish-sign fire: stock, sign, date, fwd10, fwd20."""
    fires_by_stock = cbt._load_bullish_fires_by_stock()
    logger.info("stocks with bullish fires: {}", len(fires_by_stock))
    recs = []
    with get_session() as s:
        for n, (code, fires) in enumerate(fires_by_stock.items()):
            if n % 50 == 0:
                logger.info("  {}/{}", n, len(fires_by_stock))
            sub = _load_one(s, code)
            if len(sub) < max(HORIZONS) + 3:
                continue
            dates = list(sub["date"])
            didx = {d: i for i, d in enumerate(dates)}
            o = sub["open"].to_numpy(); c = sub["close"].to_numpy()
            N = len(sub)
            seen: set[tuple[str, pd.Timestamp]] = set()
            for sign, fd in fires:
                key = pd.Timestamp(fd).normalize()
                if (sign, key) in seen:
                    continue
                seen.add((sign, key))
                fi = didx.get(key)
                if fi is None or fi + 1 + max(HORIZONS) >= N:
                    continue
                rec = {"code": code, "sign": sign, "date": key, "fy": _fy(key),
                       "ym": f"{key.year}-{key.month:02d}"}
                for hh in HORIZONS:
                    rec[f"h{hh}"] = float(c[fi + 1 + hh] / o[fi + 1] - 1.0)
                recs.append(rec)
    df = pd.DataFrame(recs)
    for hh in HORIZONS:
        df[f"h{hh}"] = _winsor(df[f"h{hh}"].to_numpy())
        # residual within (sign, year-month) across ALL stocks
        grp = df.groupby(["sign", "ym"])[f"h{hh}"]
        gmean = grp.transform("mean")
        gsize = grp.transform("size")
        df[f"res{hh}"] = df[f"h{hh}"] - gmean
        df[f"res{hh}"] = df[f"res{hh}"].where(gsize >= MIN_YM_GROUP, np.nan)
    return df


def _persistence(df: pd.DataFrame, col: str, label: str) -> None:
    """Walk each (stock,sign) cell in time order; trail mean of `col` (prior fires) vs this fire."""
    pairs = []  # (trail_edge, trail_count, fwd_value, fy)
    for (code, sign), g in df.sort_values("date").groupby(["code", "sign"]):
        vals = g[col].to_numpy()
        fys = g["fy"].to_numpy()
        run_sum = 0.0
        run_n = 0
        for k in range(len(vals)):
            if run_n >= MIN_PRIOR and np.isfinite(vals[k]):
                pairs.append((run_sum / run_n, run_n, vals[k], fys[k]))
            if np.isfinite(vals[k]):
                run_sum += vals[k]
                run_n += 1
    if not pairs:
        print(f"\n[{label}] no paired events")
        return
    P = pd.DataFrame(pairs, columns=["trail", "tn", "fwd", "fy"])
    rho, p = sstats.spearmanr(P["trail"], P["fwd"])
    print(f"\n=== PERSISTENCE [{label}]  paired events={len(P)}  "
          f"cells>= {MIN_PRIOR} prior, fwd in {'%' } ===")
    print(f"  Spearman(trail_edge, fwd) = {rho:+.4f}  (p={p:.3f})")
    # quartile table
    P["q"] = pd.qcut(P["trail"].rank(method="first"), 4, labels=[1, 2, 3, 4])
    print(f"  {'Q(trail)':>9} {'n':>6} {'trail_mean%':>12} {'fwd_mean%':>11} {'fwd_DR%':>8}")
    for q in [1, 2, 3, 4]:
        sub = P[P["q"] == q]
        print(f"  {q:>9} {len(sub):>6} {sub['trail'].mean()*100:>12.3f} "
              f"{sub['fwd'].mean()*100:>11.3f} {(sub['fwd']>0).mean()*100:>8.1f}")
    q4 = P[P["q"] == 4]["fwd"].mean(); q1 = P[P["q"] == 1]["fwd"].mean()
    print(f"  Q4-Q1 forward spread = {(q4-q1)*100:+.3f}pp  (want > 0 for the idea to live)")
    # per-FY spread robustness
    print("  per-FY Q4-Q1 forward spread (pp):")
    line = []
    for fy in sorted(P["fy"].unique()):
        s = P[P["fy"] == fy]
        if s["q"].nunique() < 4:
            line.append(f"FY{fy}:na"); continue
        d = s[s["q"] == 4]["fwd"].mean() - s[s["q"] == 1]["fwd"].mean()
        line.append(f"FY{fy}:{d*100:+.2f}(n{len(s)})")
    print("    " + "  ".join(line))


_CACHE = "/tmp/claude-1000/-home-ubuntu-cld-trade-advisor/6663b296-30c5-4b1a-89da-a580ff2781bc/scratchpad/per_stock_sign_events.pkl"


def _decompose(df: pd.DataFrame, col: str, label: str) -> None:
    """Leave-this-sign-out: separate stock x sign INTERACTION from a stock FIXED effect.

    For each event, build two trailing estimators from PRIOR fires on the same stock:
      same  = mean residual of prior fires of the SAME sign  (operator's idea)
      other = mean residual of prior fires of DIFFERENT signs (pure stock effect)
    If same and other predict fwd equally -> it's a stock fixed effect (momentum-ish),
    not a per-sign edge.  If same >> other -> real stock x sign interaction.
    """
    rows = []
    for code, g in df.sort_values("date").groupby("code"):
        g = g.reset_index(drop=True)
        vals = g[col].to_numpy(); signs = g["sign"].to_numpy(); fys = g["fy"].to_numpy()
        for k in range(len(g)):
            if not np.isfinite(vals[k]):
                continue
            prior = slice(0, k)
            pv = vals[prior]; ps = signs[prior]
            same_mask = np.isfinite(pv) & (ps == signs[k])
            other_mask = np.isfinite(pv) & (ps != signs[k])
            if same_mask.sum() < MIN_PRIOR or other_mask.sum() < MIN_PRIOR:
                continue
            rows.append((pv[same_mask].mean(), pv[other_mask].mean(), vals[k], fys[k]))
    P = pd.DataFrame(rows, columns=["same", "other", "fwd", "fy"])
    if len(P) < 100:
        print(f"\n[decompose {label}] too few paired events ({len(P)})"); return
    rs, ps = sstats.spearmanr(P["same"], P["fwd"])
    ro, po = sstats.spearmanr(P["other"], P["fwd"])
    print(f"\n=== DECOMPOSE [{label}]  paired events={len(P)} ===")
    print(f"  Spearman(SAME-sign trail, fwd)  = {rs:+.4f} (p={ps:.3f})   <- operator's stock x sign idea")
    print(f"  Spearman(OTHER-sign trail, fwd) = {ro:+.4f} (p={po:.3f})   <- pure stock fixed effect")
    # 2x2: within high/low OTHER (stock effect), does SAME still sort fwd?
    hi_o = P["other"] >= P["other"].median()
    hi_s = P["same"] >= P["same"].median()
    print(f"  {'':>22}{'SAME low':>12}{'SAME high':>12}")
    for olab, omask in [("OTHER low", ~hi_o), ("OTHER high", hi_o)]:
        a = P[omask & ~hi_s]["fwd"].mean() * 100
        b = P[omask & hi_s]["fwd"].mean() * 100
        print(f"  {olab:>22}{a:>12.3f}{b:>12.3f}")
    print("  (if SAME-high beats SAME-low WITHIN each OTHER row -> real per-sign edge "
          "survives the stock effect)")


def main() -> None:
    import os
    if os.path.exists(_CACHE):
        logger.info("loading cached events from {}", _CACHE)
        df = pd.read_pickle(_CACHE)
    else:
        df = _build_events()
        df.to_pickle(_CACHE)
        logger.info("cached events to {}", _CACHE)
    print(f"\n=== EVENTS: {len(df)} individual sign fires  stocks={df['code'].nunique()}  "
          f"signs={df['sign'].nunique()} ===")
    # density: per (stock,sign) lifetime fire count and per-12mo
    cell = df.groupby(["code", "sign"]).size()
    print(f"per (stock,sign) lifetime fires: median={cell.median():.0f}  "
          f"mean={cell.mean():.1f}  p25={cell.quantile(.25):.0f}  p75={cell.quantile(.75):.0f}")
    span_yrs = (df["date"].max() - df["date"].min()).days / 365.25
    print(f"data span ~{span_yrs:.1f}yr -> implied per (stock,sign) per-12mo fires "
          f"~{cell.mean()/max(span_yrs,1):.1f} (operator's trailing window is this thin)")
    res_ok = df.groupby(["sign", "ym"]).size()
    print(f"(sign,year-month) cells with >= {MIN_YM_GROUP} events: "
          f"{(res_ok >= MIN_YM_GROUP).mean()*100:.0f}% of cells")

    for hh in HORIZONS:
        print(f"\n################  HORIZON h{hh}  ################")
        _persistence(df, f"h{hh}", f"RAW return h{hh} (NOT residualized — = global-list test)")
        _persistence(df, f"res{hh}", f"RESIDUAL h{hh} (stock-specific, global sign effect stripped)")
        _decompose(df, f"res{hh}", f"RESIDUAL h{hh}")


if __name__ == "__main__":
    main()
