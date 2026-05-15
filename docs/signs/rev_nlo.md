# rev_nlo — Capitulation bounce after confirmed N225 trough
Fires on the first hourly bar of the day when the N225 zigzag CONFIRMS a LOW,
provided the stock's drawdown during the preceding N225 decline is at least
UNDERPERFORM_MIN × |N225 drawdown|.

Capitulation thesis: stocks that sold off hardest alongside the index are
expected to bounce most sharply once the bottom is confirmed.

Conditions:
- N225 zigzag confirms a LOW (direction = −2) at bar T
- |N225 drawdown from prior confirmed HIGH to T| ≥ N225_DD_MIN
- |stock drawdown over same window| ≥ UNDERPERFORM_MIN × |N225 drawdown|

Score = underperform_norm × 0.6 + n225_depth_bonus × 0.4
underperform_norm = min(|stock_dd| / (|n225_dd| × DEPTH_SCALE), 1.0)
[1.0 when stock fell ≥ DEPTH_SCALE × N225]
n225_depth_bonus  = min(|n225_dd| / 0.20, 1.0)

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
run_id=31  n=907  direction_rate=52.7%  p≈0.10
bench_flw=0.049  bench_rev=0.033  mean_bars=11.8
→ SKIP — not statistically significant in 2-year run.

── 7-year cross-validation (FY2018–FY2024) ──
pooled DR=45.4%  p<0.001 (reversal direction)  perm_pass=0/7
→ SKIP (confirmed reversal): the capitulation-bounce thesis is not supported.
  Stocks that underperform N225 into a confirmed low continue to underperform in
  most years. Do not use as a follow-through sign.
```
