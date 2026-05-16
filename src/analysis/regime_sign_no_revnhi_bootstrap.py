"""Bootstrap CI on Δ Sharpe / Δ mean_r for the no-rev_nhi swap.

Pre-ship certification per the recommendation in
``docs/analysis/rev_nhi_remove_from_ranking.md``.

Approach
--------
1. Re-run :mod:`regime_sign_no_revnhi_probe._run_arm` for both arms
   (baseline and ``EXCLUDE_SIGNS = {"rev_nhi"}``).
2. Extract per-trade ``return_pct`` per FY.
3. Two bootstraps:

   - **Trade-level (aggregate)**: 10 000 iters resampling individual
     trade returns *independently* within each arm (cohorts differ —
     removing rev_nhi from ranking shifts which trades exist, so paired
     bootstrap is not applicable).  Reports Δ Sharpe and Δ mean_r 95 %
     CI on the aggregate distribution.
   - **FY-level**: 10 000 iters resampling the 5 effective FYs with
     replacement; per resample, compute aggregate Sharpe/mean_r within
     each arm from the *pooled* trades of the picked FYs, then Δ.
     Tests whether the effect generalizes across FYs (the per-FY table
     showed 4/5 positive, 1 slightly negative, so this is the binding
     gate per past discipline).

Pre-registered accept gate
--------------------------
Ship the rev_nhi exclusion iff **all** of:
1. Aggregate trade-level Δ Sharpe 95 % CI lower bound > 0.
2. FY-level Δ Sharpe 95 % CI lower bound > 0.
3. ≥ 3 of 5 effective FYs have point Δ Sharpe ≥ 0.

Otherwise REJECT pre-ship and either run a wider probe or shelve the
change.

Run
---
    uv run --env-file devenv python -m src.analysis.regime_sign_no_revnhi_bootstrap
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from loguru import logger

from src.analysis import regime_sign_backtest as rsb
from src.analysis.regime_sign_no_revnhi_probe import _run_arm


_N_BOOT       = 10_000
_RANDOM_SEED  = 20260516
_REPORT_PATH  = Path(__file__).parent / "regime_sign_no_revnhi_bootstrap.md"


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return float("nan")
    m = statistics.mean(returns)
    try:
        s = statistics.stdev(returns)
    except statistics.StatisticsError:
        return float("nan")
    if s <= 0:
        return float("nan")
    return m / s * math.sqrt(252)


def _extract_per_fy(
    fy_results: list[rsb.FyBacktestResult],
) -> dict[str, list[float]]:
    """{FY label: list of trade return_pct}."""
    out: dict[str, list[float]] = {}
    for fyr in fy_results:
        out[fyr.config.label] = [r.return_pct for r in fyr.results]
    return out


def _bootstrap_trade_level(
    a: list[float],
    b: list[float],
    n_boot: int = _N_BOOT,
    seed:   int = _RANDOM_SEED,
) -> tuple[float, float, float, float, float, float, float]:
    """Independent-resample bootstrap on aggregate trade returns.

    Returns (point_d_sharpe, ci_lo_sharpe, ci_hi_sharpe,
             point_d_mr,     ci_lo_mr,     ci_hi_mr,
             p_neg_sharpe).
    """
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    d_sh: list[float] = []
    d_mr: list[float] = []
    for _ in range(n_boot):
        sa = [a[rng.randrange(na)] for _ in range(na)]
        sb = [b[rng.randrange(nb)] for _ in range(nb)]
        sha = _sharpe(sa)
        shb = _sharpe(sb)
        if math.isnan(sha) or math.isnan(shb):
            continue
        d_sh.append(sha - shb)
        d_mr.append(statistics.mean(sa) - statistics.mean(sb))
    d_sh.sort()
    d_mr.sort()
    pt_sh = _sharpe(a) - _sharpe(b)
    pt_mr = statistics.mean(a) - statistics.mean(b)
    n = len(d_sh)
    return (
        pt_sh,
        d_sh[int(0.025 * n)],
        d_sh[int(0.975 * n)],
        pt_mr,
        d_mr[int(0.025 * len(d_mr))],
        d_mr[int(0.975 * len(d_mr))],
        sum(1 for x in d_sh if x <= 0) / n,
    )


def _bootstrap_fy_level(
    per_fy_a: dict[str, list[float]],
    per_fy_b: dict[str, list[float]],
    fy_labels: list[str],
    n_boot: int = _N_BOOT,
    seed:   int = _RANDOM_SEED + 1,
) -> tuple[float, float, float, float, float, float, float]:
    """FY-level bootstrap.

    Resample the 5 effective FY labels with replacement; pool the
    picked FYs' trades to compute aggregate Sharpe per arm; report
    Δ Sharpe + Δ mean_r CIs.
    """
    rng = random.Random(seed)
    k = len(fy_labels)
    d_sh: list[float] = []
    d_mr: list[float] = []
    for _ in range(n_boot):
        picked = [fy_labels[rng.randrange(k)] for _ in range(k)]
        pool_a: list[float] = []
        pool_b: list[float] = []
        for fy in picked:
            pool_a.extend(per_fy_a[fy])
            pool_b.extend(per_fy_b[fy])
        sha = _sharpe(pool_a)
        shb = _sharpe(pool_b)
        if math.isnan(sha) or math.isnan(shb):
            continue
        d_sh.append(sha - shb)
        if pool_a and pool_b:
            d_mr.append(statistics.mean(pool_a) - statistics.mean(pool_b))
    d_sh.sort()
    d_mr.sort()
    pool_all_a = [r for fy in fy_labels for r in per_fy_a[fy]]
    pool_all_b = [r for fy in fy_labels for r in per_fy_b[fy]]
    pt_sh = _sharpe(pool_all_a) - _sharpe(pool_all_b)
    pt_mr = statistics.mean(pool_all_a) - statistics.mean(pool_all_b)
    n = len(d_sh)
    return (
        pt_sh,
        d_sh[int(0.025 * n)],
        d_sh[int(0.975 * n)],
        pt_mr,
        d_mr[int(0.025 * len(d_mr))],
        d_mr[int(0.975 * len(d_mr))],
        sum(1 for x in d_sh if x <= 0) / n,
    )


def main() -> None:
    # ── Run both arms (re-uses _run_arm from the prior probe) ──────────
    baseline_fy  = _run_arm(frozenset(),                  label="baseline")
    treatment_fy = _run_arm(frozenset({"rev_nhi"}),       label="no-rev_nhi")

    base_per_fy = _extract_per_fy(baseline_fy)
    treat_per_fy = _extract_per_fy(treatment_fy)

    # Drop FYs with zero trades in either arm (FY2019, FY2020 in dev DB)
    fy_labels = [
        fy for fy in base_per_fy
        if len(base_per_fy[fy]) > 0 and len(treat_per_fy[fy]) > 0
    ]
    logger.info("Effective FYs for bootstrap: {}", fy_labels)

    # Aggregate trade returns
    agg_base  = [r for fy in fy_labels for r in base_per_fy[fy]]
    agg_treat = [r for fy in fy_labels for r in treat_per_fy[fy]]
    logger.info("Trade counts — baseline: {}, no-rev_nhi: {}",
                len(agg_base), len(agg_treat))

    # ── Bootstrap 1: trade-level aggregate ─────────────────────────────
    (pt_sh, lo_sh, hi_sh, pt_mr, lo_mr, hi_mr, p_neg) = _bootstrap_trade_level(
        agg_treat, agg_base  # arm A = treatment so Δ = treatment − baseline
    )

    # ── Bootstrap 2: FY-level ─────────────────────────────────────────
    (fpt_sh, flo_sh, fhi_sh, fpt_mr, flo_mr, fhi_mr, fp_neg) = _bootstrap_fy_level(
        treat_per_fy, base_per_fy, fy_labels
    )

    # ── Per-FY point Sharpe deltas (for the 3-of-5 gate) ──────────────
    per_fy_d_sh: dict[str, float] = {}
    for fy in fy_labels:
        sa = _sharpe(treat_per_fy[fy])
        sb = _sharpe(base_per_fy[fy])
        per_fy_d_sh[fy] = (sa if not math.isnan(sa) else 0.0) - (
            sb if not math.isnan(sb) else 0.0
        )
    fy_pos = sum(1 for v in per_fy_d_sh.values() if v >= 0)

    # ── Pre-registered gate ────────────────────────────────────────────
    pass1 = lo_sh > 0
    pass2 = flo_sh > 0
    pass3 = fy_pos >= 3
    verdict = "SHIP" if (pass1 and pass2 and pass3) else "DO NOT SHIP"

    # ── Report ─────────────────────────────────────────────────────────
    lines = [
        "# no-rev_nhi pre-ship bootstrap CI",
        "",
        f"Generated: {__import__('datetime').date.today()}",
        f"Seed (trade-level): {_RANDOM_SEED}  |  Seed (FY-level): {_RANDOM_SEED+1}",
        f"Bootstrap iterations: {_N_BOOT:,}",
        "",
        "## Cohort",
        "",
        f"Effective FYs: {', '.join(fy_labels)}  ({len(fy_labels)} of 7)",
        f"FY2019, FY2020 excluded — zero `SignBenchmarkRun` rows in dev DB.",
        "",
        f"Trade counts — baseline n={len(agg_base)}, no-rev_nhi n={len(agg_treat)}",
        "",
        "## Bootstrap 1 — trade-level aggregate",
        "",
        "Resample individual trade returns with replacement, *independently* per",
        "arm.  Tests whether the aggregate Sharpe/mean_r gap survives trade-",
        "level variance.",
        "",
        f"- **Δ Sharpe** (no-rev_nhi − baseline): point = {pt_sh:+.3f}, "
        f"95 % CI [{lo_sh:+.3f}, {hi_sh:+.3f}], p(Δ≤0) = {p_neg:.3f}",
        f"- **Δ mean_r** : point = {pt_mr*100:+.3f} pp, "
        f"95 % CI [{lo_mr*100:+.3f} pp, {hi_mr*100:+.3f} pp]",
        "",
        f"Gate 1 (Δ Sharpe CI lower > 0): {'PASS' if pass1 else 'FAIL'}",
        "",
        "## Bootstrap 2 — FY-level",
        "",
        "Resample the 5 effective FY labels with replacement; pool the picked",
        "FYs' trades within each arm; compute Δ.  Tests whether the effect",
        "generalizes across FYs (binding gate per past bootstrap-discipline",
        "lessons).",
        "",
        f"- **Δ Sharpe** (no-rev_nhi − baseline): point = {fpt_sh:+.3f}, "
        f"95 % CI [{flo_sh:+.3f}, {fhi_sh:+.3f}], p(Δ≤0) = {fp_neg:.3f}",
        f"- **Δ mean_r** : point = {fpt_mr*100:+.3f} pp, "
        f"95 % CI [{flo_mr*100:+.3f} pp, {fhi_mr*100:+.3f} pp]",
        "",
        f"Gate 2 (FY-level Δ Sharpe CI lower > 0): {'PASS' if pass2 else 'FAIL'}",
        "",
        "## Per-FY point Δ Sharpe",
        "",
        "| FY | Δ Sharpe |",
        "|----|---------:|",
        *[f"| {fy} | {v:+.3f} |" for fy, v in per_fy_d_sh.items()],
        "",
        f"FYs with Δ Sharpe ≥ 0: **{fy_pos} of {len(fy_labels)}**",
        "",
        f"Gate 3 (≥ 3 of 5 FYs Δ Sharpe ≥ 0): {'PASS' if pass3 else 'FAIL'}",
        "",
        "## Verdict",
        "",
        f"**{verdict}** — Gate 1 {'✓' if pass1 else '✗'}, "
        f"Gate 2 {'✓' if pass2 else '✗'}, Gate 3 {'✓' if pass3 else '✗'}",
        "",
    ]
    _REPORT_PATH.write_text("\n".join(lines))
    logger.info("Wrote {}", _REPORT_PATH)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
