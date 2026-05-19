# Confluence × bearish co-active — Stage 0

Probe run: 2026-05-19.  Measures whether bearish-sign co-activity at confluence entry date predicts worse trade outcomes.

## Setup

- Bullish set (10): str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo, brk_kumo_hi, brk_tenkan_hi, chiko_hi
- Bearish set (5): rev_nhi, rev_hi, brk_kumo_lo, brk_tenkan_lo, chiko_lo
- Confluence gate: N=3 bullish co-active
- Bearish valid_bars: 5
- Total confluence trades: **251** (train 178, holdout 73)
- Min n per bucket: 30

### Pooled (FY2019-FY2025)

| bearish_count | n | DR | mean_r | Sharpe | avg_win | avg_loss |
|---|---:|---:|---:|---:|---:|---:|
| pool | 251 | 58.6% | +2.33% | +3.77 | +9.11% | -7.25% |
| bearish = 0 | 69 | 58.0% | +3.35% | +5.00 | +10.77% | -6.89% |
| bearish = 1 | 109 | 64.2% | +2.33% | +4.14 | +7.87% | -7.61% |
| bearish ≥ 2 | 73 | 50.7% | +1.38% | +2.13 | +9.68% | -7.15% |

| **bearish ≥ 1 (combined)** | 182 | 58.8% | +1.95% | — | — | — |

### Train (pre-FY2024)

| bearish_count | n | DR | mean_r | Sharpe | avg_win | avg_loss |
|---|---:|---:|---:|---:|---:|---:|
| pool | 178 | 57.9% | +2.05% | +3.38 | +8.85% | -7.30% |
| bearish = 0 | 50 | 52.0% | +2.55% | +3.53 | +11.64% | -7.30% |
| bearish = 1 | 77 | 64.9% | +2.36% | +4.50 | +7.57% | -7.30% |
| bearish ≥ 2 | 51 | 52.9% | +1.08% | +1.79 | +8.53% | -7.29% |

| **bearish ≥ 1 (combined)** | 128 | 60.2% | +1.85% | — | — | — |

### Holdout (FY2024+FY2025)

| bearish_count | n | DR | mean_r | Sharpe | avg_win | avg_loss |
|---|---:|---:|---:|---:|---:|---:|
| pool | 73 | 60.3% | +3.03% | +4.66 | +9.73% | -7.13% |
| bearish = 0 | 19 | 73.7% | +5.45% | +10.90 | +9.15% | -4.92% |
| bearish = 1 | 32 | 62.5% | +2.26% | +3.44 | +8.61% | -8.32% |
| bearish ≥ 2 | 22 | 45.5% | +2.07% | +2.73 | +12.78% | -6.86% |

| **bearish ≥ 1 (combined)** | 54 | 55.6% | +2.18% | — | — | — |

## Pre-registered gate

- PASS if: DR(bearish ≥ 1) ≤ pool DR − 5pp, AND replicates on FY2024+FY2025 (≥ 5pp gap there too), AND n(bearish ≥ 1) ≥ 30.

- Pool: n(bearish≥1) = 182, DR = 58.8%, pool DR = 58.6%, gap = -0.2pp (✗ ≥ 5pp)
- Holdout: n(bearish≥1) = 54, DR = 55.6%, holdout DR = 60.3%, gap = +4.7pp (✗ ≥ 5pp)
- n(bearish≥1) ≥ 30: ✓

**FAIL** — bearish co-active entries don't underperform pool.
