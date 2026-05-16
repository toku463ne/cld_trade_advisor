# regime_sign_backtest — A/B with rev_nhi excluded from ranking

Generated: 2026-05-16

## Question

Operator wants to demote `rev_nhi` from standalone entry sign to a UI
decision factor.  Does removing it from the ranking hurt the backtest?

## Methodology

Two arms, identical except for `EXCLUDE_SIGNS`:
- **baseline**: `EXCLUDE_SIGNS = frozenset()` (current production)
- **no-rev_nhi**: `EXCLUDE_SIGNS = frozenset({"rev_nhi"})` — `rev_nhi`
  is filtered out of `SignBenchmarkRun` lookup in `_load_run_ids`, so
  the regime-ranking table excludes all `(rev_nhi, kumo)` cells and
  no `rev_nhi` detectors are built.  On days where rev_nhi was previously
  picked by argmax, the next-best (sign, kumo) cell gets picked.

FY range: FY2019 → FY2025 (7 fiscal years, walk-forward).

## Per-FY

| FY | baseline n | baseline mean_r | baseline Sharpe | no-rev_nhi n | no-rev_nhi mean_r | no-rev_nhi Sharpe | Δn | Δmean_r | ΔSharpe |
|----|-----------:|----------------:|----------------:|-------------:|------------------:|------------------:|---:|--------:|--------:|
| FY2019 | 0 | +0.00% | — | 0 | +0.00% | — | +0 | +0.00% | +0.00 |
| FY2020 | 0 | +0.00% | — | 0 | +0.00% | — | +0 | +0.00% | +0.00 |
| FY2021 | 31 | -0.86% | -1.31 | 30 | -1.49% | -2.49 | -1 | -0.63% | -1.18 |
| FY2022 | 30 | +2.26% | 3.03 | 33 | +3.18% | 4.09 | +3 | +0.92% | +1.06 |
| FY2023 | 36 | +2.04% | 3.71 | 36 | +3.77% | 7.33 | +0 | +1.73% | +3.62 |
| FY2024 | 36 | -1.37% | -1.92 | 36 | -1.37% | -1.92 | +0 | +0.00% | +0.00 |
| FY2025 | 38 | +1.76% | 3.15 | 39 | +3.55% | 5.19 | +1 | +1.79% | +2.04 |

## Aggregate (FY2019–FY2025)

| arm | n | mean_r | Sharpe | win% | hold |
|---|--:|---:|---:|---:|---:|
| baseline (all signs)        | 171 | +0.77% | 1.20 | 55.6% | 26.0 |
| no-rev_nhi (in ranking)     | 174 | +1.64% | 2.43 | 55.7% | 25.6 |
| **Δ (no-rev_nhi − baseline)** | **+3** | **+0.87%** | **+1.23** | — | — |

