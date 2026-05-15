# div_vol — Volume-Confirmed N225 Divergence sign detector
Same conditions as div_bar PLUS:
- Volume on the divergence bar > VOL_RATIO_MIN × 20-bar rolling average volume

Score = div_bar_base_score × (1 + vol_bonus)
div_bar_base_score = (stock_ret − n225_ret) × corr_prev
vol_bonus          = min(vol_ratio / VOL_RATIO_MIN, 3.0) / 3.0   [0..1]

Volume amplifies the base score: a 6× average-volume bar scores twice as high
as one that barely clears the 2× threshold.

Valid for up to ``valid_bars`` bars, provided rolling corr(stock, N225) < 0.

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign div_vol --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=21  n=12  direction_rate=33.3%  p≈0.25
bench_flw=0.035  bench_rev=0.059  mean_bars=16.3  (mag_flw=0.106  mag_rev=0.089)
→ SKIP (n too small; designed for 1h intraday bars — re-run with --gran 1h)
```
