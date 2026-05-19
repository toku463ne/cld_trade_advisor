# brk_wall × confluence_count — Stage 0

Probe run: 2026-05-19.  Measures brk_wall per-fire EV stratified by the live confluence-bullish-sign count on the same (stock, date).  Tests the operator hypothesis (2026-05-19) that brk_wall might add Sortino value as a FALLBACK on confluence-quiet days, even though it dilutes when added to the confluence count ([[project-brk-wall-k-sweep-reject]]).

## Setup

- brk_wall fires sourced from SignBenchmarkRun (production K=10).
- Confluence-bullish set (10 signs): `str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo, brk_kumo_hi, brk_tenkan_hi, chiko_hi`.
- valid_bars per sign matches `src.strategy.confluence_sign._VALID_BARS`.
- For each brk_wall fire, count = number of DISTINCT bullish signs whose valid window covers the brk_wall fire's date (EXCLUDES brk_wall itself).

## Bucket distribution by FY

| FY | n=0 | n=1 | n=2 | n≥3 | total |
|----|---:|---:|---:|---:|---:|
| FY2019 | 380 | 14 | 68 | 218 | 680 |
| FY2020 | 345 | 2 | 18 | 277 | 642 |
| FY2021 | 379 | 2 | 20 | 243 | 644 |
| FY2022 | 333 | 0 | 14 | 286 | 633 |
| FY2023 | 475 | 2 | 25 | 376 | 878 |
| FY2024 | 285 | 0 | 14 | 225 | 524 |
| FY2025 | 524 | 9 | 53 | 419 | 1005 |

### Pooled (FY2017-FY2025)

Pooled brk_wall fires (n=5006).  Buckets are confluence_count on the brk_wall fire's stock × date.

| confluence_count | n / DR / signed_mean / follow_mag |
|---|---|
| **all (pool)** | 5006 / 53.0% / +0.80% / +7.70% |
| count = 0 | 2721 / 53.0% / +0.79% / +7.72% |
| count = 1 | 29 / 37.9% / -2.10% / +3.62% |
| count = 2 | 212 / 49.1% / -0.63% / +6.08% |
| count ≥ 3 | 2044 / 53.6% / +0.99% / +7.87% |
| count = 0 ('quiet') | 2721 / 53.0% / +0.79% / +7.72% |
| count ≥ 1 ('any other sign live') | 2285 / 53.0% / +0.80% / +7.67% |

### Train (pre-FY2024)

Pooled brk_wall fires (n=3477).  Buckets are confluence_count on the brk_wall fire's stock × date.

| confluence_count | n / DR / signed_mean / follow_mag |
|---|---|
| **all (pool)** | 3477 / 51.0% / +0.19% / +7.29% |
| count = 0 | 1912 / 51.1% / +0.14% / +7.20% |
| count = 1 | 20 / 35.0% / -2.25% / +4.76% |
| count = 2 | 145 / 44.8% / -1.48% / +5.95% |
| count ≥ 3 | 1400 / 51.8% / +0.47% / +7.55% |
| count = 0 ('quiet') | 1912 / 51.1% / +0.14% / +7.20% |
| count ≥ 1 ('any other sign live') | 1565 / 50.9% / +0.26% / +7.40% |

### Holdout (FY2024+FY2025)

Pooled brk_wall fires (n=1529).  Buckets are confluence_count on the brk_wall fire's stock × date.

| confluence_count | n / DR / signed_mean / follow_mag |
|---|---|
| **all (pool)** | 1529 / 57.5% / +2.17% / +8.52% |
| count = 0 | 809 / 57.6% / +2.33% / +8.80% |
| count = 1 | 9 / 44.4% / -1.77% / +1.62% |
| count = 2 | 67 / 58.2% / +1.23% / +6.30% |
| count ≥ 3 | 644 / 57.5% / +2.12% / +8.48% |
| count = 0 ('quiet') | 809 / 57.6% / +2.33% / +8.80% |
| count ≥ 1 ('any other sign live') | 720 / 57.4% / +1.99% / +8.21% |

## Pre-registered gate

- **PASS**: quiet-day (count=0) DR ≥ pool DR + 3pp AND quiet-day FY2024+FY2025 DR ≥ holdout pool DR + 3pp

- Pool: quiet DR = 53.0%, pool DR = 53.0%, lift = +0.0pp (✗ ≥ +3pp)
- Holdout: quiet DR = 57.6%, holdout DR = 57.5%, lift = +0.1pp (✗ ≥ +3pp)

**FAIL** — quiet-day cohort does not lift over pool.  brk_wall as fallback offers no per-fire edge.
