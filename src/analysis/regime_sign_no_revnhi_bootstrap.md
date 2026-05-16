# no-rev_nhi pre-ship bootstrap CI

Generated: 2026-05-16
Seed (trade-level): 20260516  |  Seed (FY-level): 20260517
Bootstrap iterations: 10,000

## Cohort

Effective FYs: FY2021, FY2022, FY2023, FY2024, FY2025  (5 of 7)
FY2019, FY2020 excluded — zero `SignBenchmarkRun` rows in dev DB.

Trade counts — baseline n=171, no-rev_nhi n=174

## Bootstrap 1 — trade-level aggregate

Resample individual trade returns with replacement, *independently* per
arm.  Tests whether the aggregate Sharpe/mean_r gap survives trade-
level variance.

- **Δ Sharpe** (no-rev_nhi − baseline): point = +1.232, 95 % CI [-2.125, +4.771], p(Δ≤0) = 0.244
- **Δ mean_r** : point = +0.865 pp, 95 % CI [-1.322 pp, +3.136 pp]

Gate 1 (Δ Sharpe CI lower > 0): FAIL

## Bootstrap 2 — FY-level

Resample the 5 effective FY labels with replacement; pool the picked
FYs' trades within each arm; compute Δ.  Tests whether the effect
generalizes across FYs (binding gate per past bootstrap-discipline
lessons).

- **Δ Sharpe** (no-rev_nhi − baseline): point = +1.232, 95 % CI [+0.009, +2.318], p(Δ≤0) = 0.023
- **Δ mean_r** : point = +0.865 pp, 95 % CI [+0.010 pp, +1.611 pp]

Gate 2 (FY-level Δ Sharpe CI lower > 0): PASS

## Per-FY point Δ Sharpe

| FY | Δ Sharpe |
|----|---------:|
| FY2021 | -1.176 |
| FY2022 | +1.064 |
| FY2023 | +3.622 |
| FY2024 | +0.000 |
| FY2025 | +2.043 |

FYs with Δ Sharpe ≥ 0: **4 of 5**

Gate 3 (≥ 3 of 5 FYs Δ Sharpe ≥ 0): PASS

## Verdict

**DO NOT SHIP** — Gate 1 ✗, Gate 2 ✓, Gate 3 ✓
