# brk_wall K-sweep + score calibration (2026-05-18) — K=10 retained

Operator request (2026-05-18): tune brk_wall's K parameter and check if the sign_score is informative.  Production was/is K=10, theta=0.05, lookback=120.

Score formula: `(close − wall) / wall`, capped at 5% (so score ∈ [0, 1] where 1 = breakout ≥5% above wall).

## TL;DR — final verdict

| Question | Answer |
|---|---|
| Best K per-fire? | K=15 (+0.8pp DR, +0.06pp mean_r vs K=10) — marginal |
| Score informative? | **No, at any K.**  Spearman ρ ≤ 0.020, Q4−Q1 spread ≤ 0.82pp |
| K=15 helps `regime_sign`? | **No** — trade-for-trade identical with vs without brk_wall, same as K=10 |
| K=15 helps `confluence`? | **No** — adding brk_wall@K=15 to bullish set regresses Sharpe at N=3 by 1.40 |
| Ship K=15? | **No, revert to K=10.**  Per-fire gain doesn't translate to strategy |
| Use `sign_score` in ranking? | **No** — already excluded; this probe re-confirms |

Code change: `BrkWallDetector` now exposes `K`, `theta`, `lookback` as constructor parameters (defaults preserve K=10 production).  Available for future experiments.

## Per-FY DR + EV by K

| FY | K | n | DR | mean_r |
|----|---|---:|---:|---:|
| FY2019 | K=10 | 727 | 46.4% | -2.86% |
| FY2019 | K=15 | 469 | 46.1% | -2.91% |
| FY2019 | K=20 | 230 | 46.5% | -2.79% |
| FY2019 | K=30 | 59 | 52.5% | -0.25% |
| FY2020 | K=10 | 705 | 54.5% | +1.83% |
| FY2020 | K=15 | 286 | 60.1% | +2.78% |
| FY2020 | K=20 | 120 | 57.5% | +1.40% |
| FY2020 | K=30 | 17 | 64.7% | +1.33% |
| FY2021 | K=10 | 729 | 48.7% | -0.70% |
| FY2021 | K=15 | 401 | 47.4% | -0.42% |
| FY2021 | K=20 | 129 | 41.9% | -0.42% |
| FY2021 | K=30 | 27 | 44.4% | +0.01% |
| FY2022 | K=10 | 723 | 52.0% | +0.44% |
| FY2022 | K=15 | 417 | 54.0% | +1.02% |
| FY2022 | K=20 | 246 | 51.6% | +1.03% |
| FY2022 | K=30 | 52 | 46.2% | -0.34% |
| FY2023 | K=10 | 1086 | 55.6% | +2.32% |
| FY2023 | K=15 | 736 | 55.6% | +2.12% |
| FY2023 | K=20 | 336 | 57.4% | +2.63% |
| FY2023 | K=30 | 64 | 60.9% | +3.49% |
| FY2024 | K=10 | 729 | 49.1% | -0.78% |
| FY2024 | K=15 | 441 | 50.8% | -0.83% |
| FY2024 | K=20 | 213 | 53.1% | -0.40% |
| FY2024 | K=30 | 31 | 35.5% | -1.23% |
| FY2025 | K=10 | 1125 | 59.0% | +3.27% |
| FY2025 | K=15 | 699 | 59.5% | +2.97% |
| FY2025 | K=20 | 339 | 59.0% | +2.39% |
| FY2025 | K=30 | 64 | 50.0% | +1.35% |

## Pooled across FYs

| K | total_n | pooled DR | pooled mean_r |
|---|---:|---:|---:|
| K=10 | **5824** | **52.9%** | **+0.80%** |
| K=15 | **3449** | **53.7%** | **+0.86%** |
| K=20 | **1613** | **53.5%** | **+0.83%** |
| K=30 | **314** | **51.0%** | **+0.83%** |

## Score informativeness

Pooled across all FYs.  Spearman ρ of (score, signed_return) tests whether higher scores predict better signed returns.  Quartile table splits fires by score (Q1=lowest) and shows DR + mean_r per quartile — if scores are informative, Q4 should beat Q1.

### Spearman ρ (score vs signed_return)

| K | n | Spearman ρ | p |
|---|---:|---:|---:|
| K=10 | 5824 | **+0.020** | 0.122 |
| K=15 | 3449 | **+0.014** | 0.400 |
| K=20 | 1613 | **-0.003** | 0.909 |
| K=30 | 314 | **-0.001** | 0.992 |

### Quartile EV (Q1=lowest score, Q4=highest)

**K=10** (total n=5824):

| Quartile | n | DR | mean_r |
|----------|---:|---:|---:|
| Q1 | 1456 | 51.4% | +0.47% |
| Q2 | 1456 | 54.7% | +0.82% |
| Q3 | 1456 | 51.0% | +0.62% |
| Q4 | 1456 | 54.3% | +1.28% |

  Q4−Q1 mean_r spread: **+0.81pp** — **WEAK**

**K=15** (total n=3449):

| Quartile | n | DR | mean_r |
|----------|---:|---:|---:|
| Q1 | 862 | 53.7% | +0.42% |
| Q2 | 862 | 54.1% | +0.76% |
| Q3 | 862 | 52.4% | +1.02% |
| Q4 | 863 | 54.3% | +1.23% |

  Q4−Q1 mean_r spread: **+0.82pp** — **WEAK**

**K=20** (total n=1613):

| Quartile | n | DR | mean_r |
|----------|---:|---:|---:|
| Q1 | 403 | 55.6% | +1.19% |
| Q2 | 403 | 53.8% | +0.60% |
| Q3 | 403 | 50.9% | +0.54% |
| Q4 | 404 | 53.7% | +0.97% |

  Q4−Q1 mean_r spread: **-0.22pp** — **NOISE**

**K=30** (total n=314):

| Quartile | n | DR | mean_r |
|----------|---:|---:|---:|
| Q1 | 78 | 51.3% | +0.84% |
| Q2 | 79 | 49.4% | +0.65% |
| Q3 | 78 | 50.0% | +0.60% |
| Q4 | 79 | 53.2% | +1.25% |

  Q4−Q1 mean_r spread: **+0.41pp** — **NOISE**
## Strategy A/Bs (2026-05-18) — K=15 retested at strategy level

### regime_sign A/B (K=15)

Ran `regime_sign_brk_wall_ab.py` with brk_wall's default K=15.  Result:
**trade-for-trade identical** with vs without brk_wall, every FY:

| FY | with K=15 trades | without trades | Δ Sharpe |
|----|---|---|---:|
| FY2021 | 31 | 31 | +0.00 |
| FY2022 | 31 | 31 | +0.00 |
| FY2023 | 38 | 38 | +0.00 |
| FY2024 | 36 | 36 | +0.00 |
| FY2025 | 35 | 35 | +0.00 |

Same as the prior K=10 result. brk_wall's (sign, kumo) cells never win
the regime ranking against other signs' cells regardless of K.  The
sign is inert in `regime_sign` at any K.

### Confluence inclusion A/B (K=15)

Ran `confluence_brk_wall_inclusion_ab.py` — tests whether adding
brk_wall@K=15 to the current 10-sign bullish set improves Sharpe.

| N gate | A baseline (10 signs) | B + brk_wall@K=15 | Δ Sharpe |
|---|---:|---:|---:|
| N≥1 | +0.76 | +0.39 | −0.37 |
| N≥2 | +4.05 | +3.71 | −0.34 |
| **N≥3** | **+3.72** | **+2.32** | **−1.40** |

Per-FY at N≥3: B loses in 5/7 FYs.  FY2020 (−5.99) and FY2024 (−5.72)
take the biggest hits.  Same dilution finding as K=10 — brk_wall
fundamentally doesn't add value to confluence regardless of K.

## Final ship decision

**Revert to K=10 default.**  Rebenched with K=10 restored; benchmark.md
returns to original brk_wall numbers (FY2025 OOS n=1005, DR 59.6%).

`gate_use_low`-equivalent already exists on brk_wall via `low > wall`
convention (always-on, no parameter).  `K` parameter remains available
for future experiments.

## What this confirms

- brk_wall is structurally weak as a load-bearing sign — neither
  better K nor scoring formulation makes it useful as an entry trigger
- Its value is informational: catalogue completeness + Daily display
- The `(close − wall) / wall` score is noise.  Should not be used in
  ranking for any strategy.  (Already excluded; this re-confirms.)
- The strict-K probe pattern (sweep K, then strategy A/B if interesting)
  is becoming the standard methodology — established now for brk_kumo,
  brk_tenkan, brk_sma, brk_wall.

## Confluence inclusion A/B — brk_wall@K=15

Probe run: 2026-05-18.  Tests whether adding brk_wall@K=15 to the current 10-sign bullish set improves the confluence strategy.

- **A baseline** = current 10-sign bullish set
- **B +brk_wall** = baseline + brk_wall@K=15 (in-memory)

### N ≥ 1

| FY | A trades | A Sh | B trades | B Sh | B−A |
|----|---:|---:|---:|---:|---:|
| FY2019 | 33 | -4.30 | 31 | -10.34 | -6.04 |
| FY2020 | 45 | +9.18 | 42 | +8.42 | -0.76 |
| FY2021 | 34 | -5.42 | 34 | -2.63 | +2.80 |
| FY2022 | 30 | -0.56 | 32 | -0.91 | -0.35 |
| FY2023 | 36 | +3.16 | 35 | +3.76 | +0.60 |
| FY2024 | 33 | -2.53 | 37 | -2.29 | +0.23 |
| FY2025 | 43 | +5.80 | 40 | +6.69 | +0.89 |

### N ≥ 2

| FY | A trades | A Sh | B trades | B Sh | B−A |
|----|---:|---:|---:|---:|---:|
| FY2019 | 33 | +1.65 | 35 | -0.20 | -1.85 |
| FY2020 | 35 | +7.88 | 35 | +7.99 | +0.11 |
| FY2021 | 35 | +0.36 | 35 | +0.36 | +0.00 |
| FY2022 | 30 | -1.55 | 31 | +1.64 | +3.19 |
| FY2023 | 39 | +7.35 | 40 | +6.47 | -0.88 |
| FY2024 | 39 | +4.83 | 35 | +0.91 | -3.92 |
| FY2025 | 40 | +7.85 | 41 | +8.76 | +0.92 |

### N ≥ 3

| FY | A trades | A Sh | B trades | B Sh | B−A |
|----|---:|---:|---:|---:|---:|
| FY2019 | 30 | -2.36 | 28 | +0.14 | +2.50 |
| FY2020 | 36 | +9.87 | 33 | +3.88 | -5.99 |
| FY2021 | 34 | -0.73 | 36 | -1.57 | -0.84 |
| FY2022 | 33 | +3.54 | 33 | +5.85 | +2.31 |
| FY2023 | 45 | +6.33 | 42 | +5.78 | -0.55 |
| FY2024 | 34 | +6.49 | 32 | +0.77 | -5.72 |
| FY2025 | 39 | +2.93 | 39 | +1.41 | -1.53 |

### Aggregate (FY-equal-weighted)

| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |
|---|-----|---:|---:|---:|---:|
| N≥1 | A baseline | 254 | **+0.76** | +0.38% | 52% |
| N≥1 | B +brk_wall(K=15) | 251 | **+0.39** | +0.29% | 51% |

| N≥2 | A baseline | 251 | **+4.05** | +2.50% | 59% |
| N≥2 | B +brk_wall(K=15) | 252 | **+3.71** | +2.10% | 59% |

| N≥3 | A baseline | 251 | **+3.72** | +2.21% | 57% |
| N≥3 | B +brk_wall(K=15) | 243 | **+2.32** | +1.18% | 56% |


### Sortino + EV decomposition (added 2026-05-18)

EV = P(win)·E[win] + P(loss)·E[loss]  (E[loss] is negative, so the second term subtracts).  EV check should ≈ mean_r — minor differences come from FY-equal-weighted averaging.  Sortino penalizes only downside variance (good for asymmetric returns).

| N gate | arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |
|--------|-----|---:|---:|---:|---:|---:|---:|
| N ≥ 1 | A baseline | +0.76 | **+2.30** | 51.7% | +8.26% | -8.13% | +0.35% |
| N ≥ 1 | B +brk_wall(K=15) | +0.39 | **+1.90** | 51.3% | +8.07% | -7.92% | +0.29% |

| N ≥ 2 | A baseline | +4.05 | **+9.94** | 59.0% | +8.56% | -6.50% | +2.39% |
| N ≥ 2 | B +brk_wall(K=15) | +3.71 | **+8.19** | 59.0% | +8.61% | -7.33% | +2.07% |

| N ≥ 3 | A baseline | +3.72 | **+8.70** | 57.4% | +9.23% | -7.18% | +2.25% |
| N ≥ 3 | B +brk_wall(K=15) | +2.32 | **+4.44** | 56.0% | +8.64% | -7.99% | +1.32% |


#### Marginal contribution at N≥1

### Marginal contribution (added 2026-05-18)

Comparing **B +brk_wall(K=15)** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **-3** | B +brk_wall(K=15) − A baseline (turnover impact) |
| A baseline max drawdown | +179.69% | peak-to-trough on cumulative trade returns |
| B +brk_wall(K=15) max drawdown | +154.07% | same metric, expanded arm |
| Δ drawdown | -25.62% | + = drawdown got WORSE under B +brk_wall(K=15) |
| Daily-return correlation | **+0.636** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -15.43% | A's bad days |
| B +brk_wall(K=15) on those same days | -10.77% | does new sign help when A loses? |
| Tail-hedge lift | **+4.66%** | + = B +brk_wall(K=15) cushions A baseline's tail |
| New-trade count (B-only) | 103 | trades introduced by the change |
| New-trade win rate | 57.3% | quality of the marginal trades |


#### Marginal contribution at N≥2

### Marginal contribution (added 2026-05-18)

Comparing **B +brk_wall(K=15)** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+1** | B +brk_wall(K=15) − A baseline (turnover impact) |
| A baseline max drawdown | +88.42% | peak-to-trough on cumulative trade returns |
| B +brk_wall(K=15) max drawdown | +117.78% | same metric, expanded arm |
| Δ drawdown | +29.36% | + = drawdown got WORSE under B +brk_wall(K=15) |
| Daily-return correlation | **+0.544** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -11.50% | A's bad days |
| B +brk_wall(K=15) on those same days | -8.05% | does new sign help when A loses? |
| Tail-hedge lift | **+3.45%** | + = B +brk_wall(K=15) cushions A baseline's tail |
| New-trade count (B-only) | 122 | trades introduced by the change |
| New-trade win rate | 62.3% | quality of the marginal trades |


#### Marginal contribution at N≥3

### Marginal contribution (added 2026-05-18)

Comparing **B +brk_wall(K=15)** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **-8** | B +brk_wall(K=15) − A baseline (turnover impact) |
| A baseline max drawdown | +83.01% | peak-to-trough on cumulative trade returns |
| B +brk_wall(K=15) max drawdown | +100.94% | same metric, expanded arm |
| Δ drawdown | +17.93% | + = drawdown got WORSE under B +brk_wall(K=15) |
| Daily-return correlation | **+0.491** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -12.12% | A's bad days |
| B +brk_wall(K=15) on those same days | -7.18% | does new sign help when A loses? |
| Tail-hedge lift | **+4.93%** | + = B +brk_wall(K=15) cushions A baseline's tail |
| New-trade count (B-only) | 135 | trades introduced by the change |
| New-trade win rate | 57.8% | quality of the marginal trades |
