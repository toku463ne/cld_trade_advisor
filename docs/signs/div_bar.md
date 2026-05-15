# div_bar — N225 Divergence sign detector
Fires on a single 1h bar when all hold:
- N225 bar return < -1.5 %
- Stock bar return > +0.3 %
- Rolling 20-bar corr(stock, N225) > +0.30

Score = (stock_ret - n225_ret) × corr_at_fire
Higher prior coupling and wider return gap both increase the score.

Valid for up to ``valid_bars`` bars after firing, provided the rolling
corr(stock, N225) at the query bar is still < 0 (divergence phase active).
If corr returns to positive the sign expires early.

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign div_bar --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=20  n=17  direction_rate=35.3%  p≈0.23
bench_flw=0.036  bench_rev=0.046  mean_bars=14.2  (mag_flw=0.102  mag_rev=0.071)
→ SKIP (n too small for significance; designed for 1h intraday bars, not daily)
```
