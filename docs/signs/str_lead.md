# str_lead — Post-N225-Bottom Leader sign detector
Fires on the first hourly bar of the day when the N225 zigzag CONFIRMS a LOW,
provided the stock's drawdown during the preceding N225 decline is less than
OUTPERFORM_MAX × |N225 drawdown|.

Daily highs/lows/closes are derived internally from the hourly caches (same
pattern as str_hold), so the detector accepts 1 h caches throughout.

Conditions:
- N225 zigzag confirms a LOW (direction = −2) at bar T
- |N225 drawdown from prior confirmed HIGH to T| ≥ N225_DD_MIN
- |stock drawdown over same window| < OUTPERFORM_MAX × |N225 drawdown|

Score = outperform_ratio × 0.6 + n225_depth_bonus × 0.2 + corr_bonus × 0.2
outperform_ratio = 1 − |stock_dd| / |n225_dd|        [0..1, 1 = stock was flat]
n225_depth_bonus = min(|n225_dd| / 0.20, 1.0)        [deeper correction = more meaningful]
corr_bonus       = max(0, moving_corr vs ^N225 at confirm_date, 1h window_bars=100)
[stock that normally tracks N225 but held up = stronger signal]

The rolling correlation is loaded externally from the moving_corr table and passed
in as a ``corr_n225_1h`` mapping of date → corr_value.  When absent (None or missing
key), the term defaults to 0.0 (neutral — no bonus, no penalty).

Valid for up to ``valid_bars`` *trading days* after firing (time-bounded only).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
run_id=25  n=405  direction_rate=59.5%  p<0.001
bench_flw=0.047  bench_rev=0.024  mean_bars=11.6
→ 2-year result was RECOMMEND but coincided with sustained bull market (FY2023+FY2024).

── 7-year cross-validation NO gate (FY2019–FY2025, prior-year cluster sets) ──
pooled DR=48.1%  perm_pass=3/7
FY breakdown: FY2019=0 events, FY2020=104/32.7%, FY2021=289/47.1%, FY2022=235/36.2%,
              FY2023=150/54.7%, FY2024=258/58.1%, FY2025=66/65.2% (out-of-sample)
→ CAUTION: sign is only reliable in N225 bull years (FY2020/FY2022 bear years show poor DR).
→ Tested kumo gate and N225 ADX gate — both hurt pooled DR; no gate is the best found.
```
