"""Peak Feature — Information Value analysis.

Loads peak_feature_records for a given run, optionally excluding crash
periods (is_crash=True), and computes Information Value (IV) for every
context feature.

Crash detection: n225_20d_ret < −5 % → is_crash=True.  Run with
--include-crash to include those periods for comparison.

For each peak direction (HIGH / LOW) separately:
  Favorable outcome definition:
    HIGH peak → favorable = outcome_direction == −1  (reversal down)
    LOW  peak → favorable = outcome_direction == +1  (reversal up)

  Per feature, per quartile bin (Q1=lowest … Q4=highest feature value):
    dir%   = fraction of records with favorable outcome
    mag    = mean |outcome_magnitude| for ALL records in the bin

IV interpretation: <0.02 useless · 0.02–0.10 weak · 0.10–0.30 medium · >0.30 strong

CLI:
    uv run --env-file devenv python -m src.analysis.peak_iv --run-id 2
    uv run --env-file devenv python -m src.analysis.peak_iv --run-id 2 --include-crash
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.data.db import get_session
from src.analysis.models import PeakFeatureRecord, PeakFeatureRun

# ── Feature groups ─────────────────────────────────────────────────────────

_TECH_FEATURES = ["sma20_dist", "rsi14", "bb_pct_b", "vol_ratio", "trend_age_bars"]
_REGIME_FEATURES = ["n225_sma20_dist", "n225_20d_ret"]
_CORR_FEATURES = ["corr_n225", "corr_gspc", "corr_hsi"]
_SIGN_FEATURES = [
    "sign_div_bar", "sign_div_vol", "sign_div_gap", "sign_div_peer",
    "sign_corr_flip", "sign_corr_shift", "sign_corr_peak",
    "sign_str_hold", "sign_str_lead",
    "sign_brk_sma", "sign_brk_bol",
    "sign_rev_lo", "sign_rev_hi", "sign_rev_nhi", "sign_rev_nlo",
    "sign_active_count",
]

ALL_FEATURES = _TECH_FEATURES + _REGIME_FEATURES + _CORR_FEATURES + _SIGN_FEATURES
_N_BINS = 4


# ── IV + magnitude computation ────────────────────────────────────────────────

def _bin_stats(
    feature: pd.Series,
    label: pd.Series,
    magnitude: pd.Series,
    n_bins: int = _N_BINS,
) -> tuple[float, list[tuple[float, float]]]:
    """Return (IV, [(dir_rate, mean_mag), ...]) per bin (low → high).

    Sign features (name starts with "sign_") have NaN filled with 0.
    magnitude is |outcome_magnitude|; mean is over all records in each bin.
    """
    df = pd.DataFrame({"f": feature, "y": label, "m": magnitude}).dropna(subset=["y"])

    is_sign = feature.name and str(feature.name).startswith("sign_")
    if is_sign:
        df["f"] = df["f"].fillna(0.0)
    else:
        df = df.dropna(subset=["f"])

    if len(df) < 20:
        return np.nan, []

    total_good = df["y"].sum()
    total_bad  = (1 - df["y"]).sum()
    if total_good == 0 or total_bad == 0:
        return np.nan, []

    try:
        df["bin"] = pd.qcut(df["f"], q=n_bins, duplicates="drop")
    except ValueError:
        return np.nan, []

    iv = 0.0
    bins: list[tuple[float, float]] = []
    for _, grp in df.groupby("bin", observed=True):
        n_good   = grp["y"].sum()
        n_bad    = len(grp) - n_good
        p_good   = max(n_good / total_good, 1e-9)
        p_bad    = max(n_bad  / total_bad,  1e-9)
        woe      = np.log(p_good / p_bad)
        iv      += (p_good - p_bad) * woe
        dir_rate = float(n_good / len(grp))
        mean_mag = float(grp["m"].abs().mean()) if grp["m"].notna().any() else float("nan")
        bins.append((dir_rate, mean_mag))

    return iv, bins


def _iv_table(df: pd.DataFrame, direction: int, label: str) -> pd.DataFrame:
    """Compute IV + quartile stats table for one peak direction."""
    sub = df[df["peak_direction"] == direction].copy()
    if len(sub) < 30:
        logger.warning("Too few records for direction={}: n={}", direction, len(sub))
        return pd.DataFrame()

    if direction == 2:    # HIGH peak → favorable = outcome goes DOWN
        sub["fav"] = (sub["outcome_direction"] == -1).astype(float)
    else:                  # LOW peak  → favorable = outcome goes UP
        sub["fav"] = (sub["outcome_direction"] == +1).astype(float)

    sub = sub.dropna(subset=["fav"])
    if sub.empty:
        return pd.DataFrame()

    overall_fav    = sub["fav"].mean()
    overall_mag    = sub["outcome_magnitude"].abs().mean()

    rows = []
    for feat in ALL_FEATURES:
        if feat not in sub.columns:
            continue
        iv, bins = _bin_stats(sub[feat], sub["fav"], sub["outcome_magnitude"])
        n_valid = (
            int(len(sub)) if str(feat).startswith("sign_")
            else int(sub[feat].notna().sum())
        )

        # Format each bin as "dir%(mag)"
        def _fmt(b: tuple[float, float]) -> str:
            dr, mg = b
            mag_s = f"{mg:.3f}" if not np.isnan(mg) else "—"
            return f"{dr:.0%}({mag_s})"

        bin_cols = {f"Q{i+1}": _fmt(b) for i, b in enumerate(bins)} if bins else {}
        # Pad missing quartiles (collapsed bins)
        for i in range(_N_BINS):
            bin_cols.setdefault(f"Q{i+1}", "—")

        rows.append({
            "feature": feat,
            "n":       n_valid,
            "iv":      round(iv, 4) if not np.isnan(iv) else None,
            **bin_cols,
        })

    result = (
        pd.DataFrame(rows)
        .sort_values("iv", ascending=False, na_position="last")
    )
    result.insert(0, "dir",           label)
    result.insert(1, "n_peaks",       len(sub))
    result.insert(2, "overall",       f"{overall_fav:.1%}({overall_mag:.3f})")
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run_iv(run_id: int, include_crash: bool = False) -> None:
    with get_session() as session:
        pfr = session.get(PeakFeatureRun, run_id)
        if pfr is None:
            raise SystemExit(f"No PeakFeatureRun with id={run_id}")

        logger.info(
            "Loading run {} | {} | {} – {}",
            run_id, pfr.stock_set,
            pfr.start_dt.date(), pfr.end_dt.date(),
        )

        stmt = select(PeakFeatureRecord).where(PeakFeatureRecord.run_id == run_id)
        if not include_crash:
            stmt = stmt.where(
                (PeakFeatureRecord.is_crash.is_(False))
                | (PeakFeatureRecord.is_crash.is_(None))
            )
        records = session.execute(stmt).scalars().all()

    if not records:
        raise SystemExit("No records found.")

    cols = ["peak_direction", "outcome_direction", "outcome_magnitude"] + ALL_FEATURES
    df = pd.DataFrame([
        {col: getattr(r, col) for col in cols}
        for r in records
    ])

    crash_note = "all periods" if include_crash else "crash periods excluded"
    logger.info("Loaded {} records ({})", len(records), crash_note)

    for direction, label in [(2, "HIGH"), (-2, "LOW")]:
        tbl = _iv_table(df, direction, label)
        if tbl.empty:
            continue

        n_dir = len(df[df["peak_direction"] == direction])
        print(f"\n{'='*100}")
        print(f" {label} peaks  ({crash_note})  n={n_dir}")
        print(f" Columns: dir%(mean_mag) per quartile Q1(low)→Q4(high) of feature value")
        print(f" overall = direction_rate(mean_magnitude) across all peaks")
        print(f"{'='*100}")
        pd.set_option("display.max_colwidth", 14)
        pd.set_option("display.width", 200)
        print(tbl[["feature", "n", "iv", "overall", "Q1", "Q2", "Q3", "Q4"]].to_string(index=False))

    print()


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.peak_iv")
    p.add_argument("--run-id",        type=int, required=True)
    p.add_argument("--include-crash", action="store_true",
                   help="Include crash periods in analysis")
    args = p.parse_args(argv)
    run_iv(args.run_id, include_crash=args.include_crash)


if __name__ == "__main__":
    main()
