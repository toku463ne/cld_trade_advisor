# div_gap — Opening Gap Divergence sign detector
Fires on the first hourly bar of each trading session when:
- Stock open > previous session close by > +STOCK_GAP_MIN  (gap up)
- N225 open < previous session close by < -N225_GAP_MAX    (gap down)

Score = min(stock_gap / 0.02, 1.0) × min(|n225_gap| / 0.02, 1.0)
Larger gaps in both directions produce a higher score.

Valid for up to ``valid_bars`` bars after firing (time-bounded only).
Overnight buyers are already committed; no additional situational check.

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign div_gap --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=22  n=1037  direction_rate=58.2%  p<0.001
bench_flw=0.051  bench_rev=0.029  mean_bars=12.7  (mag_flw=0.087  mag_rev=0.070)
→ RECOMMEND (FLW) — highly significant; highest bench_flw among all signs
Permutation & regime split (sign_validate):
  Permutation test: emp_p=<0.001  dedup n=924 (×1.1)  dedup DR=57.7%  stable
  Regime split: bear DR=62.6% (p<0.001, n=447)  bull DR=54.1% (p=0.062, n=529)
  → Strongest in bear regime; diverging from a falling index is a more meaningful signal.
Low-corr only (run_id=39, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign div_gap --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=355  direction_rate=54.6%  p≈0.083  bench_flw=0.046
  → Note: WORSE on low-corr stocks; div_gap works BEST when a high-corr stock diverges from a gapping index
```
