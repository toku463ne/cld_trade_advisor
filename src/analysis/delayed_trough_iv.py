"""Delayed-trough Information Value analysis (daily bars, LOW peaks only).

Hypothesis: a stock's early LOW trough that occurs AFTER the most recent
N225 confirmed low is more likely to be a genuine trough than one that
occurs simultaneously with or before N225's low.

  confirmed = 1  →  price stays above this bar's low for the next ``size`` bars
  confirmed = 0  →  price falls below this bar's low within ``size`` bars

Features measured AT the trough bar (no lookahead):
  n225_lag_bars   — stock bars elapsed since N225's last confirmed low
                    (NaN when no N225 low in the preceding lag_window bars)
  n225_recovery   — N225 % return from its low to the stock trough date
  corr_n225       — 20-bar daily rolling correlation to ^N225
  rsi14           — Wilder RSI-14
  bb_pct_b        — Bollinger %B (SMA-20 ± 2σ)
  vol_ratio       — bar volume / 20-day average volume

Summary block compares confirmation rates:
  all early lows  vs  those with an N225 prior low  vs  those without.

Interaction cross-tab: n225_lag_bars × corr_n225 — tests whether the lag
signal is stronger when the stock is highly correlated to N225 (as expected).

CLI:
    uv run --env-file devenv python -m src.analysis.delayed_trough_iv \\
        --cluster-set classified2023 --start 2024-01-01 --end 2025-03-31

    # vary size / window:
    uv run --env-file devenv python -m src.analysis.delayed_trough_iv \\
        --cluster-set classified2023 --start 2024-01-01 --end 2025-03-31 \\
        --size 3 --middle-size 1 --lag-window 20
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import StockClusterMember, StockClusterRun
from src.data.db import get_session
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache

_N225          = "^N225"
_DEFAULT_SIZE  = 3
_DEFAULT_MID   = 1
_DEFAULT_LAG_W = 30      # bars (~6 weeks of trading days)
_N_BINS        = 4

_FEATURES = [
    "n225_lag_bars",
    "n225_recovery",
    "corr_n225",
    "rsi14",
    "bb_pct_b",
    "vol_ratio",
]


# ── Daily bar helpers ─────────────────────────────────────────────────────────

class _Day(NamedTuple):
    date:   datetime.date
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float


def _to_daily(cache: DataCache) -> list[_Day]:
    groups: dict[datetime.date, list] = {}
    for b in cache.bars:
        groups.setdefault(b.dt.date(), []).append(b)
    result: list[_Day] = []
    for d in sorted(groups):
        day = groups[d]
        result.append(_Day(
            date   = d,
            open   = day[0].open,
            high   = max(b.high    for b in day),
            low    = min(b.low     for b in day),
            close  = day[-1].close,
            volume = sum(b.volume or 0 for b in day),
        ))
    return result


def _daily_indicators(days: list[_Day]) -> pd.DataFrame:
    dates  = [d.date  for d in days]
    closes = pd.Series([d.close  for d in days], index=dates, dtype=float)
    vols   = pd.Series([d.volume for d in days], index=dates, dtype=float)

    sma20 = closes.rolling(20).mean()
    std20 = closes.rolling(20).std(ddof=1)

    gains  = closes.diff().clip(lower=0)
    losses = (-closes.diff()).clip(lower=0)
    avg_g  = gains.ewm(com=13, min_periods=14).mean()
    avg_l  = losses.ewm(com=13, min_periods=14).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    rsi14  = 100.0 - 100.0 / (1.0 + rs)

    upper    = sma20 + 2 * std20
    lower_bb = sma20 - 2 * std20
    band     = (upper - lower_bb).replace(0, np.nan)
    bb_pct_b = (closes - lower_bb) / band

    vol20     = vols.rolling(20, min_periods=5).mean().replace(0, np.nan)
    vol_ratio = vols / vol20

    return pd.DataFrame({
        "rsi14":     rsi14,
        "bb_pct_b":  bb_pct_b,
        "vol_ratio": vol_ratio,
    }, index=dates)


def _daily_corr(
    days: list[_Day],
    n225_days: list[_Day],
    window: int = 20,
) -> pd.Series:
    sc = pd.Series({d.date: d.close for d in days},      dtype=float)
    nc = pd.Series({d.date: d.close for d in n225_days}, dtype=float)
    aligned = pd.concat([sc.rename("s"), nc.rename("n")], axis=1).dropna()
    return (
        aligned["s"].pct_change()
        .rolling(window, min_periods=max(5, window // 2))
        .corr(aligned["n"].pct_change())
    )


# ── N225 reference peaks ──────────────────────────────────────────────────────

def _n225_confirmed_lows(n225_days: list[_Day], size: int) -> list[datetime.date]:
    """Dates of all confirmed N225 low peaks (direction == -2)."""
    highs = [d.high for d in n225_days]
    lows  = [d.low  for d in n225_days]
    peaks = detect_peaks(highs, lows, size=size, middle_size=0)
    return sorted(
        n225_days[p.bar_index].date
        for p in peaks if p.direction == -2 and p.bar_index < len(n225_days)
    )


# ── Per-stock candidate extraction ───────────────────────────────────────────

def _extract_candidates(
    days:        list[_Day],
    ind:         pd.DataFrame,
    corr_ser:    pd.Series,
    n225_lows:   list[datetime.date],      # sorted
    n225_close:  dict[datetime.date, float],
    size:        int,
    mid:         int,
    lag_window:  int,
) -> list[dict]:
    """Return one record per early LOW-trough candidate bar."""
    if len(days) < size * 2 + 1:
        return []

    lows = [d.low for d in days]
    n    = len(lows)

    records: list[dict] = []
    for i in range(size, n - size):
        # Early trough: local min over [i-size … i+mid]
        left_min  = min(lows[i - size : i])
        right_mid = min(lows[i + 1 : i + mid + 1]) if mid > 0 else 1e18

        if lows[i] >= left_min or lows[i] >= right_mid:
            continue

        # Confirmed = price stays above this bar's low for the next size bars
        right_full = min(lows[i + 1 : i + size + 1])
        confirmed  = int(lows[i] < right_full)

        stock_date = days[i].date

        # ── N225 lag ──────────────────────────────────────────────────────────
        # Find the most recent N225 confirmed low strictly before stock_date
        # and within the last lag_window stock bars.
        cutoff_date = days[max(0, i - lag_window)].date

        pos = bisect.bisect_left(n225_lows, stock_date)  # first index >= stock_date
        n225_lag_bars: float | None = None
        n225_recovery: float | None = None

        if pos > 0:
            last_n225_low = n225_lows[pos - 1]
            if last_n225_low >= cutoff_date:
                # Count stock bars between the N225 low date and bar i
                n225_lag_bars = float(
                    sum(1 for j in range(i) if days[j].date > last_n225_low)
                )
                n225_at_low = n225_close.get(last_n225_low)
                n225_at_now = n225_close.get(stock_date)
                if n225_at_low and n225_at_now and n225_at_low > 0:
                    n225_recovery = (n225_at_now - n225_at_low) / n225_at_low

        # ── Standard features ─────────────────────────────────────────────────
        si = ind.loc[stock_date] if stock_date in ind.index else None

        def _f(row: pd.Series | None, col: str) -> float | None:
            if row is None:
                return None
            v = row[col]
            return float(v) if pd.notna(v) else None

        corr_val: float | None = None
        if stock_date in corr_ser.index:
            v = corr_ser.loc[stock_date]
            corr_val = float(v) if pd.notna(v) else None

        records.append({
            "date":          stock_date,
            "confirmed":     confirmed,
            "n225_lag_bars": n225_lag_bars,
            "n225_recovery": n225_recovery,
            "corr_n225":     corr_val,
            "rsi14":         _f(si, "rsi14"),
            "bb_pct_b":      _f(si, "bb_pct_b"),
            "vol_ratio":     _f(si, "vol_ratio"),
        })

    return records


# ── IV computation ────────────────────────────────────────────────────────────

def _bin_iv(
    feature: pd.Series,
    label:   pd.Series,
    n_bins:  int = _N_BINS,
) -> tuple[float, list[tuple[int, float, float]]]:
    df = pd.DataFrame({"f": feature, "y": label}).dropna()
    if len(df) < 20:
        return float("nan"), []

    total_pos = df["y"].sum()
    total_neg = (1 - df["y"]).sum()
    if total_pos == 0 or total_neg == 0:
        return float("nan"), []

    try:
        df["bin"] = pd.qcut(df["f"], q=n_bins, duplicates="drop")
    except ValueError:
        return float("nan"), []

    iv   = 0.0
    bins: list[tuple[int, float, float]] = []
    for interval, grp in df.groupby("bin", observed=True):
        n_pos = grp["y"].sum()
        n_neg = len(grp) - n_pos
        p_pos = max(n_pos / total_pos, 1e-9)
        p_neg = max(n_neg / total_neg, 1e-9)
        woe   = np.log(p_pos / p_neg)
        iv   += (p_pos - p_neg) * woe
        rate  = float(n_pos / len(grp))
        mid_pt = float(interval.mid)
        bins.append((len(grp), rate, mid_pt))

    return iv, bins


# ── Print helpers ─────────────────────────────────────────────────────────────

def _print_crosstab(df: pd.DataFrame, row_feat: str, col_feat: str) -> None:
    sub = df[[row_feat, col_feat, "confirmed"]].dropna()
    if len(sub) < 40:
        return
    try:
        sub = sub.copy()
        sub["row_q"] = pd.qcut(sub[row_feat], q=4, duplicates="drop", labels=False)
        sub["col_q"] = pd.qcut(sub[col_feat], q=4, duplicates="drop", labels=False)
    except ValueError:
        return

    pivot = sub.groupby(["row_q", "col_q"], observed=True)["confirmed"].agg(
        lambda x: f"{x.mean():.0%}({len(x)})"
    ).unstack("col_q")
    pivot.index   = [f"{row_feat} Q{int(i)+1}" for i in pivot.index]
    pivot.columns = [f"{col_feat} Q{int(c)+1}" for c in pivot.columns]
    print(f"\n  Confirm-rate: {row_feat} (rows) × {col_feat} (cols)")
    print(pivot.to_string())


def _print_table(df: pd.DataFrame, size: int, mid: int, lag_window: int) -> None:
    n_total  = len(df)
    n_conf   = int(df["confirmed"].sum())
    overall  = n_conf / n_total if n_total else 0.0
    n_lagged = int(df["n225_lag_bars"].notna().sum())

    print(f"\n{'='*90}")
    print(
        f" Delayed-trough IV  |  size={size}  mid={mid}  lag_window={lag_window}\n"
        f" n={n_total}  confirmed={n_conf} ({overall:.1%})"
        f"  with_n225_lag={n_lagged} ({n_lagged/n_total:.0%})"
    )
    print(f" Q1=low … Q4=high feature value  |  cell = confirm_rate(n)")
    print(f" IV: <0.02 useless · 0.02-0.10 weak · 0.10-0.30 medium · >0.30 strong")
    print(f"{'='*90}")

    rows = []
    for feat in _FEATURES:
        if feat not in df.columns:
            continue
        n_valid = int(df[feat].notna().sum())
        iv, bins = _bin_iv(df[feat], df["confirmed"])
        bin_cells = {f"Q{k+1}": f"{r:.0%}({n})" for k, (n, r, _) in enumerate(bins)}
        for j in range(_N_BINS):
            bin_cells.setdefault(f"Q{j+1}", "—")
        rows.append({
            "feature": feat,
            "n":       n_valid,
            "iv":      round(iv, 4) if not np.isnan(iv) else None,
            **bin_cells,
        })

    tbl = (
        pd.DataFrame(rows)
        .sort_values("iv", ascending=False, na_position="last")
    )
    pd.set_option("display.max_colwidth", 14)
    pd.set_option("display.width", 160)
    print(tbl[["feature", "n", "iv", "Q1", "Q2", "Q3", "Q4"]].to_string(index=False))

    # ── Interaction: lag × corr ───────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(" Interaction cross-tabs  (confirm_rate(n))")
    _print_crosstab(df, "n225_lag_bars", "corr_n225")
    _print_crosstab(df, "corr_n225",     "n225_lag_bars")

    # ── Summary: with vs without N225 prior low ───────────────────────────────
    print(f"\n{'─'*70}")
    print(" Confirm rate: all early lows vs lag-subset vs no-lag subset")
    with_lag    = df[df["n225_lag_bars"].notna()]
    without_lag = df[df["n225_lag_bars"].isna()]
    print(f"  All early lows              : {overall:.1%} ({n_total})")
    if len(with_lag) > 0:
        print(
            f"  N225 prior low within {lag_window:2d} bars : "
            f"{with_lag['confirmed'].mean():.1%} ({len(with_lag)})"
        )
    if len(without_lag) > 0:
        print(
            f"  No N225 prior low found     : "
            f"{without_lag['confirmed'].mean():.1%} ({len(without_lag)})"
        )

    # ── Lag buckets: lag=1-3, 4-7, 8-15, 16+ ─────────────────────────────────
    if n_lagged >= 20:
        print(f"\n  Confirm rate by lag bucket (N225 confirmed-low bars ago):")
        buckets = [(1, 3), (4, 7), (8, 15), (16, lag_window)]
        for lo, hi in buckets:
            sub = df[(df["n225_lag_bars"] >= lo) & (df["n225_lag_bars"] <= hi)]
            if len(sub) >= 5:
                print(
                    f"    lag {lo:2d}–{hi:2d} bars : "
                    f"{sub['confirmed'].mean():.1%} (n={len(sub)})"
                )
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    stock_codes: list[str],
    stock_set:   str,
    start:       datetime.datetime,
    end:         datetime.datetime,
    size:        int = _DEFAULT_SIZE,
    mid:         int = _DEFAULT_MID,
    lag_window:  int = _DEFAULT_LAG_W,
) -> None:
    with get_session() as session:
        logger.info("Loading ^N225 cache …")
        n225 = DataCache(_N225, "1h")
        n225.load(session, start, end)

        if not n225.bars:
            raise SystemExit("No ^N225 data for this period.")

        n225_days  = _to_daily(n225)
        n225_close = {d.date: d.close for d in n225_days}
        n225_lows  = _n225_confirmed_lows(n225_days, size=size)
        logger.info(
            "N225: {} daily bars, {} confirmed lows detected (size={})",
            len(n225_days), len(n225_lows), size,
        )

        all_records: list[dict] = []
        for i, code in enumerate(stock_codes, 1):
            logger.debug("  [{}/{}] {}", i, len(stock_codes), code)
            cache = DataCache(code, "1h")
            cache.load(session, start, end)
            if not cache.bars:
                continue
            days     = _to_daily(cache)
            ind      = _daily_indicators(days)
            corr_ser = _daily_corr(days, n225_days)
            recs     = _extract_candidates(
                days, ind, corr_ser,
                n225_lows, n225_close,
                size, mid, lag_window,
            )
            all_records.extend(recs)

    if not all_records:
        raise SystemExit("No early-trough candidates found.")

    df = pd.DataFrame(all_records)
    logger.info(
        "Loaded {} candidates across {} stocks ({} confirmed, {} not)",
        len(df), len(stock_codes),
        int(df["confirmed"].sum()), int((1 - df["confirmed"]).sum()),
    )

    _print_table(df, size, mid, lag_window)


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.delayed_trough_iv")
    p.add_argument("--cluster-set",  required=True, metavar="LABEL")
    p.add_argument("--start",        required=True)
    p.add_argument("--end",          required=True)
    p.add_argument("--size",         type=int, default=_DEFAULT_SIZE,
                   help=f"Zigzag size (default {_DEFAULT_SIZE})")
    p.add_argument("--middle-size",  type=int, default=_DEFAULT_MID,
                   help=f"Right bars for early detection (default {_DEFAULT_MID})")
    p.add_argument("--lag-window",   type=int, default=_DEFAULT_LAG_W,
                   help=f"Max bars to look back for N225 low (default {_DEFAULT_LAG_W})")
    args = p.parse_args(argv)

    with get_session() as session:
        cluster_run = session.execute(
            select(StockClusterRun)
            .where(StockClusterRun.fiscal_year == args.cluster_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise SystemExit(f"No StockClusterRun for {args.cluster_set!r}")
        codes = list(session.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == cluster_run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())
    logger.info("Loaded {} stocks from [{}]", len(codes), args.cluster_set)

    run(
        stock_codes=codes,
        stock_set=args.cluster_set,
        start=_parse_dt(args.start),
        end=_parse_dt(args.end),
        size=args.size,
        mid=args.middle_size,
        lag_window=args.lag_window,
    )


if __name__ == "__main__":
    main()
