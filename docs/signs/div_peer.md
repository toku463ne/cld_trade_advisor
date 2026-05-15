# div_peer — Intra-cluster Divergence sign detector
Fires on the first hourly bar of a trading day when:
- Stock daily return > +STOCK_RET_MIN   (+0.5 %)
- ≥ PEER_DOWN_FRAC (60 %) of the cluster peers have daily return < PEER_DOWN_MIN (−0.3 %)

Daily returns are derived from hourly caches (last close of each date).

Score = min(stock_ret / 0.02, 1.0) × peer_down_fraction
A stock rising strongly (+2 %) while all peers fall scores 1.0.
A marginal rise (+0.5 %) while 60 % of peers are down scores 0.3 × 0.6 = 0.18.

Valid for up to ``valid_bars`` *trading days* after firing (default 1 — the
underlying signal is a single-day close-to-close peer return, so a longer
validity window would let stale fires linger past the period actually measured).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign div_peer --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=33  n=474  direction_rate=57.4%  p≈0.001
bench_flw=0.048  bench_rev=0.031  mean_bars=12.4  (mag_flw=0.084  mag_rev=0.072)
→ RECOMMEND (FLW) — significant; intra-cluster divergence is a reliable follow-through signal
Low-corr only (run_id=40, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign div_peer --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=97  direction_rate=47.8%  p≈0.665  bench_flw=0.035
  → Note: reverses on low-corr stocks (p not significant, below 50%); use on all corr regimes
```
