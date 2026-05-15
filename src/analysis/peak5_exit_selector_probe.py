"""peak5_exit_selector_probe — Workflow B for /sign-debate peak5_shape cycle.

Reuses the 56k-row fire table from peak5_fire_table.py. Discovers HDBSCAN
clusters on the 9-D shape vector (5 z-prices + 4 time-fractions). For each
cluster, replays 3 representative exit rules on the forward window:

  - TIME20: exit at fire_bar + 20 (close)
  - TRAIL : peak-trailing stop, exit if drawdown from running peak > 4×ATR
  - TPSL  : take-profit at +3×ATR, stop-loss at -2×ATR, time-cap 60 bars

These are SIMPLIFIED proxies for time_stop_20, adx_trail_d8, zs_tp_sl(2.0,2.0).
Goal is to detect cluster-level differential preference between rule classes,
not produce production-grade returns. If clusters show meaningful divergence,
the next step is faithful integration with src/exit/.

Discover/Validate/OOS split:
  Discover: FY2019-FY2022. Fit HDBSCAN on 5k stratified subsample; approximate-
            predict the rest. Compute per-cluster × per-rule mean_r/Sharpe.
            Build a shape-aware selector: cluster → best-Sharpe rule.
  Validate: FY2023-FY2024. Apply selector; compare aggregate Sharpe to the
            universal-default best-of-Discover rule.
  OOS:      FY2025 blind. Same comparison.

Pre-registered falsifier:
  - HDBSCAN noise fraction on Discover ≤ 0.50.
  - At least 3 clusters with n ≥ 100 on Discover.
  - Selector aggregate Sharpe on Validate ≥ default-rule Sharpe + 0.15.
  - Selector aggregate Sharpe on OOS ≥ default-rule Sharpe + 0.10.

CLI: uv run --env-file devenv python -m src.analysis.peak5_exit_selector_probe
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from loguru import logger
from sklearn.cluster import HDBSCAN
from sqlalchemy import select

from src.data.db import get_session
from src.data.models import Ohlcv1d, Stock

_FIRE_TABLE = Path(__file__).parent.parent.parent / "data" / "analysis" / "peak5_shape" / "fire_table_2026-05-15.csv"
_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "peak5_shape"
_ATR_WIN = 14
_FORWARD_BARS = 80
_MIN_CLUSTER_SIZE = 100
_MIN_SAMPLES = 50
_SUBSAMPLE_N = 5000
_SUBSAMPLE_SEED = 20260515
_NOISE_CEILING = 0.50

_DISCOVER_END = datetime.date(2023, 3, 31)   # FY2019-FY2022 inclusive
_VALIDATE_END = datetime.date(2025, 3, 31)   # FY2023-FY2024

_TRAIL_ATR_MULT = 4.0
_TPSL_TP_MULT   = 3.0
_TPSL_SL_MULT   = 2.0
_TPSL_TIME_CAP  = 60


def _load_ohlcv_full(code: str, session) -> pd.DataFrame:
    rows = session.execute(
        select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.high_price,
               Ohlcv1d.low_price, Ohlcv1d.close_price)
        .where(Ohlcv1d.stock_code == code)
        .order_by(Ohlcv1d.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame({
        "open":  [float(r.open_price) for r in rows],
        "high":  [float(r.high_price) for r in rows],
        "low":   [float(r.low_price)  for r in rows],
        "close": [float(r.close_price) for r in rows],
    })


def _atr(df: pd.DataFrame, win: int) -> pd.Series:
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - df["close"].shift()).abs()
    l_pc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(win, min_periods=win).mean()


def _replay_exits(side: str, fire_bar: int, df: pd.DataFrame, atr: float) -> dict:
    """Replay 3 exit rules. Returns dict of rule → log_return."""
    n = len(df)
    forward_end = min(fire_bar + _FORWARD_BARS, n - 1)
    if forward_end - fire_bar < 25:
        return {"TIME20": np.nan, "TRAIL": np.nan, "TPSL": np.nan}

    # Entry at fire_bar's open (two-bar fill assumed handled at upstream sign;
    # here we pin entry to the recorded fire_bar's open).
    entry = df["open"].iloc[fire_bar]
    if entry <= 0 or atr <= 0:
        return {"TIME20": np.nan, "TRAIL": np.nan, "TPSL": np.nan}

    sign = 1 if side == "long" else -1

    # TIME20 — exit at fire_bar + 20 close
    if fire_bar + 20 < n:
        exit_p = df["close"].iloc[fire_bar + 20]
        r_time = sign * np.log(exit_p / entry)
    else:
        r_time = np.nan

    # TRAIL — peak-trailing stop with 4×ATR drawdown
    r_trail = np.nan
    if side == "long":
        peak = entry
        for k in range(1, _FORWARD_BARS + 1):
            j = fire_bar + k
            if j >= n:
                break
            bar_high = df["high"].iloc[j]
            bar_low  = df["low"].iloc[j]
            peak = max(peak, bar_high)
            if peak - bar_low >= _TRAIL_ATR_MULT * atr:
                # exit at next bar's open
                if j + 1 < n:
                    r_trail = np.log(df["open"].iloc[j + 1] / entry)
                else:
                    r_trail = np.log(df["close"].iloc[j] / entry)
                break
        else:
            r_trail = np.log(df["close"].iloc[forward_end] / entry)
    else:  # short
        trough = entry
        for k in range(1, _FORWARD_BARS + 1):
            j = fire_bar + k
            if j >= n:
                break
            bar_high = df["high"].iloc[j]
            bar_low  = df["low"].iloc[j]
            trough = min(trough, bar_low)
            if bar_high - trough >= _TRAIL_ATR_MULT * atr:
                if j + 1 < n:
                    r_trail = -np.log(df["open"].iloc[j + 1] / entry)
                else:
                    r_trail = -np.log(df["close"].iloc[j] / entry)
                break
        else:
            r_trail = -np.log(df["close"].iloc[forward_end] / entry)

    # TPSL — TP at sign * _TPSL_TP_MULT * atr above entry; SL at sign * _TPSL_SL_MULT * atr against
    tp = entry + sign * _TPSL_TP_MULT * atr
    sl = entry - sign * _TPSL_SL_MULT * atr
    r_tpsl = np.nan
    time_limit = min(fire_bar + _TPSL_TIME_CAP, n - 1)
    for k in range(1, _TPSL_TIME_CAP + 1):
        j = fire_bar + k
        if j > time_limit:
            break
        bar_high = df["high"].iloc[j]
        bar_low  = df["low"].iloc[j]
        if side == "long":
            if bar_high >= tp:
                r_tpsl = np.log(tp / entry)
                break
            if bar_low <= sl:
                r_tpsl = np.log(sl / entry)
                break
        else:
            if bar_low <= tp:  # for short, "tp" is below entry — wait, recompute
                pass
        # short branch
        if side == "short":
            tp_s = entry - _TPSL_TP_MULT * atr
            sl_s = entry + _TPSL_SL_MULT * atr
            if bar_low <= tp_s:
                r_tpsl = -np.log(tp_s / entry)
                break
            if bar_high >= sl_s:
                r_tpsl = -np.log(sl_s / entry)
                break
    if np.isnan(r_tpsl):
        if time_limit < n:
            r_tpsl = sign * np.log(df["close"].iloc[time_limit] / entry)

    return {"TIME20": r_time, "TRAIL": r_trail, "TPSL": r_tpsl}


def _build_features(fires: pd.DataFrame) -> np.ndarray:
    """9-D feature vector per fire: 5 z-prices (within-window) + 4 time fractions."""
    prices = fires[["P0_price", "P1_price", "P2_price", "P3_price", "P4_price"]].values
    bars   = fires[["P0_bar", "P1_bar", "P2_bar", "P3_bar", "P4_bar"]].values
    # Sign-flip short-side prices to pool both
    side_long = (fires["side"] == "long").values
    signs = np.where(side_long, 1.0, -1.0).reshape(-1, 1)
    prices = prices * signs   # flipped: shorts are now "rising"
    # Within-row z-score of prices
    mu = prices.mean(axis=1, keepdims=True)
    sd = prices.std(axis=1, keepdims=True)
    sd[sd == 0] = 1.0
    z_prices = (prices - mu) / sd
    # Time fractions
    total = bars[:, -1] - bars[:, 0]
    total[total <= 0] = 1
    fractions = np.diff(bars, axis=1) / total[:, None]
    return np.hstack([z_prices, fractions])  # (n, 9)


def _per_dim_zscore(X: np.ndarray, mu: np.ndarray | None, sigma: np.ndarray | None
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if mu is None or sigma is None:
        mu = X.mean(axis=0)
        sigma = X.std(axis=0)
        sigma[sigma == 0] = 1.0
    return (X - mu) / sigma, mu, sigma


def _corr_distance(X: np.ndarray) -> np.ndarray:
    """1 - Pearson correlation between rows. Returns (n, n) symmetric."""
    Xn = X - X.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(Xn, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xu = Xn / norms
    corr = Xu @ Xu.T
    return np.clip(1.0 - corr, 0.0, 2.0)


def _approximate_predict(X_query: np.ndarray, X_fit: np.ndarray,
                          labels_fit: np.ndarray) -> np.ndarray:
    """Nearest-neighbor assignment in corr-distance. Returns label per query.

    sklearn's HDBSCAN doesn't expose approximate_predict; we approximate by
    assigning each query to the label of its nearest fit point (excluding -1).
    """
    out = np.full(len(X_query), -1, dtype=int)
    # batch corr distance query vs fit
    Xq = X_query - X_query.mean(axis=1, keepdims=True)
    Xq /= np.maximum(np.linalg.norm(Xq, axis=1, keepdims=True), 1e-9)
    Xf = X_fit - X_fit.mean(axis=1, keepdims=True)
    Xf /= np.maximum(np.linalg.norm(Xf, axis=1, keepdims=True), 1e-9)
    sims = Xq @ Xf.T   # (n_query, n_fit)
    dists = 1.0 - sims
    # For each query, find closest fit point. If that fit is labeled -1, try next.
    sorted_idx = dists.argsort(axis=1)
    for i in range(len(X_query)):
        for k in range(min(5, sorted_idx.shape[1])):
            lab = labels_fit[sorted_idx[i, k]]
            if lab != -1:
                out[i] = lab
                break
    return out


def _agg_metrics(rs: np.ndarray) -> tuple[float, float, int]:
    rs = rs[~np.isnan(rs)]
    if len(rs) < 5:
        return float("nan"), float("nan"), len(rs)
    mu = float(rs.mean())
    sd = float(rs.std())
    sharpe = (mu / sd * np.sqrt(252)) if sd > 0 else float("nan")
    return mu, sharpe, len(rs)


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    if not _FIRE_TABLE.exists():
        raise SystemExit(f"Fire table not found: {_FIRE_TABLE}")
    fires = pd.read_csv(_FIRE_TABLE, parse_dates=["fire_date"])
    fires["fire_date"] = fires["fire_date"].dt.date
    logger.info("Loaded {} fires", len(fires))

    # ── Replay exit rules per fire ────────────────────────────────────────────
    logger.info("Replaying exit rules…")
    # Pre-load OHLCV per stock once
    with get_session() as session:
        results: list[dict] = []
        for stock, sub in fires.groupby("stock"):
            df = _load_ohlcv_full(stock, session)
            if df.empty:
                continue
            atr_series = _atr(df, _ATR_WIN)
            for _, row in sub.iterrows():
                fire_bar = int(row["fire_bar"])
                atr = float(atr_series.iloc[row["P4_bar"]]) if row["P4_bar"] < len(atr_series) else float("nan")
                if np.isnan(atr) or atr <= 0:
                    continue
                rs = _replay_exits(row["side"], fire_bar, df, atr)
                results.append({"stock": stock, "fire_date": row["fire_date"], **rs})

    rdf = pd.DataFrame(results).set_index(["stock", "fire_date"])
    fires_idx = fires.set_index(["stock", "fire_date"])
    fires = fires_idx.join(rdf, how="inner").reset_index()
    logger.info("Joined {} fires with exit returns", len(fires))

    # ── Build feature matrix + split ─────────────────────────────────────────
    discover = fires[fires["fire_date"] <= _DISCOVER_END].reset_index(drop=True)
    validate = fires[(fires["fire_date"] > _DISCOVER_END) & (fires["fire_date"] <= _VALIDATE_END)].reset_index(drop=True)
    oos      = fires[fires["fire_date"] > _VALIDATE_END].reset_index(drop=True)
    logger.info("Splits — Discover: {}  Validate: {}  OOS: {}",
                len(discover), len(validate), len(oos))

    X_disc = _build_features(discover)
    X_val  = _build_features(validate)
    X_oos  = _build_features(oos)

    X_disc_n, mu, sigma = _per_dim_zscore(X_disc, None, None)
    X_val_n,  _, _      = _per_dim_zscore(X_val,  mu, sigma)
    X_oos_n,  _, _      = _per_dim_zscore(X_oos,  mu, sigma)

    # ── Subsample 5k for HDBSCAN.fit (stratified by FY) ─────────────────────
    rng = np.random.default_rng(_SUBSAMPLE_SEED)
    take = min(_SUBSAMPLE_N, len(X_disc_n))
    idx = rng.choice(len(X_disc_n), size=take, replace=False)
    X_fit = X_disc_n[idx]

    D_fit = _corr_distance(X_fit)
    logger.info("Fitting HDBSCAN on subsample n={}", take)
    hdb = HDBSCAN(min_cluster_size=_MIN_CLUSTER_SIZE, min_samples=_MIN_SAMPLES,
                  metric="precomputed")
    labels_fit = hdb.fit_predict(D_fit)
    n_clusters_fit = len(set(labels_fit) - {-1})
    noise_frac_fit = (labels_fit == -1).mean()
    logger.info("HDBSCAN fit: {} clusters, noise frac={:.3f}",
                n_clusters_fit, noise_frac_fit)

    # Propagate labels to full Discover/Validate/OOS via nearest-fit-neighbor
    labels_disc = _approximate_predict(X_disc_n, X_fit, labels_fit)
    labels_val  = _approximate_predict(X_val_n,  X_fit, labels_fit)
    labels_oos  = _approximate_predict(X_oos_n,  X_fit, labels_fit)

    discover["cluster"] = labels_disc
    validate["cluster"] = labels_val
    oos["cluster"]      = labels_oos

    noise_disc = (labels_disc == -1).mean()

    # ── Per cluster × rule metrics on Discover ───────────────────────────────
    rules = ["TIME20", "TRAIL", "TPSL"]
    rows_per_cluster: list[dict] = []
    selector: dict[int, str] = {}
    cluster_ids = sorted([c for c in discover["cluster"].unique() if c != -1])
    for cid in cluster_ids:
        sub = discover[discover["cluster"] == cid]
        if len(sub) < 100:
            continue
        best_rule, best_sh = None, -np.inf
        rule_stats = {}
        for rule in rules:
            mu, sh, n = _agg_metrics(sub[rule].values)
            rule_stats[rule] = (mu, sh, n)
            if not np.isnan(sh) and sh > best_sh:
                best_sh = sh
                best_rule = rule
        if best_rule is None:
            continue
        selector[cid] = best_rule
        long_frac = (sub["side"] == "long").mean()
        rows_per_cluster.append({
            "cluster": cid, "n": len(sub), "long_frac": long_frac,
            **{f"{r}_mu": rule_stats[r][0] for r in rules},
            **{f"{r}_sh": rule_stats[r][1] for r in rules},
            "best_rule": best_rule, "best_sharpe": best_sh,
        })

    # ── Default-rule baseline on Discover (best aggregate rule, no clustering) ──
    default_sh = {}
    for rule in rules:
        _, sh, _ = _agg_metrics(discover[rule].values)
        default_sh[rule] = sh
    default_rule = max(default_sh, key=default_sh.get)
    default_sharpe_disc = default_sh[default_rule]

    # ── Apply selector to Validate & OOS ─────────────────────────────────────
    def _selector_returns(df: pd.DataFrame) -> np.ndarray:
        out = np.full(len(df), np.nan)
        for i, (_, row) in enumerate(df.iterrows()):
            cid = int(row["cluster"])
            rule = selector.get(cid, default_rule)
            out[i] = row[rule]
        return out

    val_rs = _selector_returns(validate)
    oos_rs = _selector_returns(oos)
    val_default_rs = validate[default_rule].values
    oos_default_rs = oos[default_rule].values

    val_mu, val_sh, val_n = _agg_metrics(val_rs)
    val_def_mu, val_def_sh, val_def_n = _agg_metrics(val_default_rs)
    oos_mu, oos_sh, oos_n = _agg_metrics(oos_rs)
    oos_def_mu, oos_def_sh, oos_def_n = _agg_metrics(oos_default_rs)

    val_delta = val_sh - val_def_sh
    oos_delta = oos_sh - oos_def_sh

    # ── Pre-registered falsifier ─────────────────────────────────────────────
    noise_pass = noise_disc <= _NOISE_CEILING
    cluster_pass = len([r for r in rows_per_cluster if r["n"] >= 100]) >= 3
    val_pass = (not np.isnan(val_delta)) and val_delta >= 0.15
    oos_pass = (not np.isnan(oos_delta)) and oos_delta >= 0.10
    all_pass = noise_pass and cluster_pass and val_pass and oos_pass
    verdict = "ACCEPT (shape-aware selector beats default)" if all_pass else "REJECT"

    md: list[str] = [
        "# peak5_exit_selector_probe — Workflow B",
        "",
        f"Generated: {today}",
        f"Universe: {fires['stock'].nunique()} stocks · Fires (after exit replay): {len(fires):,}",
        f"Discover/Validate/OOS: {len(discover):,} / {len(validate):,} / {len(oos):,}",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## Pre-registered falsifier gates",
        "",
        "| Gate | Observed | Threshold | Pass? |",
        "|------|----------|-----------|-------|",
        f"| HDBSCAN noise frac (Discover, full) | {noise_disc:.3f} | ≤ 0.50 | {'✓' if noise_pass else '✗'} |",
        f"| Clusters with n≥100 on Discover | {len([r for r in rows_per_cluster if r['n']>=100])} | ≥ 3 | {'✓' if cluster_pass else '✗'} |",
        f"| Selector ΔSharpe vs default on Validate | {val_delta:+.3f} | ≥ +0.15 | {'✓' if val_pass else '✗'} |",
        f"| Selector ΔSharpe vs default on OOS | {oos_delta:+.3f} | ≥ +0.10 | {'✓' if oos_pass else '✗'} |",
        "",
        "## Default-rule baselines (aggregate Sharpe over slice, no clustering)",
        "",
        "| Slice | TIME20 | TRAIL | TPSL | Default(best) |",
        "|-------|--------|-------|------|---------------|",
    ]
    for slice_name, slice_df in [("Discover", discover), ("Validate", validate), ("OOS", oos)]:
        row = f"| {slice_name} "
        for r in rules:
            _, sh, _ = _agg_metrics(slice_df[r].values)
            row += f"| {sh:.3f} "
        _, sh, _ = _agg_metrics(slice_df[default_rule].values)
        row += f"| **{default_rule}**={sh:.3f} |"
        md.append(row)

    md += [
        "",
        f"## Discovery clusters (Discover slice, n≥100, default rule = {default_rule})",
        "",
        "| cluster | n | long_frac | TIME20_sh | TRAIL_sh | TPSL_sh | best_rule | best_sh |",
        "|---------|---|-----------|-----------|----------|---------|-----------|---------|",
    ]
    rows_per_cluster.sort(key=lambda r: -r["best_sharpe"])
    for r in rows_per_cluster:
        md.append(f"| {r['cluster']} | {r['n']} | {r['long_frac']:.3f} | "
                  f"{r['TIME20_sh']:.3f} | {r['TRAIL_sh']:.3f} | {r['TPSL_sh']:.3f} | "
                  f"{r['best_rule']} | {r['best_sharpe']:.3f} |")

    md += [
        "",
        "## Selector aggregates",
        "",
        "| Slice | n | Selector mean_r | Selector Sharpe | Default mean_r | Default Sharpe | ΔSharpe |",
        "|-------|---|-----------------|-----------------|----------------|----------------|---------|",
        f"| Validate | {val_n} | {val_mu*100:+.2f}% | {val_sh:.3f} | {val_def_mu*100:+.2f}% | {val_def_sh:.3f} | **{val_delta:+.3f}** |",
        f"| OOS | {oos_n} | {oos_mu*100:+.2f}% | {oos_sh:.3f} | {oos_def_mu*100:+.2f}% | {oos_def_sh:.3f} | **{oos_delta:+.3f}** |",
        "",
        "## Notes",
        "- Exit rules are SIMPLIFIED proxies, not the production `src/exit/` rules.",
        "  TIME20 = exit at fire+20 close. TRAIL = exit on 4×ATR drawdown from peak.",
        "  TPSL = TP at 3×ATR, SL at 2×ATR, 60-bar cap. If clusters meaningfully prefer",
        "  different rules, next step is faithful integration with src/exit/.",
        "- Fires entered at fire_bar (P4-early peak bar). For production use, the same",
        "  shape clusters would need verification at sign-driven entry bars.",
        "- HDBSCAN fit on 5k subsample (corr-distance precomputed); approximate-predict",
        "  for remaining points via nearest-fit-neighbor on corr-distance.",
    ]

    out = _OUT_DIR / f"exit_selector_{today}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", out)
    print("\n".join(md))


if __name__ == "__main__":
    main()
