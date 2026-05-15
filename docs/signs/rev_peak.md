# rev_peak — Price Near Recent Same-Side Zigzag Peak (Reversal)
Fires on the bar when the bar's tested price is within ``proximity_pct``
of one of the last ``n_peaks`` confirmed same-type zigzag peaks of the
input cache (typically daily bars).

side='lo'  → test_price = bar.low  near a prior confirmed LOW
sign_type = "rev_lo"  — expect UP bounce (support test)
side='hi'  → test_price = bar.high near a prior confirmed HIGH
sign_type = "rev_hi"  — expect DOWN reversal (resistance test)

Only peaks whose zigzag confirmation has fully passed before the current
bar are used — no look-ahead. Two filters are applied at firing time:

Directional approach
The bar must be moving toward the level: close < open for rev_lo;
close > open for rev_hi.

Long rejection wick (hammer / shooting-star body)
For rev_lo, the lower wick — the distance from min(open, close) to low —
must be at least ``wick_min`` × (high − low). This captures the
buyer-stepped-in intraday rejection that distinguishes a real reversal
from a straight slide through support. For rev_hi the upper wick —
high − max(open, close) — is required.

Score = 1 − proximity / proximity_pct
1.0 when price is exactly at the prior peak; 0.0 at the boundary.

Valid for up to ``valid_bars`` bars after firing (time-bounded only).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
rev_lo (side='lo'):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_lo --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d
  run_id=28  n=1829  direction_rate=58.6%  p<0.001
  bench_flw=0.049  bench_rev=0.028  mean_bars=13.0  (mag_flw=0.083  mag_rev=0.067)
  → RECOMMEND (FLW) — strong and significant; best direction_rate among high-n signs
rev_lo low-corr only (run_id=43, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_lo --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=356  direction_rate=57.9%  p≈0.003  bench_flw=0.043
  → Note: corr-neutral; support-test thesis holds regardless of index coupling
rev_hi (side='hi'):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_hi --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d
  run_id=29  n=2180  direction_rate=50.5%  p≈0.64
  bench_flw=0.039  bench_rev=0.034  mean_bars=12.4  (mag_flw=0.077  mag_rev=0.069)
  → SKIP — no directional edge at prior-high resistance
rev_hi low-corr only (run_id=44, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_hi --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=520  direction_rate=53.8%  p≈0.083  bench_flw=0.042
  → Note: slight improvement on low-corr stocks but still borderline; remains SKIP
```
