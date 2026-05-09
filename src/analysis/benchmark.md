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

---

## Multi-Year Benchmark (FY2018–FY2024)

Generated: 2026-05-09  
Universe: Nikkei225 representatives from prior FY's cluster  
Granularity: 1d · window=20 · valid_bars=5 · ZZ_SIZE=5 · trend_cap=30  
Permutation: 1000 iterations  

### Per-Fiscal-Year Results

#### FY2019 (2019-04-01 – 2020-03-31) · cluster=classified2018

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2020 (2020-04-01 – 2021-03-31) · cluster=classified2019

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |   104 |  32.7% | <0.001  |    0.0245 |    0.0291 |    10.490 | ≈1.000  | 104 (×1.0) |    32.7% | —             | 32.7% (<0.001) |

#### FY2021 (2021-04-01 – 2022-03-31) · cluster=classified2020

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |   289 |  47.1% | ≈0.317  |    0.0307 |    0.0301 |    12.405 | ≈0.858  | 289 (×1.0) |    47.1% | —             | 47.1% (≈0.317) |

#### FY2022 (2022-04-01 – 2023-03-31) · cluster=classified2021

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |   235 |  36.2% | <0.001  |    0.0219 |    0.0353 |    11.094 | ≈1.000  | 235 (×1.0) |    36.2% | —             | 36.2% (<0.001) |

#### FY2023 (2023-04-01 – 2024-03-31) · cluster=classified2022

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |   150 |  54.7% | ≈0.253  |    0.0409 |    0.0273 |    11.480 | ≈0.130  | 150 (×1.0) |    54.7% | —             | 54.7% (≈0.253) |

#### FY2024 (2024-04-01 – 2025-03-31) · cluster=classified2023

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |   258 |  58.1% | ≈0.009  |    0.0481 |    0.0245 |    11.624 | ≈0.004  | 258 (×1.0) |    58.1% | —             | 58.1% (≈0.009) |

#### FY2025 (2025-04-01 – 2026-03-31) · cluster=classified2024

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| str_lead   |    66 |  65.2% | ≈0.014  |    0.0583 |    0.0213 |    13.697 | ≈0.009  |  66 (×1.0) |    65.2% | —             | 65.2% (≈0.014) |

### Aggregate by Sign (FY2018–FY2024)

| Sign | FYs | total_n | pooled_DR% | p_pooled | avg_bench_flw | avg_bench_rev | perm_pass | bear_DR range | bull_DR range |
|------|-----|---------|------------|----------|--------------|---------------|-----------|---------------|---------------|
| div_gap    | — | — | — | — | — | — | — | — | — |
| div_peer   | — | — | — | — | — | — | — | — | — |
| corr_flip  | — | — | — | — | — | — | — | — | — |
| corr_shift | — | — | — | — | — | — | — | — | — |
| str_hold   | — | — | — | — | — | — | — | — | — |
| str_lead   |   7 |    1102 |      48.1% | ≈0.206   |       0.0374 |        0.0279 |       3/7 | —             | 32.7–65.2%    |
| str_lag    | — | — | — | — | — | — | — | — | — |
| brk_sma    | — | — | — | — | — | — | — | — | — |
| brk_bol    | — | — | — | — | — | — | — | — | — |
| rev_lo     | — | — | — | — | — | — | — | — | — |
| rev_hi     | — | — | — | — | — | — | — | — | — |
| rev_nhi    | — | — | — | — | — | — | — | — | — |
| rev_nlo    | — | — | — | — | — | — | — | — | — |

**Notes on interpretation**
- pooled_DR% is n-weighted across all FYs; p_pooled is the binomial test on the pooled n.
- perm_pass = FYs where the permutation test passes at p<0.05.
- bear_DR / bull_DR ranges show min–max across FYs.
- Signs consistent across multiple FYs with perm_pass ≥ 4/7 are the most reliable.


---

## Regime-Split Analysis: ADX + Ichimoku Kumo

Generated: 2026-05-09
Indicators computed on ^N225 daily bars.
ADX window=14; Ichimoku: tenkan=9, kijun=26, senkou_b=52 (cloud shift=26).
Events: multi-year runs (FY2018–FY2024, run_ids≥47).
p: two-sided binomial vs H₀=50%.  vs_all: pooled DR for that sign across all regimes.

### ADX Regime Split

ADX regime states:
- **choppy** (ADX < 20): no trending momentum — index oscillating, no directional bias
- **bull** (ADX ≥ 20, +DI > −DI): uptrend with momentum
- **bear** (ADX ≥ 20, +DI ≤ −DI): downtrend with momentum

| Sign | ADX regime | n | DR% | p | vs_all |
|------|------------|---|-----|---|--------|
| div_gap    | choppy (ADX<20)           |   1133 |  55.8% |   <0.001 |  54.8% |
| div_gap    | bull (ADX≥20,+DI>−DI)     |    939 |  53.5% |   ≈0.037 |  54.8% |
| div_gap    | bear (ADX≥20,+DI≤−DI)     |    571 |  55.0% |   ≈0.019 |  54.8% |
| div_peer   | choppy (ADX<20)           |    282 |  56.0% |   ≈0.049 |  57.2% |
| div_peer   | bull (ADX≥20,+DI>−DI)     |    132 |  57.6% |   ≈0.098 |  57.2% |
| div_peer   | bear (ADX≥20,+DI≤−DI)     |     42 |  64.3% |   ≈0.088 |  57.2% |
| corr_flip  | choppy (ADX<20)           |    314 |  44.3% |   ≈0.048 |  48.0% |
| corr_flip  | bull (ADX≥20,+DI>−DI)     |    265 |  51.3% |   ≈0.713 |  48.0% |
| corr_flip  | bear (ADX≥20,+DI≤−DI)     |     71 |  52.1% |   ≈0.813 |  48.0% |
| corr_shift | choppy (ADX<20)           |   1988 |  51.0% |   ≈0.382 |  55.8% |
| corr_shift | bull (ADX≥20,+DI>−DI)     |   2007 |  61.0% |   <0.001 |  55.8% |
| corr_shift | bear (ADX≥20,+DI≤−DI)     |    312 |  53.2% |   ≈0.282 |  55.8% |
| str_hold   | choppy (ADX<20)           |   4770 |  52.3% |   ≈0.002 |  52.7% |
| str_hold   | bull (ADX≥20,+DI>−DI)     |   1611 |  50.5% |   ≈0.727 |  52.7% |
| str_hold   | bear (ADX≥20,+DI≤−DI)     |   2325 |  55.0% |   <0.001 |  52.7% |
| str_lead   | choppy (ADX<20)           |    455 |  35.8% |   <0.001 |  48.1% |
| str_lead   | bull (ADX≥20,+DI>−DI)     |     73 |  26.0% |   <0.001 |  48.1% |
| str_lead   | bear (ADX≥20,+DI≤−DI)     |    574 |  60.6% |   <0.001 |  48.1% |
| str_lag    | choppy (ADX<20)           |   3047 |  51.3% |   ≈0.158 |  53.4% |
| str_lag    | bull (ADX≥20,+DI>−DI)     |   1282 |  47.9% |   ≈0.139 |  53.4% |
| str_lag    | bear (ADX≥20,+DI≤−DI)     |    939 |  67.7% |   <0.001 |  53.4% |
| brk_sma    | choppy (ADX<20)           |   6362 |  51.8% |   ≈0.005 |  54.3% |
| brk_sma    | bull (ADX≥20,+DI>−DI)     |   3774 |  53.8% |   <0.001 |  54.3% |
| brk_sma    | bear (ADX≥20,+DI≤−DI)     |   1852 |  63.9% |   <0.001 |  54.3% |
| brk_bol    | choppy (ADX<20)           |   3509 |  49.8% |   ≈0.839 |  52.8% |
| brk_bol    | bull (ADX≥20,+DI>−DI)     |   2834 |  55.6% |   <0.001 |  52.8% |
| brk_bol    | bear (ADX≥20,+DI≤−DI)     |    356 |  60.4% |   <0.001 |  52.8% |
| rev_lo     | choppy (ADX<20)           |   7621 |  56.6% |   <0.001 |  56.6% |
| rev_lo     | bull (ADX≥20,+DI>−DI)     |   3969 |  55.5% |   <0.001 |  56.6% |
| rev_lo     | bear (ADX≥20,+DI≤−DI)     |   1865 |  58.8% |   <0.001 |  56.6% |
| rev_hi     | choppy (ADX<20)           |   8864 |  50.2% |   ≈0.726 |  52.6% |
| rev_hi     | bull (ADX≥20,+DI>−DI)     |   6424 |  53.0% |   <0.001 |  52.6% |
| rev_hi     | bear (ADX≥20,+DI≤−DI)     |   1523 |  65.3% |   <0.001 |  52.6% |
| rev_nhi    | choppy (ADX<20)           |   4293 |  52.1% |   ≈0.006 |  53.6% |
| rev_nhi    | bull (ADX≥20,+DI>−DI)     |   4504 |  54.3% |   <0.001 |  53.6% |
| rev_nhi    | bear (ADX≥20,+DI≤−DI)     |    467 |  60.6% |   <0.001 |  53.6% |
| rev_nlo    | choppy (ADX<20)           |    900 |  36.4% |   <0.001 |  53.3% |
| rev_nlo    | bull (ADX≥20,+DI>−DI)     |      0 |      — |        — |  53.3% |
| rev_nlo    | bear (ADX≥20,+DI≤−DI)     |    955 |  69.2% |   <0.001 |  53.3% |

### Ichimoku Kumo Regime Split

Kumo state (N225 close vs cloud boundaries at each fired_at date):
- **above (+1)**: close > upper cloud boundary — bullish trend confirmed
- **inside (0)**: close within cloud — transitioning / no clear trend
- **below (−1)**: close < lower cloud boundary — bearish trend confirmed

| Sign | Kumo | n | DR% | p | vs_all |
|------|------|---|-----|---|--------|
| div_gap    | above (+1)                |   1648 |  52.2% |   ≈0.072 |  54.8% |
| div_gap    | inside (0)                |    376 |  57.7% |   ≈0.003 |  54.8% |
| div_gap    | below (−1)                |    619 |  59.8% |   <0.001 |  54.8% |
| div_peer   | above (+1)                |    292 |  52.1% |   ≈0.520 |  57.2% |
| div_peer   | inside (0)                |     72 |  72.2% |   <0.001 |  57.2% |
| div_peer   | below (−1)                |     92 |  62.0% |   ≈0.028 |  57.2% |
| corr_flip  | above (+1)                |    485 |  49.1% |   ≈0.716 |  48.0% |
| corr_flip  | inside (0)                |     67 |  44.8% |   ≈0.464 |  48.0% |
| corr_flip  | below (−1)                |     98 |  44.9% |   ≈0.363 |  48.0% |
| corr_shift | above (+1)                |   3661 |  55.4% |   <0.001 |  55.8% |
| corr_shift | inside (0)                |    302 |  60.3% |   <0.001 |  55.8% |
| corr_shift | below (−1)                |    344 |  55.8% |   ≈0.035 |  55.8% |
| str_hold   | above (+1)                |   3708 |  46.2% |   <0.001 |  52.7% |
| str_hold   | inside (0)                |   1651 |  58.2% |   <0.001 |  52.7% |
| str_hold   | below (−1)                |   3347 |  57.1% |   <0.001 |  52.7% |
| str_lead   | above (+1)                |    413 |  36.3% |   <0.001 |  48.1% |
| str_lead   | inside (0)                |    244 |  45.9% |   ≈0.224 |  48.1% |
| str_lead   | below (−1)                |    417 |  61.4% |   <0.001 |  48.1% |
| str_lag    | above (+1)                |   3113 |  48.7% |   ≈0.162 |  53.4% |
| str_lag    | inside (0)                |    886 |  62.1% |   <0.001 |  53.4% |
| str_lag    | below (−1)                |   1269 |  58.8% |   <0.001 |  53.4% |
| brk_sma    | above (+1)                |   7954 |  51.8% |   ≈0.001 |  54.3% |
| brk_sma    | inside (0)                |   1602 |  59.7% |   <0.001 |  54.3% |
| brk_sma    | below (−1)                |   2432 |  58.7% |   <0.001 |  54.3% |
| brk_bol    | above (+1)                |   5369 |  52.7% |   <0.001 |  52.8% |
| brk_bol    | inside (0)                |    655 |  52.4% |   ≈0.241 |  52.8% |
| brk_bol    | below (−1)                |    675 |  54.7% |   ≈0.017 |  52.8% |
| rev_lo     | above (+1)                |   8436 |  54.7% |   <0.001 |  56.6% |
| rev_lo     | inside (0)                |   1821 |  60.4% |   <0.001 |  56.6% |
| rev_lo     | below (−1)                |   3198 |  59.3% |   <0.001 |  56.6% |
| rev_hi     | above (+1)                |  12656 |  51.0% |   ≈0.019 |  52.6% |
| rev_hi     | inside (0)                |   1772 |  58.7% |   <0.001 |  52.6% |
| rev_hi     | below (−1)                |   2383 |  56.5% |   <0.001 |  52.6% |
| rev_nhi    | above (+1)                |   7346 |  53.3% |   <0.001 |  53.6% |
| rev_nhi    | inside (0)                |    857 |  56.2% |   <0.001 |  53.6% |
| rev_nhi    | below (−1)                |   1061 |  54.0% |   ≈0.010 |  53.6% |
| rev_nlo    | above (+1)                |    717 |  46.0% |   ≈0.036 |  53.3% |
| rev_nlo    | inside (0)                |    486 |  47.3% |   ≈0.257 |  53.3% |
| rev_nlo    | below (−1)                |    652 |  65.8% |   <0.001 |  53.3% |


---

## FY2025 Out-of-Sample Backtest

Generated: 2026-05-09  
Training: FY2018–FY2024 regime ranking (Ichimoku Kumo × ADX veto)  
Test: FY2025 · classified2024 · 2025-04-01 – 2026-03-31  
Ranking cells: 39 (sign × kumo_state, min_n=30)  

### Regime Cell Detail (sign × kumo_state)

Kumo states: ▲above cloud (+1) · ~inside (0) · ▼below cloud (−1)  
Δ DR = test cell DR − sign-level baseline DR (all events for that sign).

| Sign | kumo | train_bench_flw | train_DR | train_n | test_n | test_DR | Δ DR |
|------|------|-----------------|----------|---------|--------|---------|------|
| div_peer   | ~inside | 0.0654 | 67.0% |      88 |      0 |       — | —      |
| div_peer   | ▼below  | 0.0584 | 57.6% |     172 |      0 |       — | —      |
| rev_nlo    | ▼below  | 0.0574 | 62.6% |    1170 |      0 |       — | —      |
| corr_shift | ~inside | 0.0556 | 58.6% |     413 |      0 |       — | —      |
| str_lag    | ▼below  | 0.0548 | 56.8% |    2354 |      0 |       — | —      |
| str_hold   | ~inside | 0.0520 | 59.8% |    1616 |      0 |       — | —      |
| div_peer   | ▲above  | 0.0509 | 52.7% |     372 |      0 |       — | —      |
| div_gap    | ▼below  | 0.0499 | 56.9% |    1109 |      0 |       — | —      |
| rev_lo     | ▼below  | 0.0497 | 57.6% |    5506 |      0 |       — | —      |
| div_gap    | ▲above  | 0.0492 | 56.7% |    1241 |      0 |       — | —      |
| str_hold   | ▼below  | 0.0488 | 56.9% |    4936 |      0 |       — | —      |
| corr_flip  | ▼below  | 0.0478 | 47.4% |     116 |      0 |       — | —      |
| brk_sma    | ▼below  | 0.0478 | 56.8% |    3928 |      0 |       — | —      |
| brk_sma    | ~inside | 0.0477 | 55.8% |    2241 |      0 |       — | —      |
| rev_nlo    | ~inside | 0.0476 | 42.8% |     615 |      0 |       — | —      |
| corr_shift | ▲above  | 0.0474 | 53.5% |    3143 |      0 |       — | —      |
| corr_shift | ▼below  | 0.0464 | 56.8% |     792 |      0 |       — | —      |
| rev_nhi    | ~inside | 0.0464 | 56.2% |     979 |      0 |       — | —      |
| div_gap    | ~inside | 0.0463 | 53.8% |     407 |      0 |       — | —      |
| brk_bol    | ~inside | 0.0462 | 52.8% |     809 |      0 |       — | —      |
| rev_lo     | ~inside | 0.0461 | 57.5% |    2521 |      0 |       — | —      |
| rev_lo     | ▲above  | 0.0455 | 54.1% |    8230 |      0 |       — | —      |
| rev_nhi    | ▲above  | 0.0444 | 52.0% |    6994 |      0 |       — | —      |
| str_lead   | ▼below  | 0.0441 | 61.4% |     417 |      0 |       — | —      |
| rev_hi     | ~inside | 0.0438 | 56.5% |    2289 |      0 |       — | —      |
| rev_hi     | ▼below  | 0.0437 | 55.9% |    4334 |      0 |       — | —      |
| brk_bol    | ▲above  | 0.0436 | 50.9% |    5289 |      0 |       — | —      |
| str_lag    | ▲above  | 0.0432 | 50.5% |    3687 |      0 |       — | —      |
| brk_sma    | ▲above  | 0.0431 | 50.9% |    8227 |      0 |       — | —      |
| str_lag    | ~inside | 0.0425 | 58.3% |    1469 |      0 |       — | —      |
| rev_nhi    | ▼below  | 0.0406 | 50.6% |    1980 |      0 |       — | —      |
| str_hold   | ▲above  | 0.0406 | 51.0% |    2909 |      0 |       — | —      |
| brk_bol    | ▼below  | 0.0404 | 48.5% |    1232 |      0 |       — | —      |
| corr_flip  | ▲above  | 0.0402 | 53.5% |     303 |      0 |       — | —      |
| rev_hi     | ▲above  | 0.0390 | 49.6% |   12223 |      0 |       — | —      |
| str_lead   | ~inside | 0.0358 | 45.9% |     244 |      0 |       — | —      |
| corr_flip  | ~inside | 0.0345 | 46.1% |      76 |      0 |       — | —      |
| rev_nlo    | ▲above  | 0.0287 | 36.8% |     873 |      0 |       — | —      |
| str_lead   | ▲above  | 0.0196 | 30.8% |     347 |     66 |   65.2% | +0.0%  |

### Sign Summary: All Events vs Regime-Accepted Events

Regime-accepted = (sign, kumo) cell present in training ranking AND ADX veto passes.  
regime_n% = fraction of total events retained by the regime filter.

| Sign | total_n | total_DR | regime_n | regime_DR | Δ DR | regime_n% |
|------|---------|----------|----------|-----------|------|-----------|
| div_gap    | 0 | — | — | — | — | — |
| div_peer   | 0 | — | — | — | — | — |
| corr_flip  | 0 | — | — | — | — | — |
| corr_shift | 0 | — | — | — | — | — |
| str_hold   | 0 | — | — | — | — | — |
| str_lead   |      66 |    65.2% |        0 |         — |      — |        0% |
| str_lag    | 0 | — | — | — | — | — |
| brk_sma    | 0 | — | — | — | — | — |
| brk_bol    | 0 | — | — | — | — | — |
| rev_lo     | 0 | — | — | — | — | — |
| rev_hi     | 0 | — | — | — | — | — |
| rev_nhi    | 0 | — | — | — | — | — |
| rev_nlo    | 0 | — | — | — | — | — |

**Interpretation**: Positive Δ DR means the Kumo+ADX regime filter selected
events with better follow-through outcomes in the out-of-sample year.
Low regime_n% indicates the filter is aggressive; verify test_n is large enough.

