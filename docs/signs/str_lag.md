# str_lag — Delayed-trough follower after N225 confirmed low
Fires when a stock makes an early daily LOW trough LAG_MIN–LAG_MAX *stock*
bars after N225's most recent confirmed low, while N225's own recovery from
that low is still below N225_RECOVERY_MAX.

Thesis: a high-N225-corr stock that lags behind the N225 bottom by a short
window (3–7 trading days) produces a more reliable buy point than a trough
that occurs simultaneously with or well after N225.  N225 has already proven
its low; the stock is still pricing in fear while the index has turned.

Conditions (all must hold):
1. Stock daily early LOW: lows[i] < min(lows[i−ZZ_SIZE:i])   (ZZ_SIZE=5)
AND lows[i] < min(lows[i+1:i+3])      (ZZ_MID=2)
2. N225 had a confirmed low (dir=−2) LAG_MIN–LAG_MAX stock bars before
the stock trough AND that low is already knowable in real time
(N225_ZZ_SIZE bars have elapsed since the N225 low bar).
3. N225 recovery from its confirmed low to the stock trough date
≤ N225_RECOVERY_MAX (5 %).
4. **Bull-regime gate**: the most recent confirmed N225 zigzag peak
(in either direction) knowable as of *fire_date* must be a LOW.
If a confirmed N225 HIGH has appeared since the LOW the rally has
matured and str_lag's "catching up to a bottoming index" thesis no
longer applies. The previous benchmark showed zero edge in bear
regime (DR 50.2 %, p 0.88) versus a real edge in bull (DR 53.6 %,
p 0.01); this gate concentrates fires in the productive regime.

Score = lag_score × 0.4 + recovery_score × 0.4 + corr_score × 0.2
lag_score      = 1 – (lag – LAG_MIN) / (LAG_MAX – LAG_MIN)  clipped [0.1, 1.0]
recovery_score = 1 – n225_recovery / N225_RECOVERY_MAX       clipped [0.0, 1.0]
corr_score     = max(0, corr_n225_daily_20bar at trough date)

Fire date: first bar of the day 2 bars after the trough is detectable
(= stock_dates[i + ZZ_MID], ZZ_MID=2 → fire 2 days after trough).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
All stocks (run_id=34, ZZ_SIZE=5, ZZ_MID=2):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign str_lag --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d
  n=2355  direction_rate=52.1%  p≈0.042
  bench_flw=0.051  bench_rev=0.029  mean_bars=13.0
  → PROVISIONAL (FLW)
  Note: run_id=19 (ZZ_SIZE=3, ZZ_MID=1) showed p=0.59 — ZZ tightening was essential.
High-corr only (run_id=36, --corr-mode high):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign str_lag --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode high
  n=805  direction_rate=54.1%  p≈0.020
  bench_flw=0.057  bench_rev=0.027  mean_bars=13.1
  → PROVISIONAL (FLW) — preferred; |corr|≥0.6 lifts p to 0.020 and bench_flw to 0.057 (best overall)
Permutation & regime split (sign_validate, run_id=34):
  Permutation test: emp_p=0.028  dedup n=2234 (×1.0)  dedup DR=52.0%  dedup p=0.057
  Regime split: bear DR=50.2% (p=0.876, n=1021)  bull DR=53.6% (p=0.010, n=1247)
  → KEY FINDING: zero edge in bear regime; all signal in bull regime (N225 in recovery).
    Gate required: only fire when last confirmed N225 zigzag peak was a LOW (bull regime).
```
