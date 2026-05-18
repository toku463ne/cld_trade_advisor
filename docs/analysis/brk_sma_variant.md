# brk_sma variant probe (2026-05-18) — SHIPPED

**Verdict: SHIP variant B (low, K=3) as new production for brk_sma.**

Operator request (2026-05-18): evaluate `low[T] > sma[T] AND low[T-i] ≤ sma[T-i] for i ∈ {1,2,3}` — i.e., **low-based strict whole-bar** cross with **K=3** prior-bar lookback.

Old production: `close, K=5, vol_mult=1.5`.
New production: `low, K=3, vol_mult=1.5` — `BrkSmaDetector` defaults updated.

All arms preserve the volume gate (1.5× rolling-mean) for consistency; only cross-edge (close vs low) and K (5 vs 3) vary.

## TL;DR

| Test | Result | Decision |
|---|---|---|
| Per-fire probe (4-way matrix) | Pooled DR ~equal; operator's variant +0.28pp mean_r; FY2024 regression at per-fire level (54.7% → 47.1% DR) | Mixed — run strategy A/B |
| Strategy A/B (3-arm) | At N=3: B +3.26 vs A +2.64 Sharpe (Δ +0.62); FY2024 strategy IMPROVES (−0.90 → +4.16) | **SHIP** |
| Rebench with new defaults | FY2025 OOS DR 55.8% (perm_p 0.029); perm_pass 3/7; same tier as prior brk_sma | Confirmed |

### Per-FY results

| FY | arm | n | DR | mean_r |
|----|-----|---:|---:|---:|
| FY2019 | `close,K=5 [production]` | 195 | 39.0% | -6.32% |
| FY2019 | `low,K=3   [operator]` | 275 | 46.5% | -3.12% |
| FY2019 | `low,K=5   [control]` | 215 | 47.4% | -3.57% |
| FY2019 | `close,K=3 [control]` | 237 | 38.8% | -5.71% |
| FY2020 | `close,K=5 [production]` | 235 | 55.7% | +3.67% |
| FY2020 | `low,K=3   [operator]` | 357 | 57.7% | +3.76% |
| FY2020 | `low,K=5   [control]` | 287 | 58.5% | +3.65% |
| FY2020 | `close,K=3 [control]` | 287 | 57.5% | +4.42% |
| FY2021 | `close,K=5 [production]` | 196 | 53.6% | +0.49% |
| FY2021 | `low,K=3   [operator]` | 261 | 52.5% | +0.76% |
| FY2021 | `low,K=5   [control]` | 212 | 53.3% | +0.50% |
| FY2021 | `close,K=3 [control]` | 255 | 53.3% | +0.59% |
| FY2022 | `close,K=5 [production]` | 197 | 56.9% | +1.65% |
| FY2022 | `low,K=3   [operator]` | 232 | 54.3% | +1.41% |
| FY2022 | `low,K=5   [control]` | 188 | 53.7% | +1.44% |
| FY2022 | `close,K=3 [control]` | 263 | 57.0% | +1.85% |
| FY2023 | `close,K=5 [production]` | 174 | 52.9% | +1.49% |
| FY2023 | `low,K=3   [operator]` | 277 | 54.5% | +2.21% |
| FY2023 | `low,K=5   [control]` | 211 | 55.0% | +2.18% |
| FY2023 | `close,K=3 [control]` | 236 | 53.0% | +1.86% |
| FY2024 | `close,K=5 [production]` | 254 | 54.7% | +2.03% |
| FY2024 | `low,K=3   [operator]` | 310 | 47.1% | -0.06% |
| FY2024 | `low,K=5   [control]` | 260 | 48.1% | +0.28% |
| FY2024 | `close,K=3 [control]` | 322 | 54.3% | +1.48% |
| FY2025 | `close,K=5 [production]` | 192 | 56.2% | +2.84% |
| FY2025 | `low,K=3   [operator]` | 282 | 56.0% | +3.01% |
| FY2025 | `low,K=5   [control]` | 197 | 55.8% | +3.29% |
| FY2025 | `close,K=3 [control]` | 258 | 56.6% | +2.97% |

### Pooled (FY2018–FY2025)

| arm | total_n | pooled DR | pooled mean_r |
|-----|---:|---:|---:|
| `close,K=5 [production]` | **1443** | **52.9%** | **+0.95%** |
| `low,K=3   [operator]` | **1994** | **52.8%** | **+1.23%** |
| `low,K=5   [control]` | **1570** | **53.2%** | **+1.17%** |
| `close,K=3 [control]` | **1858** | **53.2%** | **+1.20%** |

## Confluence A/B — brk_sma variant

Probe run: 2026-05-18.  Bullish set fixed at 10 signs; only brk_sma fires differ per arm.

- **A current (close,K=5)**
- **B operator (low,K=3)**
- **C control (close,K=3)**

### N ≥ 1

| FY | A trades | A Sh | B trades | B Sh | C trades | C Sh | B−A | C−A |
|----|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 37 | -5.63 | 33 | -5.24 | 36 | -3.45 | +0.39 | +2.18 |
| FY2020 | 45 | +7.99 | 45 | +9.18 | 42 | +8.46 | +1.18 | +0.47 |
| FY2021 | 34 | -4.40 | 34 | -5.42 | 34 | -4.40 | -1.03 | +0.00 |
| FY2022 | 30 | -0.83 | 30 | -0.56 | 30 | -0.83 | +0.27 | +0.00 |
| FY2023 | 36 | +3.16 | 36 | +3.16 | 36 | +4.56 | +0.00 | +1.40 |
| FY2024 | 33 | -2.25 | 33 | -2.53 | 33 | -3.29 | -0.27 | -1.04 |
| FY2025 | 45 | +4.96 | 43 | +5.80 | 45 | +4.97 | +0.84 | +0.00 |

### N ≥ 2

| FY | A trades | A Sh | B trades | B Sh | C trades | C Sh | B−A | C−A |
|----|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 32 | -4.26 | 28 | +1.26 | 32 | -4.26 | +5.52 | +0.00 |
| FY2020 | 34 | +8.26 | 35 | +7.88 | 34 | +8.26 | -0.38 | +0.00 |
| FY2021 | 35 | -4.71 | 35 | +0.36 | 36 | -1.15 | +5.07 | +3.56 |
| FY2022 | 31 | +0.22 | 30 | -1.55 | 32 | -0.85 | -1.77 | -1.06 |
| FY2023 | 36 | +10.47 | 38 | +7.86 | 38 | +9.02 | -2.61 | -1.45 |
| FY2024 | 38 | +2.36 | 39 | +4.83 | 36 | +0.62 | +2.47 | -1.74 |
| FY2025 | 37 | +5.17 | 40 | +6.31 | 37 | +5.86 | +1.14 | +0.69 |

### N ≥ 3

| FY | A trades | A Sh | B trades | B Sh | C trades | C Sh | B−A | C−A |
|----|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 25 | -3.07 | 29 | -0.25 | 25 | -1.41 | +2.82 | +1.66 |
| FY2020 | 36 | +5.01 | 36 | +9.81 | 35 | +5.25 | +4.79 | +0.24 |
| FY2021 | 33 | -0.87 | 34 | -0.73 | 32 | -2.70 | +0.13 | -1.83 |
| FY2022 | 31 | +3.04 | 31 | +2.96 | 32 | +3.31 | -0.08 | +0.27 |
| FY2023 | 37 | +11.05 | 41 | +3.96 | 41 | +10.64 | -7.09 | -0.41 |
| FY2024 | 30 | -0.90 | 34 | +4.16 | 30 | +0.66 | +5.06 | +1.56 |
| FY2025 | 36 | +4.20 | 39 | +2.93 | 37 | +3.55 | -1.26 | -0.64 |

### Aggregate (FY-equal-weighted)

| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |
|---|-----|---:|---:|---:|---:|
| N≥1 | A current (close,K=5) | 260 | **+0.43** | +0.20% | 51% |
| N≥1 | B operator (low,K=3) | 254 | **+0.63** | +0.31% | 52% |
| N≥1 | C control (close,K=3) | 256 | **+0.86** | +0.35% | 52% |

| N≥2 | A current (close,K=5) | 243 | **+2.50** | +1.24% | 56% |
| N≥2 | B operator (low,K=3) | 245 | **+3.85** | +2.26% | 59% |
| N≥2 | C control (close,K=3) | 245 | **+2.50** | +1.17% | 56% |

| N≥3 | A current (close,K=5) | 228 | **+2.64** | +1.42% | 57% |
| N≥3 | B operator (low,K=3) | 244 | **+3.26** | +1.88% | 57% |
| N≥3 | C control (close,K=3) | 232 | **+2.76** | +1.66% | 56% |
