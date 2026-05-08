# Sign Detector Benchmark Results

## Parameters

| Item | Value |
|------|-------|
| Universe | classified2023 representatives (164 stocks) |
| Period | 2023-04-01 – 2025-03-31 (2 years, ~490 trading days) |
| Granularity | 1d (daily bars for detection; daily bars for trend measurement) |
| Zigzag size | 5 bars |
| Zigzag mid size | 2 bars |
| Trend cap days | 30 trading days |
| Direction metric | First confirmed zigzag peak within 30 days (HIGH=follow, LOW=reverse) |
| Command template | `uv run --env-file devenv python -m src.analysis.sign_benchmark --sign SIGN --cluster-set classified2023 --start 2023-04-01 --end 2025-03-31 --gran 1d` |

**Note on granularity**: `--gran 1d` uses daily bars for sign detection. Signs originally designed for
intraday patterns (div_bar, div_vol) fire far fewer times on daily data — their results should be
re-evaluated with `--gran 1h` for a fair comparison.

---

## Results Table

p-value: two-tailed binomial test vs H₀ = 50 % using normal approximation.  
bench_flw = direction_rate × mag_follow;  bench_rev = (1 − direction_rate) × mag_reverse.

| Run | Sign | n | dir_rate | p-val | mag_flw | mag_rev | bench_flw | bench_rev | mean_bars | Verdict |
|-----|------|---|----------|-------|---------|---------|-----------|-----------|-----------|---------|
| 34 | str_lag    | 2355 | 52.1 % | 0.042  | 0.098 | 0.060 | 0.051 | 0.029 | 13.0 | PROVISIONAL (FLW) |
| 20 | div_bar    |   17 | 35.3 % | 0.23   | 0.102 | 0.071 | 0.036 | 0.046 | 14.2 | SKIP (n too small) |
| 21 | div_vol    |   12 | 33.3 % | 0.25   | 0.106 | 0.089 | 0.035 | 0.059 | 16.3 | SKIP (n too small) |
| 22 | div_gap    | 1037 | 58.2 % | <0.001 | 0.087 | 0.070 | 0.051 | 0.029 | 12.7 | **RECOMMEND (FLW)** |
| 23 | corr_flip  |  232 | 56.5 % | 0.048  | 0.101 | 0.062 | 0.057 | 0.027 | 12.7 | PROVISIONAL (FLW) |
| 24 | str_hold   | 3729 | 55.4 % | <0.001 | 0.083 | 0.079 | 0.046 | 0.035 | 12.1 | **RECOMMEND (FLW)** |
| 25 | str_lead   |  405 | 59.5 % | <0.001 | 0.078 | 0.058 | 0.047 | 0.024 | 11.6 | CAUTION — bull-market artifact (7-yr pooled DR=47.2%, perm_pass=3/7) |
| 26 | brk_sma    | 4800 | 53.2 % | <0.001 | 0.083 | 0.069 | 0.044 | 0.032 | 12.4 | PROVISIONAL (FLW) |
| 27 | brk_bol    | 2540 | 52.0 % | 0.044  | 0.090 | 0.071 | 0.047 | 0.034 | 12.5 | SKIP (dedup→p=0.11; no bull edge) |
| 28 | rev_lo     | 1829 | 58.6 % | <0.001 | 0.083 | 0.067 | 0.049 | 0.028 | 13.0 | **RECOMMEND (FLW)** |
| 29 | rev_hi     | 2180 | 50.5 % | 0.64   | 0.077 | 0.069 | 0.039 | 0.034 | 12.4 | SKIP |
| 30 | rev_nhi    | 3579 | 54.0 % | <0.001 | 0.088 | 0.072 | 0.047 | 0.033 | 12.6 | PROVISIONAL — bull-only; 7-yr pooled DR=48.9%, perm_pass=2/7 |
| 31 | rev_nlo    |  907 | 52.7 % | 0.10   | 0.093 | 0.071 | 0.049 | 0.033 | 11.8 | SKIP |
| 32 | corr_shift | 1654 | 51.6 % | 0.19   | 0.088 | 0.081 | 0.045 | 0.039 | 12.4 | SKIP |
| 33 | div_peer   |  474 | 57.4 % | 0.001  | 0.084 | 0.072 | 0.048 | 0.031 | 12.4 | **RECOMMEND (FLW)** |
| —  | corr_peak  |    — |      — |     —  |     — |     — |     — |     — |    — | NOT RUN (needs peak_corr DB) |

---

## Permutation Test & Regime Split (sign_validate)

Run via:
```
uv run --env-file devenv python -m src.analysis.sign_validate \
    --run-ids 22,23,24,25,26,27,28,30,31,33,34
```

**Permutation test**: 2000 iterations simulating H₀ (each event outcome Bernoulli 0.5).  
**Dedup check**: 1 event per stock per 5-day window — inflation ×N shows how many consecutive same-episode fires are in the full count; stable DR means the signal is not a clustering artefact.  
**Regime**: last confirmed N225 zigzag peak (ZZ_SIZE=5) at the fire date. `bear` = last peak was HIGH (N225 declining); `bull` = last peak was LOW (N225 rising).

| Sign | emp_p | dedup n (×infl) | dedup DR | dedup p | bear DR (p) | bull DR (p) | Regime verdict |
|------|-------|-----------------|----------|---------|-------------|-------------|----------------|
| div_gap  | <0.001 | 924 (×1.1) | 57.7% | <0.001 | 62.6% (<0.001) | 54.1% (0.062) | **bear only** |
| corr_flip| 0.022  | 223 (×1.0) | 56.5% | 0.052  | 54.1% (0.389)  | 58.4% (0.074) | neutral (bull slight) |
| str_hold | <0.001 | 1851 (×1.9) | 58.1% | <0.001 | 54.3% (<0.001) | 59.3% (<0.001) | **both** (bull stronger) |
| str_lead | <0.001 | 341 (×1.0) | 59.5% | <0.001 | — (0 events)   | 59.5% (<0.001) | bull only (by design) |
| brk_sma  | <0.001 | 4005 (×1.2) | 52.9% | <0.001 | 53.2% (0.005)  | 53.3% (0.001)  | **regime-neutral** |
| brk_bol  | 0.028  | 2189 (×1.1) | 51.7% | 0.109  | 54.0% (0.027)  | 50.6% (0.630)  | **bear only; dedup → SKIP** |
| rev_lo   | <0.001 | 1366 (×1.3) | 57.7% | <0.001 | 57.8% (<0.001) | 59.3% (<0.001) | **both** |
| rev_nhi  | <0.001 | 2672 (×1.3) | 54.5% | <0.001 | 51.2% (0.468)  | 54.4% (<0.001) | **bull only** |
| rev_nlo  | 0.069  | 806 (×1.0)  | 52.7% | 0.121  | — (0 events)   | 52.7% (0.121)  | → confirmed SKIP |
| div_peer | <0.001 | 413 (×1.1)  | 58.4% | <0.001 | 59.5% (0.017)  | 56.2% (0.032)  | **both** |
| str_lag  | 0.028  | 2234 (×1.0) | 52.0% | 0.057  | 50.2% (0.876)  | 53.6% (0.010)  | **bull only; gate required** |

### Verdict changes from validation

- **`brk_bol` → SKIP**: dedup DR drops to 51.7% (p=0.109). Bull-regime DR = 50.6% (p=0.63) — no edge in 2/3 of all events. The original p=0.044 was entirely driven by bear-regime events and was fragile.
- **`str_lag` → PROVISIONAL with bull gate**: The 1082 bear-regime events have DR=50.2% (no edge). All signal lives in bull regime (N225 in recovery). Gate: only fire when the last confirmed N225 peak was a LOW. This lifts effective DR to 53.6% on n=1247.
- **`rev_nhi` → note added**: No edge in bear regime (DR=51.2%, p=0.47). Only use in bull regime. Already fires mostly in bull (74% of events) so the headline metric is not materially affected.
- **`div_gap` → note added**: 62.6% DR in bear vs 54.1% in bull. Prioritise during N225 bear phases.
- **`str_hold` → confirmed RECOMMEND**: Dedup n=1851 (from 3555), DR rises to 58.1% — the repeat fires actually lower the average. Strong in both regimes.

### Multi-Year Verdict Revisions (FY2018–FY2024)

The 2-year benchmark (classified2023, 2023–2025) coincided with a strong bull market. Cross-FY validation
reveals two signs whose 2-year RECOMMEND was driven by that cycle, not structural edge:

- **`str_lead` → CAUTION**: 2-year DR=59.5% in a sustained bull market (FY2023+FY2024). 7-year pooled
  DR=47.2% — actually a reversal sign in 4 of 7 FYs. perm_pass=3/7 but the passing years (FY2018, FY2024)
  were both strong bull years. In non-bull years (FY2019, FY2020, FY2021, FY2022) DR ranges from 26.6–46.2%.
  **Do not use as a follow-through sign in non-bull N225 environments.**

- **`rev_nhi` → PROVISIONAL (bull-only)**: 2-year DR=54.0% confirmed mostly in bull regime. 7-year pooled
  DR=48.9%, perm_pass=2/7. Bear-regime DR consistently ≤50%; the 2-year RECOMMEND was a regime-selection
  artifact. **Only use in confirmed bull N225 regime; treat as SKIP otherwise.**

- **`rev_nlo` → confirmed SKIP (reversal)**: perm_pass=0/7, pooled_DR=45.4%. Fires as a reversal
  (price goes down after the sign) in most years — the capitulation-bounce thesis is not supported.
  The sign fires at N225 confirmed LOWs but many false bottoms cause the stock to continue falling.

---

## Corr-Mode Filter Analysis

`--corr-mode high` keeps only events where `|corr(stock, ^N225)| ≥ 0.6` at the fire date.  
`--corr-mode low` keeps only events where `|corr(stock, ^N225)| ≤ 0.3` at the fire date.

### N225-linked signs with `--corr-mode high`

| Run | Sign | n (all) | n (high) | dr (all) | dr (high) | p (all) | p (high) | bench_flw (high) | Δ |
|-----|------|---------|---------|---------|---------|---------|---------|----------|---|
| 35 | str_lead | 405 | 149 | 59.5 % | 56.3 % | <0.001 | 0.12 | 0.045 | ↓ n too small |
| 36 | str_lag  | 2355 | 805 | 52.1 % | 54.1 % | 0.042 | 0.020 | 0.057 | **↑ improved** |
| 37 | str_hold | 3729 | 819 | 55.4 % | 58.4 % | <0.001 | <0.001 | 0.048 | **↑ improved** |
| 38 | rev_nlo  | 907 | 614 | 52.7 % | 53.8 % | 0.10 | 0.060 | 0.052 | ↑ slight |

### Stock-specific signs with `--corr-mode low`

| Run | Sign | n (all) | n (low) | dr (all) | dr (low) | p (all) | p (low) | bench_flw (low) | Δ |
|-----|------|---------|---------|---------|---------|---------|---------|----------|---|
| 39 | div_gap   | 1037 | 355 | 58.2 % | 54.6 % | <0.001 | 0.083 | 0.046 | ↓ WORSE on low-corr |
| 40 | div_peer  | 474  |  97 | 57.4 % | 47.8 % | 0.001 | 0.66 | 0.035 | ↓ reverses on low-corr |
| 41 | brk_sma   | 4800 | 954 | 53.2 % | 53.3 % | <0.001 | 0.041 | 0.045 | ≈ mode-neutral |
| 42 | brk_bol   | 2540 | 636 | 52.0 % | 51.4 % | 0.044 | 0.48 | 0.050 | ↓ loses significance |
| 43 | rev_lo    | 1829 | 356 | 58.6 % | 57.9 % | <0.001 | 0.003 | 0.043 | ≈ holds |
| 44 | rev_hi    | 2180 | 520 | 50.5 % | 53.8 % | 0.64 | 0.083 | 0.042 | ↑ slight |
| 45 | rev_nhi   | 3579 | 917 | 54.0 % | 53.7 % | <0.001 | 0.025 | 0.052 | ≈ holds |
| 46 | corr_flip | 232  | 215 | 56.5 % | 56.2 % | 0.048 | 0.069 | 0.056 | ≈ neutral |

### Conclusions

**The corr-mode filter confirms the philosophy for N225-linked signs:**
- `str_hold (high)`: direction_rate 55.4 % → 58.4 %, highly significant. A stock that holds up during a N225 decline is a much stronger signal when it *usually* tracks the index.
- `str_lag (high)`: p improved 0.042 → 0.020, bench_flw jumped to 0.057 (best overall). The delayed-trough thesis requires the stock to be correlated with N225 for the lag to be meaningful.

**Stock-specific signs do NOT improve on low-corr stocks:**
- `div_gap` is actually STRONGEST across all corr regimes — it works *best* when a high-corr stock momentarily breaks from a gapping-down index. Restricting to low-corr stocks cuts the signal.
- `div_peer` and `brk_bol` lose significance entirely on low-corr stocks.
- `rev_lo` and `rev_nhi` are roughly corr-neutral — price-level signals work regardless of index coupling.

**Practical implication:** The corr-mode rule in CLAUDE.md governs **position concentration**, not **signal validity**. You may trade stock-specific signs on any stock, but you must not hold multiple high-corr positions simultaneously. The filter should be applied at portfolio level, not sign level — except for N225-linked signs (str_hold, str_lag) where restricting to high-corr stocks materially improves signal quality.

---

## Per-Sign Notes

### div_gap (run 22) — **RECOMMEND (FLW)**
- Fires when stock gaps up while N225 gaps down at the session open.
- direction_rate = 58.2 %, p < 0.001, n = 1037 — highest bench_flw of all signs (0.051).
- Logic: overnight buyers chose this stock despite a negative index open; the committed
  positioning creates genuine buying pressure.
- Previously SKIP (short period, p=0.15); the 2-year window confirms the signal is real.
- **Regime split**: DR = 62.6 % in bear regime vs 54.1 % in bull (borderline p=0.062).
  Signal is strongest when N225 is already declining — diverging from a falling index is more meaningful.
- **Use as**: primary long entry on gap-up divergence mornings; prioritise in bear-N225 regime.

### str_lead (run 25) — **RECOMMEND (FLW)**
- Fires when N225 zigzag confirms a LOW and the stock held most of its value during the decline.
- direction_rate = 59.5 %, p < 0.001, n = 405 — highest direction_rate of all signs.
- Logic: relative strength during the decline flags genuine demand; the confirmed N225 bottom
  removes macro headwind.
- **Use as**: primary long entry at confirmed N225 troughs for high-corr stocks.

### rev_lo (run 28) — **RECOMMEND (FLW)**
- Fires when a daily bar tests a prior confirmed zigzag low (within 1.5 % proximity).
- direction_rate = 58.6 %, p < 0.001, n = 1829 — second-highest direction_rate.
- Previously marked FIX (Aug 2024 bias); the 2-year window shows the support-test thesis holds
  over a full market cycle.
- **Use as**: long entry at confirmed support levels; works in trending markets.

### str_hold (run 24/37) — **RECOMMEND (FLW)**
- Fires when the stock outperforms N225 over a rolling 5-day window of N225 decline.
- All stocks: direction_rate = 55.4 %, p < 0.001, n = 3729.
- **High-corr only** (run 37): direction_rate = **58.4 %**, p < 0.001, n = 819 — confirms that a high-corr stock staying flat while N225 falls is a much stronger signal than a chronically-independent stock doing the same.
- **Preferred mode**: `--corr-mode high`; use all-stocks version only as a broad first-pass.
- **Dedup check**: inflation ×1.9 (fires on consecutive days during same N225 decline), but dedup DR *rises* to 58.1 % — the repeat fires lower the average; the underlying signal is stronger than the headline suggests.
- **Regime split**: significant in both bear (54.3 %) and bull (59.3 %) regimes. Bull-regime fires are short corrections within a recovery — also predictive.
- Fires frequently; combine with score threshold (> 0.5) to reduce noise.

### rev_nhi (run 30) — **RECOMMEND (FLW) — bull regime only**
- Fires when a bearish bar touches the prior 20-day high.
- direction_rate = 54.0 %, p < 0.001, n = 3579.
- **Important change from prior benchmark**: previously RECOMMEND (REV) with dr = 42.6 % on
  a short period. Over 2 years, touching a prior high slightly favours follow-through (FLW),
  not reversal. The Aug 2024 crash distorted the earlier result.
- bench_flw = 0.047 is competitive; mag_flw (0.088) is among the best.
- **Regime split**: bull DR = 54.4 % (p < 0.001, n=2471); bear DR = 51.2 % (p = 0.47, n=920) — no
  edge in bear. Breakouts follow through only when the broad trend supports them.
- **Use as**: breakout-confirmation entry in bull-N225 regime; skip in bear regime.

### div_peer (run 33) — **RECOMMEND (FLW)**
- Fires when the stock rises while ≥ 60 % of cluster peers decline on the same day.
- direction_rate = 57.4 %, p = 0.001, n = 474.
- Previously FIX (cluster size = 0 with old settings); fixed by using correct classified2023
  clusters.
- Logic: intra-cluster divergence isolates genuine stock-specific demand.
- **Use as**: low-corr long entry when stock leads its sector peers.

### corr_flip (run 23) — PROVISIONAL (FLW)
- Fires when rolling corr(stock, N225) crosses from negative to positive.
- direction_rate = 56.5 %, p = 0.048, n = 232 — borderline significant with small n.
- bench_flw = 0.057 is the *highest of all signs* despite the small sample.
- **Use as**: watch carefully; accumulate data. Strong when it fires but fires rarely.

### brk_sma (run 26) — PROVISIONAL (FLW)
- direction_rate = 53.2 %, p < 0.001, n = 4800 — significant but weak edge.
- Fires very frequently (avg 29/stock/2yr); the low dr is probably diluted by false breakouts.
- **Recommendation**: add a volume-confirmation filter to raise dr before using.

### brk_bol (run 27) — **SKIP** *(downgraded from PROVISIONAL)*
- direction_rate = 52.0 %, p = 0.044 — barely significant on the full set.
- **Dedup check**: after removing same-stock repeat fires within 5 days (×1.1 inflation),
  dedup DR = 51.7 %, p = 0.109 — loses significance with only light deduplication.
- **Regime split**: bear DR = 54.0 % (p = 0.027); bull DR = 50.6 % (p = 0.63). Two-thirds of
  all events fall in bull regime where there is no edge whatsoever.
- The full-set p=0.044 was entirely driven by bear-regime events; this is too narrow to rely on.
- **Downgraded to SKIP**. Re-evaluate after adding bear-regime gate + volume confirmation.

### str_lag (run 34) — PROVISIONAL (FLW) — bull regime gate required
- Fires when stock makes a daily early low trough 3–7 bars after N225's confirmed low with < 5 % N225 recovery.
- Parameters: `_STOCK_ZZ_SIZE=5`, `_STOCK_ZZ_MID=2`, `_N225_ZZ_SIZE=3`, `LAG_MIN=3`, `LAG_MAX=7`.
- direction_rate = 52.1 %, p ≈ 0.042, n = 2355 — borderline significant.
- bench_flw = 0.051 ties div_gap for highest among all signs; mag_flw = 0.098 is strongest.
- Tightening ZZ_SIZE 3→5 / ZZ_MID 1→2 halved event count (4446 → 2355) and pushed p from 0.59 to 0.042.
- **Regime split (key finding)**: bear DR = 50.2 % (p = 0.876, n=1021) — zero edge. Bull DR = 53.6 % (p = 0.010, n=1247) — all the signal lives in bull regime. The bear-regime fires occur when a prior N225 HIGH was the last confirmed peak; the stock lag may be following the index down rather than lagging a recovery.
- **Gate required**: only fire when the last confirmed N225 zigzag peak was a LOW (bull regime). This halves the event pool but eliminates the dead-weight bear events entirely.
- Dedup check: inflation ×1.0 (no clustering) — the signal is not a clustering artefact.

### corr_shift (run 32) — SKIP
- direction_rate = 51.6 %, p = 0.19 — no significant edge over 2 years.
- Previously RECOMMEND (REV) on 11-month period; the 2023 bull market dilutes the
  bear-regime effect. The sign is regime-conditional and needs a bear-N225 gate.
- **Recommendation**: re-evaluate with `--start 2024-01-01` (post-peak) or add
  N225 downtrend filter.

### rev_nlo (run 31) — SKIP
- direction_rate = 52.7 %, p = 0.10 — not significant.
- Fires when N225 zigzag confirms a LOW and the stock underperformed during the decline
  (capitulation bounce thesis). The capitulation thesis fires rarely in bull markets.
- n = 907 over 2 years; too few confirmed N225 troughs in a bull-market-dominant period.

### rev_hi (run 29) — SKIP
- direction_rate = 50.5 %, p = 0.64 — essentially random.
- Testing prior highs carries no predictive edge at this granularity.

### corr_shift (run 32) — SKIP
- See note above.

### div_bar (run 20) & div_vol (run 21) — SKIP (n too small)
- These fire on intraday bar patterns (1h candle vs N225); on daily data they are nearly
  inactive (17 and 12 events respectively). Re-run with `--gran 1h` for a fair benchmark.

### corr_peak — NOT RUN
- Requires `PeakCorrRun` DB table to be populated first.
- Run `uv run --env-file devenv python -m src.analysis.peak_corr` then re-run benchmark.

---

## Watchlist Recommendations

| Priority | Sign | Direction | Rationale |
|----------|------|-----------|-----------|
| 1 | div_gap  | Follow | Highest bench_flw; highly significant; clear overnight-conviction logic |
| 1 | str_lead | Follow | Highest direction_rate; highly significant; N225 trough + relative strength |
| 1 | rev_lo   | Follow | Highly significant; confirmed support-test follow-through |
| 2 | str_hold | Follow | Large sample; highly significant; moderate edge; needs score gate |
| 2 | rev_nhi  | Follow | Highly significant; breakout-confirmation on 20-day high |
| 2 | div_peer | Follow | Significant; intra-cluster divergence isolates genuine alpha |
| Watch | corr_flip | Follow | emp_p=0.022; n=232 too small for per-regime significance; slight bull preference |
| Watch | str_lag  | Follow | Bull-regime only (gate required); dedup stable; needs corr gate |
| Watch | corr_shift | Regime-gated | No edge over full cycle; may work in bear-N225 regime only |
| Rework | div_bar / div_vol | — | Re-run with --gran 1h for fair evaluation |
| Rework | corr_peak | — | Populate PeakCorrRun DB first |
| Rework | brk_bol | — | Add bear-regime gate + volume confirmation before re-evaluating |

---

## Signs Requiring Rework

| Sign | Issue | Suggested Fix |
|------|-------|--------------|
| str_lag | Bull-regime only (bear DR=50.2%); still borderline overall | Add bull-N225-regime gate (last confirmed N225 peak = LOW) |
| brk_bol | No edge in bull regime (2/3 of events); dedup p=0.109 | Add bear-regime gate + volume confirmation; re-benchmark |
| div_bar / div_vol | Nearly no events on 1d gran | Re-benchmark with `--gran 1h` |
| corr_peak | PeakCorrRun not populated | Run peak_corr analysis over classified2023 members |
| corr_shift | Regime-conditional | Add N225 downtrend filter (e.g. N225 < 50-day SMA) |

---

## Multi-Year Benchmark (FY2018–FY2024)

Generated: 2026-05-08  
Universe: Nikkei225 representatives from prior FY's cluster  
Granularity: 1d · window=20 · valid_bars=5 · ZZ_SIZE=5 · trend_cap=30  
Permutation: 1000 iterations  

```
nohup uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear > /tmp/multiyear_bench.log 2>&1 &
```

Phases run in order: `download` → `cluster` (classified2017–classified2022) → `benchmark` → `validate` → `report`  
To re-run a single phase or sign:
```
uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear --phase benchmark --sign str_hold
uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear --phase validate report
```

### Per-Fiscal-Year Results

#### FY2018 (2018-04-01 – 2019-03-31) · cluster=classified2017

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   256 |  49.2% | ≈0.803  |    0.0409 |    0.0436 |    12.504 | ≈0.634  | 248 (×1.0) |    49.6% | 48.5% (≈0.732) | 50.0% (≈1.000) |
| div_peer   |    10 |  50.0% | ≈1.000  |    0.0373 |    0.0278 |    13.000 | ≈0.619  |   9 (×1.1) |    55.6% | 60.0% (≈0.655) | 40.0% (≈0.655) |
| corr_flip  |    94 |  56.4% | ≈0.216  |    0.0325 |    0.0339 |    12.681 | ≈0.119  |  94 (×1.0) |    56.4% | 66.7% (≈0.157) | 54.1% (≈0.485) |
| corr_shift |  1063 |  55.2% | <0.001  |    0.0358 |    0.0388 |    13.430 | <0.001  | 533 (×2.0) |    52.7% | 53.1% (≈0.126) | 58.2% (<0.001) |
| str_hold   |  1364 |  59.5% | <0.001  |    0.0427 |    0.0317 |    12.435 | <0.001  | 873 (×1.6) |    60.6% | 62.0% (<0.001) | 50.6% (≈0.820) |
| str_lead   |   123 |  60.2% | ≈0.024  |    0.0416 |    0.0205 |    13.252 | ≈0.017  | 123 (×1.0) |    60.2% | —             | 60.2% (≈0.024) |
| str_lag    |   910 |  46.4% | ≈0.029  |    0.0340 |    0.0376 |    11.997 | ≈0.983  | 897 (×1.0) |    46.2% | 44.6% (≈0.050) | 45.1% (≈0.024) |
| brk_sma    |  2722 |  49.8% | ≈0.848  |    0.0365 |    0.0444 |    12.446 | ≈0.597  | 2341 (×1.2) |    50.0% | 51.5% (≈0.343) | 48.9% (≈0.346) |
| brk_bol    |  1257 |  48.2% | ≈0.204  |    0.0305 |    0.0437 |    12.566 | ≈0.898  | 1098 (×1.1) |    48.4% | 45.7% (≈0.121) | 49.1% (≈0.577) |
| rev_lo     |  3447 |  52.1% | ≈0.012  |    0.0383 |    0.0383 |    12.681 | ≈0.013  | 2039 (×1.7) |    51.7% | 53.7% (≈0.001) | 50.3% (≈0.803) |
| rev_hi     |  3485 |  47.9% | ≈0.012  |    0.0293 |    0.0404 |    12.375 | ≈0.998  | 2041 (×1.7) |    47.9% | 53.0% (≈0.038) | 45.1% (<0.001) |
| rev_nhi    |  1832 |  43.8% | <0.001  |    0.0285 |    0.0509 |    12.937 | ≈1.000  | 1428 (×1.3) |    43.5% | 39.8% (<0.001) | 45.3% (<0.001) |
| rev_nlo    |   720 |  53.1% | ≈0.101  |    0.0413 |    0.0308 |    13.581 | ≈0.060  | 720 (×1.0) |    53.1% | —             | 53.1% (≈0.101) |

#### FY2019 (2019-04-01 – 2020-03-31) · cluster=classified2018

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   367 |  46.0% | ≈0.130  |    0.0348 |    0.0651 |    13.030 | ≈0.946  | 345 (×1.1) |    46.4% | 48.7% (≈0.667) | 39.2% (≈0.029) |
| div_peer   |    32 |  46.9% | ≈0.724  |    0.0243 |    0.0568 |    12.781 | ≈0.694  |  29 (×1.1) |    41.4% | 66.7% (≈0.248) | 35.0% (≈0.180) |
| corr_flip  |   109 |  61.5% | ≈0.017  |    0.0359 |    0.0357 |    12.752 | ≈0.014  | 109 (×1.0) |    61.5% | 62.5% (≈0.061) | 60.4% (≈0.131) |
| corr_shift |   882 |  44.6% | ≈0.001  |    0.0293 |    0.0507 |    12.797 | ≈0.998  | 492 (×1.8) |    43.3% | 51.9% (≈0.483) | 39.9% (<0.001) |
| str_hold   |   943 |  54.1% | ≈0.012  |    0.0404 |    0.0566 |    12.912 | ≈0.007  | 555 (×1.7) |    53.2% | 54.0% (≈0.025) | 54.9% (≈0.260) |
| str_lead   |    94 |  26.6% | <0.001  |    0.0180 |    0.0392 |    11.043 | ≈1.000  |  94 (×1.0) |    26.6% | —             | 26.6% (<0.001) |
| str_lag    |  1542 |  48.6% | ≈0.263  |    0.0411 |    0.0549 |    12.659 | ≈0.873  | 1515 (×1.0) |    48.1% | 38.5% (<0.001) | 57.5% (<0.001) |
| brk_sma    |  2557 |  49.1% | ≈0.353  |    0.0362 |    0.0520 |    13.064 | ≈0.824  | 2191 (×1.2) |    48.9% | 48.4% (≈0.229) | 49.9% (≈0.953) |
| brk_bol    |  1326 |  45.9% | ≈0.003  |    0.0303 |    0.0444 |    12.808 | ≈0.999  | 1156 (×1.1) |    46.4% | 41.7% (<0.001) | 47.7% (≈0.166) |
| rev_lo     |  3007 |  43.1% | <0.001  |    0.0285 |    0.0677 |    13.329 | ≈1.000  | 1780 (×1.7) |    43.1% | 42.7% (<0.001) | 43.6% (<0.001) |
| rev_hi     |  3944 |  51.1% | ≈0.171  |    0.0347 |    0.0402 |    12.759 | ≈0.096  | 2257 (×1.7) |    50.9% | 53.5% (≈0.004) | 49.2% (≈0.441) |
| rev_nhi    |  1890 |  45.9% | <0.001  |    0.0293 |    0.0375 |    12.470 | ≈1.000  | 1442 (×1.3) |    45.8% | 48.5% (≈0.471) | 44.9% (<0.001) |
| rev_nlo    |   321 |  23.7% | <0.001  |    0.0222 |    0.0387 |    10.271 | ≈1.000  | 321 (×1.0) |    23.7% | —             | 23.7% (<0.001) |

#### FY2020 (2020-04-01 – 2021-03-31) · cluster=classified2019

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   543 |  58.9% | <0.001  |    0.0699 |    0.0258 |    12.707 | <0.001  | 506 (×1.1) |    58.7% | 66.9% (<0.001) | 52.2% (≈0.449) |
| div_peer   |    43 |  65.1% | ≈0.047  |    0.0921 |    0.0240 |    13.372 | ≈0.032  |  36 (×1.2) |    58.3% | 68.8% (≈0.134) | 63.0% (≈0.178) |
| corr_flip  |    64 |  62.5% | ≈0.046  |    0.0678 |    0.0268 |    13.078 | ≈0.030  |  64 (×1.0) |    62.5% | 58.8% (≈0.467) | 63.8% (≈0.058) |
| corr_shift |   671 |  63.9% | <0.001  |    0.0791 |    0.0253 |    13.359 | <0.001  | 396 (×1.7) |    64.6% | 76.1% (<0.001) | 48.8% (≈0.686) |
| str_hold   |   892 |  68.8% | <0.001  |    0.0786 |    0.0183 |    12.186 | <0.001  | 596 (×1.5) |    68.5% | 70.1% (<0.001) | 58.5% (≈0.099) |
| str_lead   |   106 |  31.1% | <0.001  |    0.0234 |    0.0290 |    10.519 | ≈1.000  | 106 (×1.0) |    31.1% | —             | 31.1% (<0.001) |
| str_lag    |  1263 |  56.1% | <0.001  |    0.0649 |    0.0302 |    13.054 | <0.001  | 1247 (×1.0) |    56.1% | 67.5% (<0.001) | 48.7% (≈0.469) |
| brk_sma    |  2920 |  55.3% | <0.001  |    0.0665 |    0.0304 |    13.029 | <0.001  | 2584 (×1.1) |    55.6% | 58.7% (<0.001) | 52.8% (≈0.021) |
| brk_bol    |  1834 |  56.9% | <0.001  |    0.0631 |    0.0328 |    12.866 | <0.001  | 1634 (×1.1) |    57.5% | 62.8% (<0.001) | 54.7% (<0.001) |
| rev_lo     |  2702 |  55.4% | <0.001  |    0.0617 |    0.0298 |    12.770 | <0.001  | 1626 (×1.7) |    55.4% | 65.0% (<0.001) | 47.2% (≈0.034) |
| rev_hi     |  3319 |  55.1% | <0.001  |    0.0586 |    0.0296 |    12.990 | <0.001  | 2124 (×1.6) |    55.9% | 60.9% (<0.001) | 51.6% (≈0.158) |
| rev_nhi    |  2414 |  54.3% | <0.001  |    0.0578 |    0.0335 |    12.737 | <0.001  | 1829 (×1.3) |    54.0% | 65.8% (<0.001) | 50.0% (≈1.000) |
| rev_nlo    |   479 |  27.8% | <0.001  |    0.0243 |    0.0406 |    11.668 | ≈1.000  | 479 (×1.0) |    27.8% | —             | 27.8% (<0.001) |

#### FY2021 (2021-04-01 – 2022-03-31) · cluster=classified2020

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   545 |  51.9% | ≈0.368  |    0.0444 |    0.0361 |    12.519 | ≈0.183  | 516 (×1.1) |    51.6% | 47.6% (≈0.535) | 53.8% (≈0.136) |
| div_peer   |    66 |  40.9% | ≈0.140  |    0.0414 |    0.0548 |    12.742 | ≈0.946  |  60 (×1.1) |    41.7% | 50.0% (≈1.000) | 33.3% (≈0.046) |
| corr_flip  |    36 |  61.1% | ≈0.182  |    0.0381 |    0.0322 |    11.306 | ≈0.119  |  36 (×1.0) |    61.1% | 53.3% (≈0.796) | 66.7% (≈0.127) |
| corr_shift |   633 |  55.1% | ≈0.010  |    0.0407 |    0.0377 |    12.237 | ≈0.004  | 371 (×1.7) |    55.3% | 55.8% (≈0.034) | 54.4% (≈0.131) |
| str_hold   |  1767 |  58.1% | <0.001  |    0.0494 |    0.0299 |    12.060 | <0.001  | 1091 (×1.6) |    55.0% | 58.5% (<0.001) | 56.3% (≈0.025) |
| str_lead   |   290 |  46.2% | ≈0.196  |    0.0305 |    0.0308 |    12.476 | ≈0.909  | 290 (×1.0) |    46.2% | —             | 46.2% (≈0.196) |
| str_lag    |  1814 |  54.9% | <0.001  |    0.0387 |    0.0365 |    12.353 | <0.001  | 1779 (×1.0) |    55.4% | 55.3% (<0.001) | 54.4% (≈0.013) |
| brk_sma    |  2746 |  50.4% | ≈0.703  |    0.0379 |    0.0392 |    12.375 | ≈0.355  | 2369 (×1.2) |    50.8% | 52.2% (≈0.104) | 48.6% (≈0.302) |
| brk_bol    |  1128 |  47.7% | ≈0.122  |    0.0337 |    0.0440 |    12.283 | ≈0.943  | 1003 (×1.1) |    48.7% | 52.8% (≈0.299) | 45.5% (≈0.012) |
| rev_lo     |  3242 |  51.2% | ≈0.182  |    0.0391 |    0.0347 |    12.327 | ≈0.102  | 1956 (×1.7) |    50.2% | 51.7% (≈0.161) | 50.6% (≈0.646) |
| rev_hi     |  3273 |  51.7% | ≈0.052  |    0.0365 |    0.0367 |    12.347 | ≈0.038  | 1958 (×1.7) |    50.3% | 54.1% (≈0.003) | 50.0% (≈0.982) |
| rev_nhi    |  1569 |  46.3% | ≈0.003  |    0.0342 |    0.0441 |    12.491 | ≈1.000  | 1248 (×1.3) |    46.5% | 50.4% (≈0.850) | 44.6% (<0.001) |
| rev_nlo    |   574 |  50.2% | ≈0.933  |    0.0441 |    0.0322 |    13.078 | ≈0.491  | 574 (×1.0) |    50.2% | —             | 50.2% (≈0.933) |

#### FY2022 (2022-04-01 – 2023-03-31) · cluster=classified2021

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   445 |  53.9% | ≈0.097  |    0.0426 |    0.0270 |    12.542 | ≈0.053  | 422 (×1.1) |    53.6% | 51.4% (≈0.713) | 55.8% (≈0.063) |
| div_peer   |    74 |  51.4% | ≈0.816  |    0.0487 |    0.0249 |    12.216 | ≈0.459  |  62 (×1.2) |    54.8% | 40.7% (≈0.336) | 57.4% (≈0.307) |
| corr_flip  |   104 |  34.6% | ≈0.002  |    0.0198 |    0.0360 |    11.962 | ≈0.999  | 104 (×1.0) |    34.6% | 46.8% (≈0.662) | 24.6% (<0.001) |
| corr_shift |   784 |  53.1% | ≈0.086  |    0.0391 |    0.0242 |    12.404 | ≈0.055  | 391 (×2.0) |    57.0% | 51.6% (≈0.609) | 53.7% (≈0.085) |
| str_hold   |  1844 |  47.1% | ≈0.012  |    0.0345 |    0.0315 |    12.415 | ≈0.997  | 1058 (×1.7) |    47.7% | 46.8% (≈0.033) | 47.5% (≈0.171) |
| str_lead   |   235 |  36.6% | <0.001  |    0.0226 |    0.0352 |    11.106 | ≈1.000  | 235 (×1.0) |    36.6% | —             | 36.6% (<0.001) |
| str_lag    |  1421 |  57.1% | <0.001  |    0.0414 |    0.0251 |    12.018 | <0.001  | 1404 (×1.0) |    57.2% | 54.5% (≈0.031) | 58.9% (<0.001) |
| brk_sma    |  2772 |  52.3% | ≈0.014  |    0.0389 |    0.0286 |    12.293 | ≈0.013  | 2399 (×1.2) |    53.0% | 51.5% (≈0.298) | 53.0% (≈0.018) |
| brk_bol    |  1276 |  47.3% | ≈0.057  |    0.0328 |    0.0363 |    12.236 | ≈0.974  | 1120 (×1.1) |    47.1% | 53.8% (≈0.154) | 44.8% (≈0.002) |
| rev_lo     |  3195 |  53.8% | <0.001  |    0.0378 |    0.0265 |    12.437 | <0.001  | 1898 (×1.7) |    55.1% | 51.5% (≈0.251) | 55.8% (<0.001) |
| rev_hi     |  3934 |  48.6% | ≈0.079  |    0.0328 |    0.0307 |    12.176 | ≈0.965  | 2215 (×1.8) |    49.8% | 52.4% (≈0.073) | 46.6% (<0.001) |
| rev_nhi    |  1723 |  49.6% | ≈0.718  |    0.0341 |    0.0335 |    12.184 | ≈0.651  | 1326 (×1.3) |    48.8% | 52.7% (≈0.274) | 48.6% (≈0.308) |
| rev_nlo    |   532 |  50.8% | ≈0.729  |    0.0466 |    0.0315 |    12.271 | ≈0.376  | 532 (×1.0) |    50.8% | —             | 50.8% (≈0.729) |

#### FY2023 (2023-04-01 – 2024-03-31) · cluster=classified2022

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   259 |  51.0% | ≈0.756  |    0.0518 |    0.0386 |    12.409 | ≈0.411  | 237 (×1.1) |    52.3% | 57.1% (≈0.131) | 46.3% (≈0.364) |
| div_peer   |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| corr_flip  |    89 |  57.3% | ≈0.168  |    0.0511 |    0.0269 |    12.787 | ≈0.099  |  89 (×1.0) |    57.3% | 54.5% (≈0.602) | 58.9% (≈0.181) |
| corr_shift |   318 |  49.7% | ≈0.911  |    0.0371 |    0.0285 |    12.572 | ≈0.568  | 177 (×1.8) |    52.5% | 40.4% (≈0.050) | 54.2% (≈0.219) |
| str_hold   |   632 |  46.0% | ≈0.047  |    0.0440 |    0.0353 |    12.028 | ≈0.980  | 323 (×2.0) |    46.7% | 45.6% (≈0.062) | 47.1% (≈0.448) |
| str_lead   |    79 |  68.4% | ≈0.001  |    0.0434 |    0.0148 |    11.101 | ≈0.001  |  79 (×1.0) |    68.4% | —             | 68.4% (≈0.001) |
| str_lag    |   413 |  53.3% | ≈0.184  |    0.0570 |    0.0250 |    13.421 | ≈0.091  | 411 (×1.0) |    53.0% | 48.6% (≈0.683) | 58.4% (≈0.019) |
| brk_sma    |   925 |  53.6% | ≈0.028  |    0.0491 |    0.0247 |    12.729 | ≈0.018  | 794 (×1.2) |    53.4% | 55.0% (≈0.054) | 52.7% (≈0.200) |
| brk_bol    |   567 |  54.7% | ≈0.026  |    0.0483 |    0.0276 |    12.397 | ≈0.018  | 504 (×1.1) |    54.4% | 51.0% (≈0.775) | 56.6% (≈0.011) |
| rev_lo     |  1250 |  57.0% | <0.001  |    0.0426 |    0.0169 |    12.661 | <0.001  | 702 (×1.8) |    58.0% | 51.4% (≈0.532) | 60.8% (<0.001) |
| rev_hi     |  1528 |  54.4% | <0.001  |    0.0426 |    0.0220 |    12.325 | <0.001  | 842 (×1.8) |    54.6% | 52.4% (≈0.241) | 55.8% (<0.001) |
| rev_nhi    |   725 |  53.5% | ≈0.058  |    0.0386 |    0.0247 |    12.510 | ≈0.038  | 561 (×1.3) |    55.6% | 52.8% (≈0.410) | 54.0% (≈0.069) |
| rev_nlo    |   128 |  50.8% | ≈0.860  |    0.0574 |    0.0315 |    11.500 | ≈0.473  | 128 (×1.0) |    50.8% | —             | 50.8% (≈0.860) |

#### FY2024 (2024-04-01 – 2025-03-31) · cluster=classified2023

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   461 |  58.4% | <0.001  |    0.0484 |    0.0337 |    12.584 | <0.001  | 432 (×1.1) |    57.2% | 69.0% (<0.001) | 49.0% (≈0.749) |
| div_peer   |   274 |  56.6% | ≈0.030  |    0.0478 |    0.0343 |    12.226 | ≈0.016  | 236 (×1.2) |    58.1% | 62.1% (≈0.014) | 53.2% (≈0.400) |
| corr_flip  |   116 |  52.6% | ≈0.577  |    0.0531 |    0.0246 |    12.328 | ≈0.327  | 116 (×1.0) |    52.6% | 52.9% (≈0.633) | 52.2% (≈0.768) |
| corr_shift |   839 |  45.8% | ≈0.014  |    0.0362 |    0.0530 |    12.231 | ≈0.990  | 458 (×1.8) |    47.4% | 53.1% (≈0.291) | 41.9% (<0.001) |
| str_hold   |  2133 |  58.1% | <0.001  |    0.0480 |    0.0401 |    11.867 | <0.001  | 1108 (×1.9) |    61.5% | 56.9% (<0.001) | 63.8% (<0.001) |
| str_lead   |   216 |  62.0% | <0.001  |    0.0512 |    0.0223 |    11.597 | <0.001  | 216 (×1.0) |    62.0% | —             | 62.0% (<0.001) |
| str_lag    |  1002 |  50.7% | ≈0.658  |    0.0503 |    0.0312 |    12.726 | ≈0.333  | 988 (×1.0) |    50.9% | 53.6% (≈0.154) | 48.9% (≈0.573) |
| brk_sma    |  2272 |  55.1% | <0.001  |    0.0456 |    0.0338 |    12.448 | <0.001  | 1996 (×1.1) |    54.4% | 58.4% (<0.001) | 52.9% (≈0.030) |
| brk_bol    |   960 |  49.3% | ≈0.651  |    0.0444 |    0.0435 |    12.259 | ≈0.681  | 857 (×1.1) |    49.5% | 57.7% (≈0.012) | 46.0% (≈0.037) |
| rev_lo     |  2597 |  57.3% | <0.001  |    0.0458 |    0.0308 |    12.446 | <0.001  | 1489 (×1.7) |    56.3% | 57.4% (<0.001) | 57.1% (<0.001) |
| rev_hi     |  2604 |  51.8% | ≈0.060  |    0.0376 |    0.0372 |    12.257 | ≈0.039  | 1559 (×1.7) |    52.6% | 57.5% (<0.001) | 48.6% (≈0.270) |
| rev_nhi    |  1227 |  50.5% | ≈0.711  |    0.0429 |    0.0447 |    12.465 | ≈0.354  | 980 (×1.3) |    50.4% | 55.3% (≈0.054) | 48.8% (≈0.463) |
| rev_nlo    |   439 |  54.0% | ≈0.095  |    0.0584 |    0.0294 |    12.014 | ≈0.052  | 439 (×1.0) |    54.0% | —             | 54.0% (≈0.095) |

### Aggregate by Sign (FY2018–FY2024)

| Sign | FYs | total_n | pooled_DR% | p_pooled | avg_bench_flw | avg_bench_rev | perm_pass | bear_DR range | bull_DR range |
|------|-----|---------|------------|----------|--------------|---------------|-----------|---------------|---------------|
| div_gap    |   7 |    2876 |      53.5% | <0.001   |       0.0476 |        0.0386 |       2/7 | 47.6–69.0%    | 39.2–55.8%    |
| div_peer   |   7 |     499 |      53.7% | ≈0.098   |       0.0486 |        0.0371 |       3/7 | 40.7–68.8%    | 33.3–63.0%    |
| corr_flip  |   7 |     612 |      53.9% | ≈0.052   |       0.0426 |        0.0309 |       2/7 | 46.8–66.7%    | 24.6–66.7%    |
| corr_shift |   7 |    5190 |      52.3% | <0.001   |       0.0425 |        0.0369 |       3/7 | 40.4–76.1%    | 39.9–58.2%    |
| str_hold   |   7 |    9575 |      56.0% | <0.001   |       0.0482 |        0.0348 |       5/7 | 45.6–70.1%    | 47.1–63.8%    |
| str_lead   |   7 |    1143 |      47.2% | ≈0.062   |       0.0330 |        0.0274 |       3/7 | —             | 26.6–68.4%    |
| str_lag    |   7 |    8365 |      52.8% | <0.001   |       0.0468 |        0.0344 |       3/7 | 38.5–67.5%    | 45.1–58.9%    |
| brk_sma    |   7 |   16914 |      52.1% | <0.001   |       0.0444 |        0.0362 |       4/7 | 48.4–58.7%    | 48.6–53.0%    |
| brk_bol    |   7 |    8348 |      50.1% | ≈0.861   |       0.0405 |        0.0389 |       2/7 | 41.7–62.8%    | 44.8–56.6%    |
| rev_lo     |   7 |   19440 |      52.3% | <0.001   |       0.0420 |        0.0350 |       5/7 | 42.7–65.0%    | 43.6–60.8%    |
| rev_hi     |   7 |   22087 |      51.2% | <0.001   |       0.0389 |        0.0338 |       4/7 | 52.4–60.9%    | 45.1–55.8%    |
| rev_nhi    |   7 |   11380 |      48.9% | ≈0.024   |       0.0379 |        0.0384 |       2/7 | 39.8–65.8%    | 44.6–54.0%    |
| rev_nlo    |   7 |    3193 |      45.4% | <0.001   |       0.0420 |        0.0335 |       0/7 | —             | 23.7–54.0%    |

**Notes on interpretation**
- pooled_DR% is n-weighted across all FYs; p_pooled is the binomial test on the pooled n.
- perm_pass = FYs where the permutation test passes at p<0.05.
- bear_DR / bull_DR ranges show min–max across FYs.
- Signs consistent across multiple FYs with perm_pass ≥ 4/7 are the most reliable.

