"""Bootstrap CI on Δ Sharpe / Δ mean_r for combined-drop swap.

Pre-ship certification per [[project-regime-sign-combined-drop-pass]].
Adapted from `regime_sign_no_revnhi_bootstrap.py` — same 3-gate AND
structure that the rev_nhi swap failed in 2026-05-16 on trade-level CI.

Treatment arm
-------------
EXCLUDE_SIGNS = frozenset({"corr_shift", "div_peer", "str_lag"})

Aggregate A/B already PASS'd the 4-criterion gate (ΔSh +1.62, ΔSo +2.97,
5/5 testable FYs positive, both holdouts positive).  This bootstrap
tests whether the effect survives:

  Gate 1: trade-level Δ Sharpe 95 % CI lower bound > 0
  Gate 2: FY-level   Δ Sharpe 95 % CI lower bound > 0
  Gate 3: ≥ 3 of 5 effective FYs have point Δ Sharpe ≥ 0

Ship iff all 3 PASS.  Otherwise fall back to UI-only salvage same as
the rev_nhi 2026-05-16 outcome.

Run
---
    uv run --env-file devenv python -m src.analysis.regime_sign_combined_drop_bootstrap
"""

from __future__ import annotations

import datetime
import math
import random
import statistics
from pathlib import Path

from loguru import logger

from src.analysis import regime_sign_backtest as rsb
from src.analysis.regime_sign_no_revnhi_probe import _run_arm

_DROP: frozenset[str] = frozenset({"corr_shift", "div_peer", "str_lag"})

_N_BOOT      = 10_000
_RANDOM_SEED = 20260519
_REPORT_PATH = Path(__file__).parent / "regime_sign_combined_drop_bootstrap.md"


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


def _extract_per_fy(fy_results) -> dict[str, list[float]]:
    return {fyr.config.label: [r.return_pct for r in fyr.results]
            for fyr in fy_results}


def _bootstrap_trade_level(a, b, n_boot=_N_BOOT, seed=_RANDOM_SEED):
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    d_sh, d_mr = [], []
    for _ in range(n_boot):
        sa = [a[rng.randrange(na)] for _ in range(na)]
        sb = [b[rng.randrange(nb)] for _ in range(nb)]
        sha, shb = _sharpe(sa), _sharpe(sb)
        if math.isnan(sha) or math.isnan(shb):
            continue
        d_sh.append(sha - shb)
        d_mr.append(statistics.mean(sa) - statistics.mean(sb))
    d_sh.sort(); d_mr.sort()
    pt_sh = _sharpe(a) - _sharpe(b)
    pt_mr = statistics.mean(a) - statistics.mean(b)
    n = len(d_sh)
    return (pt_sh,
            d_sh[int(0.025 * n)], d_sh[int(0.975 * n)],
            pt_mr,
            d_mr[int(0.025 * len(d_mr))], d_mr[int(0.975 * len(d_mr))],
            sum(1 for x in d_sh if x <= 0) / n)


def _bootstrap_fy_level(per_fy_a, per_fy_b, fy_labels,
                        n_boot=_N_BOOT, seed=_RANDOM_SEED + 1):
    rng = random.Random(seed)
    k = len(fy_labels)
    d_sh, d_mr = [], []
    for _ in range(n_boot):
        picked = [fy_labels[rng.randrange(k)] for _ in range(k)]
        pool_a, pool_b = [], []
        for fy in picked:
            pool_a.extend(per_fy_a[fy])
            pool_b.extend(per_fy_b[fy])
        sha, shb = _sharpe(pool_a), _sharpe(pool_b)
        if math.isnan(sha) or math.isnan(shb):
            continue
        d_sh.append(sha - shb)
        if pool_a and pool_b:
            d_mr.append(statistics.mean(pool_a) - statistics.mean(pool_b))
    d_sh.sort(); d_mr.sort()
    pool_all_a = [r for fy in fy_labels for r in per_fy_a[fy]]
    pool_all_b = [r for fy in fy_labels for r in per_fy_b[fy]]
    pt_sh = _sharpe(pool_all_a) - _sharpe(pool_all_b)
    pt_mr = statistics.mean(pool_all_a) - statistics.mean(pool_all_b)
    n = len(d_sh)
    return (pt_sh,
            d_sh[int(0.025 * n)], d_sh[int(0.975 * n)],
            pt_mr,
            d_mr[int(0.025 * len(d_mr))], d_mr[int(0.975 * len(d_mr))],
            sum(1 for x in d_sh if x <= 0) / n)


def main() -> None:
    baseline_fy  = _run_arm(frozenset(), label="baseline")
    treatment_fy = _run_arm(_DROP, label=f"-{','.join(sorted(_DROP))}")

    base_per_fy  = _extract_per_fy(baseline_fy)
    treat_per_fy = _extract_per_fy(treatment_fy)

    fy_labels = [
        fy for fy in base_per_fy
        if len(base_per_fy[fy]) > 0 and len(treat_per_fy[fy]) > 0
    ]
    logger.info("Effective FYs for bootstrap: {}", fy_labels)

    agg_base  = [r for fy in fy_labels for r in base_per_fy[fy]]
    agg_treat = [r for fy in fy_labels for r in treat_per_fy[fy]]
    logger.info("Trade counts — baseline: {}, combined-drop: {}",
                len(agg_base), len(agg_treat))

    (pt_sh, lo_sh, hi_sh, pt_mr, lo_mr, hi_mr, p_neg) = _bootstrap_trade_level(
        agg_treat, agg_base
    )
    (fpt_sh, flo_sh, fhi_sh, fpt_mr, flo_mr, fhi_mr, fp_neg) = _bootstrap_fy_level(
        treat_per_fy, base_per_fy, fy_labels
    )

    per_fy_d_sh: dict[str, float] = {}
    for fy in fy_labels:
        sa = _sharpe(treat_per_fy[fy])
        sb = _sharpe(base_per_fy[fy])
        per_fy_d_sh[fy] = (sa if not math.isnan(sa) else 0.0) - (
            sb if not math.isnan(sb) else 0.0
        )
    fy_pos = sum(1 for v in per_fy_d_sh.values() if v >= 0)

    pass1 = lo_sh  > 0
    pass2 = flo_sh > 0
    pass3 = fy_pos >= 3
    verdict = "SHIP" if (pass1 and pass2 and pass3) else "DO NOT SHIP"

    lines = [
        "# regime_sign combined-drop pre-ship bootstrap CI",
        "",
        f"Generated: {datetime.date.today()}",
        f"Treatment drop set: {{{', '.join(sorted(_DROP))}}}",
        f"Seed (trade-level): {_RANDOM_SEED}  |  Seed (FY-level): {_RANDOM_SEED+1}",
        f"Bootstrap iterations: {_N_BOOT:,}",
        "",
        "## Cohort",
        "",
        f"Effective FYs: {', '.join(fy_labels)}  ({len(fy_labels)} of 7)",
        "FY2019, FY2020 excluded — zero `SignBenchmarkRun` rows in dev DB.",
        "",
        f"Trade counts — baseline n={len(agg_base)}, combined-drop n={len(agg_treat)}",
        "",
        "## Bootstrap 1 — trade-level aggregate",
        "",
        "Resample individual trade returns with replacement, *independently* per",
        "arm.  Tests whether the aggregate Sharpe/mean_r gap survives trade-",
        "level variance.",
        "",
        f"- **Δ Sharpe** (combined-drop − baseline): point = {pt_sh:+.3f}, "
        f"95 % CI [{lo_sh:+.3f}, {hi_sh:+.3f}], p(Δ≤0) = {p_neg:.3f}",
        f"- **Δ mean_r** : point = {pt_mr*100:+.3f} pp, "
        f"95 % CI [{lo_mr*100:+.3f} pp, {hi_mr*100:+.3f} pp]",
        "",
        f"Gate 1 (Δ Sharpe CI lower > 0): **{'PASS' if pass1 else 'FAIL'}**",
        "",
        "## Bootstrap 2 — FY-level",
        "",
        "Resample the effective FY labels with replacement; pool the picked",
        "FYs' trades within each arm; compute Δ.  Tests whether the effect",
        "generalizes across FYs (binding gate per past bootstrap-discipline",
        "lessons).",
        "",
        f"- **Δ Sharpe** (combined-drop − baseline): point = {fpt_sh:+.3f}, "
        f"95 % CI [{flo_sh:+.3f}, {fhi_sh:+.3f}], p(Δ≤0) = {fp_neg:.3f}",
        f"- **Δ mean_r** : point = {fpt_mr*100:+.3f} pp, "
        f"95 % CI [{flo_mr*100:+.3f} pp, {fhi_mr*100:+.3f} pp]",
        "",
        f"Gate 2 (FY-level Δ Sharpe CI lower > 0): **{'PASS' if pass2 else 'FAIL'}**",
        "",
        "## Per-FY point Δ Sharpe",
        "",
        "| FY | Δ Sharpe |",
        "|----|---------:|",
        *[f"| {fy} | {v:+.3f} |" for fy, v in per_fy_d_sh.items()],
        "",
        f"FYs with Δ Sharpe ≥ 0: **{fy_pos} of {len(fy_labels)}**",
        "",
        f"Gate 3 (≥ 3 of {len(fy_labels)} FYs Δ Sharpe ≥ 0): "
        f"**{'PASS' if pass3 else 'FAIL'}**",
        "",
        "## Verdict",
        "",
        f"**{verdict}** — Gate 1 {'✓' if pass1 else '✗'}, "
        f"Gate 2 {'✓' if pass2 else '✗'}, Gate 3 {'✓' if pass3 else '✗'}",
        "",
        "If SHIP: edit `regime_sign_backtest.EXCLUDE_SIGNS` to include the "
        "drop set, regenerate `regime_sign_backtest.md`, then update "
        "production strategy.  If DO NOT SHIP: fall back to UI-only salvage "
        "same as the rev_nhi 2026-05-16 outcome — extend "
        "`_HIDDEN_PROPOSAL_SIGNS` for the surfacing layer.",
        "",
    ]
    _REPORT_PATH.write_text("\n".join(lines))
    logger.info("Wrote {}", _REPORT_PATH)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
