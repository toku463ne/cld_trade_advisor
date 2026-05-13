"""k_bar_stock_stationarity_probe — does per-stock historical K-bar behavior
predict future K-bar behavior at the same stock?

Stage 1: cycle-7 cache only (div_gap + rev_nlo, 4,498 events FY2018–FY2024 at
data/analysis/wait_iv_early_cut_probe/events_2026-05-14.csv).

Tests (Critic's tightened spec, 2026-05-14):
  1. Walk-forward Spearman ρ between prior_median_mae_03(stock, date) and
     current event's mae_03. Pooled AND within-regime (ADX states).
     Bootstrap 1000x for 95% CI. Uses cycle-7's signed mae_03 definition.
  2. ICC variance decomposition: σ²_between / (σ²_between + σ²_within) for
     r_k3 ~ stock_code (stocks with n_events ≥ 5).
  3. Tertile persistence: chronological half-split per stock (n_stock ≥ 10).
     Binomial test vs 1/3 null.
  4. Sign-flip falsifier: stock_code-permuted shuffle of the prior_median feature
     (perturbs same axis as the signal). Mean ρ over 5 shuffles; expect ≈ 0.

Accept gate: Test 1 pooled ρ ≥ +0.10 with bootstrap 95% LB > 0, AND
Test 3 persistence rate ≥ 40% with binomial p < 0.05, AND
Test 4 mean |ρ| < 0.05.

Falsifier: Test 1 LB ≤ 0 OR Test 3 rate ≤ 36% OR Test 4 |ρ| ≥ 0.05.

CLI: uv run --env-file devenv python -m src.analysis.k_bar_stock_stationarity_probe
"""

from __future__ import annotations

import datetime
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats
from sqlalchemy import select

from src.analysis.models import N225RegimeSnapshot
from src.data.db import get_session

_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "analysis" / "wait_iv_early_cut_probe" / "events_2026-05-14.csv"
_OUT_DIR  = Path(__file__).parent.parent.parent / "data" / "analysis" / "k_bar_stationarity"

_ADX_CHOPPY    = 20.0
_N_PRIOR_MIN   = 3       # Test 1
_N_ICC_MIN     = 5       # Test 2
_N_TERT_MIN    = 10      # Test 3 (Critic floor)
_BOOTSTRAP_N   = 1000
_SHUFFLE_N     = 5
_RNG_SEED      = 20260514


@dataclass
class _Test1Result:
    label:   str
    n:       int
    rho:     float
    p_value: float
    ci_low:  float
    ci_high: float


# ──────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────

def _load_events() -> pd.DataFrame:
    if not _CSV_PATH.exists():
        raise FileNotFoundError(f"Missing cycle-7 cache: {_CSV_PATH}")
    df = pd.read_csv(_CSV_PATH, parse_dates=["fire_date"])
    df["fire_date"] = df["fire_date"].dt.date
    df = df.dropna(subset=["mae_03", "r_k3"]).copy()
    df = df.sort_values(["stock", "fire_date"]).reset_index(drop=True)
    logger.info("Loaded {:,} events from cycle-7 cache", len(df))
    return df


def _load_regime_map() -> dict[datetime.date, str]:
    """Return date → ADX state ('bull' / 'bear' / 'choppy' / 'unknown')."""
    with get_session() as s:
        rows = s.execute(select(N225RegimeSnapshot)).scalars().all()
    out: dict[datetime.date, str] = {}
    for r in rows:
        if r.adx is None or r.adx_pos is None or r.adx_neg is None:
            out[r.date] = "unknown"
            continue
        if r.adx < _ADX_CHOPPY:
            out[r.date] = "choppy"
        elif r.adx_pos > r.adx_neg:
            out[r.date] = "bull"
        else:
            out[r.date] = "bear"
    logger.info("Loaded {:,} regime snapshots", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Feature construction
# ──────────────────────────────────────────────────────────────────────────

def _add_prior_median(df: pd.DataFrame, value_col: str, group_col: str = "stock") -> pd.Series:
    """For each row, compute median of value_col over rows in the same group
    with strictly earlier fire_date. NaN where fewer than _N_PRIOR_MIN priors.
    """
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for _, grp in df.groupby(group_col, sort=False):
        # grp already sorted by fire_date (df was pre-sorted)
        vals = grp[value_col].to_numpy()
        idx = grp.index.to_numpy()
        for i in range(len(grp)):
            if i < _N_PRIOR_MIN:
                continue
            out.iat[df.index.get_loc(idx[i])] = float(np.median(vals[:i]))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────

def _bootstrap_ci_spearman(x: np.ndarray, y: np.ndarray, n_boot: int, rng: np.random.Generator) -> tuple[float, float]:
    n = len(x)
    if n < 10:
        return float("nan"), float("nan")
    rhos = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        rhos[i] = stats.spearmanr(x[idx], y[idx]).statistic
    return float(np.percentile(rhos, 2.5)), float(np.percentile(rhos, 97.5))


def test1_walkforward_spearman(
    df: pd.DataFrame, regime_map: dict[datetime.date, str],
) -> list[_Test1Result]:
    df = df.copy()
    df["prior_med_mae"] = _add_prior_median(df, "mae_03")
    df["regime"] = df["fire_date"].map(regime_map).fillna("unknown")
    use = df.dropna(subset=["prior_med_mae"]).copy()
    logger.info("Test 1: {:,} of {:,} events have ≥{} priors",
                len(use), len(df), _N_PRIOR_MIN)
    rng = np.random.default_rng(_RNG_SEED)

    results: list[_Test1Result] = []
    for label, sub in [("POOLED", use), *[(reg, use[use["regime"] == reg])
                                          for reg in ["bull", "bear", "choppy"]]]:
        n = len(sub)
        if n < 10:
            results.append(_Test1Result(label, n, float("nan"), float("nan"),
                                        float("nan"), float("nan")))
            continue
        s = stats.spearmanr(sub["prior_med_mae"].to_numpy(), sub["mae_03"].to_numpy())
        lo, hi = _bootstrap_ci_spearman(sub["prior_med_mae"].to_numpy(),
                                        sub["mae_03"].to_numpy(), _BOOTSTRAP_N, rng)
        results.append(_Test1Result(label, n, float(s.statistic), float(s.pvalue), lo, hi))
    return results


def test2_icc(df: pd.DataFrame, value_col: str = "r_k3") -> tuple[float, int, int]:
    """One-way ANOVA ICC: σ²_between / (σ²_between + σ²_within).
    Returns (icc, n_stocks_used, n_events_used)."""
    counts = df["stock"].value_counts()
    keep_stocks = counts[counts >= _N_ICC_MIN].index
    use = df[df["stock"].isin(keep_stocks)]
    if use.empty:
        return float("nan"), 0, 0
    groups = [g[value_col].to_numpy() for _, g in use.groupby("stock")]
    k = len(groups)
    n_total = sum(len(g) for g in groups)
    grand_mean = float(use[value_col].mean())
    ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in groups)
    ss_within  = sum(((g - g.mean()) ** 2).sum() for g in groups)
    if k <= 1 or n_total - k <= 0:
        return float("nan"), k, n_total
    ms_between = ss_between / (k - 1)
    ms_within  = ss_within  / (n_total - k)
    avg_n = n_total / k
    icc = (ms_between - ms_within) / (ms_between + (avg_n - 1) * ms_within)
    return float(icc), int(k), int(n_total)


def test3_tertile_persistence(df: pd.DataFrame, value_col: str = "mae_03") -> tuple[float, int, int, float]:
    """Per stock with n ≥ _N_TERT_MIN: split chronologically, tertile each half by
    median(value_col), check if halves land in same tertile. Binomial p vs 1/3.
    Returns (rate, n_match, n_stocks_used, p_value)."""
    counts = df["stock"].value_counts()
    keep = counts[counts >= _N_TERT_MIN].index
    use = df[df["stock"].isin(keep)].sort_values(["stock", "fire_date"])
    if use.empty:
        return float("nan"), 0, 0, float("nan")

    # Compute each stock's median(value_col) on each chronological half
    halves: list[tuple[str, float, float]] = []
    for stock, grp in use.groupby("stock"):
        vals = grp[value_col].to_numpy()
        mid = len(vals) // 2
        if mid < 2 or len(vals) - mid < 2:
            continue
        halves.append((stock, float(np.median(vals[:mid])), float(np.median(vals[mid:]))))
    if not halves:
        return float("nan"), 0, 0, float("nan")

    first_meds  = np.array([h[1] for h in halves])
    second_meds = np.array([h[2] for h in halves])
    # Tertile cutoffs from each half's distribution separately
    f_q1, f_q2 = np.percentile(first_meds,  [33.333, 66.667])
    s_q1, s_q2 = np.percentile(second_meds, [33.333, 66.667])

    def _tertile(v: float, q1: float, q2: float) -> int:
        return 0 if v <= q1 else (1 if v <= q2 else 2)

    matches = 0
    for _, f_m, s_m in halves:
        if _tertile(f_m, f_q1, f_q2) == _tertile(s_m, s_q1, s_q2):
            matches += 1
    n = len(halves)
    rate = matches / n
    # Binomial test vs 1/3
    res = stats.binomtest(matches, n, p=1/3, alternative="greater")
    return float(rate), int(matches), int(n), float(res.pvalue)


def test4_signflip_falsifier(df: pd.DataFrame) -> tuple[float, float]:
    """Permute stock_code, recompute prior_median_mae under shuffled labels,
    measure Spearman ρ vs actual mae_03. Average over _SHUFFLE_N shuffles.
    Returns (mean_rho, std_rho)."""
    rng = random.Random(_RNG_SEED)
    rhos: list[float] = []
    for _ in range(_SHUFFLE_N):
        shuffled = df.copy()
        stocks = shuffled["stock"].tolist()
        rng.shuffle(stocks)
        shuffled["stock"] = stocks
        shuffled = shuffled.sort_values(["stock", "fire_date"]).reset_index(drop=True)
        shuffled["prior_med_mae_perm"] = _add_prior_median(shuffled, "mae_03")
        use = shuffled.dropna(subset=["prior_med_mae_perm"])
        if len(use) < 10:
            continue
        s = stats.spearmanr(use["prior_med_mae_perm"].to_numpy(), use["mae_03"].to_numpy())
        rhos.append(float(s.statistic))
    arr = np.array(rhos)
    return float(arr.mean()), float(arr.std())


# ──────────────────────────────────────────────────────────────────────────
# Verdict + Report
# ──────────────────────────────────────────────────────────────────────────

def _decide(t1: list[_Test1Result], t3: tuple, t4: tuple) -> tuple[str, list[str]]:
    pooled = next((r for r in t1 if r.label == "POOLED"), None)
    pool_lb = pooled.ci_low if pooled else float("nan")
    pool_rho = pooled.rho if pooled else float("nan")
    t3_rate, _, _, t3_p = t3
    t4_mean, _ = t4

    pool_ok = (not math.isnan(pool_rho)) and pool_rho >= 0.10 and pool_lb > 0
    t3_ok   = (not math.isnan(t3_rate)) and t3_rate >= 0.40 and t3_p < 0.05
    t4_ok   = abs(t4_mean) < 0.05

    notes = [
        f"Test 1 pooled ρ={pool_rho:+.4f} LB={pool_lb:+.4f} ({'✓' if pool_ok else '✗'} need ρ≥+0.10, LB>0)",
        f"Test 3 rate={t3_rate*100:.1f}% p={t3_p:.4f} ({'✓' if t3_ok else '✗'} need rate≥40%, p<0.05)",
        f"Test 4 |ρ|={abs(t4_mean):.4f} ({'✓' if t4_ok else '✗'} need <0.05)",
    ]

    # Falsifier first (definitive reject)
    if (not math.isnan(pool_lb) and pool_lb <= 0) \
       or (not math.isnan(t3_rate) and t3_rate <= 0.36) \
       or (not math.isnan(t4_mean) and abs(t4_mean) >= 0.05):
        return "REJECT (falsifier triggered)", notes
    if pool_ok and t3_ok and t4_ok:
        return "ACCEPT (proceed to Stage 2 debate)", notes
    return "INSUFFICIENT (between gates)", notes


def _write_report(
    t1: list[_Test1Result], t2: tuple, t3: tuple, t4: tuple,
    verdict: str, notes: list[str],
) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = _OUT_DIR / f"probe_{today}.md"

    icc, k_stocks, n_total = t2
    rate, n_match, n_stk, p_val = t3
    t4_mean, t4_std = t4

    md: list[str] = [
        "# K-bar Stock Stationarity Probe — Stage 1",
        "",
        f"Generated: {today}  ",
        f"Source: `{_CSV_PATH.relative_to(Path(__file__).parent.parent.parent)}` "
        "(cycle-7 cache, div_gap + rev_nlo, FY2018–FY2024)  ",
        "",
        "## Test 1 — Walk-forward Spearman ρ",
        "",
        f"prior_median_mae_03(stock, date) vs current event's mae_03 "
        f"(n_prior ≥ {_N_PRIOR_MIN}; bootstrap 95% CI from {_BOOTSTRAP_N} resamples)",
        "",
        "| Regime | n | ρ | p | CI low | CI high |",
        "|--------|--:|--:|--:|--:|--:|",
    ]
    for r in t1:
        if math.isnan(r.rho):
            md.append(f"| {r.label} | {r.n} | — | — | — | — |")
        else:
            md.append(
                f"| {r.label} | {r.n} | {r.rho:+.4f} | {r.p_value:.4f} | "
                f"{r.ci_low:+.4f} | {r.ci_high:+.4f} |"
            )

    md += [
        "",
        "## Test 2 — Intraclass correlation (r_k3 ~ stock_code)",
        "",
        f"Stocks with n_events ≥ {_N_ICC_MIN}: **{k_stocks}**  ",
        f"Events used: **{n_total:,}**  ",
        f"ICC: **{icc:+.4f}** (σ²_between / (σ²_between + σ²_within))",
        "",
        "## Test 3 — Tertile persistence (chronological half-split)",
        "",
        f"Stocks with n_events ≥ {_N_TERT_MIN}: **{n_stk}**  ",
        f"Halves matching tertile: **{n_match} / {n_stk}**  ",
        f"Persistence rate: **{rate*100:.1f}%**  (null = 33.3%)  ",
        f"Binomial p-value (one-sided, greater than 1/3): **{p_val:.4f}**",
        "",
        "## Test 4 — Sign-flip falsifier (stock_code permutation)",
        "",
        f"prior_median_mae_03 under shuffled stock labels vs actual mae_03  ",
        f"Shuffles: {_SHUFFLE_N}  ",
        f"Mean ρ: **{t4_mean:+.4f}** (std {t4_std:.4f})  ",
        f"Required: |ρ| < 0.05",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
    ]
    for n in notes:
        md.append(f"- {n}")
    md.append("")

    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", path)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    df = _load_events()
    regime_map = _load_regime_map()
    t1 = test1_walkforward_spearman(df, regime_map)
    t2 = test2_icc(df, "r_k3")
    t3 = test3_tertile_persistence(df, "mae_03")
    t4 = test4_signflip_falsifier(df)
    verdict, notes = _decide(t1, t3, t4)
    _write_report(t1, t2, t3, t4, verdict, notes)
    print("\n=== STAGE-1 VERDICT ===")
    print(verdict)
    for n in notes:
        print("  -", n)


if __name__ == "__main__":
    main()
