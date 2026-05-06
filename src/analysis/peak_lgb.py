"""LightGBM reversal predictor trained on peak_feature_records.

Features (selected by IV analysis):
  n225_20d_ret, n225_sma20_dist  — regime (dominant)
  sma20_dist, bb_pct_b, rsi14   — technical
  vol_ratio, trend_age_bars     — weak but present
  peak_direction                — +2 HIGH / -2 LOW (as int, lets model split HIGH/LOW)

Target:
  fav = 1  if next confirmed peak is in the favorable direction
           (HIGH → reversal down;  LOW → reversal up)
       0  otherwise

Validation:
  Three expanding-window folds; test window = last ~3 months of each split.
  Reports AUC, Brier score, and calibration bin table per fold + overall.

Model saved to:  models/peak_reversal_<run_id>.lgb
Threshold saved: models/peak_reversal_<run_id>.threshold

CLI:
    uv run --env-file devenv python -m src.analysis.peak_lgb --run-id 2
    uv run --env-file devenv python -m src.analysis.peak_lgb --run-id 2 --no-save
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from loguru import logger
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from sqlalchemy import select

from src.data.db import get_session
from src.analysis.models import PeakFeatureRecord, PeakFeatureRun

# ── Feature set ───────────────────────────────────────────────────────────────

FEATURES = [
    "peak_direction",    # +2 / -2  (lets model learn asymmetric regime responses)
    "n225_20d_ret",      # IV=0.146 HIGH, 0.013 LOW
    "n225_sma20_dist",   # IV=0.096 HIGH, 0.086 LOW
    "sma20_dist",        # IV=0.011 HIGH, 0.035 LOW
    "bb_pct_b",          # IV=0.010 HIGH, 0.038 LOW
    "rsi14",             # IV=0.006 HIGH, 0.016 LOW
    "vol_ratio",         # IV=0.002 HIGH, 0.004 LOW
    "trend_age_bars",    # IV=0.003 HIGH, 0.002 LOW
]

_LGB_PARAMS: dict = {
    "objective":        "binary",
    "metric":           ["binary_logloss", "auc"],
    "learning_rate":    0.05,
    "num_leaves":       31,
    "min_child_samples": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "lambda_l1":        0.1,
    "lambda_l2":        0.1,
    "verbose":          -1,
    "seed":             42,
}
_N_ESTIMATORS = 500
_EARLY_STOP   = 40


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
            "outcome_magnitude"] + FEATURES[1:]  # skip peak_direction duplicate
    df = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in records])
    df["confirmed_at"] = pd.to_datetime(df["confirmed_at"], utc=True)
    df.sort_values("confirmed_at", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Favorable label
    df["fav"] = np.where(
        df["peak_direction"] == 2,
        (df["outcome_direction"] == -1).astype(int),
        (df["outcome_direction"] ==  1).astype(int),
    )
    df["fav"] = df["fav"].where(df["outcome_direction"].notna(), other=np.nan)

    logger.info("Loaded {} records  (fav rate {:.1%})",
                len(df), df["fav"].mean())
    return df


# ── Expanding-window CV ───────────────────────────────────────────────────────

def _time_splits(n: int, n_folds: int = 3) -> list[tuple[int, int, int]]:
    """Return [(train_end, val_start, val_end), ...] index splits."""
    step = n // (n_folds + 1)
    splits = []
    for k in range(n_folds):
        train_end  = step * (k + 1)
        val_start  = train_end
        val_end    = min(train_end + step, n)
        splits.append((train_end, val_start, val_end))
    return splits


def _calibration_table(y_true: np.ndarray, y_prob: np.ndarray,
                       n_bins: int = 5) -> str:
    frac_pos, mean_pred = calibration_curve(y_true, y_prob, n_bins=n_bins,
                                             strategy="quantile")
    lines = ["  pred_prob  actual_rate"]
    for mp, fp in zip(mean_pred, frac_pos):
        lines.append(f"  {mp:.3f}      {fp:.3f}")
    return "\n".join(lines)


# ── Train + evaluate ──────────────────────────────────────────────────────────

def train(
    df: pd.DataFrame,
    save_path: Path | None = None,
) -> tuple[lgb.Booster, float]:
    """Train with expanding-window CV, report metrics, optionally save.

    Returns (final_booster_trained_on_all_data, best_threshold).
    """
    df_valid = df.dropna(subset=["fav"] + FEATURES)
    X = df_valid[FEATURES].values.astype(np.float32)
    y = df_valid["fav"].values.astype(np.float32)
    n = len(X)
    logger.info("Training set: {} rows, {} features", n, len(FEATURES))

    splits = _time_splits(n, n_folds=3)

    oof_prob  = np.full(n, np.nan)
    fold_aucs: list[float] = []

    for fold_i, (tr_end, va_start, va_end) in enumerate(splits, 1):
        X_tr, y_tr = X[:tr_end],        y[:tr_end]
        X_va, y_va = X[va_start:va_end], y[va_start:va_end]

        dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURES)
        dval   = lgb.Dataset(X_va, label=y_va, feature_name=FEATURES,
                             reference=dtrain)

        callbacks = [
            lgb.early_stopping(_EARLY_STOP, verbose=False),
            lgb.log_evaluation(period=-1),
        ]
        booster = lgb.train(
            _LGB_PARAMS,
            dtrain,
            num_boost_round=_N_ESTIMATORS,
            valid_sets=[dval],
            callbacks=callbacks,
        )

        prob_va = booster.predict(X_va)
        oof_prob[va_start:va_end] = prob_va

        auc = roc_auc_score(y_va, prob_va)
        bs  = brier_score_loss(y_va, prob_va)
        ll  = log_loss(y_va, prob_va)
        fold_aucs.append(auc)

        print(f"\n── Fold {fold_i}  train={tr_end}  val=[{va_start},{va_end})  "
              f"trees={booster.num_trees()}")
        print(f"   AUC={auc:.4f}  Brier={bs:.4f}  LogLoss={ll:.4f}")
        print(f"   Calibration (quantile bins):")
        try:
            print(_calibration_table(y_va, prob_va))
        except ValueError:
            print("  (too few samples for calibration curve)")

    # OOF summary over all folds that have predictions
    oof_mask = ~np.isnan(oof_prob)
    if oof_mask.sum() > 0:
        y_oof = y[oof_mask]
        p_oof = oof_prob[oof_mask]
        print(f"\n── OOF summary ({oof_mask.sum()} samples)")
        print(f"   AUC={roc_auc_score(y_oof, p_oof):.4f}  "
              f"Brier={brier_score_loss(y_oof, p_oof):.4f}  "
              f"LogLoss={log_loss(y_oof, p_oof):.4f}")

    # ── Isotonic calibration on last fold's val set ───────────────────────
    last_tr_end, last_va_start, last_va_end = splits[-1]
    X_cal = X[last_va_start:last_va_end]
    y_cal = y[last_va_start:last_va_end]
    # Retrain booster on data up to cal split for calibration
    dtrain_cal = lgb.Dataset(X[:last_tr_end], label=y[:last_tr_end],
                             feature_name=FEATURES)
    dval_cal   = lgb.Dataset(X_cal, label=y_cal, feature_name=FEATURES,
                             reference=dtrain_cal)
    cbs = [lgb.early_stopping(_EARLY_STOP, verbose=False),
           lgb.log_evaluation(period=-1)]
    booster_cal = lgb.train(_LGB_PARAMS, dtrain_cal,
                            num_boost_round=_N_ESTIMATORS,
                            valid_sets=[dval_cal], callbacks=cbs)
    raw_cal = booster_cal.predict(X_cal)
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_cal, y_cal)

    # ── Threshold: maximise F1 on calibrated OOF probs ───────────────────
    cal_oof = iso.predict(oof_prob[oof_mask])
    best_thr, best_f1 = 0.5, -1.0
    for thr in np.arange(0.3, 0.75, 0.01):
        pred = (cal_oof >= thr).astype(int)
        tp = ((pred == 1) & (y_oof == 1)).sum()
        fp = ((pred == 1) & (y_oof == 0)).sum()
        fn = ((pred == 0) & (y_oof == 1)).sum()
        prec = tp / (tp + fp + 1e-9)
        rec  = tp / (tp + fn + 1e-9)
        f1   = 2 * prec * rec / (prec + rec + 1e-9)
        if f1 > best_f1:
            best_f1, best_thr = f1, float(thr)

    print(f"\n── Calibrated threshold scan → best threshold={best_thr:.2f}  "
          f"OOF F1={best_f1:.4f}")

    # ── Feature importance ────────────────────────────────────────────────
    print(f"\n── Feature importance (gain)")
    dtrain_full = lgb.Dataset(X, label=y, feature_name=FEATURES)
    callbacks_full = [lgb.log_evaluation(period=-1)]
    final_booster = lgb.train(
        _LGB_PARAMS,
        dtrain_full,
        num_boost_round=int(np.mean([booster.num_trees() for _ in splits]) + 20),
        callbacks=callbacks_full,
    )
    imp = pd.Series(
        final_booster.feature_importance(importance_type="gain"),
        index=FEATURES,
    ).sort_values(ascending=False)
    for feat, gain in imp.items():
        print(f"   {feat:<22}  {gain:>10.1f}")

    # ── Save ──────────────────────────────────────────────────────────────
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        final_booster.save_model(str(save_path))
        thr_path = save_path.with_suffix(".threshold")
        thr_path.write_text(f"{best_thr:.4f}\n")
        logger.info("Model  saved → {}", save_path)
        logger.info("Threshold → {}  ({})", thr_path, best_thr)

    return final_booster, best_thr


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.peak_lgb")
    p.add_argument("--run-id",        type=int, required=True)
    p.add_argument("--include-crash", action="store_true")
    p.add_argument("--no-save",       action="store_true",
                   help="Skip saving the model (default: save)")
    args = p.parse_args(argv)

    df = load_dataframe(args.run_id, include_crash=args.include_crash)

    save_path = (
        None if args.no_save
        else Path(f"models/peak_reversal_{args.run_id}.lgb")
    )
    train(df, save_path=save_path)


if __name__ == "__main__":
    main()
