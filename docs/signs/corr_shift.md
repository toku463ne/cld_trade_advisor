# corr_shift — Overseas Correlation Crossover sign detector
Fires on the *entry* into a "US-leading" regime, defined by a state machine
over a single combined measure — the spread between the two correlations:

spread       = corr(stock, ^GSPC) − corr(stock, ^N225)
spread_delta = spread − spread.shift(delta_window)

ENTER (all three must hold; entry condition must persist ``persist_days``
consecutive days):
spread_delta > spread_delta_min        # spread has widened meaningfully
Δcorr(stock, ^GSPC) > 0                # GSPC corr is actually rising
(not just N225 falling faster)
spread > 0                             # GSPC has crossed above N225
(the cross has actually happened)

EXIT (hysteresis):
spread < exit_spread_max               # cross reversed (slight negative
threshold prevents flip-flop)

The detector emits one fire per regime — the day the entry condition has
held for ``persist_days`` consecutive days.

Score = 1 / (1 + exp(−score_k × (spread_delta − x₀)))
Logistic in spread_delta. x₀ defaults to spread_delta_min so a bare-entry
signal scores ~0.5; scores climb smoothly toward 1.0 for larger shifts
(no hard saturation).

Valid for up to ``valid_bars`` trading days after firing (time-bounded only).
The corr series are loaded externally from the moving_corr table and passed
in as pd.Series (ts → corr_value) at daily granularity.

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign corr_shift --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=32  n=1654  direction_rate=51.6%  p≈0.19
bench_flw=0.045  bench_rev=0.039  mean_bars=12.4  (mag_flw=0.088  mag_rev=0.081)
→ SKIP — no statistically significant directional edge
```
