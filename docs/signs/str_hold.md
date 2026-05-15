# str_hold — Multi-day Relative Strength During Decline sign detector
Fires on the first hourly bar of a trading day when, over the rolling
5-day window of completed days ending on that day:
- N225 cumulative return < -2 %
- Stock cumulative return > -0.5 % (flat or positive)
- At least 3 of the 5 individual days: stock daily return >= N225 daily return

Daily returns are derived from hourly caches (last close of each date),
so the detector accepts the same 1 h caches as div_bar / corr_flip.

Score = rel_gap_norm × 0.6 + consistency × 0.4
rel_gap_norm = min((stock_5d - n225_5d) / 0.05, 1.0)
consistency  = consistent_days / 5

Valid for up to ``valid_bars`` *trading days* after firing (time-bounded only).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
All stocks (run_id=24):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign str_hold --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d
  n=3729  direction_rate=55.4%  p<0.001
  bench_flw=0.046  bench_rev=0.035  mean_bars=12.1
  → RECOMMEND (FLW)
High-corr only (run_id=37, --corr-mode high):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign str_hold --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode high
  n=819  direction_rate=58.4%  p<0.001
  bench_flw=0.048  bench_rev=0.026  mean_bars=12.2
  → RECOMMEND (FLW) — preferred; filtering to |corr|≥0.6 lifts direction_rate 55.4%→58.4%
Permutation & regime split (sign_validate):
  Permutation test: emp_p=<0.001  dedup n=1851 (×1.9)  dedup DR=58.1%  ↑ rises after dedup
  Regime split: bear DR=54.3% (p<0.001, n=2761)  bull DR=59.3% (p<0.001, n=794)
  → Valid in both regimes; bull-regime fires (short corrections in recovery) are stronger.
```
