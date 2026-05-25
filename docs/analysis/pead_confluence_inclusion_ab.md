# PEAD up-revision → confluence sign — inclusion A/B

**Verdict (2026-05-25): PROMISING at N=3, escalate to the binding fill-order null — DO NOT ship yet.**

`pead_up` = management-forecast up-revision (ΔFEPS > 0), valid 60 trading bars, added as an
11th confluence vote (in-memory from `jq_statements`; nothing written to `sign_benchmark`).
Follow-on to the PEAD study (ACCEPT, all 7 gates — `pead_forecast_revision_results.md`).

- **At N=3 (production gate) it helps and diversifies:** avg per-trade Sharpe +3.01→**+3.71**
  (+0.70), Sortino +6.45→**+8.90**, mean_r +1.86%→+2.10%. The 219 new B-only trades win
  **61.6%**; A-vs-B daily-return corr is only **+0.34** (a genuinely different axis, as
  expected from an earnings-event sign), and B cushions A's worst-quintile days strongly
  (**tail-hedge lift +10.3pp**). Per-FY B−A: FY2020 +0.71, **FY2021 +2.36** (rescues the
  baseline's only losing year), FY2022 −0.18, FY2023 +0.22, **FY2024 −1.63**, **FY2025 OOS
  +1.43** → positive 4/6 comparable FYs, OOS passes.
- **Caveats (why not ship):** (1) **N-gate-specific** — at N≥2 it is *worse* (+5.22→+2.86);
  the 60-bar validity floods weak candidates over a loose gate, and the edge exists only at
  N≥3 where the up-revision must ANCHOR two valid price signs. (2) Per-trade Sharpe is NOT the
  portfolio metric, and per-FY is 4/6 with a material FY2024 drag (−1.63). The binding ship
  gate is the **capital-aware 6-slot book + paired fill-order null** (project_confluence_fill_order_null).
- **Next:** run the capital-aware book + paired fill-order null at N=3 (baseline vs +pead_up,
  same shuffled fill order applied to both arms). Ship only if the whole Δ-Sharpe band clears
  the null and FY2024 isn't a sign-flip killer.

Full A/B tables below.

## Confluence inclusion A/B — pead_up (forecast up-revision, vb=60)

Probe run: 2026-05-25.  Tests whether adding **pead_up** (management-forecast up-revision, valid 60 trading bars) as an 11th confluence vote improves the shipped strategy.

- **A baseline** = current 10-sign bullish set
- **B +pead_up** = baseline + pead_up (vb=60, in-memory from jq_statements)

Per-trade Sharpe (matches the brk_sma / ichimoku inclusion-A/B precedents). If B wins at N=3 with per-FY robustness + OOS, escalate to the capital-aware 6-slot book + paired fill-order null (the binding ship gate).

### N ≥ 1

| FY | A trades | A Sh | B trades | B Sh | B−A |
|----|---:|---:|---:|---:|---:|
| FY2019 | 59 | -1.43 | 56 | -1.48 | -0.05 |
| FY2020 | 48 | +6.64 | 50 | +8.17 | +1.53 |
| FY2021 | 46 | -1.77 | 46 | -2.08 | -0.31 |
| FY2022 | 47 | -3.86 | 44 | -1.15 | +2.71 |
| FY2023 | 52 | +6.08 | 51 | +7.10 | +1.02 |
| FY2024 | 54 | -0.01 | 53 | -0.10 | -0.08 |
| FY2025 | 60 | +7.62 | 60 | +9.29 | +1.67 |

### N ≥ 2

| FY | A trades | A Sh | B trades | B Sh | B−A |
|----|---:|---:|---:|---:|---:|
| FY2019 | 14 | +12.79 | 45 | -3.58 | -16.37 |
| FY2020 | 52 | +3.34 | 52 | +5.54 | +2.20 |
| FY2021 | 50 | +0.97 | 48 | +3.84 | +2.87 |
| FY2022 | 51 | +5.00 | 49 | +0.93 | -4.07 |
| FY2023 | 58 | +5.42 | 56 | +5.86 | +0.44 |
| FY2024 | 50 | +1.09 | 51 | -0.01 | -1.11 |
| FY2025 | 55 | +7.89 | 59 | +7.45 | -0.44 |

### N ≥ 3

| FY | A trades | A Sh | B trades | B Sh | B−A |
|----|---:|---:|---:|---:|---:|
| FY2019 | 0 | — | 4 | +5.00 | — |
| FY2020 | 48 | +7.08 | 48 | +7.79 | +0.71 |
| FY2021 | 52 | -2.10 | 52 | +0.26 | +2.36 |
| FY2022 | 46 | +3.45 | 49 | +3.27 | -0.18 |
| FY2023 | 62 | +5.70 | 60 | +5.92 | +0.22 |
| FY2024 | 51 | +1.77 | 51 | +0.14 | -1.63 |
| FY2025 | 55 | +2.18 | 57 | +3.61 | +1.43 |

### Aggregate (FY-equal-weighted)

| N | arm | total trades | avg Sharpe | avg mean_r | avg win% |
|---|-----|---:|---:|---:|---:|
| N≥1 | A baseline | 366 | **+1.90** | +1.16% | 57% |
| N≥1 | B +pead_up | 360 | **+2.82** | +1.74% | 58% |

| N≥2 | A baseline | 330 | **+5.22** | +2.89% | 62% |
| N≥2 | B +pead_up | 360 | **+2.86** | +1.71% | 54% |

| N≥3 | A baseline | 314 | **+3.01** | +1.86% | 59% |
| N≥3 | B +pead_up | 321 | **+3.71** | +2.10% | 58% |


### Sortino + EV decomposition (added 2026-05-18)

EV = P(win)·E[win] + P(loss)·E[loss]  (E[loss] is negative, so the second term subtracts).  EV check should ≈ mean_r — minor differences come from FY-equal-weighted averaging.  Sortino penalizes only downside variance (good for asymmetric returns).

| N gate | arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |
|--------|-----|---:|---:|---:|---:|---:|---:|
| N ≥ 1 | A baseline | +1.90 | **+3.95** | 56.9% | +7.94% | -7.81% | +1.14% |
| N ≥ 1 | B +pead_up | +2.82 | **+5.86** | 57.6% | +8.24% | -7.63% | +1.52% |

| N ≥ 2 | A baseline | +5.22 | **+15.36** | 62.1% | +8.85% | -6.64% | +2.98% |
| N ≥ 2 | B +pead_up | +2.86 | **+6.80** | 54.0% | +9.27% | -7.14% | +1.73% |

| N ≥ 3 | A baseline | +3.01 | **+6.45** | 59.3% | +8.55% | -8.00% | +1.82% |
| N ≥ 3 | B +pead_up | +3.71 | **+8.90** | 58.0% | +8.24% | -6.54% | +2.03% |


#### Marginal contribution at N≥1

### Marginal contribution (added 2026-05-18)

Comparing **B +pead_up** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **-6** | B +pead_up − A baseline (turnover impact) |
| A baseline max drawdown | +173.24% | peak-to-trough on cumulative trade returns |
| B +pead_up max drawdown | +140.26% | same metric, expanded arm |
| Δ drawdown | -32.98% | + = drawdown got WORSE under B +pead_up |
| Daily-return correlation | **+0.658** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -15.44% | A's bad days |
| B +pead_up on those same days | -8.11% | does new sign help when A loses? |
| Tail-hedge lift | **+7.34%** | + = B +pead_up cushions A baseline's tail |
| New-trade count (B-only) | 170 | trades introduced by the change |
| New-trade win rate | 59.4% | quality of the marginal trades |


#### Marginal contribution at N≥2

### Marginal contribution (added 2026-05-18)

Comparing **B +pead_up** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+30** | B +pead_up − A baseline (turnover impact) |
| A baseline max drawdown | +95.38% | peak-to-trough on cumulative trade returns |
| B +pead_up max drawdown | +166.91% | same metric, expanded arm |
| Δ drawdown | +71.53% | + = drawdown got WORSE under B +pead_up |
| Daily-return correlation | **+0.537** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -12.08% | A's bad days |
| B +pead_up on those same days | -6.47% | does new sign help when A loses? |
| Tail-hedge lift | **+5.61%** | + = B +pead_up cushions A baseline's tail |
| New-trade count (B-only) | 230 | trades introduced by the change |
| New-trade win rate | 52.6% | quality of the marginal trades |


#### Marginal contribution at N≥3

### Marginal contribution (added 2026-05-18)

Comparing **B +pead_up** against **A baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+7** | B +pead_up − A baseline (turnover impact) |
| A baseline max drawdown | +111.78% | peak-to-trough on cumulative trade returns |
| B +pead_up max drawdown | +72.23% | same metric, expanded arm |
| Δ drawdown | -39.55% | + = drawdown got WORSE under B +pead_up |
| Daily-return correlation | **+0.342** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| A baseline's worst-quintile day mean | -13.99% | A's bad days |
| B +pead_up on those same days | -3.70% | does new sign help when A loses? |
| Tail-hedge lift | **+10.30%** | + = B +pead_up cushions A baseline's tail |
| New-trade count (B-only) | 219 | trades introduced by the change |
| New-trade win rate | 61.6% | quality of the marginal trades |
