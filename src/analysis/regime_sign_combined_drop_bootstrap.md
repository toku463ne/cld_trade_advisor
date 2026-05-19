# regime_sign combined-drop pre-ship bootstrap CI

Generated: 2026-05-19
Treatment drop set: {corr_shift, div_peer, str_lag}
Seed (trade-level): 20260519  |  Seed (FY-level): 20260520
Bootstrap iterations: 10,000

## Cohort

Effective FYs: FY2021, FY2022, FY2023, FY2024, FY2025  (5 of 7)
FY2019, FY2020 excluded — zero `SignBenchmarkRun` rows in dev DB.

Trade counts — baseline n=171, combined-drop n=170

## Bootstrap 1 — trade-level aggregate

Resample individual trade returns with replacement, *independently* per
arm.  Tests whether the aggregate Sharpe/mean_r gap survives trade-
level variance.

- **Δ Sharpe** (combined-drop − baseline): point = +1.343, 95 % CI [-2.158, +4.902], p(Δ≤0) = 0.227
- **Δ mean_r** : point = +0.936 pp, 95 % CI [-1.328 pp, +3.189 pp]

Gate 1 (Δ Sharpe CI lower > 0): **FAIL**

## Bootstrap 2 — FY-level

Resample the effective FY labels with replacement; pool the picked
FYs' trades within each arm; compute Δ.  Tests whether the effect
generalizes across FYs (binding gate per past bootstrap-discipline
lessons).

- **Δ Sharpe** (combined-drop − baseline): point = +1.343, 95 % CI [+0.470, +2.894], p(Δ≤0) = 0.000
- **Δ mean_r** : point = +0.936 pp, 95 % CI [+0.486 pp, +1.789 pp]

Gate 2 (FY-level Δ Sharpe CI lower > 0): **PASS**

## Per-FY point Δ Sharpe

| FY | Δ Sharpe |
|----|---------:|
| FY2021 | +4.210 |
| FY2022 | +1.121 |
| FY2023 | +1.401 |
| FY2024 | +0.482 |
| FY2025 | +0.865 |

FYs with Δ Sharpe ≥ 0: **5 of 5**

Gate 3 (≥ 3 of 5 FYs Δ Sharpe ≥ 0): **PASS**

## Verdict

**DO NOT SHIP** — Gate 1 ✗, Gate 2 ✓, Gate 3 ✓

If SHIP: edit `regime_sign_backtest.EXCLUDE_SIGNS` to include the drop set, regenerate `regime_sign_backtest.md`, then update production strategy.  If DO NOT SHIP: fall back to UI-only salvage same as the rev_nhi 2026-05-16 outcome — extend `_HIDDEN_PROPOSAL_SIGNS` for the surfacing layer.
