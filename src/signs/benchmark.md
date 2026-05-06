# Sign Detector Benchmark Results

## Parameters

| Item | Value |
|------|-------|
| Universe | classified2023 representatives (164 stocks) |
| Period | 2024-05-01 – 2025-03-31 |
| Zigzag size | 5 bars |
| Zigzag mid size | 2 bars |
| Trend cap days | 30 trading days |
| Direction metric | First zigzag peak type within 30 days (HIGH=follow, LOW=reverse) |

**Period caveat:** The benchmark period includes the August 2024 Nikkei crash (−12% in one day, the sharpest single-day drop since 1987). Signs that depend on support levels holding (e.g. `rev_lo`) are negatively biased; signs that fire during market stress (e.g. `corr_shift`) are positively biased.

---

## Results Table

| Run | Sign | n_events | direction_rate | p-value | mag_follow | mag_reverse | bench_flw | bench_rev | mean_bars | Verdict |
|-----|------|----------|---------------|---------|-----------|------------|----------|----------|-----------|---------|
| 1 | div_bar | 103 | 44.4 % | 0.32 | 0.078 | 0.209 | 0.035 | 0.116 | 12.0 | PROVISIONAL (REV) |
| 2 | corr_flip | 2069 | 49.5 % | 0.66 | 0.060 | 0.087 | 0.030 | 0.044 | 11.8 | SKIP |
| 4 | str_hold | 1932 | 48.4 % | 0.18 | 0.071 | 0.086 | 0.034 | 0.044 | 11.0 | SKIP |
| 5 | str_lead | 223 | 58.3 % | 0.016 | 0.090 | 0.048 | 0.052 | 0.020 | 10.4 | **RECOMMEND (FLW)** |
| 6 | div_vol | 48 | 28.6 % | 0.006 | 0.108 | 0.209 | 0.031 | 0.149 | 11.0 | PROVISIONAL (REV) |
| 7 | div_gap | 499 | 53.3 % | 0.15 | 0.086 | 0.113 | 0.046 | 0.053 | 12.3 | SKIP |
| 8 | div_peer | 0 | — | — | — | — | — | — | — | FIX (cluster size) |
| 9 | corr_shift | 568 | 39.0 % | <0.001 | 0.053 | 0.122 | 0.021 | 0.074 | 11.7 | **RECOMMEND (REV)** |
| 10 | corr_peak | 0 | — | — | — | — | — | — | — | FIX (coverage) |
| 11 | brk_sma | 12832 | 51.7 % | <0.001 | 0.074 | 0.083 | 0.038 | 0.040 | 12.2 | SKIP (fires too often) |
| 12 | brk_bol | 6627 | 49.5 % | 0.39 | 0.072 | 0.077 | 0.036 | 0.039 | 12.4 | SKIP |
| 15 | rev_lo | 27308 | 47.0 % | <0.001 | 0.072 | 0.089 | 0.034 | 0.042 | 12.3 | FIX (regime filter) |
| 16 | rev_hi | 26885 | 49.8 % | 0.50 | 0.072 | 0.075 | 0.036 | 0.038 | 12.1 | SKIP |
| 17 | rev_nhi | 3048 | 42.6 % | <0.001 | 0.066 | 0.088 | 0.028 | 0.051 | 12.3 | **RECOMMEND (REV)** |
| 18 | rev_nlo | 2976 | 51.7 % | 0.064 | 0.084 | 0.084 | 0.043 | 0.040 | 12.2 | PROVISIONAL (FLW) |

*p-value: two-tailed binomial test vs H₀ = 50 %. bench_flw = direction_rate × mag_follow; bench_rev = (1 − direction_rate) × mag_reverse.*

---

## Per-Sign Notes

### div_bar (run 1) — PROVISIONAL (REV)
- Small sample (n=103); the strong reversal magnitude (0.209) is worth monitoring.
- High `bench_rev` (0.116) with statistically weak signal (p=0.32) — likely a sampling artifact, but the effect is plausible: stocks that diverge down from N225 tend to mean-revert.
- **Action**: gather more data; do not use as primary signal.

### corr_flip (run 2) — SKIP
- Near-random direction (49.5 %), no meaningful edge in either direction.
- Large n (2069) confirms the null — the sign is firing too promiscuously.

### str_hold (run 4) — SKIP
- Hourly redesign did not improve over daily version (run 3).
- Negligible edge (direction_rate 48.4 %). Under-performs even random.

### str_lead (run 5) — **RECOMMEND (FLW)**
- Statistically significant follow-through direction (p=0.016).
- `bench_flw` = 0.052 is the best follow-through score among all signs.
- Logic: stock holds drawdown < 50 % of N225 drawdown into N225 confirmed LOW → stock is leading and will likely advance.
- **Use as**: primary long entry signal after confirmed N225 trough.

### div_vol (run 6) — PROVISIONAL (REV)
- Very strong reversal magnitude (0.209) with strong p-value (0.006), but tiny sample (n=48).
- direction_rate = 28.6 % → price more often continues down after firing; `bench_rev` = 0.149 is the highest of all signs.
- **Caution**: "reversal" in the downward direction (stock gapped up on high volume but then continued lower in 70 % of cases). Could be a short/fade signal in bear regime.
- **Action**: treat with caution; monitor with more data.

### div_gap (run 7) — SKIP
- Moderate counts (499) but no significant edge (p=0.15).
- Small gap between bench_flw (0.046) and bench_rev (0.053).

### div_peer (run 8) — FIX
- Zero events because classified2023 clusters are too small (avg 1.4 members); the 60 % threshold cannot be satisfied with 1–2 members.
- **Fix needed**: lower `_PEER_DOWN_FRAC` threshold, OR merge small clusters, OR use sector index as synthetic peer.

### corr_shift (run 9) — **RECOMMEND (REV)**
- Highly significant inverse direction (p<0.001): stock tends to *reverse* when domestic corr weakens and overseas corr strengthens.
- `bench_rev` = 0.074 is second-best reversal score.
- Logic: overseas-coupling shift often precedes N225-decoupled move, which can be a short squeeze or overseas rally that later normalises.
- **Use as**: early warning of regime change; combine with short-term momentum filter.

### corr_peak (run 10) — FIX
- Zero events because the peak_corr run 1 in the DB covers only 63 stocks and none overlap with the 164 classified2023 representatives used in this benchmark.
- **Fix needed**: run a full peak_corr calculation over all classified2023 members, or compute `B_metric` (down-correlation) on-the-fly inside the benchmark.

### brk_sma (run 11) — SKIP
- Fires 12832 times (avg 78 events per stock over 11 months) — far too frequently to be selective.
- Despite large n, direction_rate only 51.7 %; bench_flw = 0.038 is unremarkable.
- **Alternative**: add a volume-confirmation filter or require multi-bar hold above SMA before firing.

### brk_bol (run 12) — SKIP
- Near-random (49.5 %, p=0.39); no useful signal.

### rev_lo (run 15) — FIX (regime filter needed)
- direction_rate = 47.0 % (below 50 %): support levels *failed* more often than they held during this period.
- The August 2024 crash caused mass breakdowns through prior support levels, biasing this sign negatively.
- `bench_rev` (0.042) marginally exceeds `bench_flw` (0.034) — mild opposite-direction effect consistent with breakdown continuation.
- **Fix needed**: add regime filter, e.g. only fire when N225 is above its 20-day SMA (uptrending market).

### rev_hi (run 16) — SKIP
- Essentially random (49.8 %, p=0.50); no actionable edge in either direction.

### rev_nhi (run 17) — **RECOMMEND (REV)**
- Fires when a bearish hourly bar touches or exceeds the prior 20-trading-day high (n_days=20, window=20).
- direction_rate = 42.6% (p<0.001): price at a 20-day high reverses DOWN in 57.4% of cases — highly significant.
- bench_rev = 0.051 is solid; n=3048 gives strong statistical confidence.
- Logic: after reaching a 20-day high, buying pressure is exhausted and sellers step in; the bearish-bar filter confirms immediate rejection.
- Event rate ~18.6 per stock over 11 months (~1.7/month) — selective enough to be actionable.
- **Use as**: short-side entry or exit signal when stock makes a new 20-day high on a bearish bar.

### rev_nlo (run 18) — PROVISIONAL (FLW)
- Fires when a bullish hourly bar touches or falls below the prior 20-day low.
- direction_rate = 51.7% (p=0.064): marginally above random.
- bench_flw = 0.043 is the second-best follow-through score after str_lead (0.052).
- Period bias: August 2024 crash produced many 20-day lows that continued lower, suppressing direction_rate. True signal likely stronger in normal markets.
- mag_follow == mag_reverse (both 0.084) — the asymmetry comes entirely from the direction probability.
- **Action**: watch with regime filter (N225 above 20-day SMA); do not use as standalone signal in current form.

---

## Watchlist Recommendations

| Priority | Sign | Direction | Rationale |
|----------|------|-----------|-----------|
| 1 | str_lead | Follow | Statistically significant; best bench_flw; logically sound |
| 2 | corr_shift | Reverse | Highly significant; consistent with regime-change thesis |
| 3 | rev_nhi | Reverse | Highly significant; solid bench_rev; well-defined entry condition |
| Watch | div_bar | Reverse | High magnitude but small n; needs more data |
| Watch | div_vol | Reverse (bear) | Extreme magnitude; caution with direction |
| Watch | rev_nlo | Follow | Marginal significance; stronger with regime filter |

## Signs Requiring Rework

| Sign | Issue | Suggested Fix |
|------|-------|--------------|
| div_peer | Cluster too small (avg 1.4 members) | Merge small clusters or use sector ETF as synthetic peer |
| corr_peak | No coverage overlap | Re-run peak_corr over all classified2023 members |
| rev_lo | Period bias (2024 crash) | Add regime filter: only fire when N225 > 20-day SMA |
