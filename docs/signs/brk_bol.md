# brk_bol — Bollinger Band Breakout sign detector

Fires on the bar where close *decisively* breaks above the upper Bollinger Band
(N-bar SMA + n_std × rolling σ), i.e.:

- The previous `min_below_bars` bars all closed at-or-below the upper band
  (volatility containment phase), AND
- The current bar closes above the upper band, AND
- The current bar's volume is at least `volume_mult` × the rolling-mean
  volume over the BB window (default 1.5×). Without volume confirmation, a
  "breakout" can simply be a quiet drift while σ stays small (the band moves
  toward price, rather than price expanding through the band).

A pure 1-bar crossover is rejected — we require sustained pre-crossover
containment AND volume confirmation to filter noisy oscillation around the band.

## Score

```
Score = min(0.5 + excess × 0.5, 1.0)
  excess = (close − upper_band) / σ   [how many σ above the upper band]
```

A close right at the upper band scores 0.5; each additional σ adds 0.5 more.

## Validity

Valid for up to `valid_bars` bars after firing, provided close remains above
the upper band. If price retreats below the upper band the sign expires early.

## Benchmark (classified2023 · 164 stocks · 2023-04-01 → 2025-03-31 · gran=1d)

```
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign brk_bol --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
```

- `run_id=27`  n=2540  direction_rate=52.0%  p≈0.044
- `bench_flw=0.047`  `bench_rev=0.034`  mean_bars=12.5  (mag_flw=0.090  mag_rev=0.071)
- **→ SKIP** (downgraded from PROVISIONAL after sign_validate)
  - Permutation test: emp_p=0.028 (passes)
  - Dedup check: dedup n=2189 (×1.1)  dedup DR=51.7%  dedup p=0.109 — loses significance
  - Regime split: bear DR=54.0% (p=0.027)  bull DR=50.6% (p=0.630)
  - 2/3 of events are in bull regime where DR is 50.6% (random). The headline p=0.044
    was entirely driven by bear-regime events. Add bear-regime gate + volume filter before reuse.

### Low-corr only (run_id=42, --corr-mode low)

```
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign brk_bol --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
```

- n=636  direction_rate=51.4%  p≈0.48  bench_flw=0.050
- Note: loses significance on low-corr stocks; use on all corr regimes.
