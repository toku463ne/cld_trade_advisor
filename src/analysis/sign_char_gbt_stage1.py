"""Stage-1 — does the d3 HGBT interaction signal survive the temporal split?

Train depth-3 HGBT FROZEN on discover (FY2010-16). Freeze the top-quartile
selection threshold from discover predictions (the tradeable rule: trade a fire
when its predicted score clears the discover Q75 bar). Then push forward, no
refit:
  validate FY2017-21  → holdout FY2022-24  → strategy FY2025 (blind)

Per split: selected-cohort mean fwd vs rest, date-blocked bootstrap 95% CI,
per-FY breakdown. Two targets: raw fwd_ret_h and market-demeaned (per-date
cross-sectional mean removed). Binding gate = holdout CI excludes 0 AND per-FY
sign-consistent AND FY2025 blind positive.

OUTCOME (2026-05-21): REJECT — both targets fail. RAW held validate (+0.88pp)
and holdout (+0.82pp, CI excl 0) then FLIPPED to −1.06pp [−1.74,−0.38] in FY2025
blind = corr_hsi beta-trap reversal (FY19-24 6/6 positive = same regime band as
discover). DEMEANED (beta stripped) had no durable edge: validate +0.28pp →
holdout −0.03pp [−0.18,+0.12] (CI incl 0). Learned-weights/NN path CLOSED for
this feature set: regime non-stationarity, not capacity, is the wall. Lesson:
holdout-CI alone is insufficient when holdout sits in the same regime band as
discover — need per-FY consistency + a regime-distinct blind split. See memory
project_gbt_learned_weights_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.sign_char_gbt_stage1
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sqlalchemy import text

from src.data.db import get_session

warnings.filterwarnings("ignore")

_NUM = ["sma_dist", "kumo_dist", "chiko_dist", "tenkan_dist", "zz_momentum",
        "corr_n225", "corr_gspc", "corr_hsi", "valid_n", "n225_valid_n",
        "sign_score"]
_WINSOR = 0.6
_NBOOT = 5000
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
    df["fwd_dm"] = df["fwd_ret_h"] - df.groupby("fired_on")["fwd_ret_h"].transform("mean")
    df["sign_code"] = df["sign_type"].astype("category").cat.codes
    df["split"] = np.where(df["fy_n"] <= 2016, "discover",
                  np.where(df["fy_n"] <= 2021, "validate",
                  np.where(df["fy_n"] <= 2024, "holdout", "strategy")))
    return df


def _Xmat(d: pd.DataFrame) -> np.ndarray:
    return np.hstack([d[_NUM].to_numpy(dtype=float),
                      d["sign_code"].to_numpy().reshape(-1, 1).astype(float)])


def _boot_ci_dateblock(d: pd.DataFrame, sel_mask: np.ndarray, ycol: str,
                       rng: np.random.Generator) -> tuple[float, float, float, float]:
    """Date-blocked bootstrap on mean(selected) - mean(rest). Returns
    (point, lo, hi, p(diff<=0))."""
    y = d[ycol].to_numpy(dtype=float)
    dates = d["fired_on"].to_numpy()
    uniq = np.unique(dates)
    # index lists per date
    by_date = {dt: np.where(dates == dt)[0] for dt in uniq}
    point = y[sel_mask].mean() - y[~sel_mask].mean()
    diffs = np.empty(_NBOOT)
    for i in range(_NBOOT):
        pick = rng.choice(uniq, len(uniq), replace=True)
        idx = np.concatenate([by_date[dt] for dt in pick])
        sm = sel_mask[idx]
        if sm.sum() < 5 or (~sm).sum() < 5:
            diffs[i] = np.nan
            continue
        diffs[i] = y[idx][sm].mean() - y[idx][~sm].mean()
    diffs = diffs[~np.isnan(diffs)]
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    p_le0 = float((diffs <= 0).mean())
    return float(point), float(lo), float(hi), p_le0


def _decile_spread(pred: np.ndarray, actual: np.ndarray, edges: np.ndarray) -> float:
    b = np.digitize(pred, edges)
    top = actual[b >= 9]
    bot = actual[b <= 0]
    if len(top) < 20 or len(bot) < 20:
        return np.nan
    return (top.mean() - bot.mean()) * 100


def run() -> None:
    df = _load()
    disc = df[df["split"] == "discover"]
    logger.info("discover train n={}", len(disc))
    rng = np.random.default_rng(_SEED)

    for tgt, ycol in [("RAW fwd_ret_h", "fwd_ret_h"),
                      ("MARKET-DEMEANED fwd_ret_h", "fwd_dm")]:
        model = HistGradientBoostingRegressor(
            max_depth=3, max_iter=400, learning_rate=0.05,
            categorical_features=[len(_NUM)], random_state=_SEED,
            l2_regularization=1.0)
        model.fit(_Xmat(disc), disc[ycol].to_numpy(dtype=float))

        disc_pred = model.predict(_Xmat(disc))
        q75 = np.quantile(disc_pred, 0.75)                 # frozen selection bar
        dec_edges = np.quantile(disc_pred, np.linspace(0.1, 0.9, 9))  # frozen deciles

        print("\n" + "=" * 80)
        print(f"STAGE-1 — target: {tgt}   (frozen discover Q75 bar = {q75:+.5f})")
        print("=" * 80)
        print(f"{'split':<10}{'n_sel':>7}{'n_rest':>8}{'sel_pp':>9}{'rest_pp':>9}"
              f"{'Δpp':>8}{'95% CI':>20}{'p(Δ≤0)':>9}{'dec_sp':>8}")
        for sp in ["discover", "validate", "holdout", "strategy"]:
            d = df[df["split"] == sp].reset_index(drop=True)
            pred = model.predict(_Xmat(d))
            sel = pred >= q75
            if sel.sum() < 5 or (~sel).sum() < 5:
                continue
            y = d[ycol].to_numpy(dtype=float)
            point, lo, hi, ple0 = _boot_ci_dateblock(d, sel, ycol, rng)
            dsp = _decile_spread(pred, y, dec_edges)
            print(f"{sp:<10}{int(sel.sum()):>7}{int((~sel).sum()):>8}"
                  f"{y[sel].mean()*100:>9.3f}{y[~sel].mean()*100:>9.3f}"
                  f"{point*100:>8.3f}{'['+f'{lo*100:+.2f},{hi*100:+.2f}'+']':>20}"
                  f"{ple0:>9.3f}{dsp:>8.3f}")

        # per-FY breakdown on validate+holdout+strategy
        print(f"\n  per-FY selected−rest (Δpp) [n_sel]:")
        oos = df[df["split"] != "discover"]
        line = []
        for fy in sorted(oos["fy_n"].unique()):
            d = df[df["fy_n"] == fy].reset_index(drop=True)
            pred = model.predict(_Xmat(d))
            sel = pred >= q75
            if sel.sum() < 10 or (~sel).sum() < 10:
                line.append(f"FY{fy}: n/a")
                continue
            y = d[ycol].to_numpy(dtype=float)
            line.append(f"FY{fy}: {(y[sel].mean()-y[~sel].mean())*100:+.2f} [{int(sel.sum())}]")
        print("    " + "  ".join(line))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
