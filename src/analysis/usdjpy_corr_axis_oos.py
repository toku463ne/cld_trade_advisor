"""usdjpy_corr_axis_oos — OOS holdout sanity check for the USDJPY corr-axis finding.

The full-sample probe (`usdjpy_corr_axis_probe`) found that low-|corr|-to-USDJPY
stocks (U=L) systematically out-predict high-|corr| (U=H) stocks within the same
N225 bucket, on technical signs. That probe fit tertile cuts and measured lift on
the *same* FY2021–FY2025 event set — no holdout.

This script splits the events:

- **train** : fire_date <  2024-04-01  (FY2021–FY2023, ~3 FY)
- **test**  : fire_date >= 2024-04-01  (FY2024–FY2025 OOS, ~2 FY)

Procedure:

1. Fit empirical tertile cuts (N225-corr and USDJPY-corr) on **train only**.
2. Apply those frozen cuts to both train and test.
3. On **train**, identify "discovered" cells: n ≥ 100 AND ΔDR ≥ +3pp AND ΔEV ≥ 0
   (the same accept gate as the full probe).
4. On **test**, look up those same (sign, N, U) cells — report how many still
   show ΔDR > 0 and the mean OOS ΔDR. This is the holdout reproduction check.
5. Aggregate asymmetry: pool all signs, within each N225 bucket compute
   DR(U=L) − DR(U=H) on the test set. The mechanistic claim predicts this is
   positive.
6. Shuffle falsifier on the test set (max-over-cells, frozen train cuts).

PASS if: ≥50% of discovered cells reproduce ΔDR > 0 OOS AND the pooled
U=L−U=H asymmetry is positive on test AND test-set shuffle p < 0.05.

CLI: uv run --env-file devenv python -m src.analysis.usdjpy_corr_axis_oos
"""

from __future__ import annotations

import datetime
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from src.analysis.usdjpy_corr_axis_probe import (
    _CELL_MIN_N,
    _DELTA_DR_ACCEPT,
    _N_SHUFFLES,
    _N225_CODE,
    _RNG_SEED,
    _USDJPY_CODE,
    _CellStat,
    _cell_stats_with_loo,
    _compute_event_corrs,
    _load_bars,
    _load_events,
)

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "usdjpy_corr_axis"
_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SPLIT_DATE = datetime.date(2024, 4, 1)   # FY2024 start — train < this, test >= this


def _fit_cuts(df: pd.DataFrame) -> tuple[float, float, float, float]:
    """Empirical 33/67 tertile cutpoints for N225-corr and USDJPY-corr."""
    nq1, nq2 = np.percentile(df["corr_n225"].to_numpy(), [33.333, 66.667])
    uq1, uq2 = np.percentile(df["corr_usdjpy"].to_numpy(), [33.333, 66.667])
    return float(nq1), float(nq2), float(uq1), float(uq2)


def _apply_cuts(df: pd.DataFrame, cuts: tuple[float, float, float, float]) -> pd.DataFrame:
    nq1, nq2, uq1, uq2 = cuts
    df = df.copy()
    df["n_bucket"] = pd.cut(df["corr_n225"], bins=[-1.01, nq1, nq2, 1.01],
                            labels=["L", "M", "H"]).astype(str)
    df["u_bucket"] = pd.cut(df["corr_usdjpy"], bins=[-1.01, uq1, uq2, 1.01],
                            labels=["L", "M", "H"]).astype(str)
    return df


def _shuffle_falsifier_frozen(df: pd.DataFrame, observed_max: float) -> tuple[float, list[float]]:
    """Max-over-cells shuffle on a single df (buckets already assigned)."""
    rng = np.random.default_rng(_RNG_SEED)
    perm_max: list[float] = []
    n_ge = 0
    for i in range(_N_SHUFFLES):
        df2 = df.copy()
        perm = rng.permutation(len(df2))
        df2["u_bucket"] = df2["u_bucket"].to_numpy()[perm]
        max_dd = float("-inf")
        for (sign, nb, ub), sub in df2.groupby(["sign", "n_bucket", "u_bucket"]):
            if len(sub) < _CELL_MIN_N:
                continue
            cell_dr = float((sub["dir"] == 1).mean())
            pool = df2[(df2["sign"] == sign) & (df2["n_bucket"] == nb) & (df2["u_bucket"] != ub)]
            if len(pool) == 0:
                continue
            pool_dr = float((pool["dir"] == 1).mean())
            max_dd = max(max_dd, cell_dr - pool_dr)
        perm_max.append(max_dd)
        if max_dd >= observed_max:
            n_ge += 1
        if (i + 1) % 100 == 0:
            logger.info("  shuffle {}/{}  current p={:.3f}", i + 1, _N_SHUFFLES, n_ge / (i + 1))
    return n_ge / _N_SHUFFLES, perm_max


def _aggregate_asymmetry(df: pd.DataFrame) -> dict[str, tuple[int, int, float, float, float]]:
    """Pool all signs; within each N225 bucket: DR(U=L) vs DR(U=H)."""
    out: dict[str, tuple[int, int, float, float, float]] = {}
    for nb in ("L", "M", "H"):
        sub = df[df["n_bucket"] == nb]
        ul = sub[sub["u_bucket"] == "L"]
        uh = sub[sub["u_bucket"] == "H"]
        dr_l = float((ul["dir"] == 1).mean()) if len(ul) else float("nan")
        dr_h = float((uh["dir"] == 1).mean()) if len(uh) else float("nan")
        out[nb] = (len(ul), len(uh), dr_l, dr_h, dr_l - dr_h)
    return out


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    n225_ret = _load_bars(_N225_CODE).pct_change()
    usdjpy_ret_shifted = _load_bars(_USDJPY_CODE).pct_change().shift(1)

    events = _load_events()
    events = _compute_event_corrs(events, n225_ret, usdjpy_ret_shifted)
    events = events.dropna(subset=["corr_n225", "corr_usdjpy"]).copy()
    logger.info("Events with both corrs: {:,}", len(events))

    train = events[events["fire_date"] < _SPLIT_DATE].copy()
    test = events[events["fire_date"] >= _SPLIT_DATE].copy()
    logger.info("Split @ {}: train={:,}  test={:,}", _SPLIT_DATE, len(train), len(test))

    cuts = _fit_cuts(train)
    logger.info("Train-fit tertile cuts: N225 [{:+.3f}, {:+.3f}]  USDJPY [{:+.3f}, {:+.3f}]", *cuts)

    train_b = _apply_cuts(train, cuts)
    test_b = _apply_cuts(test, cuts)

    train_cells = _cell_stats_with_loo(train_b)
    test_cells = _cell_stats_with_loo(test_b)
    test_idx = {(c.sign, c.n_bucket, c.u_bucket): c for c in test_cells}

    # Step 3: discovered cells on train (full probe accept gate)
    discovered = [c for c in train_cells
                  if c.n >= _CELL_MIN_N
                  and not math.isnan(c.delta_dr)
                  and c.delta_dr >= _DELTA_DR_ACCEPT
                  and c.delta_ev >= 0]
    discovered.sort(key=lambda c: -c.delta_dr)
    logger.info("Discovered cells on train (n≥{}, ΔDR≥+3pp, ΔEV≥0): {}",
                _CELL_MIN_N, len(discovered))

    # Step 4: OOS reproduction
    repro_rows: list[tuple[_CellStat, _CellStat | None]] = []
    n_repro = 0
    oos_deltas: list[float] = []
    for c in discovered:
        tc = test_idx.get((c.sign, c.n_bucket, c.u_bucket))
        repro_rows.append((c, tc))
        if tc is not None and not math.isnan(tc.delta_dr):
            oos_deltas.append(tc.delta_dr)
            if tc.delta_dr > 0:
                n_repro += 1
    repro_frac = n_repro / len(discovered) if discovered else float("nan")
    mean_oos_dd = float(np.mean(oos_deltas)) if oos_deltas else float("nan")

    # Step 5: aggregate asymmetry on test
    asym = _aggregate_asymmetry(test_b)
    pooled_l = test_b[test_b["u_bucket"] == "L"]
    pooled_h = test_b[test_b["u_bucket"] == "H"]
    pooled_asym = (float((pooled_l["dir"] == 1).mean())
                   - float((pooled_h["dir"] == 1).mean()))

    # Step 6: shuffle falsifier on test
    test_obs_max = max(
        (c.delta_dr for c in test_cells
         if c.n >= _CELL_MIN_N and not math.isnan(c.delta_dr)),
        default=float("-inf"),
    )
    logger.info("Test-set observed max ΔDR (n≥{}): {:+.4f}", _CELL_MIN_N, test_obs_max)
    logger.info("Running test-set shuffle falsifier ({} perms)…", _N_SHUFFLES)
    test_p, perm_max = _shuffle_falsifier_frozen(test_b, test_obs_max)

    # Verdict
    cond_repro = repro_frac >= 0.50
    cond_asym = pooled_asym > 0
    cond_shuffle = test_p < 0.05
    verdict = ("PASS — OOS holdout reproduces the finding"
               if (cond_repro and cond_asym and cond_shuffle)
               else "FAIL — OOS holdout does not reproduce")

    # ── report ──
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = _OUT_DIR / f"oos_holdout_{today}.md"
    md: list[str] = [
        "# USDJPY Corr-Axis — OOS Holdout Sanity Check",
        "",
        f"Generated: {today}  ",
        f"Split: train fire_date < {_SPLIT_DATE} ({len(train):,} events), "
        f"test fire_date ≥ {_SPLIT_DATE} ({len(test):,} events)  ",
        f"Tertile cuts fit on **train only**: "
        f"N225 [{cuts[0]:+.3f}, {cuts[1]:+.3f}]  USDJPY [{cuts[2]:+.3f}, {cuts[3]:+.3f}]  ",
        "",
        f"## Verdict: **{verdict}**",
        "",
        f"- Discovered cells on train (n≥{_CELL_MIN_N}, ΔDR≥+3pp, ΔEV≥0): **{len(discovered)}**",
        f"- OOS reproduction: **{n_repro}/{len(discovered)}** cells keep ΔDR > 0 on test "
        f"({repro_frac*100:.0f}%) — gate ≥50%  {'✓' if cond_repro else '✗'}",
        f"- Mean OOS ΔDR over discovered cells: **{mean_oos_dd*100:+.2f}pp**",
        f"- Pooled test asymmetry DR(U=L)−DR(U=H): **{pooled_asym*100:+.2f}pp** "
        f"— gate >0  {'✓' if cond_asym else '✗'}",
        f"- Test-set shuffle p (max-over-cells, {_N_SHUFFLES} perms): **{test_p:.4f}** "
        f"— gate <0.05  {'✓' if cond_shuffle else '✗'}",
        "",
        "## Per-N225-bucket asymmetry on test set (all signs pooled)",
        "",
        "| N225 bucket | n(U=L) | n(U=H) | DR(U=L) | DR(U=H) | DR(U=L)−DR(U=H) |",
        "|-------------|-------:|-------:|--------:|--------:|----------------:|",
    ]
    for nb in ("L", "M", "H"):
        nl, nh, dl, dh, d = asym[nb]
        md.append(f"| {nb} | {nl} | {nh} | {dl*100:.1f}% | {dh*100:.1f}% | {d*100:+.2f}pp |")
    md += [
        "",
        "## Discovered cells (train) vs OOS (test)",
        "",
        "| sign | N | U | train n | train ΔDR | train ΔEV | test n | test ΔDR | test ΔEV | OOS? |",
        "|------|---|---|--------:|----------:|----------:|-------:|---------:|---------:|:----:|",
    ]
    for c, tc in repro_rows:
        if tc is None:
            md.append(f"| {c.sign} | {c.n_bucket} | {c.u_bucket} | {c.n} | "
                      f"{c.delta_dr*100:+.2f}pp | {c.delta_ev*100:+.2f}pp | "
                      f"— | — | — | n/a |")
        else:
            ok = "✓" if (not math.isnan(tc.delta_dr) and tc.delta_dr > 0) else "✗"
            md.append(f"| {c.sign} | {c.n_bucket} | {c.u_bucket} | {c.n} | "
                      f"{c.delta_dr*100:+.2f}pp | {c.delta_ev*100:+.2f}pp | "
                      f"{tc.n} | {tc.delta_dr*100:+.2f}pp | {tc.delta_ev*100:+.2f}pp | {ok} |")
    md += [
        "",
        "## Test-set shuffle falsifier",
        "",
        f"Observed max ΔDR (test, n≥{_CELL_MIN_N}): **{test_obs_max*100:+.2f}pp**  ",
        f"Permutation max ΔDR: min={min(perm_max)*100:+.2f}pp, "
        f"median={np.median(perm_max)*100:+.2f}pp, "
        f"95th={np.percentile(perm_max, 95)*100:+.2f}pp, max={max(perm_max)*100:+.2f}pp  ",
        f"p-value: **{test_p:.4f}** ({_N_SHUFFLES} perms)",
        "",
    ]
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote OOS holdout report to {}", path)

    with _BENCH_MD.open("a", encoding="utf-8") as f:
        f.write("\n".join([
            "", "---", "",
            f"## USDJPY Corr-Axis — OOS Holdout — {today}",
            "",
            f"Probe-only. Full table at `{path.relative_to(Path(__file__).parent.parent.parent)}`.  ",
            f"Verdict: **{verdict}**  ",
            f"Train-discovered cells: {len(discovered)}; OOS reproduce ΔDR>0: "
            f"{n_repro}/{len(discovered)} ({repro_frac*100:.0f}%); "
            f"mean OOS ΔDR {mean_oos_dd*100:+.2f}pp.  ",
            f"Pooled test DR(U=L)−DR(U=H): {pooled_asym*100:+.2f}pp; "
            f"test shuffle p={test_p:.4f}.",
            "",
        ]))
    logger.info("Appended summary to {}", _BENCH_MD)

    print("\n=== USDJPY CORR-AXIS — OOS HOLDOUT ===")
    print(verdict)
    print(f"  - discovered cells (train): {len(discovered)}")
    print(f"  - OOS reproduce ΔDR>0: {n_repro}/{len(discovered)} ({repro_frac*100:.0f}%)")
    print(f"  - mean OOS ΔDR: {mean_oos_dd*100:+.2f}pp")
    print(f"  - pooled test DR(U=L)−DR(U=H): {pooled_asym*100:+.2f}pp")
    print(f"  - test shuffle p: {test_p:.4f}")


if __name__ == "__main__":
    main()
