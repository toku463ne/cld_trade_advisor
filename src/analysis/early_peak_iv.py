"""Early-peak Information Value analysis (daily bars, HIGH peaks only).

For every daily bar that looks like an early HIGH peak — local max over
[bar-size … bar+middle_size] — we ask: does it later *confirm* as a full
zigzag peak (local max over [bar-size … bar+size])?

  confirmed = 1  →  price stayed below this bar's high for the next `size` bars
  confirmed = 0  →  price came back above this bar's high within `size` bars

Features measured AT the peak bar (no lookahead):
  rsi14        — 14-day Wilder RSI of daily closes
  bb_pct_b     — Bollinger %B  (SMA-20 ± 2σ)
  sma20_dist   — (close − SMA-20) / SMA-20
  vol_ratio    — bar volume / 20-day average daily volume
  n225_20d_ret — N225 20-day return (market-regime proxy)
  n225_sma20_dist — N225 distance from its SMA-20

IV interpretation: <0.02 useless · 0.02–0.10 weak · 0.10–0.30 medium · >0.30 strong

CLI:
    uv run --env-file devenv python -m src.analysis.early_peak_iv \\
        --cluster-set classified2023 --start 2024-05-01 --end 2025-03-31

    # vary size / middle-size:
    uv run --env-file devenv python -m src.analysis.early_peak_iv \\
        --cluster-set classified2023 --start 2024-05-01 --end 2025-03-31 \\
        --size 3 --middle-size 1
"""

from __future__ import annotations

import argparse
import datetime
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import StockClusterMember, StockClusterRun
from src.data.db import get_session
from src.simulator.cache import DataCache

_N225        = "^N225"
_DEFAULT_SIZE = 3
_DEFAULT_MID  = 1
_N_BINS       = 4

_FEATURES = ["rsi14", "bb_pct_b", "sma20_dist", "vol_ratio",
             "n225_20d_ret", "n225_sma20_dist", "corr_n225"]


# ── Daily bar derivation ──────────────────────────────────────────────────────

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
            date=d,
            open=day[0].open,
            high=max(b.high   for b in day),
            low=min(b.low    for b in day),
            close=day[-1].close,
            volume=sum(b.volume or 0 for b in day),
        ))
    return result


# ── Daily technical indicators ────────────────────────────────────────────────

def _daily_corr(
    days: list[_Day],
    n225_days: list[_Day],
    window: int = 20,
) -> pd.Series:
    """Rolling Pearson correlation of daily returns vs N225 (daily bars)."""
    sc = pd.Series({d.date: d.close for d in days},      dtype=float)
    nc = pd.Series({d.date: d.close for d in n225_days}, dtype=float)
    aligned = pd.concat([sc.rename("s"), nc.rename("n")], axis=1).dropna()
    return (
        aligned["s"].pct_change()
        .rolling(window, min_periods=max(5, window // 2))
        .corr(aligned["n"].pct_change())
    )


def _daily_indicators(days: list[_Day]) -> pd.DataFrame:
    dates  = [d.date  for d in days]
    closes = pd.Series([d.close for d in days], index=dates, dtype=float)
    vols   = pd.Series([d.volume for d in days], index=dates, dtype=float)

    sma20 = closes.rolling(20).mean()
    std20 = closes.rolling(20).std(ddof=1)

    gains  = closes.diff().clip(lower=0)
    losses = (-closes.diff()).clip(lower=0)
    avg_g  = gains.ewm(com=13, min_periods=14).mean()
    avg_l  = losses.ewm(com=13, min_periods=14).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    rsi14  = 100.0 - 100.0 / (1.0 + rs)

    upper  = sma20 + 2 * std20
    lower  = sma20 - 2 * std20
    band   = (upper - lower).replace(0, np.nan)
    bb_pct_b   = (closes - lower) / band
    sma20_dist = (closes - sma20) / sma20.replace(0, np.nan)

    vol20      = vols.rolling(20, min_periods=5).mean().replace(0, np.nan)
    vol_ratio  = vols / vol20

    ret20      = closes.pct_change(20)

    return pd.DataFrame({
        "rsi14":      rsi14,
        "bb_pct_b":   bb_pct_b,
        "sma20_dist": sma20_dist,
        "vol_ratio":  vol_ratio,
        "ret20":      ret20,
    }, index=dates)


# ── Per-stock candidate extraction ───────────────────────────────────────────

def _extract_candidates(
    days:      list[_Day],
    ind:       pd.DataFrame,
    n225_ind:  pd.DataFrame,
    corr_ser:  pd.Series,
    size:      int,
    mid:       int,
) -> list[dict]:
    """Return one record per early-HIGH-peak candidate bar."""
    if len(days) < size * 2 + 1:
        return []

    highs = [d.high for d in days]
    n     = len(highs)
    records: list[dict] = []

    for i in range(size, n - size):
        # Early peak condition: local max over [i-size … i+mid] (inclusive)
        left_max  = max(highs[i - size : i])
        right_mid = max(highs[i + 1   : i + mid + 1]) if mid > 0 else -1e18

        if highs[i] <= left_max or highs[i] <= right_mid:
            continue

        # Confirmed iff still the max over the full right window
        right_full = max(highs[i + 1 : i + size + 1])
        confirmed  = int(highs[i] > right_full)

        # Features at bar i
        d = days[i].date
        si = ind.loc[d]   if d in ind.index   else None
        ni = n225_ind.loc[d] if d in n225_ind.index else None

        def _f(row: pd.Series | None, col: str) -> float | None:
            if row is None:
                return None
            v = row[col]
            return float(v) if pd.notna(v) else None

        corr_val: float | None = None
        if d in corr_ser.index:
            v = corr_ser.loc[d]
            corr_val = float(v) if pd.notna(v) else None

        records.append({
            "date":            d,
            "confirmed":       confirmed,
            "rsi14":           _f(si, "rsi14"),
            "bb_pct_b":        _f(si, "bb_pct_b"),
            "sma20_dist":      _f(si, "sma20_dist"),
            "vol_ratio":       _f(si, "vol_ratio"),
            "n225_20d_ret":    _f(ni, "ret20"),
            "n225_sma20_dist": _f(ni, "sma20_dist"),
            "corr_n225":       corr_val,
        })

    return records


# ── IV computation ─────────────────────────────────────────────────────────────

def _bin_iv(
    feature:  pd.Series,
    label:    pd.Series,
    n_bins:   int = _N_BINS,
) -> tuple[float, list[tuple[int, float, float]]]:
    """Return (IV, [(n, confirm_rate, bin_edge_mean), ...]) low→high bins."""
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
        n_pos  = grp["y"].sum()
        n_neg  = len(grp) - n_pos
        p_pos  = max(n_pos / total_pos, 1e-9)
        p_neg  = max(n_neg / total_neg, 1e-9)
        woe    = np.log(p_pos / p_neg)
        iv    += (p_pos - p_neg) * woe
        rate   = float(n_pos / len(grp))
        mid_pt = float(interval.mid)  # type: ignore[union-attr]
        bins.append((len(grp), rate, mid_pt))

    return iv, bins


# ── Interaction cross-tab ─────────────────────────────────────────────────────

def _print_crosstab(df: pd.DataFrame, row_feat: str, col_feat: str) -> None:
    """2D confirm-rate table: row_feat quartile × col_feat quartile."""
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

    print(f"\n  Confirm rate: {row_feat} (rows) × {col_feat} (cols)")
    print(pivot.to_string())


# ── Print table ───────────────────────────────────────────────────────────────

def _print_table(df: pd.DataFrame, size: int, mid: int) -> None:
    n_total   = len(df)
    n_conf    = int(df["confirmed"].sum())
    overall   = n_conf / n_total if n_total else 0.0

    print(f"\n{'='*90}")
    print(f" Early-HIGH peak IV  |  size={size}, middle_size={mid}  "
          f"|  n={n_total}  confirmed={n_conf} ({overall:.1%})")
    print(f" Q1=low … Q4=high feature value  |  cell = confirm_rate (n in bin)")
    print(f" IV: <0.02 useless · 0.02-0.10 weak · 0.10-0.30 medium · >0.30 strong")
    print(f"{'='*90}")

    rows = []
    for feat in _FEATURES:
        if feat not in df.columns:
            continue
        n_valid = int(df[feat].notna().sum())
        iv, bins = _bin_iv(df[feat], df["confirmed"])
        bin_cells = {f"Q{i+1}": f"{r:.0%}({n})" for i, (n, r, _) in enumerate(bins)}
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

    # ── Interaction: is n225_20d_ret signal driven by high-corr stocks? ────
    print(f"\n{'─'*70}")
    print(" Interaction cross-tabs  (confirm_rate(n))")
    _print_crosstab(df, "n225_20d_ret", "corr_n225")
    _print_crosstab(df, "corr_n225",    "n225_20d_ret")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    stock_codes: list[str],
    stock_set:   str,
    start:       datetime.datetime,
    end:         datetime.datetime,
    size:        int = _DEFAULT_SIZE,
    mid:         int = _DEFAULT_MID,
) -> None:
    with get_session() as session:
        logger.info("Loading ^N225 cache …")
        n225 = DataCache(_N225, "1h")
        n225.load(session, start, end)

        if not n225.bars:
            raise SystemExit("No ^N225 data for this period.")

        n225_days = _to_daily(n225)
        n225_ind  = _daily_indicators(n225_days)

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
            recs     = _extract_candidates(days, ind, n225_ind, corr_ser, size, mid)
            all_records.extend(recs)

    if not all_records:
        raise SystemExit("No early-peak candidates found.")

    df = pd.DataFrame(all_records)
    logger.info(
        "Loaded {} candidates across {} stocks ({} confirmed, {} not)",
        len(df), len(stock_codes),
        int(df["confirmed"].sum()), int((1 - df["confirmed"]).sum()),
    )

    _print_table(df, size, mid)


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.early_peak_iv")
    p.add_argument("--cluster-set", required=True, metavar="LABEL")
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         required=True)
    p.add_argument("--size",        type=int, default=_DEFAULT_SIZE,
                   help=f"Zigzag size for confirmed peak (default {_DEFAULT_SIZE})")
    p.add_argument("--middle-size", type=int, default=_DEFAULT_MID,
                   help=f"Right bars needed for early detection (default {_DEFAULT_MID})")
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
    )


if __name__ == "__main__":
    main()
