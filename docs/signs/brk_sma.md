# brk_sma — SMA Breakout sign detector
Fires on the bar where close crosses *decisively* above the N-bar SMA, i.e.:
- The previous ``min_below_bars`` bars all closed at-or-below the SMA
(consolidation / accumulation phase below the line), AND
- The current bar closes above the SMA, AND
- The current bar's volume is at least ``volume_mult`` × the rolling-mean
volume over the SMA window (default 1.5×) — classic breakout-confirmation
filter that separates real accumulation breakouts from low-conviction noise.

A pure 1-bar crossover (close[i-1] ≤ SMA, close[i] > SMA) is not enough — we
require sustained pre-crossover containment AND volume confirmation to filter
noisy oscillation around the line.

Score = min((close − SMA) / SMA, 0.02) / 0.02
Normalised distance above SMA at the crossing bar; saturates at 2 %.

Valid for up to ``valid_bars`` bars after firing, provided close remains > SMA.
The sign expires early if price falls back below the SMA.

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign brk_sma --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=26  n=4800  direction_rate=53.2%  p<0.001
bench_flw=0.044  bench_rev=0.032  mean_bars=12.4  (mag_flw=0.083  mag_rev=0.069)
→ PROVISIONAL (FLW) — significant but weak dr; fires too frequently
Low-corr only (run_id=41, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign brk_sma --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=954  direction_rate=53.3%  p≈0.041  bench_flw=0.045
  → Note: mode-neutral; corr filter neither helps nor hurts
```
