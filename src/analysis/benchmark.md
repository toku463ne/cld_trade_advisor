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


---


---

---

## Multi-Year Benchmark (FY2018–FY2024)

Generated: 2026-05-17  
Universe: Nikkei225 representatives from prior FY's cluster  
Granularity: 1d · window=20 · valid_bars=5 · ZZ_SIZE=5 · trend_cap=30  
Permutation: 1000 iterations  

### Per-Fiscal-Year Results

#### FY2019 (2019-04-01 – 2020-03-31) · cluster=classified2018

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   511 |  36.4% | <0.001  |    0.0299 |    0.0958 |    12.699 | ≈1.000  | 448 (×1.1) |    35.7% | —             | —             |

#### FY2020 (2020-04-01 – 2021-03-31) · cluster=classified2019

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   317 |  54.3% | ≈0.129  |    0.0544 |    0.0319 |    12.644 | ≈0.052  | 274 (×1.2) |    56.2% | 59.7% (≈0.018) | 47.2% (≈0.475) |

#### FY2021 (2021-04-01 – 2022-03-31) · cluster=classified2020

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   666 |  53.0% | ≈0.121  |    0.0437 |    0.0315 |    12.399 | ≈0.062  | 586 (×1.1) |    52.0% | 51.9% (≈0.483) | 54.2% (≈0.133) |

#### FY2022 (2022-04-01 – 2023-03-31) · cluster=classified2021

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   541 |  59.9% | <0.001  |    0.0413 |    0.0183 |    12.227 | <0.001  | 472 (×1.1) |    60.4% | 54.6% (≈0.138) | 64.9% (<0.001) |

#### FY2023 (2023-04-01 – 2024-03-31) · cluster=classified2022

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   331 |  50.2% | ≈0.956  |    0.0452 |    0.0303 |    12.710 | ≈0.502  | 289 (×1.1) |    49.1% | 49.6% (≈0.927) | 50.5% (≈0.891) |

#### FY2024 (2024-04-01 – 2025-03-31) · cluster=classified2023

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   672 |  55.2% | ≈0.007  |    0.0482 |    0.0326 |    12.796 | ≈0.002  | 597 (×1.1) |    56.4% | 57.7% (≈0.003) | 52.3% (≈0.425) |

#### FY2025 (2025-04-01 – 2026-03-31) · cluster=classified2024

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| brk_lo_sideway |   272 |  51.5% | ≈0.628  |    0.0493 |    0.0308 |    12.107 | ≈0.339  | 241 (×1.1) |    53.1% | 52.5% (≈0.584) | 50.7% (≈0.871) |

### Aggregate by Sign (FY2018–FY2024)

| Sign | FYs | total_n | pooled_DR% | p_pooled | avg_bench_flw | avg_bench_rev | perm_pass | bear_DR range | bull_DR range |
|------|-----|---------|------------|----------|--------------|---------------|-----------|---------------|---------------|
| div_gap    | — | — | — | — | — | — | — | — | — |
| div_peer   | — | — | — | — | — | — | — | — | — |
| corr_flip  | — | — | — | — | — | — | — | — | — |
| corr_shift | — | — | — | — | — | — | — | — | — |
| str_hold   | — | — | — | — | — | — | — | — | — |
| str_lead   | — | — | — | — | — | — | — | — | — |
| str_lag    | — | — | — | — | — | — | — | — | — |
| brk_sma    | — | — | — | — | — | — | — | — | — |
| brk_bol    | — | — | — | — | — | — | — | — | — |
| brk_hi_sideway | — | — | — | — | — | — | — | — | — |
| brk_lo_sideway |   7 |    3310 |      51.7% | ≈0.048   |       0.0446 |        0.0387 |       2/7 | 49.6–59.7%    | 47.2–64.9%    |
| rev_lo     | — | — | — | — | — | — | — | — | — |
| rev_hi     | — | — | — | — | — | — | — | — | — |
| rev_nhi    | — | — | — | — | — | — | — | — | — |
| rev_nlo    | — | — | — | — | — | — | — | — | — |
| rev_nhold  | — | — | — | — | — | — | — | — | — |

**Notes on interpretation**
- pooled_DR% is n-weighted across all FYs; p_pooled is the binomial test on the pooled n.
- perm_pass = FYs where the permutation test passes at p<0.05.
- bear_DR / bull_DR ranges show min–max across FYs.
- Signs consistent across multiple FYs with perm_pass ≥ 4/7 are the most reliable.


---

## Regime-Split Analysis: ADX + Ichimoku Kumo

Generated: 2026-05-17
Indicators computed on ^N225 daily bars.
ADX window=14; Ichimoku: tenkan=9, kijun=26, senkou_b=52 (cloud shift=26).
Events: multi-year runs (FY2018–FY2024, run_ids≥47).
p: two-sided binomial vs H₀=50%.  vs_all: pooled DR for that sign across all regimes.
mag_flw / mag_rev: mean trend_magnitude on follow / reverse events in this regime.
EV = DR × mag_flw − (1−DR) × mag_rev — the regime-conditional expected return per trade.
mag_flw / mag_rev / EV are masked ("—") unless the cell passes both:
  - p < 0.05  (the direction rate is not just noise)
  - min(n_flw, n_rev) ≥ 10  (each magnitude average is reliable)

### ADX Regime Split

ADX regime states:
- **choppy** (ADX < 20): no trending momentum — index oscillating, no directional bias
- **bull** (ADX ≥ 20, +DI > −DI): uptrend with momentum
- **bear** (ADX ≥ 20, +DI ≤ −DI): downtrend with momentum

| Sign | ADX regime | n | DR% | p | mag_flw | mag_rev | EV | vs_all |
|------|---|---|-----|---|---------|---------|----|--------|
| div_gap    | choppy (ADX<20)           |   1645 |  55.5% |   <0.001 |  0.0844 |  0.0673 |  +0.0169 |  54.9% |
| div_gap    | bull (ADX≥20,+DI>−DI)     |   1063 |  53.1% |   ≈0.050 |  0.1032 |  0.0754 |  +0.0194 |  54.9% |
| div_gap    | bear (ADX≥20,+DI≤−DI)     |   1015 |  55.9% |   <0.001 |  0.0916 |  0.0651 |  +0.0224 |  54.9% |
| div_peer   | choppy (ADX<20)           |    401 |  55.6% |   ≈0.028 |  0.0845 |  0.0732 |  +0.0145 |  55.1% |
| div_peer   | bull (ADX≥20,+DI>−DI)     |    170 |  57.1% |   ≈0.077 |       — |       — |        — |  55.1% |
| div_peer   | bear (ADX≥20,+DI≤−DI)     |     99 |  57.6% |   ≈0.159 |       — |       — |        — |  55.1% |
| corr_flip  | choppy (ADX<20)           |    362 |  45.3% |   ≈0.083 |       — |       — |        — |  49.1% |
| corr_flip  | bull (ADX≥20,+DI>−DI)     |    297 |  53.2% |   ≈0.296 |       — |       — |        — |  49.1% |
| corr_flip  | bear (ADX≥20,+DI≤−DI)     |     90 |  51.1% |   ≈0.916 |       — |       — |        — |  49.1% |
| corr_shift | choppy (ADX<20)           |    556 |  53.2% |   ≈0.138 |       — |       — |        — |  50.8% |
| corr_shift | bull (ADX≥20,+DI>−DI)     |    473 |  48.6% |   ≈0.581 |       — |       — |        — |  50.8% |
| corr_shift | bear (ADX≥20,+DI≤−DI)     |    126 |  48.4% |   ≈0.789 |       — |       — |        — |  50.8% |
| str_hold   | choppy (ADX<20)           |   5834 |  52.7% |   <0.001 |  0.0826 |  0.0592 |  +0.0156 |  54.8% |
| str_hold   | bull (ADX≥20,+DI>−DI)     |   2042 |  53.9% |   <0.001 |  0.0999 |  0.0642 |  +0.0242 |  54.8% |
| str_hold   | bear (ADX≥20,+DI≤−DI)     |   3712 |  58.6% |   <0.001 |  0.0868 |  0.0812 |  +0.0173 |  54.8% |
| str_lead   | choppy (ADX<20)           |    518 |  35.3% |   <0.001 |  0.0700 |  0.0591 |  -0.0135 |  47.8% |
| str_lead   | bull (ADX≥20,+DI>−DI)     |     76 |  25.0% |   <0.001 |  0.0909 |  0.0469 |  -0.0125 |  47.8% |
| str_lead   | bear (ADX≥20,+DI≤−DI)     |    653 |  60.3% |   <0.001 |  0.0745 |  0.0544 |  +0.0234 |  47.8% |
| str_lag    | choppy (ADX<20)           |   1531 |  53.3% |   ≈0.011 |  0.0794 |  0.0681 |  +0.0105 |  50.7% |
| str_lag    | bull (ADX≥20,+DI>−DI)     |    879 |  49.5% |   ≈0.787 |       — |       — |        — |  50.7% |
| str_lag    | bear (ADX≥20,+DI≤−DI)     |    371 |  42.6% |   ≈0.005 |  0.0797 |  0.0777 |  -0.0107 |  50.7% |
| brk_sma    | choppy (ADX<20)           |    723 |  54.8% |   ≈0.011 |  0.0950 |  0.0737 |  +0.0187 |  52.6% |
| brk_sma    | bull (ADX≥20,+DI>−DI)     |    311 |  51.1% |   ≈0.734 |       — |       — |        — |  52.6% |
| brk_sma    | bear (ADX≥20,+DI≤−DI)     |    203 |  63.1% |   <0.001 |  0.0988 |  0.0803 |  +0.0327 |  52.6% |
| brk_bol    | choppy (ADX<20)           |   1592 |  51.6% |   ≈0.201 |       — |       — |        — |  52.8% |
| brk_bol    | bull (ADX≥20,+DI>−DI)     |   1188 |  55.9% |   <0.001 |  0.1054 |  0.0719 |  +0.0272 |  52.8% |
| brk_bol    | bear (ADX≥20,+DI≤−DI)     |    281 |  56.6% |   ≈0.032 |  0.0919 |  0.0800 |  +0.0173 |  52.8% |
| rev_lo     | choppy (ADX<20)           |   4224 |  52.9% |   <0.001 |  0.0823 |  0.0664 |  +0.0123 |  52.4% |
| rev_lo     | bull (ADX≥20,+DI>−DI)     |   1870 |  54.2% |   <0.001 |  0.0897 |  0.0547 |  +0.0235 |  52.4% |
| rev_lo     | bear (ADX≥20,+DI≤−DI)     |   1414 |  58.7% |   <0.001 |  0.0803 |  0.0677 |  +0.0192 |  52.4% |
| rev_hi     | choppy (ADX<20)           |   4522 |  49.4% |   ≈0.466 |       — |       — |        — |  51.5% |
| rev_hi     | bull (ADX≥20,+DI>−DI)     |   2752 |  51.4% |   ≈0.153 |       — |       — |        — |  51.5% |
| rev_hi     | bear (ADX≥20,+DI≤−DI)     |   1349 |  60.1% |   <0.001 |  0.0803 |  0.0691 |  +0.0207 |  51.5% |
| rev_nhi    | choppy (ADX<20)           |   6587 |  51.4% |   ≈0.027 |  0.0834 |  0.0756 |  +0.0061 |  51.8% |
| rev_nhi    | bull (ADX≥20,+DI>−DI)     |   5843 |  54.0% |   <0.001 |  0.0994 |  0.0680 |  +0.0224 |  51.8% |
| rev_nhi    | bear (ADX≥20,+DI≤−DI)     |   1130 |  53.9% |   ≈0.010 |  0.0849 |  0.0734 |  +0.0119 |  51.8% |
| rev_nlo    | choppy (ADX<20)           |   1249 |  39.5% |   <0.001 |  0.0847 |  0.0674 |  -0.0073 |  48.8% |
| rev_nlo    | bull (ADX≥20,+DI>−DI)     |    312 |  22.1% |   <0.001 |  0.0583 |  0.0693 |  -0.0411 |  48.8% |
| rev_nlo    | bear (ADX≥20,+DI≤−DI)     |   1442 |  62.6% |   <0.001 |  0.0957 |  0.0586 |  +0.0380 |  48.8% |
| rev_nhold  | choppy (ADX<20)           |      0 |      — |        — |       — |       — |        — |  43.1% |
| rev_nhold  | bull (ADX≥20,+DI>−DI)     |      0 |      — |        — |       — |       — |        — |  43.1% |
| rev_nhold  | bear (ADX≥20,+DI≤−DI)     |    153 |  43.1% |   ≈0.106 |       — |       — |        — |  43.1% |

### Ichimoku Kumo Regime Split

Kumo state (N225 close vs cloud boundaries at each fired_at date):
- **above (+1)**: close > upper cloud boundary — bullish trend confirmed
- **inside (0)**: close within cloud — transitioning / no clear trend
- **below (−1)**: close < lower cloud boundary — bearish trend confirmed

| Sign | Kumo | n | DR% | p | mag_flw | mag_rev | EV | vs_all |
|------|---|---|-----|---|---------|---------|----|--------|
| div_gap    | above (+1)                |   1994 |  53.7% |   <0.001 |  0.0920 |  0.0703 |  +0.0169 |  54.9% |
| div_gap    | inside (0)                |    457 |  55.4% |   ≈0.025 |  0.0840 |  0.0634 |  +0.0182 |  54.9% |
| div_gap    | below (−1)                |   1136 |  57.2% |   <0.001 |  0.0904 |  0.0695 |  +0.0220 |  54.9% |
| div_peer   | above (+1)                |    392 |  53.8% |   ≈0.143 |       — |       — |        — |  55.1% |
| div_peer   | inside (0)                |     89 |  66.3% |   ≈0.003 |  0.0976 |  0.0619 |  +0.0438 |  55.1% |
| div_peer   | below (−1)                |    173 |  57.2% |   ≈0.068 |       — |       — |        — |  55.1% |
| corr_flip  | above (+1)                |    541 |  50.1% |   ≈1.000 |       — |       — |        — |  49.1% |
| corr_flip  | inside (0)                |     80 |  46.2% |   ≈0.576 |       — |       — |        — |  49.1% |
| corr_flip  | below (−1)                |    116 |  47.4% |   ≈0.643 |       — |       — |        — |  49.1% |
| corr_shift | above (+1)                |    861 |  49.9% |   ≈1.000 |       — |       — |        — |  50.8% |
| corr_shift | inside (0)                |    120 |  59.2% |   ≈0.055 |       — |       — |        — |  50.8% |
| corr_shift | below (−1)                |    169 |  49.1% |   ≈0.878 |       — |       — |        — |  50.8% |
| str_hold   | above (+1)                |   4461 |  50.2% |   ≈0.788 |       — |       — |        — |  54.8% |
| str_hold   | inside (0)                |   2055 |  59.6% |   <0.001 |  0.0887 |  0.0586 |  +0.0291 |  54.8% |
| str_hold   | below (−1)                |   4955 |  57.0% |   <0.001 |  0.0858 |  0.0604 |  +0.0230 |  54.8% |
| str_lead   | above (+1)                |    467 |  34.9% |   <0.001 |  0.0724 |  0.0586 |  -0.0128 |  47.8% |
| str_lead   | inside (0)                |    273 |  44.0% |   ≈0.053 |       — |       — |        — |  47.8% |
| str_lead   | below (−1)                |    479 |  62.8% |   <0.001 |  0.0712 |  0.0577 |  +0.0233 |  47.8% |
| str_lag    | above (+1)                |   1950 |  49.7% |   ≈0.803 |       — |       — |        — |  50.7% |
| str_lag    | inside (0)                |    360 |  53.1% |   ≈0.268 |       — |       — |        — |  50.7% |
| str_lag    | below (−1)                |    389 |  49.1% |   ≈0.761 |       — |       — |        — |  50.7% |
| brk_sma    | above (+1)                |    774 |  54.9% |   ≈0.007 |  0.1014 |  0.0738 |  +0.0224 |  52.6% |
| brk_sma    | inside (0)                |    155 |  49.7% |   ≈1.000 |       — |       — |        — |  52.6% |
| brk_sma    | below (−1)                |    288 |  59.7% |   ≈0.001 |  0.0960 |  0.0692 |  +0.0295 |  52.6% |
| brk_bol    | above (+1)                |   2199 |  53.8% |   <0.001 |  0.0984 |  0.0763 |  +0.0177 |  52.8% |
| brk_bol    | inside (0)                |    287 |  51.9% |   ≈0.555 |       — |       — |        — |  52.8% |
| brk_bol    | below (−1)                |    469 |  52.5% |   ≈0.310 |       — |       — |        — |  52.8% |
| rev_lo     | above (+1)                |   4316 |  53.1% |   <0.001 |  0.0844 |  0.0620 |  +0.0157 |  52.4% |
| rev_lo     | inside (0)                |    990 |  58.2% |   <0.001 |  0.0801 |  0.0615 |  +0.0209 |  52.4% |
| rev_lo     | below (−1)                |   2019 |  55.7% |   <0.001 |  0.0838 |  0.0665 |  +0.0172 |  52.4% |
| rev_hi     | above (+1)                |   5725 |  49.7% |   ≈0.672 |       — |       — |        — |  51.5% |
| rev_hi     | inside (0)                |    867 |  57.8% |   <0.001 |  0.0769 |  0.0596 |  +0.0193 |  51.5% |
| rev_hi     | below (−1)                |   1813 |  55.6% |   <0.001 |  0.0770 |  0.0655 |  +0.0137 |  51.5% |
| rev_nhi    | above (+1)                |   9679 |  53.4% |   <0.001 |  0.0918 |  0.0711 |  +0.0159 |  51.8% |
| rev_nhi    | inside (0)                |   1103 |  55.2% |   <0.001 |  0.0854 |  0.0659 |  +0.0176 |  51.8% |
| rev_nhi    | below (−1)                |   2067 |  50.1% |   ≈0.930 |       — |       — |        — |  51.8% |
| rev_nlo    | above (+1)                |   1026 |  40.9% |   <0.001 |  0.0778 |  0.0631 |  -0.0054 |  48.8% |
| rev_nlo    | inside (0)                |    615 |  42.8% |   <0.001 |  0.1113 |  0.0632 |  +0.0114 |  48.8% |
| rev_nlo    | below (−1)                |   1170 |  62.6% |   <0.001 |  0.0918 |  0.0611 |  +0.0345 |  48.8% |
| rev_nhold  | above (+1)                |      0 |      — |        — |       — |       — |        — |  43.1% |
| rev_nhold  | inside (0)                |     63 |  14.3% |   <0.001 |       — |       — |        — |  43.1% |
| rev_nhold  | below (−1)                |     90 |  63.3% |   ≈0.015 |  0.0719 |  0.0879 |  +0.0133 |  43.1% |


---

## Score Calibration: Does sign_score Predict Outcomes?

Generated: 2026-05-17  
Events: multi-year runs (FY2018–FY2024, run_ids ≥ 47).  
signed_return = trend_direction × trend_magnitude (+ when sign follows, − when reverses).  
ρ: Spearman correlation between sign_score and signed_return.  
Per-quartile rows with n < 50 are shown but their stats are masked.  

### Summary

| Sign | n | score range | ρ | p(ρ) | verdict |
|------|---|-------------|---|------|---------|
| div_gap    |   3723 | 0.063–0.950 |  +0.052 |  ≈0.001 | informative |
| div_peer   |    740 | 0.171–1.000 |  -0.038 |  ≈0.298 | noise (p≥0.05) |
| corr_flip  |    749 | 0.090–0.859 |  -0.023 |  ≈0.537 | noise (p≥0.05) |
| corr_shift |   1155 | 0.502–1.000 |  +0.030 |  ≈0.305 | noise (p≥0.05) |
| str_hold   |  11588 | 0.426–1.000 |  -0.003 |  ≈0.767 | noise (p≥0.05) |
| str_lead   |   1247 | 0.354–0.794 |  +0.024 |  ≈0.404 | noise (p≥0.05) |
| str_lag    |   2781 | 0.084–0.902 |  +0.011 |  ≈0.561 | noise (p≥0.05) |
| brk_sma    |   1412 | 0.002–1.000 |  +0.034 |  ≈0.197 | noise (p≥0.05) |
| brk_bol    |   3508 | 0.500–1.000 |  +0.036 |  ≈0.033 | informative |
| rev_lo     |   8787 | 0.000–1.000 |  +0.004 |  ≈0.674 | noise (p≥0.05) |
| rev_hi     |  10047 | 0.000–1.000 |  -0.010 |  ≈0.310 | noise (p≥0.05) |
| rev_nhi    |  15568 | 1.000–1.000 |       — |       — | n/a (constant) |
| rev_nlo    |   3003 | 0.251–0.945 |  +0.018 |  ≈0.329 | noise (p≥0.05) |
| rev_nhold  |    153 | 0.224–1.000 |  -0.054 |  ≈0.511 | noise (p≥0.05) |

### Quartile Breakdown

DR = direction-rate; mag_flw / mag_rev = mean trend_magnitude when the trend follows / reverses;
EV = DR × mag_flw − (1−DR) × mag_rev (expected return per trade in that quartile).
If the score is informative we expect EV(Q4) ≫ EV(Q1).

| Sign | Quartile | score range | n | DR% | mag_flw | mag_rev | EV |
|------|----------|-------------|---|-----|---------|---------|----|
| **div_gap**    | Q1 | 0.063–0.124   |   931 | 50.8% |  0.0804 |  0.0692 |  +0.0068 |
|                | Q2 | 0.124–0.180   |   931 | 57.0% |  0.0914 |  0.0632 |  +0.0249 |
|                | Q3 | 0.180–0.283   |   930 | 54.4% |  0.0911 |  0.0710 |  +0.0172 |
|                | Q4 | 0.283–0.950   |   931 | 57.4% |  0.1021 |  0.0729 |  +0.0275 |
| **div_peer**   | Q1 | 0.171–0.327   |   185 | 56.2% |  0.0956 |  0.0653 |  +0.0251 |
|                | Q2 | 0.328–0.455   |   185 | 60.5% |  0.0925 |  0.0691 |  +0.0287 |
|                | Q3 | 0.460–0.667   |   186 | 53.8% |  0.0962 |  0.0805 |  +0.0145 |
|                | Q4 | 0.667–1.000   |   184 | 50.0% |  0.1069 |  0.0894 |  +0.0087 |
| **corr_flip**  | Q1 | 0.090–0.199   |   188 | 51.1% |  0.0861 |  0.0602 |  +0.0145 |
|                | Q2 | 0.199–0.275   |   187 | 50.8% |  0.0759 |  0.0687 |  +0.0048 |
|                | Q3 | 0.276–0.430   |   187 | 47.1% |  0.0734 |  0.0716 |  -0.0034 |
|                | Q4 | 0.430–0.859   |   187 | 47.6% |  0.0905 |  0.0624 |  +0.0104 |
| **corr_shift** | Q1 | 0.502–0.701   |   289 | 48.4% |  0.0877 |  0.0723 |  +0.0052 |
|                | Q2 | 0.702–0.858   |   289 | 49.1% |  0.0919 |  0.0684 |  +0.0103 |
|                | Q3 | 0.858–0.967   |   288 | 53.8% |  0.0898 |  0.0796 |  +0.0116 |
|                | Q4 | 0.967–1.000   |   289 | 51.9% |  0.0764 |  0.0788 |  +0.0018 |
| **str_hold**   | Q1 | 0.426–0.673   |  2897 | 54.5% |  0.0820 |  0.0551 |  +0.0197 |
|                | Q2 | 0.673–0.840   |  4063 | 54.2% |  0.0934 |  0.0686 |  +0.0192 |
|                | Q3 | 0.840–0.920   |  3292 | 55.5% |  0.0876 |  0.0734 |  +0.0160 |
|                | Q4 | 0.920–1.000   |  1336 | 55.7% |  0.0776 |  0.0685 |  +0.0128 |
| **str_lead**   | Q1 | 0.354–0.448   |   312 | 48.7% |  0.0711 |  0.0585 |  +0.0047 |
|                | Q2 | 0.449–0.522   |   312 | 45.8% |  0.0766 |  0.0553 |  +0.0052 |
|                | Q3 | 0.522–0.597   |   311 | 50.2% |  0.0687 |  0.0577 |  +0.0057 |
|                | Q4 | 0.597–0.794   |   312 | 46.5% |  0.0788 |  0.0533 |  +0.0081 |
| **str_lag**    | Q1 | 0.084–0.397   |   705 | 47.7% |  0.0918 |  0.0714 |  +0.0064 |
|                | Q2 | 0.401–0.561   |   693 | 49.8% |  0.0997 |  0.0661 |  +0.0164 |
|                | Q3 | 0.562–0.692   |   724 | 52.9% |  0.0781 |  0.0697 |  +0.0085 |
|                | Q4 | 0.693–0.902   |   659 | 52.4% |  0.0857 |  0.0746 |  +0.0093 |
| **brk_bol**    | Q1 | 0.500–0.619   |   877 | 51.4% |  0.0939 |  0.0812 |  +0.0088 |
|                | Q2 | 0.619–0.751   |   877 | 52.2% |  0.0986 |  0.0788 |  +0.0139 |
|                | Q3 | 0.752–0.938   |   877 | 52.2% |  0.0943 |  0.0742 |  +0.0138 |
|                | Q4 | 0.938–1.000   |   877 | 55.4% |  0.0943 |  0.0718 |  +0.0202 |
| **rev_lo**     | Q1 | 0.000–0.244   |  2197 | 52.3% |  0.0797 |  0.0707 |  +0.0081 |
|                | Q2 | 0.244–0.489   |  2197 | 51.6% |  0.0826 |  0.0709 |  +0.0083 |
|                | Q3 | 0.489–0.740   |  2196 | 53.1% |  0.0818 |  0.0741 |  +0.0087 |
|                | Q4 | 0.740–1.000   |  2197 | 52.5% |  0.0829 |  0.0716 |  +0.0096 |
| **rev_hi**     | Q1 | 0.000–0.244   |  2512 | 52.5% |  0.0793 |  0.0722 |  +0.0073 |
|                | Q2 | 0.244–0.501   |  2512 | 52.0% |  0.0812 |  0.0705 |  +0.0083 |
|                | Q3 | 0.501–0.747   |  2511 | 50.3% |  0.0801 |  0.0671 |  +0.0069 |
|                | Q4 | 0.747–1.000   |  2512 | 51.2% |  0.0787 |  0.0683 |  +0.0069 |
| **rev_nlo**    | Q1 | 0.251–0.376   |   751 | 50.2% |  0.0751 |  0.0596 |  +0.0080 |
|                | Q2 | 0.376–0.474   |   751 | 48.3% |  0.0832 |  0.0633 |  +0.0075 |
|                | Q3 | 0.474–0.608   |   750 | 49.1% |  0.0957 |  0.0675 |  +0.0126 |
|                | Q4 | 0.608–0.945   |   751 | 47.5% |  0.1076 |  0.0679 |  +0.0155 |
| **rev_nhold**  | Q1 | 0.224–0.559   |    39 | — | — | — | — |
|                | Q2 | 0.579–0.805   |    68 | 33.8% |  0.0699 |  0.0511 |  -0.0102 |
|                | Q3 | 0.816–0.816   |    40 | — | — | — | — |
|                | Q4 | 0.951–1.000   |     6 | — | — | — | — |

---

## Sign Score Calibration by Regime

Generated: 2026-05-17  
Events: multi-year runs (FY2018–FY2024, run_ids ≥ 47).  
corr_mode tagged per event via 20-bar returns-corr to ^N225 (high ≥ 0.6, low ≤ 0.3, mid in between).  
Only (sign, corr_mode) cells with n ≥ 200 are tabulated.  
q = Benjamini–Hochberg FDR across listed cells.  
ρ_loo_min / ρ_loo_max: ρ recomputed leaving one FY out, worst / best.  
flips: FYs where leave-one-out ρ has the opposite sign vs full-sample ρ.  
monotone: quartile EV ordering (asc = Q1<Q2<Q3<Q4, desc = reverse, no = neither).  
Verdict gates: strong = n≥1000 ∧ |ρ|≥0.05 ∧ p<0.05 ∧ q<0.05 ∧ monotone ∧ 0 flips;  
moderate = n≥200 ∧ |ρ|≥0.10 ∧ p<0.01 ∧ q<0.05 ∧ monotone ∧ 0 flips.  

### Per-cell summary

| Sign | corr | n | ρ | p | q | ρ_loo_min | ρ_loo_max | flips | mono | verdict |
|------|------|---|---|---|---|-----------|-----------|-------|------|---------|
| **brk_bol** | high |   902 | +0.028 |  ≈0.400 | 0.724 | +0.006 | +0.048 | 0 | no | noise |
|            | mid  |  1269 | +0.011 |  ≈0.691 | 0.896 | -0.008 | +0.037 | 2 | no | noise |
|            | low  |   814 | +0.080 |  ≈0.022 | 0.138 | +0.063 | +0.095 | 0 | no | borderline |
| **brk_hi_sideway** | high |  1530 | +0.028 |  ≈0.282 | 0.630 | +0.015 | +0.042 | 0 | no | noise |
|            | mid  |  1791 | -0.021 |  ≈0.364 | 0.692 | -0.040 | -0.001 | 0 | no | noise |
|            | low  |   980 | +0.092 |  ≈0.004 | 0.050 | +0.066 | +0.130 | 0 | asc | borderline |
| **brk_lo_sideway** | high |  1289 | +0.172 |  <0.001 | 0.000 | +0.116 | +0.210 | 0 | asc | **strong** |
|            | mid  |  1028 | +0.021 |  ≈0.496 | 0.805 | -0.008 | +0.049 | 1 | no | noise |
|            | low  |   475 | -0.055 |  ≈0.233 | 0.623 | -0.096 | -0.030 | 0 | no | noise |
| **brk_sma** | high |   521 | -0.001 |  ≈0.990 | 0.990 | -0.031 | +0.031 | 4 | no | noise |
|            | mid  |   476 | +0.082 |  ≈0.073 | 0.349 | +0.058 | +0.113 | 0 | no | noise |
|            | low  |   230 | +0.027 |  ≈0.687 | 0.896 | +0.006 | +0.039 | 0 | no | noise |
| **corr_flip** | low  |   695 | -0.063 |  ≈0.099 | 0.378 | -0.080 | -0.042 | 0 | no | noise |
| **corr_shift** | mid  |   268 | +0.011 |  ≈0.861 | 0.962 | -0.048 | +0.094 | 2 | no | noise |
|            | low  |   877 | +0.031 |  ≈0.356 | 0.692 | -0.007 | +0.074 | 1 | no | noise |
| **div_gap** | high |   811 | +0.154 |  <0.001 | 0.000 | +0.116 | +0.190 | 0 | asc | moderate |
|            | mid  |  1567 | +0.060 |  ≈0.017 | 0.138 | +0.041 | +0.072 | 0 | no | borderline |
|            | low  |  1310 | -0.006 |  ≈0.832 | 0.962 | -0.022 | +0.007 | 3 | no | noise |
| **div_peer** | high |   223 | +0.003 |  ≈0.962 | 0.989 | -0.036 | +0.047 | 4 | no | noise |
|            | mid  |   279 | -0.074 |  ≈0.220 | 0.623 | -0.088 | -0.041 | 0 | no | noise |
| **rev_hi** | high |  3215 | -0.024 |  ≈0.177 | 0.613 | -0.030 | -0.018 | 0 | no | noise |
|            | mid  |  3469 | -0.007 |  ≈0.676 | 0.896 | -0.013 | +0.003 | 1 | no | noise |
|            | low  |  1883 | +0.004 |  ≈0.846 | 0.962 | -0.026 | +0.038 | 2 | no | noise |
| **rev_lo** | high |  3101 | +0.001 |  ≈0.954 | 0.989 | -0.021 | +0.021 | 3 | no | noise |
|            | mid  |  2920 | +0.014 |  ≈0.437 | 0.755 | -0.012 | +0.030 | 2 | no | noise |
|            | low  |  1455 | +0.024 |  ≈0.362 | 0.692 | +0.013 | +0.041 | 0 | no | noise |
| **rev_nlo** | high |  1996 | +0.025 |  ≈0.260 | 0.623 | -0.077 | +0.076 | 1 | asc | noise |
|            | mid  |   758 | +0.024 |  ≈0.508 | 0.805 | -0.003 | +0.066 | 1 | no | noise |
|            | low  |   249 | -0.082 |  ≈0.199 | 0.623 | -0.132 | -0.058 | 0 | no | noise |
| **str_hold** | high |  2796 | +0.021 |  ≈0.262 | 0.623 | +0.006 | +0.044 | 0 | no | noise |
|            | mid  |  5002 | +0.004 |  ≈0.760 | 0.931 | -0.011 | +0.029 | 3 | no | noise |
|            | low  |  3790 | -0.010 |  ≈0.552 | 0.839 | -0.020 | +0.021 | 1 | desc | noise |
| **str_lag** | high |   850 | +0.065 |  ≈0.057 | 0.309 | +0.047 | +0.079 | 0 | no | noise |
|            | mid  |  1245 | +0.011 |  ≈0.708 | 0.896 | -0.037 | +0.033 | 1 | no | noise |
|            | low  |   666 | -0.089 |  ≈0.021 | 0.138 | -0.125 | -0.065 | 0 | no | borderline |
| **str_lead** | high |   400 | +0.084 |  ≈0.093 | 0.378 | +0.030 | +0.150 | 0 | no | noise |
|            | mid  |   589 | -0.017 |  ≈0.684 | 0.896 | -0.106 | +0.023 | 2 | no | noise |
|            | low  |   258 | -0.003 |  ≈0.963 | 0.989 | -0.060 | +0.054 | 3 | no | noise |

### Quartile EV by cell

EV = DR × mag_flw − (1−DR) × mag_rev. Quartile cells with n < 30 are masked.  

| Sign | corr | Q1 EV (n) | Q2 EV (n) | Q3 EV (n) | Q4 EV (n) |
|------|------|-----------|-----------|-----------|-----------|
| **brk_bol** | high | +0.0068 (226) | +0.0202 (225) | +0.0008 (225) | +0.0185 (226) |
|            | mid  | +0.0148 (318) | +0.0155 (317) | +0.0202 (317) | +0.0162 (317) |
|            | low  | — (0)      | — (0)      | — (0)      | — (0)      |
| **brk_hi_sideway** | high | +0.0103 (383) | +0.0150 (382) | -0.0010 (382) | +0.0192 (383) |
|            | mid  | +0.0188 (448) | +0.0147 (448) | +0.0140 (447) | +0.0156 (448) |
|            | low  | -0.0003 (245) | +0.0117 (245) | +0.0134 (245) | +0.0259 (245) |
| **brk_lo_sideway** | high | +0.0123 (323) | +0.0156 (322) | +0.0288 (322) | +0.0513 (322) |
|            | mid  | +0.0100 (257) | +0.0092 (257) | +0.0090 (257) | +0.0159 (257) |
|            | low  | +0.0169 (119) | -0.0099 (119) | -0.0014 (118) | +0.0062 (119) |
| **brk_sma** | high | — (0)      | — (0)      | — (0)      | — (0)      |
|            | mid  | — (0)      | — (0)      | — (0)      | — (0)      |
|            | low  | — (0)      | — (0)      | — (0)      | — (0)      |
| **corr_flip** | low  | +0.0175 (174) | +0.0079 (174) | -0.0104 (173) | +0.0048 (174) |
| **corr_shift** | mid  | +0.0180 (67) | +0.0016 (67) | +0.0130 (67) | +0.0098 (67) |
|            | low  | -0.0014 (220) | +0.0163 (219) | +0.0101 (219) | -0.0024 (219) |
| **div_gap** | high | -0.0084 (203) | +0.0212 (203) | +0.0273 (202) | +0.0301 (203) |
|            | mid  | +0.0102 (392) | +0.0220 (392) | +0.0202 (391) | +0.0313 (392) |
|            | low  | +0.0125 (328) | +0.0203 (327) | +0.0123 (327) | +0.0158 (328) |
| **div_peer** | high | +0.0208 (56) | +0.0441 (56) | +0.0218 (55) | +0.0297 (56) |
|            | mid  | +0.0313 (70) | +0.0256 (70) | +0.0330 (69) | +0.0088 (70) |
| **rev_hi** | high | +0.0166 (804) | +0.0113 (804) | +0.0053 (803) | +0.0128 (804) |
|            | mid  | +0.0091 (868) | +0.0105 (867) | +0.0101 (867) | +0.0101 (867) |
|            | low  | +0.0020 (471) | +0.0062 (471) | +0.0083 (470) | +0.0031 (471) |
| **rev_lo** | high | +0.0169 (776) | +0.0172 (775) | +0.0202 (775) | +0.0187 (775) |
|            | mid  | +0.0105 (730) | +0.0152 (730) | +0.0149 (730) | +0.0145 (730) |
|            | low  | +0.0161 (364) | +0.0101 (364) | +0.0169 (363) | +0.0174 (364) |
| **rev_nlo** | high | +0.0088 (499) | +0.0097 (499) | +0.0113 (499) | +0.0182 (499) |
|            | mid  | +0.0088 (190) | +0.0001 (189) | +0.0070 (189) | +0.0168 (190) |
|            | low  | +0.0072 (63) | +0.0222 (62) | +0.0201 (62) | -0.0078 (62) |
| **str_hold** | high | +0.0235 (699) | +0.0247 (699) | +0.0243 (699) | +0.0262 (699) |
|            | mid  | +0.0174 (1251) | +0.0207 (1698) | +0.0142 (1425) | +0.0113 (628) |
|            | low  | +0.0175 (948) | +0.0128 (1146) | +0.0126 (1281) | +0.0090 (415) |
| **str_lag** | high | +0.0004 (218) | +0.0190 (224) | +0.0142 (226) | +0.0121 (182) |
|            | mid  | -0.0009 (313) | +0.0166 (310) | +0.0120 (312) | +0.0046 (310) |
|            | low  | +0.0229 (172) | +0.0182 (167) | -0.0017 (167) | +0.0027 (160) |
| **str_lead** | high | -0.0080 (100) | -0.0080 (100) | +0.0053 (100) | +0.0081 (100) |
|            | mid  | +0.0205 (148) | +0.0005 (147) | +0.0084 (147) | +0.0119 (147) |
|            | low  | +0.0087 (65) | -0.0014 (64) | +0.0101 (64) | +0.0062 (65) |

---

## FY2025 Out-of-Sample Backtest

Generated: 2026-05-17  
Training: FY2018–FY2024 regime ranking (Ichimoku Kumo × ADX veto)  
Test: FY2025 · classified2024 · 2025-04-01 – 2026-03-31  
Ranking cells: 41 (sign × kumo_state, min_n=30)  

### Regime Cell Detail (sign × kumo_state)

Kumo states: ▲above cloud (+1) · ~inside (0) · ▼below cloud (−1)  
Δ DR = test cell DR − sign-level baseline DR (all events for that sign).

| Sign | kumo | train_bench_flw | train_DR | train_n | test_n | test_DR | Δ DR |
|------|------|-----------------|----------|---------|--------|---------|------|
| div_peer   | ~inside | 0.0654 | 67.0% |      88 |      0 |       — | —      |
| div_peer   | ▼below  | 0.0584 | 57.6% |     172 |      0 |       — | —      |
| rev_nlo    | ▼below  | 0.0574 | 62.6% |    1170 |      0 |       — | —      |
| brk_sma    | ▼below  | 0.0564 | 59.2% |     284 |      0 |       — | —      |
| brk_sma    | ▲above  | 0.0547 | 55.1% |     608 |      0 |       — | —      |
| brk_lo_sideway | ▼below  | 0.0533 | 59.1% |    1173 |      0 |       — | —      |
| str_hold   | ~inside | 0.0520 | 59.8% |    1616 |      0 |       — | —      |
| div_peer   | ▲above  | 0.0509 | 52.7% |     372 |      0 |       — | —      |
| brk_bol    | ▼below  | 0.0503 | 52.5% |     444 |      0 |       — | —      |
| div_gap    | ▼below  | 0.0499 | 56.9% |    1109 |      0 |       — | —      |
| corr_shift | ~inside | 0.0496 | 58.7% |     109 |      0 |       — | —      |
| div_gap    | ▲above  | 0.0492 | 56.7% |    1241 |      0 |       — | —      |
| brk_hi_sideway | ~inside | 0.0492 | 62.0% |     368 |      0 |       — | —      |
| str_hold   | ▼below  | 0.0488 | 56.9% |    4936 |      0 |       — | —      |
| corr_flip  | ▼below  | 0.0478 | 47.4% |     116 |      0 |       — | —      |
| rev_nlo    | ~inside | 0.0476 | 42.8% |     615 |      0 |       — | —      |
| brk_bol    | ▲above  | 0.0474 | 52.3% |    1611 |      0 |       — | —      |
| rev_lo     | ▼below  | 0.0467 | 55.7% |    2017 |      0 |       — | —      |
| rev_nhi    | ~inside | 0.0464 | 56.2% |     979 |      0 |       — | —      |
| div_gap    | ~inside | 0.0463 | 53.8% |     407 |      0 |       — | —      |
| brk_bol    | ~inside | 0.0462 | 51.9% |     270 |      0 |       — | —      |
| brk_sma    | ~inside | 0.0459 | 50.3% |     153 |      0 |       — | —      |
| corr_shift | ▲above  | 0.0457 | 51.5% |     571 |      0 |       — | —      |
| rev_nhold  | ▼below  | 0.0455 | 63.3% |      90 |      0 |       — | —      |
| rev_lo     | ~inside | 0.0455 | 57.7% |     955 |      0 |       — | —      |
| str_lead   | ▼below  | 0.0447 | 62.8% |     479 |      0 |       — | —      |
| str_lag    | ▲above  | 0.0446 | 50.3% |    1546 |      0 |       — | —      |
| rev_nhi    | ▲above  | 0.0444 | 52.0% |    6994 |      0 |       — | —      |
| rev_hi     | ~inside | 0.0443 | 57.2% |     842 |      0 |       — | —      |
| rev_lo     | ▲above  | 0.0437 | 52.9% |    3342 |      0 |       — | —      |
| rev_hi     | ▼below  | 0.0425 | 55.5% |    1790 |      0 |       — | —      |
| str_lag    | ▼below  | 0.0421 | 49.1% |     389 |      0 |       — | —      |
| brk_hi_sideway | ▼below  | 0.0421 | 55.1% |     700 |      0 |       — | —      |
| rev_nhi    | ▼below  | 0.0406 | 50.6% |    1980 |      0 |       — | —      |
| brk_lo_sideway | ▲above  | 0.0405 | 50.0% |    1010 |    267 |   51.3% | -0.2%  |
| corr_flip  | ▲above  | 0.0402 | 53.5% |     303 |      0 |       — | —      |
| brk_hi_sideway | ▲above  | 0.0382 | 49.8% |    2199 |      0 |       — | —      |
| brk_lo_sideway | ~inside | 0.0375 | 56.6% |     311 |      5 |   60.0% | +8.5%  |
| rev_hi     | ▲above  | 0.0374 | 48.3% |    4277 |      0 |       — | —      |
| str_lead   | ~inside | 0.0348 | 44.0% |     273 |      0 |       — | —      |
| corr_flip  | ~inside | 0.0345 | 46.1% |      76 |      0 |       — | —      |

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
| str_lead   | 0 | — | — | — | — | — |
| str_lag    | 0 | — | — | — | — | — |
| brk_sma    | 0 | — | — | — | — | — |
| brk_bol    | 0 | — | — | — | — | — |
| brk_hi_sideway | 0 | — | — | — | — | — |
| brk_lo_sideway |     272 |    51.5% |      272 |     51.5% |  +0.0% |      100% |
| rev_lo     | 0 | — | — | — | — | — |
| rev_hi     | 0 | — | — | — | — | — |
| rev_nhi    | 0 | — | — | — | — | — |
| rev_nlo    | 0 | — | — | — | — | — |
| rev_nhold  | 0 | — | — | — | — | — |

**Interpretation**: Positive Δ DR means the Kumo+ADX regime filter selected
events with better follow-through outcomes in the out-of-sample year.
Low regime_n% indicates the filter is aggressive; verify test_n is large enough.


## str_hold Feature Probe

Probe run: 2026-05-17.  Read-only diagnostic — does any of (gap_pct, body_dir_prev, body_pct_T, body_frac_T) carry marginal signal on the 11,582 str_hold fire events?

**Pre-registered gate** (per /sign-debate 2026-05-17):
  - pooled |Δ EV (top − bottom bucket)| ≥ 0.5pp
  - pooled 95% bootstrap CI excludes 0
  - per-FY direction consistent in ≥4 of 5 training FYs
  - FY2025 OOS Δ EV sign matches training-pooled sign
  - per-FY CI excludes 0 in ≥2 of 5 training FYs

`fire_time_legal=False` features use bar-T close (str_hold detector consumes close[T] at qualify time per src/signs/str_hold.py:79-103); any production gate built on these would be look-ahead.

### Per-feature buckets

| Feature | fire_time_legal | bucket | n | DR | EV |
|---------|:---:|:---:|---:|---:|---:|
| gap_pct | ✓ | lo | 3861 | 54.9% | +1.73pp |
| gap_pct | ✓ | mid | 3860 | 54.2% | +1.25pp |
| gap_pct | ✓ | hi | 3861 | 55.3% | +2.30pp |
| body_dir_prev | ✓ | -1 | 4796 | 54.7% | +1.85pp |
| body_dir_prev | ✓ | 0 | 161 | 53.4% | +1.44pp |
| body_dir_prev | ✓ | 1 | 6625 | 54.9% | +1.70pp |
| body_pct_T | ✗ | lo | 3861 | 55.9% | +2.25pp |
| body_pct_T | ✗ | mid | 3860 | 53.1% | +1.09pp |
| body_pct_T | ✗ | hi | 3861 | 55.3% | +1.93pp |
| body_frac_T | ✗ | lo | 3861 | 54.0% | +1.41pp |
| body_frac_T | ✗ | mid | 3860 | 54.5% | +1.60pp |
| body_frac_T | ✗ | hi | 3861 | 55.9% | +2.26pp |

### Pooled Δ EV (top − bottom) + bootstrap CI

| Feature | legal | pooled ΔEV | 95% CI | FY2025 OOS Δ | FY consistent | FY CI-pass | Gate |
|---------|:---:|---:|---|---:|:---:|:---:|:---:|
| gap_pct | ✓ | +0.92pp | [+0.41, +1.44]pp | -1.21pp | 3/5 | 1/5 | **FAIL** |
|  |  | gate notes: only 3/5 FYs direction-consistent (<4); OOS sign mismatch (oos Δ=-1.21pp); only 1/5 FYs with CI excluding 0 (<2) |  |  |  |  |  |
| body_dir_prev | ✓ | +0.08pp | [-0.35, +0.51]pp | -1.15pp | 3/5 | 0/5 | **FAIL** |
|  |  | gate notes: pooled |ΔEV| 0.08pp < 0.5pp; pooled CI [-0.35,+0.51]pp includes 0; only 3/5 FYs direction-consistent (<4); OOS sign mismatch (oos Δ=-1.15pp); only 0/5 FYs with CI excluding 0 (<2) |  |  |  |  |  |
| body_pct_T | ✗ | -0.17pp | [-0.70, +0.40]pp | -0.72pp | 3/5 | 4/5 | **FAIL** |
|  |  | gate notes: pooled |ΔEV| 0.17pp < 0.5pp; pooled CI [-0.70,+0.40]pp includes 0; only 3/5 FYs direction-consistent (<4) |  |  |  |  |  |
| body_frac_T | ✗ | +0.80pp | [+0.28, +1.30]pp | +0.90pp | 5/5 | 1/5 | **FAIL** |
|  |  | gate notes: only 1/5 FYs with CI excluding 0 (<2) |  |  |  |  |  |

### Per-FY Δ EV (top − bottom)

| Feature | FY2020 | FY2021 | FY2022 | FY2023 | FY2024 | FY2025 |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|
| gap_pct | -1.03pp | +0.21pp | +0.28pp | -0.32pp | +2.19pp | -1.21pp |
| body_dir_prev | +1.18pp | -0.16pp | -0.27pp | +0.31pp | +0.00pp | -1.15pp |
| body_pct_T | -3.11pp | -0.81pp | +1.30pp | -1.35pp | +1.42pp | -0.72pp |
| body_frac_T | +0.88pp | +0.43pp | +0.24pp | +0.09pp | +1.84pp | +0.90pp |

### Cross-tab: gap_pct × (ADX state × Kumo state) — Δ EV (top − bottom bucket)

| ADX | Kumo above | Kumo inside | Kumo below |
|-----|------------|-------------|------------|
| **choppy** | -0.96pp (n=874) | +2.35pp (n=1376) | +0.73pp (n=3503) |
| **bull** | +0.15pp (n=2005) | — | — |
| **bear** | +1.70pp (n=1580) | -1.20pp (n=679) | +0.22pp (n=1448) |

### Verdict

**No feature cleared the gate.**  Q1 (add candle/gap features) and Q2 (GA-tune the 4 thresholds) are both rejected for this iteration.  str_hold detector unchanged.
## Long-Term High Continuation Probe

Probe run: 2026-05-17.  Read-only diagnostic — does a close-based N-bar high carry continuation signal for N ∈ [60, 120, 250]?

**Pre-registered gate** (per /sign-debate 2026-05-17, judge-mandated):
  - pooled EV (training FYs FY2018..FY2024) ≥ +0.020
  - FY2025 OOS EV > 0
  - all training FYs EV ≥ 0
  - DR ≥ 53% (secondary)
  - rev_nhi same-bar overlap ≤ 50%
  - non-overlap subset still clears pooled EV ≥ +0.020 AND FY2025 EV > 0

Metrics use `trend_direction` (next confirmed zigzag, ZZ size=5, mid=2, cap=30 bars) — matches benchmark.md convention. Forward return at H=10 (two-bar fill) is reported as secondary.

### Per N — Pooled (training) + per-FY EV table

| N | n_total | n_train | overlap rev_nhi | pooled EV | DR | mean r_h10 | EV FY2018 | EV FY2019 | EV FY2020 | EV FY2021 | EV FY2022 | EV FY2023 | EV FY2024 | EV FY2025 | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 60 | 30712 | 24112 | 12.3% | +0.0027 | 50.3% | +0.37pp | — | -0.0116 (n=3623) | +0.0164 (n=5253) | -0.0094 (n=2902) | -0.0114 (n=3064) | +0.0260 (n=5992) | -0.0216 (n=3278) | +0.0286 (n=6600) | **FAIL** |
|  |  |  |  |  |  |  | | | | | | | | | notes: pooled EV +0.0027 < +0.0200; negative-EV FYs: FY2019,FY2021,FY2022,FY2024; DR 50.3% < 53% |
| 120 | 24134 | 18365 | 12.1% | +0.0045 | 51.0% | +0.46pp | — | -0.0131 (n=2567) | +0.0212 (n=4027) | -0.0046 (n=2161) | -0.0100 (n=2159) | +0.0223 (n=4995) | -0.0201 (n=2456) | +0.0283 (n=5769) | **FAIL** |
|  |  |  |  |  |  |  | | | | | | | | | notes: pooled EV +0.0045 < +0.0200; negative-EV FYs: FY2019,FY2021,FY2022,FY2024; DR 51.0% < 53% |
| 250 | 18864 | 14000 | 11.9% | +0.0030 | 50.9% | +0.42pp | — | -0.0096 (n=1546) | +0.0119 (n=2622) | -0.0057 (n=1900) | -0.0100 (n=1499) | +0.0214 (n=4431) | -0.0214 (n=2002) | +0.0296 (n=4864) | **FAIL** |
|  |  |  |  |  |  |  | | | | | | | | | notes: pooled EV +0.0030 < +0.0200; negative-EV FYs: FY2019,FY2021,FY2022,FY2024; DR 50.9% < 53% |
### Non-overlap subset (fires NOT same-bar with rev_nhi)

| N | n_train | n_oos | pooled EV (train) | FY2025 EV | DR | Non-overlap gate |
|---|---:|---:|---:|---:|---:|---|
| 60 | 21203 | 5726 | +0.0024 | +0.0283 | 50.3% | **FAIL** |
| 120 | 16216 | 5004 | +0.0040 | +0.0282 | 50.9% | **FAIL** |
| 250 | 12400 | 4225 | +0.0027 | +0.0294 | 50.9% | **FAIL** |

### Verdict

**No N cleared the gate.**  Q1 (long-term peak breakout as a new sign) is REJECTED for this iteration.  Probe B1 (sideways breakout) deferred — the parent hypothesis (long-window high carries continuation signal) does not hold on this universe / framing.

Per /sign-debate Path E: log gap in docs/followups.md and close cycle.
## Long-Term High Continuation Probe (Strict)

Probe run: 2026-05-17.  Spec-corrected sibling of the close-based probe — does a STRICT N-bar high breakout (`low[T] > rolling_max(close, N)[T-1]`, i.e. entire bar above prior resistance) carry continuation signal for N ∈ [60, 120, 250]?

Every strict fire is also a close-based fire (low > x implies close ≥ low > x), so this is a SUBSET of the events tested in the prior probe. The hypothesis being re-tested: clean breakouts (no intraday retracement) carry the edge the loose close-based formulation does not.

**Pre-registered gate** (unchanged from prior cycle):
  - pooled EV (training FYs FY2018..FY2024) ≥ +0.020
  - FY2025 OOS EV > 0
  - all training FYs EV ≥ 0
  - DR ≥ 53% (secondary)
  - rev_nhi same-bar overlap ≤ 50%

### Per N — Pooled (training) + per-FY EV table

| N | n_total | n_train | overlap rev_nhi | pooled EV | DR | mean r_h10 | EV FY2018 | EV FY2019 | EV FY2020 | EV FY2021 | EV FY2022 | EV FY2023 | EV FY2024 | EV FY2025 | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 60 | 9460 | 7473 | 20.1% | +0.0016 | 50.5% | +0.25pp | — | -0.0133 (n=1195) | +0.0153 (n=1714) | -0.0113 (n=909) | -0.0162 (n=880) | +0.0243 (n=1906) | -0.0229 (n=869) | +0.0289 (n=1987) | **FAIL** |
|  |  |  |  |  |  |  | | | | | | | | | notes: pooled EV +0.0016 < +0.0200; negative-EV FYs: FY2019,FY2021,FY2022,FY2024; DR 50.5% < 53% |
| 120 | 7329 | 5590 | 19.8% | +0.0031 | 51.1% | +0.36pp | — | -0.0190 (n=809) | +0.0214 (n=1280) | -0.0058 (n=675) | -0.0157 (n=585) | +0.0205 (n=1600) | -0.0225 (n=641) | +0.0302 (n=1739) | **FAIL** |
|  |  |  |  |  |  |  | | | | | | | | | notes: pooled EV +0.0031 < +0.0200; negative-EV FYs: FY2019,FY2021,FY2022,FY2024; DR 51.1% < 53% |
| 250 | 5627 | 4165 | 19.4% | +0.0012 | 50.8% | +0.33pp | — | -0.0146 (n=461) | +0.0123 (n=792) | -0.0076 (n=598) | -0.0200 (n=393) | +0.0190 (n=1396) | -0.0234 (n=525) | +0.0317 (n=1462) | **FAIL** |
|  |  |  |  |  |  |  | | | | | | | | | notes: pooled EV +0.0012 < +0.0200; negative-EV FYs: FY2019,FY2021,FY2022,FY2024; DR 50.8% < 53% |

### Verdict

**Strict variant also REJECTS — no N cleared the gate.**  The clean-breakout hypothesis does not invert the loose-breakout null on this universe / framing.  Closing both the close-based and strict variants as REJECT.
## Bullish Confluence Probe

Probe run: 2026-05-17.  Read-only diagnostic — does multi-sign confluence on a (stock, date) predict EV uplift vs single-sign fires?

Bullish sign set: str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo

**Pre-registered gate**:
  - EV[≥3 signs] − EV[1 sign] ≥ +1.0pp
  - EV[≥2 signs] − EV[1 sign] ≥ +0.5pp
  - uplift sign consistent in ≥4 of 6 training FYs
  - FY2025 OOS uplift sign matches pooled training sign
  - n[≥3 signs] in FY2025 ≥ 50

### Confluence buckets — pooled (training)

| Bucket | n_train | n_oos | DR (train) | EV (train) | EV (FY2025) | mean signs/day |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 26211 | 4185 | 52.3% | +0.0094 | +0.0297 | 1.00 |
| 2 | 814 | 124 | 56.3% | +0.0218 | +0.0142 | 2.00 |
| ≥3 | 13 | 5 | 53.8% | +0.0259 | +0.0663 | 3.00 |

### Pooled uplifts (training)

- EV[≥2 signs] − EV[1 sign] = **+1.25pp**  (gate ≥ +0.5pp)
- EV[≥3 signs] − EV[1 sign] = **+1.65pp**  (gate ≥ +1.0pp)
- FY2025 OOS uplift EV[≥3] − EV[1] = **+3.66pp**

### Per-FY EV by confluence bucket

| FY | EV[1] (n) | EV[2] (n) | EV[≥3] (n) | Uplift[≥3]−[1] |
|----|:---:|:---:|:---:|:---:|
| FY2019 | -0.0369 (n=1825) | -0.0463 (n=19) | — | — |
| FY2020 | +0.0313 (n=3307) | +0.0232 (n=106) | -0.0513 (n=3) | **-8.26pp** |
| FY2021 | +0.0056 (n=5169) | +0.0269 (n=159) | +0.0912 (n=4) | **+8.56pp** |
| FY2022 | +0.0067 (n=5344) | +0.0155 (n=171) | — | — |
| FY2023 | +0.0198 (n=4522) | +0.0184 (n=142) | — | — |
| FY2024 | +0.0091 (n=6044) | +0.0307 (n=217) | +0.0635 (n=3) | **+5.44pp** |
| FY2025 | +0.0297 (n=4185) | +0.0142 (n=124) | +0.0663 (n=5) | **+3.66pp** |

### Gate verdict

**FAIL** — gate notes: only 2/6 training FYs uplift-consistent (<4); FY2025 n[≥3 signs] = 5 < 50

**Confluence framework does NOT clear the gate on existing signs.**  Adding brk_nhi to a non-functional tally is pointless.  Two interpretations: (a) bullish-sign-set definition is wrong (try a narrower set), or (b) confluence as a factor doesn't carry signal on this universe — same events, same outcomes regardless of co-fire count.  Operator decision required before next probe.
## Bullish Confluence Probe (validity-windowed)

Probe run: 2026-05-17.  v2 of bullish-confluence — uses each sign's `valid_bars` (3 or 5 trading days per the detector defaults) so a fire counts toward confluence on every trade_date within its validity window, not only the calendar day it fired.

Bullish set + valid_bars: str_hold(3), str_lead(5), str_lag(5), brk_sma(5), brk_bol(3), rev_lo(5), rev_nlo(5)

Outcome at trade_date = next confirmed zigzag peak from that date (ZZ size=5, mid=2, cap=30 bars) — same convention as benchmark.md.  Trade_dates with zero valid signs are skipped (not investable in this framework).

**Pre-registered gate** (unchanged from v1):
  - EV[≥3 signs] − EV[1 sign] ≥ +1.0pp
  - EV[≥2 signs] − EV[1 sign] ≥ +0.5pp
  - uplift sign consistent in ≥4 of 7 training FYs
  - FY2025 OOS uplift sign matches pooled training sign
  - n[≥3 signs] in FY2025 ≥ 50

### Confluence buckets — pooled (training)

| Bucket | n_train | n_oos | DR (train) | EV (train) | EV (FY2025) | mean signs/day |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 84449 | 14019 | 55.1% | +0.0089 | +0.0259 | 1.00 |
| 2 | 16260 | 2363 | 60.1% | +0.0179 | +0.0407 | 2.00 |
| ≥3 | 1746 | 280 | 64.3% | +0.0320 | +0.0502 | 3.08 |

### Pooled uplifts (training)

- EV[≥2 signs] − EV[1 sign] = **+0.90pp**  (gate ≥ +0.5pp)
- EV[≥3 signs] − EV[1 sign] = **+2.31pp**  (gate ≥ +1.0pp)
- FY2025 OOS uplift EV[≥3] − EV[1] = **+2.44pp**

### Per-FY EV by confluence bucket

| FY | EV[1] (n) | EV[2] (n) | EV[≥3] (n) | Uplift[≥3]−[1] |
|----|:---:|:---:|:---:|:---:|
| FY2018 | — | — | — | — |
| FY2019 | -0.0215 (n=7823) | -0.0351 (n=431) | +0.0200 (n=41) | **+4.15pp** |
| FY2020 | +0.0277 (n=12966) | +0.0388 (n=2000) | +0.0490 (n=263) | **+2.13pp** |
| FY2021 | +0.0076 (n=16759) | +0.0133 (n=3411) | +0.0333 (n=407) | **+2.57pp** |
| FY2022 | +0.0127 (n=14997) | +0.0146 (n=3545) | +0.0171 (n=366) | **+0.44pp** |
| FY2023 | +0.0181 (n=13871) | +0.0254 (n=2832) | +0.0268 (n=245) | **+0.88pp** |
| FY2024 | -0.0002 (n=18033) | +0.0147 (n=4041) | +0.0371 (n=424) | **+3.74pp** |
| FY2025 | +0.0259 (n=14019) | +0.0407 (n=2363) | +0.0502 (n=280) | **+2.44pp** |

### Gate verdict

**PASS** — all gates clear

**Confluence framework (validity-windowed) is empirically real.**  Authorize brk_nhi as a sign that feeds the confluence tally; re-run this probe with brk_nhi included to verify incremental value.
## brk_hi_sideway Probe

Probe run: 2026-05-17.  Fires when a bar's low breaks above a recent sideways-range wall:

```
sideways range at i: (max H − min L) / mean C ≤ θ on bars [i-K+1, i]
wall[T] = max(tight_window_high[j] for j in [T-lookback, T-K-1])
fire[T] = (low[T] > wall[T-1]) AND (low[T-1] ≤ wall[T-1])

K        = 10 bars (sideways window)
θ        = 0.05 (range/mean tightness)
lookback = 120 bars (~6 months)
validity = 5 trading days (for confluence inclusion)
```

### 1. Standalone fire-rate and EV

| FY | n fires | DR | EV | mean score |
|----|---:|---:|---:|---:|
| FY2018 | 0 | — | — | — |
| FY2019 | 745 | 69.8% | +0.0080 | +2.17% |
| FY2020 | 707 | 72.8% | +0.0426 | +2.64% |
| FY2021 | 731 | 71.0% | +0.0235 | +2.37% |
| FY2022 | 723 | 72.6% | +0.0250 | +2.11% |
| FY2023 | 1094 | 77.4% | +0.0443 | +2.28% |
| FY2024 | 733 | 69.9% | +0.0228 | +2.50% |
| FY2025 | 1138 | 74.3% | +0.0443 | +2.48% |
| **pooled train** | **4733** | **72.6%** | **+0.0288** | — |
| **FY2025 OOS** | **1138** | **74.3%** | **+0.0443** | — |

**Standalone gate** (same as long-high probes — pooled EV ≥ +0.020, FY2025 EV > 0, DR ≥ 53%): **PASS**

### 2. Confluence-incremental value (vs v2 7-sign baseline)

Compares EV uplifts (≥3 sign confluence vs 1 sign) WITHOUT brk_hi_sideway in the bullish set vs WITH it included.  If brk_hi_sideway pushes more days into the ≥2/≥3 buckets AND those new entries carry the same edge, the uplift gap should widen.

| FY | EV[1] before | EV[≥3] before | uplift before | EV[1] after | EV[≥3] after | uplift after | Δ uplift |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2018 | — | — | — | — | — | — | **—** |
| FY2019 | -0.0215 | +0.0200 | +4.15pp | -0.0215 | -0.0376 | -1.62pp | **-5.77pp** |
| FY2020 | +0.0277 | +0.0490 | +2.13pp | +0.0252 | +0.0403 | +1.51pp | **-0.62pp** |
| FY2021 | +0.0076 | +0.0333 | +2.57pp | +0.0053 | +0.0221 | +1.68pp | **-0.89pp** |
| FY2022 | +0.0127 | +0.0171 | +0.44pp | +0.0119 | +0.0119 | -0.00pp | **-0.45pp** |
| FY2023 | +0.0181 | +0.0268 | +0.88pp | +0.0209 | +0.0277 | +0.68pp | **-0.20pp** |
| FY2024 | -0.0002 | +0.0371 | +3.74pp | -0.0007 | +0.0267 | +2.74pp | **-1.00pp** |
| FY2025 | +0.0259 | +0.0502 | +2.44pp | +0.0270 | +0.0436 | +1.66pp | **-0.78pp** |

**Pooled training**: uplift before = +2.31pp (n[≥3]=1746), uplift after = +1.48pp (n[≥3]=3441); **Δ uplift = -0.83pp**, n[≥3] grew by 1695.
**FY2025 OOS n[≥3] after** = 660 (gate ≥ 50).

### Verdict
## brk_lo_sideway Probe

Probe run: 2026-05-17.  Fires when today's high breaks BELOW a recent sideways-range floor:

```
sideways range at i: (max H − min L) / mean C ≤ θ on bars [i-K+1, i]
floor[T] = min(tight_window_low[j] for j in [T-lookback, T-K-1])
fire[T] = (high[T] < floor[T-1]) AND (high[T-1] ≥ floor[T-1])

K        = 10 bars (sideways window)
θ        = 0.05 (range/mean tightness)
lookback = 120 bars (~6 months)
validity = 5 trading days (for confluence inclusion)
```

**DR interpretation (lo-side)**: trend_direction counts the next confirmed zigzag peak.  Low DR (HIGH fraction small) means breakdowns persist → useful AVOID-LONG signal.  High DR means breakdowns revert → noise / bear-trap territory.

### 1. Standalone fire-rate and EV

| FY | n fires | DR | EV | mean score |
|----|---:|---:|---:|---:|
| FY2018 | 0 | — | — | — |
| FY2019 | 701 | 22.1% | -0.0782 | +2.68% |
| FY2020 | 446 | 39.0% | -0.0007 | +2.42% |
| FY2021 | 671 | 34.1% | -0.0123 | +2.53% |
| FY2022 | 668 | 36.7% | -0.0026 | +1.96% |
| FY2023 | 325 | 36.1% | -0.0037 | +2.33% |
| FY2024 | 672 | 35.4% | -0.0130 | +2.98% |
| FY2025 | 486 | 40.5% | +0.0126 | +3.02% |
| **pooled train** | **3483** | **33.2%** | **-0.0216** | — |
| **FY2025 OOS** | **486** | **40.5%** | **+0.0126** | — |

**Standalone gate** (pooled EV ≤ −0.020, FY2025 EV < 0, (1−DR) ≥ 53% — breakdown-persistence test): **FAIL**

### 2. Confluence-incremental: SKIPPED

The bullish-confluence v2 framework was validated for the long direction only.  No equivalent bearish-confluence framework has been built/tested in this repo.  Re-running the v2 probe with brk_lo_sideway in the bullish set would be nonsensical (a bearish event in a bullish tally).  Defer the confluence question to a separate cycle that validates a bearish-set first.

### Verdict

**brk_lo_sideway standalone FAIL** — breakdowns do not persist by the pre-registered gate.  Defer detector build.
