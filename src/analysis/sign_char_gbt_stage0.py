"""Stage-0 GBT probe — do fire-time features predict fwd_ret_h with INTERACTIONS
the additive linear-scan misses? Discover-only (FY2010-16), date-blocked CV.

Ladder (all same features, same folds):
  M0    per-sign mean         (no features  → the floor)
  ridge linear additive       (the linear-scan analog)
  d1    HGBT max_depth=1       (additive trees, monotone-free)
  d3    HGBT max_depth=3       (2-3 way interactions allowed)

If d3 ~ d1 ~ ridge → no useful interactions → the additive per-cell story is the
whole story → an NN buys nothing.  Two targets: raw fwd_ret_h and a
market-demeaned variant (per-date cross-sectional mean removed) — if skill
vanishes under demeaning, it was universe beta.

Metrics per held-out fold: Spearman rank corr(pred, actual) and decile spread
(mean actual fwd of top-pred-decile − bottom-pred-decile, pp).

OUTCOME (2026-05-21): NOT a free kill — the ladder is monotone ridge<d1<d3 on
both targets, so real interaction signal exists within discover (demeaned d3
decile spread +1.00±0.43pp, mean>2σ). corr_hsi is the #1 permutation feature
(beta-trap warning). Proceeds to Stage-1 → which REJECTS (see
sign_char_gbt_stage1.py and memory project_gbt_learned_weights_reject.md).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.sign_char_gbt_stage0
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import text

from src.data.db import get_session

warnings.filterwarnings("ignore")

_NUM = ["sma_dist", "kumo_dist", "chiko_dist", "tenkan_dist", "zz_momentum",
        "corr_n225", "corr_gspc", "corr_hsi", "valid_n", "n225_valid_n",
        "sign_score"]
_WINSOR = 0.6
_NFOLD = 5
_SEED = 20260521


def _load() -> pd.DataFrame:
    cols = ["stock_code", "fired_on", "fy", "sign_type", "fwd_ret_h"] + _NUM
    sql = (f"select {','.join(set(cols))} from sign_feature_records "
           f"where run_id=3 and fwd_ret_h is not null")
    with get_session() as s:
        df = pd.DataFrame(s.execute(text(sql)).mappings().all())
    df["fired_on"] = pd.to_datetime(df["fired_on"])
    df["fy_n"] = df["fy"].str[2:].astype(int)
    df["fwd_ret_h"] = df["fwd_ret_h"].astype(float).clip(-_WINSOR, _WINSOR)
    for c in _NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # market-demeaned target: strip the per-date cross-sectional mean (the
    # dominant common-factor / beta component).
    df["fwd_dm"] = df["fwd_ret_h"] - df.groupby("fired_on")["fwd_ret_h"].transform("mean")
    df["sign_code"] = df["sign_type"].astype("category").cat.codes
    df["block"] = df["fired_on"].dt.to_period("M").astype(str)  # CV group
    return df


def _decile_spread(pred: np.ndarray, actual: np.ndarray) -> float:
    if len(pred) < 50:
        return np.nan
    q = pd.qcut(pd.Series(pred).rank(method="first"), 10, labels=False)
    top = actual[q == 9].mean()
    bot = actual[q == 0].mean()
    return (top - bot) * 100  # pp


def _eval_fold(name, pred, actual) -> tuple[float, float]:
    rho = spearmanr(pred, actual).correlation
    return rho, _decile_spread(pred, actual)


def _make_models():
    cat = [c for c in _NUM]  # numeric only for ridge pipeline handled separately
    return {
        "ridge": "ridge",
        "hgbt_d1": HistGradientBoostingRegressor(
            max_depth=1, max_iter=300, learning_rate=0.05,
            categorical_features=[len(_NUM)], random_state=_SEED,
            l2_regularization=1.0),
        "hgbt_d3": HistGradientBoostingRegressor(
            max_depth=3, max_iter=400, learning_rate=0.05,
            categorical_features=[len(_NUM)], random_state=_SEED,
            l2_regularization=1.0),
    }


def _fit_predict(name, model, Xtr, ytr, Xte, sg_tr, sg_te):
    """Return predictions on Xte. Ridge gets one-hot sign + standardized nums."""
    if name == "ridge":
        med = np.nanmedian(Xtr, axis=0)
        Xtr_f = np.where(np.isnan(Xtr), med, Xtr)
        Xte_f = np.where(np.isnan(Xte), med, Xte)
        sc = StandardScaler().fit(Xtr_f)
        oh = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(sg_tr.reshape(-1, 1))
        Atr = np.hstack([sc.transform(Xtr_f), oh.transform(sg_tr.reshape(-1, 1))])
        Ate = np.hstack([sc.transform(Xte_f), oh.transform(sg_te.reshape(-1, 1))])
        m = Ridge(alpha=10.0).fit(Atr, ytr)
        return m.predict(Ate)
    # HGBT: numeric (NaN-native) + sign_code as last categorical column
    Atr = np.hstack([Xtr, sg_tr.reshape(-1, 1).astype(float)])
    Ate = np.hstack([Xte, sg_te.reshape(-1, 1).astype(float)])
    model.fit(Atr, ytr)
    return model.predict(Ate)


def run() -> None:
    df = _load()
    logger.info("loaded {} fires", len(df))
    disc = df[df["fy_n"] <= 2016].reset_index(drop=True)
    logger.info("discover (FY2010-16): {} fires, {} signs, {} month-blocks",
                len(disc), disc["sign_type"].nunique(), disc["block"].nunique())

    X = disc[_NUM].to_numpy(dtype=float)
    sg = disc["sign_code"].to_numpy()
    groups = disc["block"].to_numpy()
    gkf = GroupKFold(n_splits=_NFOLD)

    results = {t: {m: {"rho": [], "spread": []}
                   for m in ["per_sign_mean", "ridge", "hgbt_d1", "hgbt_d3"]}
               for t in ["raw", "demeaned"]}

    for tgt, ycol in [("raw", "fwd_ret_h"), ("demeaned", "fwd_dm")]:
        y = disc[ycol].to_numpy(dtype=float)
        for fold, (tr, te) in enumerate(gkf.split(X, y, groups)):
            # M0: per-sign mean from train fold
            sign_mean = pd.Series(y[tr]).groupby(disc["sign_type"].to_numpy()[tr]).mean()
            gm = y[tr].mean()
            pred0 = np.array([sign_mean.get(s, gm) for s in disc["sign_type"].to_numpy()[te]])
            rho, sp = _eval_fold("m0", pred0, y[te])
            results[tgt]["per_sign_mean"]["rho"].append(rho)
            results[tgt]["per_sign_mean"]["spread"].append(sp)
            for name, model in _make_models().items():
                pred = _fit_predict(name, model, X[tr], y[tr], X[te], sg[tr], sg[te])
                rho, sp = _eval_fold(name, pred, y[te])
                results[tgt][name]["rho"].append(rho)
                results[tgt][name]["spread"].append(sp)
            logger.info("  [{}] fold {}/{} done", tgt, fold + 1, _NFOLD)

    # ── report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("STAGE-0 GBT PROBE — discover FY2010-16, {}-fold month-blocked CV".format(_NFOLD))
    print("metric = held-out Spearman rho (pred,actual)  &  decile spread (pp)")
    print("=" * 78)
    for tgt in ["raw", "demeaned"]:
        lab = "RAW fwd_ret_h" if tgt == "raw" else "MARKET-DEMEANED fwd_ret_h (beta stripped)"
        print(f"\n--- target: {lab} ---")
        print(f"{'model':<16}{'rho mean±std':<22}{'decile spread pp mean±std':<28}")
        for m in ["per_sign_mean", "ridge", "hgbt_d1", "hgbt_d3"]:
            r = np.array(results[tgt][m]["rho"], dtype=float)
            s = np.array(results[tgt][m]["spread"], dtype=float)
            print(f"{m:<16}{np.nanmean(r):+.4f} ± {np.nanstd(r):.4f}      "
                  f"{np.nanmean(s):+.3f} ± {np.nanstd(s):.3f}")

    # ── permutation importance on a full-discover depth-3 model ──────────────
    print("\n--- permutation importance (depth-3, raw target, full discover) ---")
    y = disc["fwd_ret_h"].to_numpy(dtype=float)
    A = np.hstack([X, sg.reshape(-1, 1).astype(float)])
    base = HistGradientBoostingRegressor(max_depth=3, max_iter=400, learning_rate=0.05,
                                         categorical_features=[len(_NUM)],
                                         random_state=_SEED, l2_regularization=1.0).fit(A, y)
    rng = np.random.default_rng(_SEED)
    base_rho = spearmanr(base.predict(A), y).correlation
    names = _NUM + ["sign_type"]
    imps = []
    for j in range(A.shape[1]):
        Ap = A.copy()
        Ap[:, j] = rng.permutation(Ap[:, j])
        drop = base_rho - spearmanr(base.predict(Ap), y).correlation
        imps.append((names[j], drop))
    for nm, drop in sorted(imps, key=lambda t: -t[1]):
        print(f"  {nm:<14} Δrho={drop:+.4f}")
    print(f"  (in-sample base rho={base_rho:.4f})")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
