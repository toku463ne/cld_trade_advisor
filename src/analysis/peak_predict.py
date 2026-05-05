"""Early-peak confirmation predictor using moving-correlation features.

Pipeline
--------
1. Build labeled dataset  — every bar that qualifies as an early peak
   (local extreme over size+middle_size bars) is labeled confirmed (1) if it
   is also a local extreme over 2*size bars, or not-confirmed (0) otherwise.
   Moving-corr values at that bar become the raw features.

2. EDA  — mean ± std of each raw ρ feature grouped by (confirmed, direction).
   Mann-Whitney U p-value tests whether the distributions differ.

3. Feature engineering  — 5 candidate feature families per indicator:
     F1  rho           ρ value at peak bar
     F2  delta_rho     ρ(t) - ρ(t-k)           (k=DELTA_K=5 bars)
     F3  sign_rho      sign(ρ) × peak_direction  (alignment)
     F4  abs_rho       |ρ|
     F5  rel_N225      ρ_^N225 / ρ_^GSPC        (domestic vs US coupling)

4. Threshold scan  — for every feature, find the threshold T and direction
   (> or <) that maximises F1 for predicting the confirmed class.

CLI
---
    uv run --env-file devenv python -m src.analysis.peak_predict \\
        --cluster-set classified2023 \\
        --start 2023-04-01 --end 2024-03-31 \\
        --window 20

    # Dump the labeled dataset to CSV for external analysis:
    uv run --env-file devenv python -m src.analysis.peak_predict \\
        --cluster-set classified2023 \\
        --start 2023-04-01 --end 2024-03-31 \\
        --window 20 --csv peaks.csv
"""

from __future__ import annotations

import argparse
import datetime
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import MovingCorr, StockClusterMember, StockClusterRun
from src.analysis.peak_corr import MAJOR_INDICATORS
from src.config import load_stock_codes
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP

# ── Constants ─────────────────────────────────────────────────────────────────

ZZ_SIZE        = 5     # bars on each side for confirmed peak
ZZ_MIDDLE      = 2     # bars on right side for early peak
DELTA_K        = 5     # look-back bars for delta_rho feature
_N225          = "^N225"
_GSPC          = "^GSPC"
_STOCK_CODES_INI = "configs/stock_codes.ini"


# ── Step 0: data loading ──────────────────────────────────────────────────────

def _load_ohlcv_bulk(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
) -> dict[str, pd.DataFrame]:
    """Return {code: DataFrame(high, low, close) indexed by date} for all codes."""
    model  = OHLCV_MODEL_MAP[gran]
    rows   = session.execute(
        select(model.stock_code, model.ts, model.high_price,
               model.low_price, model.close_price)
        .where(model.stock_code.in_(codes), model.ts >= start, model.ts < end)
        .order_by(model.stock_code, model.ts)
    ).all()

    result: dict[str, pd.DataFrame] = {}
    tmp: dict[str, list] = {}
    for r in rows:
        tmp.setdefault(r.stock_code, []).append(
            (r.ts.date(), float(r.high_price), float(r.low_price), float(r.close_price))
        )
    for code, items in tmp.items():
        df = pd.DataFrame(items, columns=["date", "high", "low", "close"])
        df.set_index("date", inplace=True)
        result[code] = df
    return result


def _load_mc_bulk(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
    window_bars: int,
) -> dict[str, pd.DataFrame]:
    """Return {code: DataFrame(indicators as columns) indexed by date}."""
    rows = session.execute(
        select(MovingCorr.stock_code, MovingCorr.indicator,
               MovingCorr.ts, MovingCorr.corr_value)
        .where(
            MovingCorr.stock_code.in_(codes),
            MovingCorr.ts >= start, MovingCorr.ts < end,
            MovingCorr.granularity == gran,
            MovingCorr.window_bars == window_bars,
        )
    ).all()

    raw: dict[str, dict[str, dict]] = {}
    for r in rows:
        raw.setdefault(r.stock_code, {}).setdefault(r.indicator, {})[
            r.ts.date()
        ] = float(r.corr_value)

    result: dict[str, pd.DataFrame] = {}
    for code, ind_map in raw.items():
        df = pd.DataFrame(ind_map)
        df.index.name = "date"
        df.sort_index(inplace=True)
        result[code] = df
    return result


# ── Step 1: build labeled dataset ─────────────────────────────────────────────

def _find_early_peaks(
    highs: np.ndarray,
    lows: np.ndarray,
    size: int = ZZ_SIZE,
    middle: int = ZZ_MIDDLE,
) -> list[tuple[int, int, int]]:
    """Return list of (bar_idx, direction, confirmed).

    direction:  1 = high peak,  -1 = low trough
    confirmed:  1 = local extreme over full 2*size window,
                0 = only over size+middle window (early but not confirmed)
    """
    n = len(highs)
    peaks: list[tuple[int, int, int]] = []

    for i in range(size, n - size):
        # Windows
        left        = i - size
        right_early = i + middle + 1
        right_full  = i + size + 1

        # Early high: max over left..right_early
        is_early_h = float(highs[i]) == float(np.max(highs[left:right_early]))
        is_early_l = float(lows[i])  == float(np.min(lows[left:right_early]))

        if is_early_h and not is_early_l:
            confirmed = 1 if float(highs[i]) == float(np.max(highs[left:right_full])) else 0
            peaks.append((i, 1, confirmed))
        elif is_early_l and not is_early_h:
            confirmed = 1 if float(lows[i]) == float(np.min(lows[left:right_full])) else 0
            peaks.append((i, -1, confirmed))

    return peaks


def build_dataset(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str      = "1d",
    window_bars: int = 20,
    size: int      = ZZ_SIZE,
    middle: int    = ZZ_MIDDLE,
) -> pd.DataFrame:
    """Build the labeled peak dataset with raw ρ features.

    Columns: stock_code, bar_date, direction, confirmed,
             price, rho_<indicator> × 8
    """
    logger.info("Loading OHLCV for {} codes …", len(codes))
    ohlcv_map = _load_ohlcv_bulk(session, codes, start, end, gran)

    logger.info("Loading moving_corr for {} codes …", len(codes))
    mc_map = _load_mc_bulk(session, codes, start, end, gran, window_bars)

    records: list[dict] = []
    skipped_no_ohlcv = 0
    skipped_no_mc    = 0

    for code in codes:
        if code not in ohlcv_map:
            skipped_no_ohlcv += 1
            continue
        if code not in mc_map:
            skipped_no_mc += 1
            continue

        price_df = ohlcv_map[code]
        mc_df    = mc_map[code]

        dates  = list(price_df.index)
        highs  = price_df["high"].values
        lows   = price_df["low"].values
        closes = price_df["close"].values

        peak_list = _find_early_peaks(highs, lows, size, middle)

        for bar_idx, direction, confirmed in peak_list:
            d = dates[bar_idx]
            if d not in mc_df.index:
                continue
            mc_row = mc_df.loc[d]

            row: dict = {
                "stock_code": code,
                "bar_date":   d,
                "direction":  direction,
                "confirmed":  confirmed,
                "price":      float(highs[bar_idx] if direction > 0 else lows[bar_idx]),
            }
            for ind in MAJOR_INDICATORS:
                row[f"rho_{ind}"] = mc_row.get(ind, float("nan"))
            records.append(row)

    if skipped_no_ohlcv:
        logger.warning("Skipped {} codes with no OHLCV data", skipped_no_ohlcv)
    if skipped_no_mc:
        logger.warning("Skipped {} codes with no moving_corr data", skipped_no_mc)

    df = pd.DataFrame(records)
    # Drop rows missing any raw ρ feature
    rho_cols = [f"rho_{ind}" for ind in MAJOR_INDICATORS]
    df.dropna(subset=rho_cols, inplace=True)
    df.reset_index(drop=True, inplace=True)
    logger.info(
        "Dataset: {} peaks  ({} confirmed, {} not confirmed)",
        len(df),
        df["confirmed"].sum(),
        (df["confirmed"] == 0).sum(),
    )
    return df


# ── Step 2: EDA ───────────────────────────────────────────────────────────────

def run_eda(df: pd.DataFrame) -> None:
    """Print mean ± std of raw ρ per indicator, grouped by (direction, confirmed)."""
    rho_cols = [f"rho_{ind}" for ind in MAJOR_INDICATORS]

    for direction, dir_label in [(1, "HIGH peaks"), (-1, "LOW troughs")]:
        sub = df[df["direction"] == direction]
        if sub.empty:
            continue
        n_conf = sub["confirmed"].sum()
        n_not  = (sub["confirmed"] == 0).sum()
        print(f"\n{'='*70}")
        print(f"EDA — {dir_label}  (confirmed={n_conf}, not_confirmed={n_not})")
        print(f"{'='*70}")
        print(f"{'Indicator':<12}  {'mean(ρ|conf=1)':>16}  {'mean(ρ|conf=0)':>16}  "
              f"{'Δmean':>8}  {'p-value':>10}")
        print("-" * 70)
        for col in rho_cols:
            g1 = sub.loc[sub["confirmed"] == 1, col].dropna()
            g0 = sub.loc[sub["confirmed"] == 0, col].dropna()
            if len(g1) < 3 or len(g0) < 3:
                continue
            m1, s1 = g1.mean(), g1.std()
            m0, s0 = g0.mean(), g0.std()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _, pval = stats.mannwhitneyu(g1, g0, alternative="two-sided")
            ind = col.replace("rho_", "")
            print(f"{ind:<12}  {m1:>+8.4f}±{s1:5.4f}  {m0:>+8.4f}±{s0:5.4f}  "
                  f"{m1-m0:>+8.4f}  {pval:>10.4f}{'  *' if pval < 0.05 else ''}")


# ── Step 3: feature engineering ───────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all 5 feature families to df (in-place) and return it.

    F1  rho_<ind>          (already present — raw ρ at peak bar)
    F2  delta_rho_<ind>    ρ(t) - ρ(t-DELTA_K) per stock (trend)
    F3  sign_rho_<ind>     sign(ρ) × direction  (alignment with peak)
    F4  abs_rho_<ind>      |ρ|
    F5  rel_N225           ρ_^N225 / ρ_^GSPC    (domestic vs US coupling)
    """
    df = df.copy()

    # ── F2: delta_rho  (rolling diff per stock) ────────────────────────────
    delta_parts: list[pd.DataFrame] = []
    for code, grp in df.groupby("stock_code", sort=False):
        grp = grp.sort_values("bar_date")
        for ind in MAJOR_INDICATORS:
            col = f"rho_{ind}"
            delta_col = f"delta_rho_{ind}"
            grp[delta_col] = grp[col].diff(DELTA_K)
        delta_parts.append(grp)
    df = pd.concat(delta_parts).sort_index()

    # ── F3: sign_rho ───────────────────────────────────────────────────────
    for ind in MAJOR_INDICATORS:
        df[f"sign_rho_{ind}"] = np.sign(df[f"rho_{ind}"]) * df["direction"]

    # ── F4: abs_rho ────────────────────────────────────────────────────────
    for ind in MAJOR_INDICATORS:
        df[f"abs_rho_{ind}"] = df[f"rho_{ind}"].abs()

    # ── F5: rel_N225 = ρ_^N225 / ρ_^GSPC ──────────────────────────────────
    rho_n225 = df[f"rho_{_N225}"]
    rho_gspc = df[f"rho_{_GSPC}"]
    denom = rho_gspc.abs().clip(lower=0.05) * np.sign(rho_gspc).replace(0, 1)
    df["rel_N225"] = (rho_n225 / denom).clip(-10, 10)

    return df


# ── Step 4: threshold scan ────────────────────────────────────────────────────

def _precision_recall_f1(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float, float]:
    tp = int(np.sum((y_pred == 1) & (y_true == 1)))
    fp = int(np.sum((y_pred == 1) & (y_true == 0)))
    fn = int(np.sum((y_pred == 0) & (y_true == 1)))
    prec   = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1     = 2 * prec * recall / (prec + recall) if (prec + recall) > 0 else 0.0
    return prec, recall, f1


def threshold_scan(df: pd.DataFrame) -> pd.DataFrame:
    """Scan every feature for the threshold maximising F1 on the confirmed class.

    Returns a DataFrame of best results, sorted by F1 descending.
    """
    feature_cols: list[str] = []
    for ind in MAJOR_INDICATORS:
        feature_cols += [f"rho_{ind}", f"delta_rho_{ind}",
                         f"sign_rho_{ind}", f"abs_rho_{ind}"]
    feature_cols.append("rel_N225")

    results: list[dict] = []

    for direction, dir_label in [(1, "HIGH"), (-1, "LOW"), (0, "ALL")]:
        sub = df if direction == 0 else df[df["direction"] == direction]
        if len(sub) < 20:
            continue
        y_true = sub["confirmed"].values

        for feat in feature_cols:
            if feat not in sub.columns:
                continue
            vals = sub[feat].values
            valid = ~np.isnan(vals)
            if valid.sum() < 20:
                continue
            v = vals[valid]
            yt = y_true[valid]

            thresholds = np.unique(v)
            if len(thresholds) < 3:
                continue
            # Sample up to 200 candidate thresholds
            if len(thresholds) > 200:
                thresholds = thresholds[::len(thresholds) // 200]

            best = {"f1": -1.0}
            for t in thresholds:
                for op, sym in [(">", ">"), ("<", "<")]:
                    pred = (v > t).astype(int) if op == ">" else (v < t).astype(int)
                    if pred.sum() < 5 or (1 - pred).sum() < 5:
                        continue
                    prec, rec, f1 = _precision_recall_f1(yt, pred)
                    if f1 > best["f1"]:
                        best = {
                            "f1": f1, "prec": prec, "recall": rec,
                            "threshold": float(t), "op": sym,
                            "n_pred": int(pred.sum()),
                        }

            if best["f1"] >= 0:
                results.append({
                    "direction": dir_label,
                    "feature":   feat,
                    "threshold": best["threshold"],
                    "op":        best["op"],
                    "prec":      best["prec"],
                    "recall":    best["recall"],
                    "f1":        best["f1"],
                    "n_pred":    best["n_pred"],
                    "n_total":   int(valid.sum()),
                })

    out = pd.DataFrame(results).sort_values("f1", ascending=False)
    return out.reset_index(drop=True)


def print_threshold_scan(scan_df: pd.DataFrame, top_n: int = 20) -> None:
    print(f"\n{'='*80}")
    print(f"Threshold Scan — top {top_n} features by F1 (predicting: confirmed=1)")
    print(f"{'='*80}")
    print(f"{'Dir':<6} {'Feature':<26} {'Op':>3} {'Thresh':>8}  "
          f"{'Prec':>6}  {'Rec':>6}  {'F1':>6}  {'N_pred':>7}  {'N_total':>8}")
    print("-" * 80)
    for _, row in scan_df.head(top_n).iterrows():
        print(
            f"{row['direction']:<6} {row['feature']:<26} "
            f"{row['op']:>3} {row['threshold']:>8.4f}  "
            f"{row['prec']:>6.3f}  {row['recall']:>6.3f}  {row['f1']:>6.3f}  "
            f"{row['n_pred']:>7d}  {row['n_total']:>8d}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_codes(cluster_set: str | None,
                stock_set:   str | None,
                code_list:   list[str] | None,
                session: Session) -> list[str]:
    if code_list:
        return code_list
    if cluster_set:
        run = session.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == cluster_set)
        ).scalar_one_or_none()
        if run is None:
            raise RuntimeError(f"No StockClusterRun for fiscal_year={cluster_set!r}")
        return [
            m.stock_code for m in session.execute(
                select(StockClusterMember)
                .where(StockClusterMember.run_id == run.id,
                       StockClusterMember.is_representative.is_(True))
            ).scalars().all()
        ]
    if stock_set:
        return load_stock_codes(_STOCK_CODES_INI, stock_set)
    raise ValueError("Provide --cluster-set, --stock-set, or --code")


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.analysis.peak_predict",
        description="Predict early-peak confirmation using moving-corr features",
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--cluster-set", metavar="LABEL")
    grp.add_argument("--stock-set",   metavar="SECTION")
    grp.add_argument("--code",        nargs="+", metavar="CODE")
    p.add_argument("--start",    required=True)
    p.add_argument("--end",      required=True)
    p.add_argument("--window",   type=int, default=20)
    p.add_argument("--gran",     default="1d")
    p.add_argument("--zz-size",  type=int, default=ZZ_SIZE)
    p.add_argument("--zz-mid",   type=int, default=ZZ_MIDDLE)
    p.add_argument("--top",      type=int, default=20,
                   help="Top N features to show in threshold scan")
    p.add_argument("--csv",      default=None,
                   help="Dump labeled dataset to CSV path")
    args = p.parse_args(argv)

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end)

    with get_session() as session:
        codes = _load_codes(args.cluster_set, args.stock_set, args.code, session)
        logger.info("Stocks: {}", len(codes))

        # Step 1
        df_raw = build_dataset(
            session, codes, start, end,
            gran=args.gran, window_bars=args.window,
            size=args.zz_size, middle=args.zz_mid,
        )

    if df_raw.empty:
        logger.error("No peaks found — check date range and OHLCV data.")
        sys.exit(1)

    # Step 2
    run_eda(df_raw)

    # Step 3
    logger.info("Engineering features …")
    df = engineer_features(df_raw)

    if args.csv:
        out = Path(args.csv)
        df.to_csv(out, index=False)
        logger.info("Saved dataset to {}", out)

    # Step 4
    logger.info("Running threshold scan …")
    scan = threshold_scan(df)
    print_threshold_scan(scan, top_n=args.top)

    # Summary by direction
    for direction, label in [(1, "HIGH"), (-1, "LOW")]:
        sub = scan[scan["direction"] == label]
        if sub.empty:
            continue
        best = sub.iloc[0]
        print(f"\nBest single feature ({label}): "
              f"{best['feature']} {best['op']} {best['threshold']:.4f}  "
              f"F1={best['f1']:.3f}  prec={best['prec']:.3f}  rec={best['recall']:.3f}")


if __name__ == "__main__":
    main()
