# corr_flip — Correlation Regime Flip sign detector
Fires on a bar where rolling corr(stock, indicator) crosses from negative to
positive, having been negative for at least ``min_neg_bars`` consecutive bars.

Score = neg_depth × 0.4 + neg_duration_norm × 0.3 + cross_strength × 0.3
neg_depth         = min(|min corr during negative phase|, 1.0)
neg_duration_norm = min(consecutive_neg_bars / 20, 1.0)
cross_strength    = min(corr_at_crossing / 0.5, 1.0)

Longer/deeper negative phases followed by a strong upward crossing score
higher — these represent a more decisive re-coupling after a divergence.

Valid for up to ``valid_bars`` bars after firing, provided the rolling corr
at the query bar is still > 0 (re-coupling holding).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign corr_flip --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=23  n=232  direction_rate=56.5%  p≈0.048
bench_flw=0.057  bench_rev=0.027  mean_bars=12.7  (mag_flw=0.101  mag_rev=0.062)
→ PROVISIONAL (FLW) — borderline p and small n; best bench_flw of all signs
Low-corr only (run_id=46, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign corr_flip --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=215  direction_rate=56.2%  p≈0.069  bench_flw=0.056
  → Note: mode-neutral; sign captures re-coupling after divergence regardless of typical corr level
```
