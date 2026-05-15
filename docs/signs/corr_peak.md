# corr_peak — Peak Correlation B-Metric Alignment sign detector
Fires on the first hourly bar of the day when N225 zigzag confirms a new LOW,
for stocks where the peak-correlation B-metric vs ^N225 DOWN peaks is negative.

A negative B-metric means the stock historically *rises* in the window after
a confirmed N225 low — making it a natural buy candidate at each N225 bottom.

Conditions:
- ``n225_down_corr_b`` < 0  (stock tends to rise after N225 confirmed lows)
- N225 zigzag just confirmed a LOW (direction = −2)

Score = min(−n225_down_corr_b, 1.0)
More negative B → higher confidence → higher score.

Valid for up to ``valid_bars`` *hourly bars* after firing (time-bounded only).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign corr_peak --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
NOT RUN — requires a prior peak_corr analysis run (PeakCorrRun table must be populated)
Run peak-corr analysis first, then re-execute the benchmark command above.
```
