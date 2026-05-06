"""Peak context clustering — group peaks by feature similarity, label by outcome.

Clusters confirmed peaks (from peak_feature_records) by their context features.
Each cluster is then labelled with its direction rate and mean magnitude.
Clusters with consistently high (or low) direction rates become trade filters:
  - enter when an early peak's context matches a high-confidence cluster.

Algorithm
---------
1. Scale features (StandardScaler).
2. AgglomerativeClustering with Ward linkage over a range of k values.
   Ward minimises within-cluster variance, producing compact, similarly-sized
   clusters — same choice as the existing stock-correlation clustering.
3. For each k, compute per-cluster direction_rate and mean magnitude.
4. Report: cluster summary table, tradeable clusters (dir_rate > threshold),
   silhouette score to guide k selection.
5. Save best model to models/peak_cluster_<run_id>.pkl for runtime lookup.

CLI:
    uv run --env-file devenv python -m src.analysis.peak_cluster --run-id 2
    uv run --env-file devenv python -m src.analysis.peak_cluster --run-id 2 --k 10
    uv run --env-file devenv python -m src.analysis.peak_cluster --run-id 2 --k-range 5,20
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select

from src.analysis.models import PeakFeatureRecord, PeakFeatureRun
from src.data.db import get_session

# ── Feature set (same as peak_lgb) ───────────────────────────────────────────

FEATURES = [
    "peak_direction",    # +2 HIGH / -2 LOW
    "n225_20d_ret",
    "n225_sma20_dist",
    "sma20_dist",
    "bb_pct_b",
    "rsi14",
    "vol_ratio",
    "trend_age_bars",
]

_DIR_THRESHOLD = 0.60   # cluster direction rate above this → "tradeable long"
_DIR_LOW_THRESH = 0.40  # below this → "tradeable short / avoid"
_MIN_CLUSTER_N  = 30    # ignore clusters with fewer than this many records


# ── Data loading ──────────────────────────────────────────────────────────────

def load_dataframe(run_id: int, include_crash: bool = False) -> pd.DataFrame:
    with get_session() as session:
        pfr = session.get(PeakFeatureRun, run_id)
        if pfr is None:
            raise SystemExit(f"No PeakFeatureRun with id={run_id}")
        logger.info("Run {}: {} | {} – {}", run_id, pfr.stock_set,
                    pfr.start_dt.date(), pfr.end_dt.date())

        stmt = select(PeakFeatureRecord).where(PeakFeatureRecord.run_id == run_id)
        if not include_crash:
            stmt = stmt.where(
                (PeakFeatureRecord.is_crash.is_(False))
                | (PeakFeatureRecord.is_crash.is_(None))
            )
        records = session.execute(stmt).scalars().all()

    if not records:
        raise SystemExit("No records found.")

    cols = ["confirmed_at", "peak_direction", "outcome_direction",
            "outcome_magnitude"] + FEATURES[1:]
    df = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in records])
    df["confirmed_at"] = pd.to_datetime(df["confirmed_at"], utc=True)
    df.sort_values("confirmed_at", inplace=True)
    df.reset_index(drop=True, inplace=True)

    df["fav"] = np.where(
        df["peak_direction"] == 2,
        (df["outcome_direction"] == -1).astype(float),
        (df["outcome_direction"] ==  1).astype(float),
    )
    df["fav"] = df["fav"].where(df["outcome_direction"].notna(), other=np.nan)

    df_clean = df.dropna(subset=FEATURES + ["fav"]).copy()
    logger.info("Loaded {} records → {} after dropping NaN  (fav rate {:.1%})",
                len(df), len(df_clean), df_clean["fav"].mean())
    return df_clean


# ── Clustering ────────────────────────────────────────────────────────────────

def fit_clusters(
    X_scaled: np.ndarray,
    k: int,
) -> tuple[np.ndarray, KMeans]:
    """Fit KMeans and return (cluster labels, fitted model)."""
    model = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = model.fit_predict(X_scaled)
    return labels, model


def cluster_stats(
    df: pd.DataFrame,
    labels: np.ndarray,
    scaler: StandardScaler,
    k: int,
) -> pd.DataFrame:
    """Return per-cluster summary DataFrame sorted by direction rate."""
    df = df.copy()
    df["cluster"] = labels

    rows = []
    for c in range(k):
        sub = df[df["cluster"] == c]
        n   = len(sub)
        if n == 0:
            continue

        fav_valid = sub["fav"].dropna()
        n_outcome = len(fav_valid)
        dir_rate  = fav_valid.mean() if n_outcome > 0 else float("nan")

        mag_fav = sub.loc[sub["fav"] == 1, "outcome_magnitude"].abs().mean()
        mag_all = sub["outcome_magnitude"].abs().mean()

        # Cluster centroid in original scale
        centroid_scaled = df.loc[df["cluster"] == c, FEATURES].mean()
        # Inverse-transform to get interpretable centroid
        centroid_orig = pd.Series(
            scaler.inverse_transform(centroid_scaled.values.reshape(1, -1))[0],
            index=FEATURES,
        )

        n_high = (sub["peak_direction"] ==  2).sum()
        n_low  = (sub["peak_direction"] == -2).sum()

        rows.append({
            "cluster":   c,
            "n":         n,
            "n_high":    n_high,
            "n_low":     n_low,
            "n_outcome": n_outcome,
            "dir_rate":  round(dir_rate, 4) if not np.isnan(dir_rate) else None,
            "mag_fav":   round(mag_fav, 4)  if not np.isnan(mag_fav)  else None,
            "mag_all":   round(mag_all, 4)  if not np.isnan(mag_all)  else None,
            # Key centroid values
            "c_peak_dir":     round(centroid_orig["peak_direction"],    2),
            "c_n225_ret":     round(centroid_orig["n225_20d_ret"],      4),
            "c_n225_sma":     round(centroid_orig["n225_sma20_dist"],   4),
            "c_sma20":        round(centroid_orig["sma20_dist"],        4),
            "c_bb":           round(centroid_orig["bb_pct_b"],          4),
            "c_rsi":          round(centroid_orig["rsi14"],             1),
            "c_vol":          round(centroid_orig["vol_ratio"],         3),
            "c_trend_age":    round(centroid_orig["trend_age_bars"],    0),
        })

    result = pd.DataFrame(rows).sort_values("dir_rate", ascending=False)
    return result.reset_index(drop=True)


def print_cluster_table(stats: pd.DataFrame, k: int, sil: float) -> None:
    print(f"\n{'='*110}")
    print(f" k={k}  silhouette={sil:.4f}")
    print(f" dir_rate = fraction with favorable outcome  |  "
          f"threshold: >{_DIR_THRESHOLD:.0%} tradeable, <{_DIR_LOW_THRESH:.0%} avoid")
    print(f"{'='*110}")
    print(f"  {'cl':>3}  {'n':>5}  {'hi/lo':>7}  {'out':>5}  "
          f"{'dir%':>6}  {'mag_fav':>8}  "
          f"{'pk_dir':>6}  {'n225ret':>8}  {'n225sma':>8}  "
          f"{'sma20':>7}  {'bb%B':>6}  {'rsi':>5}  {'vol':>5}  {'age':>5}  flag")
    print(f"  {'-'*105}")
    for _, row in stats.iterrows():
        n        = row["n"]
        dir_rate = row["dir_rate"]
        flag = ""
        if n >= _MIN_CLUSTER_N and dir_rate is not None:
            if dir_rate >= _DIR_THRESHOLD:
                flag = "✓ TRADE"
            elif dir_rate <= _DIR_LOW_THRESH:
                flag = "✗ AVOID"
        dr_str = f"{dir_rate:.1%}" if dir_rate is not None else "  —"
        mf_str = f"{row['mag_fav']:.4f}" if row["mag_fav"] is not None else "     —"
        print(
            f"  {row['cluster']:>3}  {n:>5}  "
            f"{row['n_high']:>3}/{row['n_low']:<3}  "
            f"{row['n_outcome']:>5}  "
            f"{dr_str:>6}  {mf_str:>8}  "
            f"{row['c_peak_dir']:>+6.1f}  {row['c_n225_ret']:>+8.3f}  "
            f"{row['c_n225_sma']:>+8.3f}  "
            f"{row['c_sma20']:>+7.3f}  {row['c_bb']:>6.3f}  "
            f"{row['c_rsi']:>5.1f}  {row['c_vol']:>5.3f}  "
            f"{row['c_trend_age']:>5.0f}  {flag}"
        )


# ── K selection ───────────────────────────────────────────────────────────────

def scan_k(
    X_scaled: np.ndarray,
    df: pd.DataFrame,
    k_values: list[int],
    scaler: StandardScaler,
) -> None:
    print(f"\n{'='*70}")
    print(f" K scan  (n_tradeable = clusters with dir>{_DIR_THRESHOLD:.0%} "
          f"or dir<{_DIR_LOW_THRESH:.0%} and n>={_MIN_CLUSTER_N})")
    print(f"{'='*70}")
    print(f"  {'k':>4}  {'silhouette':>11}  {'n_trade':>8}  "
          f"{'best_dir%':>10}  {'worst_dir%':>11}")
    print(f"  {'-'*60}")

    for k in k_values:
        labels, _ = fit_clusters(X_scaled, k)
        try:
            sil = silhouette_score(X_scaled, labels, sample_size=5000, random_state=42)
        except Exception:
            sil = float("nan")
        stats = cluster_stats(df, labels, scaler, k)
        large = stats[stats["n"] >= _MIN_CLUSTER_N].dropna(subset=["dir_rate"])
        n_trade = ((large["dir_rate"] >= _DIR_THRESHOLD) |
                   (large["dir_rate"] <= _DIR_LOW_THRESH)).sum()
        best  = large["dir_rate"].max()  if len(large) else float("nan")
        worst = large["dir_rate"].min()  if len(large) else float("nan")
        print(f"  {k:>4}  {sil:>11.4f}  {n_trade:>8}  "
              f"{best:>10.1%}  {worst:>11.1%}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    run_id: int,
    k: int,
    k_range: tuple[int, int] | None,
    include_crash: bool,
    save: bool,
) -> None:
    df = load_dataframe(run_id, include_crash)

    X = df[FEATURES].values.astype(np.float32)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # K scan if requested
    if k_range is not None:
        k_values = list(range(k_range[0], k_range[1] + 1))
        scan_k(X_scaled, df, k_values, scaler)

    # Detailed report for chosen k
    labels, km_model = fit_clusters(X_scaled, k)
    try:
        sil = silhouette_score(X_scaled, labels, sample_size=5000, random_state=42)
    except Exception:
        sil = float("nan")

    stats = cluster_stats(df, labels, scaler, k)
    print_cluster_table(stats, k, sil)

    # Tradeable clusters summary
    large = stats[stats["n"] >= _MIN_CLUSTER_N].dropna(subset=["dir_rate"])
    tradeable = large[
        (large["dir_rate"] >= _DIR_THRESHOLD) |
        (large["dir_rate"] <= _DIR_LOW_THRESH)
    ]
    print(f"\n── Tradeable clusters (n≥{_MIN_CLUSTER_N}, "
          f"dir>{_DIR_THRESHOLD:.0%} or dir<{_DIR_LOW_THRESH:.0%}): "
          f"{len(tradeable)} / {k}")
    if len(tradeable):
        n_covered = tradeable["n"].sum()
        dr_mean   = np.average(tradeable["dir_rate"],
                               weights=tradeable["n"])
        print(f"   Records covered: {n_covered} / {len(df)} "
              f"({n_covered/len(df):.1%})")
        print(f"   Weighted mean dir_rate: {dr_mean:.1%}")

    if save:
        out = Path(f"models/peak_cluster_{run_id}_k{k}.pkl")
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scaler":   scaler,
            "model":    km_model,
            "k":        k,
            "features": FEATURES,
            "stats":    stats,
            # cluster_id → (dir_rate, mag_fav) for runtime lookup
            "cluster_profile": {
                int(row["cluster"]): {
                    "dir_rate": row["dir_rate"],
                    "mag_fav":  row["mag_fav"],
                    "n":        row["n"],
                }
                for _, row in stats.iterrows()
            },
        }
        with open(out, "wb") as f:
            pickle.dump(payload, f)
        logger.info("Saved → {}", out)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.peak_cluster")
    p.add_argument("--run-id",        type=int, required=True)
    p.add_argument("--k",             type=int, default=10,
                   help="Number of clusters for detailed report (default 10)")
    p.add_argument("--k-range",       metavar="MIN,MAX",
                   help="Scan k from MIN to MAX and show silhouette + tradeable count")
    p.add_argument("--include-crash", action="store_true")
    p.add_argument("--no-save",       action="store_true")
    args = p.parse_args(argv)

    k_range = None
    if args.k_range:
        lo, hi = args.k_range.split(",")
        k_range = (int(lo), int(hi))

    run(
        run_id=args.run_id,
        k=args.k,
        k_range=k_range,
        include_crash=args.include_crash,
        save=not args.no_save,
    )


if __name__ == "__main__":
    main()
