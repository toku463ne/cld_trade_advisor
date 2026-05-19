# trend_score Stage 1 — A/B reports

Stage-1 path A from [[project-trend-score-stage0]]: drop weak-context fires
identified by Stage-0 per-sign decile shape.  Two A/Bs:

1. **Confluence floor** — drop {brk_kumo_hi, chiko_hi} fires when trend_score < 25
2. **Regime_sign ceiling** — drop {rev_nlo, rev_hi, str_lead, str_lag} proposals
   when trend_score > 75

## Verdict (2026-05-19)

**Both REJECT.**  Stage-0 per-sign decile shape did NOT survive the strategy
gates.

- **Floor (confluence)**: NO-OP.  Only 169 of 25,004 floor-sign fires (0.7%)
  fell below the floor over 7 FYs.  Confluence's strict-bar low/high + N≥3
  cooldown already filters out the bad-context fires by other means.  At
  N=3, ΔSharpe = 0.00 in 5 of 7 FYs; FY2019 −0.08, FY2023 +0.05.  Gate
  technically passes (no FY worsens beyond −0.10) but **no economic uplift**.

- **Ceiling (regime_sign)**: **NET NEGATIVE.**  Aggregate ΔSharpe = −0.43;
  FY2023 lost −1.82 Sharpe by dropping profitable rev_hi/str_lead fires;
  FY2021 −0.33.  Of 5 testable FYs, 0 positive / 3 neutral / 2 negative.
  Drawdown improved (−11.49pp) but the substituted trades had 45.5% win
  rate vs baseline 56.7%.  **Strategy's Kumo+ADX gates already select
  for the right trend context** — adding a redundant ceiling drops
  high-quality regime-confirmed trades.

**Pattern reinforced**: per-sign decile shape on raw fires ≠ per-sign decile
shape after strategy gates.  Same as [[feedback-probe-vs-canonical]] in a
different form: the binding test is always the strategy A/B, never the
per-fire EV table.

**Salvage paths considered, not pursued**:
- Continuous sizing tilt (Stage-1 path B from Stage 0 memo)
- Re-measurement with strategy-gate-conditioned EV (Stage-1 path C)
- Score itself as a regime feature in the ranking (untested)

---

## Confluence floor A/B (brk_kumo_hi, chiko_hi ≥ 25)

Probe run: 2026-05-19.  Stage 1 path A for trend_score: drop floor-sign fires when trend_score < 25.

- **Floor signs**: brk_kumo_hi, chiko_hi
- **Floor**: trend_score < 25 → fire dropped
- **Score**: 5-feature 250-bar pct-rank per stock (`src.analysis._trend_score`)
- **Floor fires dropped (pooled across FYs)**: 169 of 25004 scored floor-sign fires (0.7%)

### N ≥ 1

| FY | A trades | A Sh | B trades | B Sh | ΔSh | Δtrades |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 33 | -4.30 | 33 | -4.30 | **+0.00** | +0 |
| FY2020 | 45 | +9.18 | 45 | +9.18 | **+0.00** | +0 |
| FY2021 | 34 | -5.42 | 34 | -5.42 | **+0.00** | +0 |
| FY2022 | 30 | -0.56 | 30 | -0.56 | **+0.00** | +0 |
| FY2023 | 36 | +3.16 | 36 | +3.16 | **+0.00** | +0 |
| FY2024 | 33 | -2.53 | 33 | -2.53 | **+0.00** | +0 |
| FY2025 | 43 | +5.80 | 43 | +5.80 | **+0.00** | +0 |

### N ≥ 2

| FY | A trades | A Sh | B trades | B Sh | ΔSh | Δtrades |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 33 | +1.65 | 33 | +1.65 | **+0.00** | +0 |
| FY2020 | 35 | +7.88 | 35 | +7.88 | **+0.00** | +0 |
| FY2021 | 35 | +0.36 | 35 | +0.36 | **+0.00** | +0 |
| FY2022 | 30 | -1.55 | 30 | -1.55 | **+0.00** | +0 |
| FY2023 | 39 | +7.35 | 39 | +7.35 | **+0.00** | +0 |
| FY2024 | 39 | +4.83 | 39 | +5.08 | **+0.26** | +0 |
| FY2025 | 40 | +7.85 | 40 | +7.85 | **+0.00** | +0 |

### N ≥ 3

| FY | A trades | A Sh | B trades | B Sh | ΔSh | Δtrades |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 30 | -2.36 | 30 | -2.45 | **-0.08** | +0 |
| FY2020 | 36 | +9.87 | 36 | +9.87 | **+0.00** | +0 |
| FY2021 | 34 | -0.73 | 34 | -0.73 | **+0.00** | +0 |
| FY2022 | 33 | +3.54 | 33 | +3.54 | **+0.00** | +0 |
| FY2023 | 45 | +6.33 | 45 | +6.37 | **+0.05** | +0 |
| FY2024 | 34 | +6.49 | 34 | +6.49 | **+0.00** | +0 |
| FY2025 | 39 | +2.93 | 39 | +2.93 | **+0.00** | +0 |

### Aggregate (FY-equal-weighted)

| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |
|---|-----|---:|---:|---:|---:|
| N≥1 | A baseline | 254 | **+0.76** | +0.38% | 52% |
| N≥1 | B +floor | 254 | **+0.76** | +0.38% | 52% |

| N≥2 | A baseline | 251 | **+4.05** | +2.50% | 59% |
| N≥2 | B +floor | 251 | **+4.09** | +2.52% | 59% |

| N≥3 | A baseline | 251 | **+3.72** | +2.21% | 57% |
| N≥3 | B +floor | 251 | **+3.72** | +2.21% | 57% |


### Sortino + EV decomposition (added 2026-05-18)

EV = P(win)·E[win] + P(loss)·E[loss]  (E[loss] is negative, so the second term subtracts).  EV check should ≈ mean_r — minor differences come from FY-equal-weighted averaging.  Sortino penalizes only downside variance (good for asymmetric returns).

| N gate | arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |
|--------|-----|---:|---:|---:|---:|---:|---:|
| N ≥ 1 | A baseline | +0.76 | **+2.30** | 51.7% | +8.26% | -8.13% | +0.35% |
| N ≥ 1 | B +floor | +0.76 | **+2.30** | 51.7% | +8.26% | -8.13% | +0.35% |

| N ≥ 2 | A baseline | +4.05 | **+9.94** | 59.0% | +8.56% | -6.50% | +2.39% |
| N ≥ 2 | B +floor | +4.09 | **+10.05** | 59.0% | +8.56% | -6.46% | +2.41% |

| N ≥ 3 | A baseline | +3.72 | **+8.70** | 57.4% | +9.23% | -7.18% | +2.25% |
| N ≥ 3 | B +floor | +3.72 | **+8.71** | 57.4% | +9.24% | -7.19% | +2.25% |


#### Marginal contribution at N≥1

### Marginal contribution (added 2026-05-18)

Comparing **B +floor** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+0** | B +floor − A baseline (turnover impact) |
| A baseline max drawdown | +179.69% | peak-to-trough on cumulative trade returns |
| B +floor max drawdown | +179.69% | same metric, expanded arm |
| Δ drawdown | +0.00% | + = drawdown got WORSE under B +floor |
| Daily-return correlation | **+1.000** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -15.43% | A's bad days |
| B +floor on those same days | -15.43% | does new sign help when A loses? |
| Tail-hedge lift | **+0.00%** | + = B +floor cushions A baseline's tail |
| New-trade count (B-only) | 0 | trades introduced by the change |
| New-trade win rate | — | quality of the marginal trades |


#### Marginal contribution at N≥2

### Marginal contribution (added 2026-05-18)

Comparing **B +floor** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+0** | B +floor − A baseline (turnover impact) |
| A baseline max drawdown | +88.42% | peak-to-trough on cumulative trade returns |
| B +floor max drawdown | +88.42% | same metric, expanded arm |
| Δ drawdown | +0.00% | + = drawdown got WORSE under B +floor |
| Daily-return correlation | **+1.000** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -11.50% | A's bad days |
| B +floor on those same days | -11.38% | does new sign help when A loses? |
| Tail-hedge lift | **+0.11%** | + = B +floor cushions A baseline's tail |
| New-trade count (B-only) | 1 | trades introduced by the change |
| New-trade win rate | 0.0% | quality of the marginal trades |


#### Marginal contribution at N≥3

### Marginal contribution (added 2026-05-18)

Comparing **B +floor** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+0** | B +floor − A baseline (turnover impact) |
| A baseline max drawdown | +83.01% | peak-to-trough on cumulative trade returns |
| B +floor max drawdown | +84.74% | same metric, expanded arm |
| Δ drawdown | +1.73% | + = drawdown got WORSE under B +floor |
| Daily-return correlation | **+0.996** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -12.12% | A's bad days |
| B +floor on those same days | -12.16% | does new sign help when A loses? |
| Tail-hedge lift | **-0.04%** | + = B +floor cushions A baseline's tail |
| New-trade count (B-only) | 2 | trades introduced by the change |
| New-trade win rate | 50.0% | quality of the marginal trades |


### Ship gate

Pre-registered (locked before run):
- avg Sharpe at N=3 in B ≥ A
- ≥ 5 / 7 FYs non-negative ΔSharpe
- FY2024 + FY2025 both non-negative ΔSharpe (holdout)

## Regime_sign ceiling A/B (rev_nlo, rev_hi, str_lead, str_lag ≤ 75)

Probe run: 2026-05-19.  Stage 1 path A for trend_score: drop ceiling-sign proposals when trend_score > 75.

- **Ceiling signs**: rev_hi, rev_nlo, str_lag, str_lead
- **Ceiling**: trend_score > 75 → proposal dropped
- **Score**: 5-feature 250-bar pct-rank per stock (`src.analysis._trend_score`)
- Missing-score proposals are KEPT (same convention as floor A/B)

### Per-FY

| FY | A trades | A Sh | A mean_r | B trades | B Sh | B mean_r | ΔSh | ΔmeanR |
|----|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 0 | — | — | 0 | — | — | **—** | **—** |
| FY2020 | 0 | — | — | 0 | — | — | **—** | **—** |
| FY2021 | 31 | -1.06 | -0.68% | 30 | -1.38 | -0.83% | **-0.33** | **-0.15%** |
| FY2022 | 31 | +1.73 | +1.37% | 31 | +1.73 | +1.37% | **+0.00** | **+0.00%** |
| FY2023 | 38 | +6.91 | +3.64% | 39 | +5.08 | +2.61% | **-1.82** | **-1.03%** |
| FY2024 | 36 | +1.44 | +1.05% | 36 | +1.44 | +1.05% | **+0.00** | **+0.00%** |
| FY2025 | 35 | +5.16 | +3.23% | 35 | +5.16 | +3.23% | **+0.00** | **+0.00%** |

### Aggregate (FY-equal-weighted)

- A baseline: total trades 171, avg Sharpe +2.83, avg mean_r +1.72%
- B +ceiling: total trades 171, avg Sharpe +2.40, avg mean_r +1.49%
- **ΔSharpe = -0.43** ; **ΔmeanR = -0.24%**

### Sortino + EV decomposition

| arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |
|-----|---:|---:|---:|---:|---:|---:|
| A baseline | +2.83 | **+5.70** | 56.7% | +9.37% | -8.17% | +1.78% |
| B +ceiling | +2.40 | **+4.97** | 54.4% | +9.19% | -7.63% | +1.52% |

### Marginal contribution (B vs A)

### Marginal contribution (added 2026-05-18)

Comparing **B +ceiling** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+0** | B +ceiling − A baseline (turnover impact) |
| A baseline max drawdown | +74.62% | peak-to-trough on cumulative trade returns |
| B +ceiling max drawdown | +63.13% | same metric, expanded arm |
| Δ drawdown | -11.49% | + = drawdown got WORSE under B +ceiling |
| Daily-return correlation | **+0.950** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -13.07% | A's bad days |
| B +ceiling on those same days | -12.60% | does new sign help when A loses? |
| Tail-hedge lift | **+0.47%** | + = B +ceiling cushions A baseline's tail |
| New-trade count (B-only) | 11 | trades introduced by the change |
| New-trade win rate | 45.5% | quality of the marginal trades |


### Ship gate

Pre-registered (locked before run):
- avg Sharpe (FY-equal-weighted) in B ≥ A
- ≥ 5 / 7 FYs non-negative ΔSharpe
- FY2024 + FY2025 both non-negative ΔSharpe (holdout)
