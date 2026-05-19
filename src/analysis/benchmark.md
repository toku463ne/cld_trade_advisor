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

Generated: 2026-05-18  
Universe: Nikkei225 representatives from prior FY's cluster  
Granularity: 1d · window=20 · valid_bars=5 · ZZ_SIZE=5 · trend_cap=30  
Permutation: 1000 iterations  

### Per-Fiscal-Year Results

#### FY2019 (2019-04-01 – 2020-03-31) · cluster=classified2018

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| div_peer   |    61 |  44.3% | ≈0.370  |    0.0291 |    0.0704 |    13.213 | <0.001  |  55 (×1.1) |    40.0% | —             | —             |
| corr_flip  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| corr_shift |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| str_hold   |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| str_lead   |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| str_lag    |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| brk_sma    |   253 |  46.6% | ≈0.285  |    0.0334 |    0.0718 |    13.648 | <0.001  | 253 (×1.0) |    46.6% | —             | —             |
| brk_bol    |   421 |  45.6% | ≈0.071  |    0.0308 |    0.0478 |    12.865 | <0.001  | 421 (×1.0) |    45.6% | —             | —             |
| brk_wall   |   680 |  47.2% | ≈0.145  |    0.0254 |    0.0515 |    12.891 | <0.001  | 599 (×1.1) |    46.7% | —             | —             |
| brk_floor  |   511 |  36.4% | <0.001  |    0.0299 |    0.0958 |    12.699 | <0.001  | 448 (×1.1) |    35.7% | —             | —             |
| brk_kumo_hi |   682 |  45.7% | ≈0.026  |    0.0352 |    0.0608 |    13.491 | <0.001  | 621 (×1.1) |    45.6% | —             | —             |
| brk_kumo_lo |   627 |  38.1% | <0.001  |    0.0303 |    0.1002 |    12.909 | <0.001  | 556 (×1.1) |    37.9% | —             | —             |
| brk_tenkan_hi |  3688 |  48.4% | ≈0.056  |    0.0368 |    0.0505 |    13.293 | <0.001  | 3159 (×1.2) |    47.9% | —             | —             |
| brk_tenkan_lo |  3511 |  47.3% | ≈0.002  |    0.0360 |    0.0617 |    13.053 | <0.001  | 3070 (×1.1) |    46.7% | —             | —             |
| chiko_hi   |   965 |  39.5% | <0.001  |    0.0228 |    0.0552 |    12.452 | <0.001  | 939 (×1.0) |    39.7% | —             | —             |
| chiko_lo   |   934 |  36.2% | <0.001  |    0.0247 |    0.0803 |    12.451 | <0.001  | 909 (×1.0) |    36.0% | —             | —             |
| rev_lo     |  1270 |  40.9% | <0.001  |    0.0270 |    0.0645 |    13.018 | <0.001  | 1027 (×1.2) |    41.4% | —             | —             |
| rev_hi     |  1407 |  50.2% | ≈0.894  |    0.0344 |    0.0425 |    12.930 | <0.001  | 1183 (×1.2) |    51.1% | —             | —             |
| rev_nhi    |  1993 |  45.4% | <0.001  |    0.0287 |    0.0372 |    12.434 | <0.001  | 1522 (×1.3) |    45.5% | —             | —             |
| rev_nlo    |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2020 (2020-04-01 – 2021-03-31) · cluster=classified2019

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   445 |  59.6% | <0.001  |    0.0658 |    0.0264 |    12.663 | <0.001  | 417 (×1.1) |    59.5% | 65.4% (<0.001) | 46.5% (≈0.428) |
| div_peer   |    77 |  62.3% | ≈0.030  |    0.0809 |    0.0264 |    13.273 | <0.001  |  66 (×1.2) |    59.1% | 66.7% (≈0.083) | 64.5% (≈0.106) |
| corr_flip  |    58 |  56.9% | ≈0.294  |    0.0595 |    0.0317 |    13.259 | <0.001  |  58 (×1.0) |    56.9% | 53.3% (≈0.796) | 60.0% (≈0.237) |
| corr_shift |   120 |  58.3% | ≈0.068  |    0.0692 |    0.0278 |    13.067 | <0.001  | 119 (×1.0) |    58.0% | 64.6% (≈0.043) | 54.3% (≈0.473) |
| str_hold   |   800 |  69.5% | <0.001  |    0.0771 |    0.0180 |    11.971 | <0.001  | 520 (×1.5) |    68.7% | 70.0% (<0.001) | —             |
| str_lead   |   107 |  31.8% | <0.001  |    0.0239 |    0.0298 |    10.421 | <0.001  | 107 (×1.0) |    31.8% | —             | 31.8% (<0.001) |
| str_lag    |   305 |  60.3% | <0.001  |    0.0593 |    0.0269 |    12.331 | <0.001  | 304 (×1.0) |    60.5% | —             | 59.3% (≈0.002) |
| brk_sma    |   347 |  58.2% | ≈0.002  |    0.0712 |    0.0321 |    13.104 | <0.001  | 347 (×1.0) |    58.2% | 66.1% (<0.001) | 49.2% (≈0.824) |
| brk_bol    |   594 |  60.6% | <0.001  |    0.0662 |    0.0289 |    12.838 | <0.001  | 594 (×1.0) |    60.6% | 69.0% (<0.001) | 54.3% (≈0.122) |
| brk_wall   |   642 |  52.8% | ≈0.155  |    0.0451 |    0.0289 |    12.791 | <0.001  | 574 (×1.1) |    54.4% | 59.5% (≈0.004) | 48.0% (≈0.443) |
| brk_floor  |   317 |  54.3% | ≈0.129  |    0.0544 |    0.0319 |    12.644 | <0.001  | 274 (×1.2) |    56.2% | 59.7% (≈0.018) | 47.2% (≈0.475) |
| brk_kumo_hi |   917 |  53.2% | ≈0.051  |    0.0537 |    0.0301 |    13.035 | <0.001  | 815 (×1.1) |    53.6% | 59.4% (<0.001) | 48.9% (≈0.606) |
| brk_kumo_lo |   619 |  58.8% | <0.001  |    0.0644 |    0.0238 |    12.381 | <0.001  | 569 (×1.1) |    59.1% | 71.1% (<0.001) | 47.9% (≈0.440) |
| brk_tenkan_hi |  4211 |  56.6% | <0.001  |    0.0673 |    0.0306 |    12.984 | <0.001  | 3690 (×1.1) |    56.6% | 64.1% (<0.001) | 46.3% (≈0.001) |
| brk_tenkan_lo |  3774 |  55.7% | <0.001  |    0.0657 |    0.0294 |    13.007 | <0.001  | 3294 (×1.1) |    56.2% | 64.2% (<0.001) | 47.0% (≈0.012) |
| chiko_hi   |  1197 |  53.7% | ≈0.010  |    0.0592 |    0.0316 |    12.629 | <0.001  | 1152 (×1.0) |    53.3% | 61.2% (<0.001) | 46.1% (≈0.037) |
| chiko_lo   |   825 |  57.8% | <0.001  |    0.0605 |    0.0292 |    12.313 | <0.001  | 796 (×1.0) |    57.4% | 62.3% (<0.001) | 49.9% (≈0.959) |
| rev_lo     |   983 |  57.2% | <0.001  |    0.0584 |    0.0290 |    12.728 | <0.001  | 811 (×1.2) |    57.7% | 65.4% (<0.001) | 49.6% (≈0.866) |
| rev_hi     |  1207 |  51.5% | ≈0.287  |    0.0523 |    0.0321 |    12.813 | <0.001  | 1011 (×1.2) |    52.6% | 59.2% (<0.001) | 44.8% (≈0.008) |
| rev_nhi    |  2496 |  53.9% | <0.001  |    0.0578 |    0.0342 |    12.796 | <0.001  | 1903 (×1.3) |    53.8% | 61.6% (<0.001) | 53.2% (≈0.018) |
| rev_nlo    |   501 |  27.9% | <0.001  |    0.0241 |    0.0412 |    11.583 | <0.001  | 501 (×1.0) |    27.9% | —             | 27.9% (<0.001) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2021 (2021-04-01 – 2022-03-31) · cluster=classified2020

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   635 |  52.1% | ≈0.284  |    0.0462 |    0.0363 |    12.643 | <0.001  | 604 (×1.1) |    51.7% | 45.7% (≈0.257) | 54.0% (≈0.090) |
| div_peer   |   146 |  49.3% | ≈0.869  |    0.0477 |    0.0441 |    12.342 | <0.001  | 137 (×1.1) |    49.6% | 59.3% (≈0.152) | 40.8% (≈0.123) |
| corr_flip  |    41 |  56.1% | ≈0.435  |    0.0362 |    0.0344 |    11.683 | <0.001  |  41 (×1.0) |    56.1% | 55.6% (≈0.637) | 56.5% (≈0.532) |
| corr_shift |   121 |  48.8% | ≈0.785  |    0.0384 |    0.0439 |    12.835 | <0.001  | 119 (×1.0) |    47.9% | 61.2% (≈0.116) | 40.3% (≈0.099) |
| str_hold   |  2082 |  58.2% | <0.001  |    0.0504 |    0.0303 |    12.031 | <0.001  | 1268 (×1.6) |    55.8% | 58.5% (<0.001) | 56.6% (≈0.011) |
| str_lead   |   339 |  47.2% | ≈0.302  |    0.0310 |    0.0306 |    12.519 | <0.001  | 339 (×1.0) |    47.2% | —             | 47.2% (≈0.302) |
| str_lag    |   517 |  42.7% | <0.001  |    0.0311 |    0.0477 |    12.364 | <0.001  | 515 (×1.0) |    42.9% | —             | 38.3% (<0.001) |
| brk_sma    |   246 |  51.6% | ≈0.610  |    0.0477 |    0.0410 |    12.362 | <0.001  | 246 (×1.0) |    51.6% | 64.1% (≈0.013) | 46.1% (≈0.312) |
| brk_bol    |   413 |  48.7% | ≈0.588  |    0.0395 |    0.0452 |    12.346 | <0.001  | 413 (×1.0) |    48.7% | 59.7% (≈0.028) | 43.6% (≈0.032) |
| brk_wall   |   644 |  51.1% | ≈0.581  |    0.0377 |    0.0416 |    12.615 | <0.001  | 582 (×1.1) |    51.4% | 53.0% (≈0.330) | 49.7% (≈0.918) |
| brk_floor  |   666 |  53.0% | ≈0.121  |    0.0437 |    0.0315 |    12.399 | <0.001  | 586 (×1.1) |    52.0% | 51.9% (≈0.483) | 54.2% (≈0.133) |
| brk_kumo_hi |   739 |  47.9% | ≈0.254  |    0.0378 |    0.0442 |    12.402 | <0.001  | 671 (×1.1) |    47.5% | 53.9% (≈0.154) | 43.0% (≈0.005) |
| brk_kumo_lo |   801 |  49.8% | ≈0.916  |    0.0433 |    0.0440 |    12.566 | <0.001  | 712 (×1.1) |    49.7% | 52.4% (≈0.331) | 46.9% (≈0.236) |
| brk_tenkan_hi |  3474 |  50.6% | ≈0.455  |    0.0381 |    0.0394 |    12.445 | <0.001  | 3036 (×1.1) |    50.6% | 54.8% (<0.001) | 47.7% (≈0.047) |
| brk_tenkan_lo |  3576 |  50.6% | ≈0.482  |    0.0414 |    0.0376 |    12.371 | <0.001  | 3069 (×1.2) |    50.6% | 51.2% (≈0.312) | 49.0% (≈0.414) |
| chiko_hi   |   966 |  47.8% | ≈0.177  |    0.0350 |    0.0420 |    12.395 | <0.001  | 926 (×1.0) |    48.1% | 48.3% (≈0.565) | 47.6% (≈0.214) |
| chiko_lo   |   967 |  50.1% | ≈0.974  |    0.0407 |    0.0355 |    12.158 | <0.001  | 938 (×1.0) |    50.1% | 53.2% (≈0.149) | 46.4% (≈0.129) |
| rev_lo     |  1304 |  47.9% | ≈0.135  |    0.0368 |    0.0394 |    12.158 | <0.001  | 1073 (×1.2) |    48.0% | 48.2% (≈0.388) | 48.0% (≈0.290) |
| rev_hi     |  1375 |  50.6% | ≈0.647  |    0.0359 |    0.0370 |    12.414 | <0.001  | 1136 (×1.2) |    50.9% | 50.5% (≈0.809) | 50.9% (≈0.636) |
| rev_nhi    |  1815 |  46.6% | ≈0.003  |    0.0353 |    0.0443 |    12.515 | <0.001  | 1443 (×1.3) |    46.2% | 50.8% (≈0.728) | 44.8% (<0.001) |
| rev_nlo    |   647 |  51.9% | ≈0.326  |    0.0479 |    0.0311 |    13.328 | <0.001  | 647 (×1.0) |    51.9% | —             | 51.9% (≈0.326) |
| rev_nhold  |   143 |  39.9% | ≈0.015  |    0.0231 |    0.0362 |    12.175 | <0.001  | 143 (×1.0) |    39.9% | —             | 39.9% (≈0.015) |

#### FY2022 (2022-04-01 – 2023-03-31) · cluster=classified2021

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   550 |  55.5% | ≈0.011  |    0.0430 |    0.0277 |    12.600 | <0.001  | 524 (×1.0) |    55.5% | 55.1% (≈0.177) | 54.7% (≈0.094) |
| div_peer   |   169 |  55.0% | ≈0.191  |    0.0480 |    0.0274 |    12.450 | <0.001  | 145 (×1.2) |    56.6% | 41.5% (≈0.216) | 59.4% (≈0.052) |
| corr_flip  |   139 |  35.3% | <0.001  |    0.0219 |    0.0403 |    12.201 | <0.001  | 139 (×1.0) |    35.3% | 50.8% (≈0.896) | 23.8% (<0.001) |
| corr_shift |   172 |  54.7% | ≈0.222  |    0.0402 |    0.0316 |    12.128 | <0.001  | 170 (×1.0) |    54.7% | 56.2% (≈0.292) | 53.5% (≈0.482) |
| str_hold   |  2257 |  49.9% | ≈0.916  |    0.0375 |    0.0298 |    12.608 | <0.001  | 1287 (×1.8) |    50.8% | 49.0% (≈0.464) | 49.2% (≈0.617) |
| str_lead   |   284 |  35.6% | <0.001  |    0.0215 |    0.0357 |    11.092 | <0.001  | 284 (×1.0) |    35.6% | —             | 35.6% (<0.001) |
| str_lag    |   431 |  57.5% | ≈0.002  |    0.0383 |    0.0314 |    11.459 | <0.001  | 431 (×1.0) |    57.5% | —             | 57.5% (≈0.002) |
| brk_sma    |   215 |  51.6% | ≈0.633  |    0.0393 |    0.0287 |    12.749 | <0.001  | 215 (×1.0) |    51.6% | 49.5% (≈0.920) | 53.4% (≈0.458) |
| brk_bol    |   444 |  52.5% | ≈0.296  |    0.0401 |    0.0332 |    12.559 | <0.001  | 444 (×1.0) |    52.5% | 54.1% (≈0.344) | 51.8% (≈0.531) |
| brk_wall   |   633 |  48.0% | ≈0.320  |    0.0286 |    0.0296 |    11.619 | <0.001  | 547 (×1.2) |    47.9% | 51.0% (≈0.747) | 46.2% (≈0.130) |
| brk_floor  |   541 |  59.9% | <0.001  |    0.0413 |    0.0183 |    12.227 | <0.001  | 472 (×1.1) |    60.4% | 54.6% (≈0.138) | 64.9% (<0.001) |
| brk_kumo_hi |   854 |  46.5% | ≈0.040  |    0.0324 |    0.0347 |    12.513 | <0.001  | 772 (×1.1) |    46.4% | 44.3% (≈0.038) | 47.9% (≈0.335) |
| brk_kumo_lo |   828 |  51.3% | ≈0.445  |    0.0364 |    0.0260 |    11.885 | <0.001  | 743 (×1.1) |    50.9% | 47.9% (≈0.403) | 54.7% (≈0.056) |
| brk_tenkan_hi |  3696 |  51.0% | ≈0.224  |    0.0372 |    0.0320 |    12.268 | <0.001  | 3230 (×1.1) |    50.7% | 52.5% (≈0.060) | 50.2% (≈0.866) |
| brk_tenkan_lo |  3537 |  53.4% | <0.001  |    0.0399 |    0.0278 |    12.093 | <0.001  | 2956 (×1.2) |    52.7% | 53.5% (≈0.008) | 53.7% (<0.001) |
| chiko_hi   |  1056 |  48.1% | ≈0.218  |    0.0359 |    0.0320 |    12.044 | <0.001  | 1013 (×1.0) |    47.7% | 47.3% (≈0.438) | 48.3% (≈0.321) |
| chiko_lo   |   969 |  55.4% | <0.001  |    0.0424 |    0.0229 |    12.282 | <0.001  | 941 (×1.0) |    55.2% | 48.8% (≈0.575) | 63.7% (<0.001) |
| rev_lo     |  1493 |  49.6% | ≈0.776  |    0.0355 |    0.0283 |    12.549 | <0.001  | 1226 (×1.2) |    50.1% | 46.1% (≈0.041) | 52.7% (≈0.127) |
| rev_hi     |  1637 |  48.3% | ≈0.174  |    0.0317 |    0.0316 |    12.104 | <0.001  | 1307 (×1.3) |    50.0% | 49.7% (≈0.870) | 47.5% (≈0.113) |
| rev_nhi    |  1987 |  50.6% | ≈0.606  |    0.0355 |    0.0328 |    12.165 | <0.001  | 1545 (×1.3) |    49.8% | 54.6% (≈0.039) | 49.2% (≈0.551) |
| rev_nlo    |   594 |  50.2% | ≈0.935  |    0.0446 |    0.0327 |    12.098 | <0.001  | 594 (×1.0) |    50.2% | —             | 50.2% (≈0.935) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2023 (2023-04-01 – 2024-03-31) · cluster=classified2022

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   648 |  55.9% | ≈0.003  |    0.0508 |    0.0256 |    12.267 | <0.001  | 605 (×1.1) |    56.0% | 55.4% (≈0.054) | 55.7% (≈0.040) |
| div_peer   |   167 |  57.5% | ≈0.053  |    0.0589 |    0.0236 |    13.269 | <0.001  | 151 (×1.1) |    57.6% | 56.6% (≈0.336) | 59.0% (≈0.064) |
| corr_flip  |   124 |  61.3% | ≈0.012  |    0.0475 |    0.0242 |    13.000 | <0.001  | 124 (×1.0) |    61.3% | 58.2% (≈0.225) | 63.2% (≈0.029) |
| corr_shift |   210 |  57.6% | ≈0.027  |    0.0476 |    0.0294 |    12.748 | <0.001  | 209 (×1.0) |    57.9% | 52.9% (≈0.588) | 60.2% (≈0.027) |
| str_hold   |  1581 |  49.8% | ≈0.860  |    0.0403 |    0.0299 |    12.103 | <0.001  | 855 (×1.8) |    50.4% | 48.9% (≈0.458) | 52.0% (≈0.393) |
| str_lead   |   173 |  56.1% | ≈0.110  |    0.0434 |    0.0268 |    11.757 | <0.001  | 173 (×1.0) |    56.1% | —             | 56.1% (≈0.110) |
| str_lag    |   457 |  53.6% | ≈0.123  |    0.0594 |    0.0243 |    13.103 | <0.001  | 453 (×1.0) |    53.4% | 29.9% (<0.001) | 58.4% (≈0.001) |
| brk_sma    |   254 |  55.5% | ≈0.079  |    0.0499 |    0.0289 |    12.622 | <0.001  | 254 (×1.0) |    55.5% | 58.1% (≈0.064) | 51.8% (≈0.705) |
| brk_bol    |   558 |  55.2% | ≈0.014  |    0.0540 |    0.0273 |    12.998 | <0.001  | 558 (×1.0) |    55.2% | 53.7% (≈0.295) | 54.2% (≈0.140) |
| brk_wall   |   878 |  54.8% | ≈0.005  |    0.0464 |    0.0267 |    12.787 | <0.001  | 779 (×1.1) |    55.3% | 50.0% (≈1.000) | 57.5% (<0.001) |
| brk_floor  |   331 |  50.2% | ≈0.956  |    0.0455 |    0.0303 |    12.710 | <0.001  | 289 (×1.1) |    49.1% | 49.6% (≈0.927) | 50.5% (≈0.890) |
| brk_kumo_hi |   824 |  54.0% | ≈0.021  |    0.0438 |    0.0298 |    12.831 | <0.001  | 752 (×1.1) |    54.9% | 53.7% (≈0.132) | 54.4% (≈0.080) |
| brk_kumo_lo |   723 |  53.0% | ≈0.110  |    0.0459 |    0.0272 |    12.752 | <0.001  | 638 (×1.1) |    53.0% | 50.7% (≈0.819) | 54.7% (≈0.056) |
| brk_tenkan_hi |  3876 |  53.9% | <0.001  |    0.0467 |    0.0273 |    12.407 | <0.001  | 3384 (×1.1) |    54.2% | 49.9% (≈0.959) | 56.6% (<0.001) |
| brk_tenkan_lo |  3239 |  56.2% | <0.001  |    0.0513 |    0.0251 |    12.760 | <0.001  | 2855 (×1.1) |    55.9% | 50.2% (≈0.908) | 59.0% (<0.001) |
| chiko_hi   |  1090 |  53.2% | ≈0.034  |    0.0455 |    0.0290 |    12.755 | <0.001  | 1059 (×1.0) |    52.8% | 56.1% (≈0.034) | 52.1% (≈0.239) |
| chiko_lo   |   807 |  54.3% | ≈0.015  |    0.0499 |    0.0280 |    12.971 | <0.001  | 777 (×1.0) |    54.3% | 52.7% (≈0.295) | 55.5% (≈0.020) |
| rev_lo     |  1368 |  61.2% | <0.001  |    0.0540 |    0.0242 |    12.607 | <0.001  | 1111 (×1.2) |    60.8% | 58.5% (<0.001) | 63.3% (<0.001) |
| rev_hi     |  1453 |  56.0% | <0.001  |    0.0477 |    0.0267 |    12.476 | <0.001  | 1191 (×1.2) |    56.0% | 54.7% (≈0.028) | 56.2% (<0.001) |
| rev_nhi    |  2732 |  55.8% | <0.001  |    0.0485 |    0.0263 |    12.662 | <0.001  | 2053 (×1.3) |    57.1% | 50.6% (≈0.762) | 56.7% (<0.001) |
| rev_nlo    |   493 |  50.3% | ≈0.893  |    0.0377 |    0.0385 |    11.566 | <0.001  | 493 (×1.0) |    50.3% | —             | 50.3% (≈0.893) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2024 (2024-04-01 – 2025-03-31) · cluster=classified2023

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   615 |  58.7% | <0.001  |    0.0471 |    0.0320 |    12.779 | <0.001  | 572 (×1.1) |    58.0% | 68.6% (<0.001) | 47.6% (≈0.433) |
| div_peer   |    98 |  58.2% | ≈0.106  |    0.0540 |    0.0396 |    12.663 | <0.001  |  86 (×1.1) |    62.8% | 54.5% (≈0.546) | 67.3% (≈0.015) |
| corr_flip  |   145 |  52.4% | ≈0.561  |    0.0494 |    0.0292 |    12.241 | <0.001  | 145 (×1.0) |    52.4% | 54.9% (≈0.345) | 48.1% (≈0.785) |
| corr_shift |   231 |  43.3% | ≈0.041  |    0.0389 |    0.0530 |    11.918 | <0.001  | 226 (×1.0) |    42.5% | 43.2% (≈0.155) | 43.3% (≈0.144) |
| str_hold   |  2858 |  57.4% | <0.001  |    0.0465 |    0.0398 |    11.841 | <0.001  | 1498 (×1.9) |    60.3% | 56.7% (<0.001) | 57.8% (≈0.004) |
| str_lead   |   276 |  57.6% | ≈0.011  |    0.0480 |    0.0249 |    11.688 | <0.001  | 276 (×1.0) |    57.6% | —             | 57.6% (≈0.011) |
| str_lag    |   667 |  48.0% | ≈0.296  |    0.0370 |    0.0328 |    12.789 | <0.001  | 665 (×1.0) |    47.8% | —             | 48.0% (≈0.296) |
| brk_sma    |   294 |  46.9% | ≈0.294  |    0.0471 |    0.0441 |    12.480 | <0.001  | 294 (×1.0) |    46.9% | 64.5% (≈0.002) | 36.4% (<0.001) |
| brk_bol    |   448 |  43.8% | ≈0.008  |    0.0403 |    0.0484 |    12.373 | <0.001  | 448 (×1.0) |    43.8% | 56.0% (≈0.152) | 38.1% (<0.001) |
| brk_wall   |   524 |  53.4% | ≈0.116  |    0.0403 |    0.0345 |    12.263 | <0.001  | 463 (×1.1) |    51.8% | 61.8% (<0.001) | 48.3% (≈0.542) |
| brk_floor  |   672 |  55.2% | ≈0.007  |    0.0482 |    0.0326 |    12.796 | <0.001  | 597 (×1.1) |    56.4% | 57.7% (≈0.003) | 52.3% (≈0.425) |
| brk_kumo_hi |   863 |  50.3% | ≈0.865  |    0.0422 |    0.0316 |    12.538 | <0.001  | 783 (×1.1) |    50.7% | 61.3% (<0.001) | 41.0% (<0.001) |
| brk_kumo_lo |   911 |  60.2% | <0.001  |    0.0579 |    0.0277 |    12.487 | <0.001  | 805 (×1.1) |    60.5% | 65.5% (<0.001) | 53.1% (≈0.224) |
| brk_tenkan_hi |  3813 |  49.5% | ≈0.571  |    0.0405 |    0.0388 |    12.348 | <0.001  | 3366 (×1.1) |    49.9% | 53.4% (≈0.008) | 46.9% (≈0.003) |
| brk_tenkan_lo |  3867 |  53.8% | <0.001  |    0.0476 |    0.0393 |    12.478 | <0.001  | 3370 (×1.1) |    53.3% | 55.7% (<0.001) | 50.9% (≈0.443) |
| chiko_hi   |  1122 |  53.9% | ≈0.009  |    0.0432 |    0.0400 |    12.339 | <0.001  | 1086 (×1.0) |    54.0% | 57.3% (≈0.004) | 52.1% (≈0.265) |
| chiko_lo   |  1074 |  57.3% | <0.001  |    0.0503 |    0.0297 |    12.446 | <0.001  | 1048 (×1.0) |    57.5% | 61.1% (<0.001) | 52.4% (≈0.292) |
| rev_lo     |  1358 |  56.7% | <0.001  |    0.0441 |    0.0300 |    12.416 | <0.001  | 1127 (×1.2) |    56.3% | 58.1% (<0.001) | 55.2% (≈0.007) |
| rev_hi     |  1472 |  49.5% | ≈0.677  |    0.0347 |    0.0398 |    12.103 | <0.001  | 1187 (×1.2) |    49.4% | 54.8% (≈0.021) | 45.9% (≈0.015) |
| rev_nhi    |  1649 |  49.4% | ≈0.605  |    0.0396 |    0.0455 |    12.466 | <0.001  | 1323 (×1.2) |    49.0% | 51.7% (≈0.460) | 48.4% (≈0.281) |
| rev_nlo    |   615 |  55.9% | ≈0.003  |    0.0591 |    0.0274 |    11.990 | <0.001  | 615 (×1.0) |    55.9% | —             | 55.9% (≈0.003) |
| rev_nhold  |    10 |  90.0% | ≈0.011  |    0.1295 |    0.0033 |    13.200 | <0.001  |  10 (×1.0) |    90.0% | —             | 90.0% (≈0.011) |

#### FY2025 (2025-04-01 – 2026-03-31) · cluster=classified2024

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   830 |  50.6% | ≈0.729  |    0.0521 |    0.0359 |    12.552 | <0.001  | 756 (×1.1) |    51.5% | 46.7% (≈0.293) | 51.0% (≈0.663) |
| div_peer   |    22 |  68.2% | ≈0.088  |    0.0746 |    0.0122 |    11.364 | <0.001  |  21 (×1.0) |    66.7% | 100.0% (≈0.008) | 54.5% (≈0.763) |
| corr_flip  |   242 |  45.9% | ≈0.199  |    0.0374 |    0.0371 |    12.541 | <0.001  | 242 (×1.0) |    45.9% | 50.6% (≈0.915) | 43.2% (≈0.092) |
| corr_shift |   301 |  47.5% | ≈0.387  |    0.0396 |    0.0330 |    12.399 | <0.001  | 297 (×1.0) |    47.8% | 46.2% (≈0.380) | 45.4% (≈0.274) |
| str_hold   |  2010 |  51.3% | ≈0.228  |    0.0524 |    0.0213 |    12.448 | <0.001  | 1216 (×1.7) |    51.6% | 46.7% (≈0.022) | 57.5% (<0.001) |
| str_lead   |    68 |  66.2% | ≈0.008  |    0.0584 |    0.0207 |    13.647 | <0.001  |  68 (×1.0) |    66.2% | —             | 66.2% (≈0.008) |
| str_lag    |   404 |  47.3% | ≈0.274  |    0.0554 |    0.0424 |    12.587 | <0.001  | 403 (×1.0) |    47.1% | 40.7% (≈0.084) | 49.1% (≈0.737) |
| brk_sma    |   265 |  55.8% | ≈0.057  |    0.0572 |    0.0310 |    12.408 | <0.001  | 265 (×1.0) |    55.8% | 68.2% (<0.001) | 47.9% (≈0.590) |
| brk_bol    |   630 |  57.6% | <0.001  |    0.0666 |    0.0304 |    12.795 | <0.001  | 630 (×1.0) |    57.6% | 71.2% (<0.001) | 53.3% (≈0.182) |
| brk_wall   |  1005 |  59.6% | <0.001  |    0.0536 |    0.0237 |    12.605 | <0.001  | 881 (×1.1) |    59.3% | 65.7% (<0.001) | 55.4% (≈0.007) |
| brk_floor  |   272 |  51.5% | ≈0.628  |    0.0493 |    0.0308 |    12.107 | <0.001  | 241 (×1.1) |    53.1% | 52.5% (≈0.584) | 50.7% (≈0.871) |
| brk_kumo_hi |   732 |  54.2% | ≈0.022  |    0.0660 |    0.0324 |    12.399 | <0.001  | 662 (×1.1) |    54.7% | 54.8% (≈0.094) | 53.8% (≈0.113) |
| brk_kumo_lo |   492 |  52.2% | ≈0.321  |    0.0571 |    0.0331 |    11.941 | <0.001  | 450 (×1.1) |    52.9% | 49.7% (≈0.941) | 53.7% (≈0.192) |
| brk_tenkan_hi |  4284 |  57.2% | <0.001  |    0.0611 |    0.0273 |    12.773 | <0.001  | 3730 (×1.1) |    57.3% | 61.3% (<0.001) | 53.0% (≈0.004) |
| brk_tenkan_lo |  3636 |  54.8% | <0.001  |    0.0594 |    0.0271 |    12.351 | <0.001  | 3145 (×1.2) |    55.2% | 56.9% (<0.001) | 51.8% (≈0.110) |
| chiko_hi   |  1188 |  60.2% | <0.001  |    0.0613 |    0.0244 |    12.477 | <0.001  | 1157 (×1.0) |    60.1% | 62.8% (<0.001) | 56.3% (<0.001) |
| chiko_lo   |   681 |  51.8% | ≈0.338  |    0.0559 |    0.0308 |    12.198 | <0.001  | 649 (×1.0) |    51.6% | 51.5% (≈0.579) | 51.6% (≈0.558) |
| rev_lo     |  1011 |  54.4% | ≈0.005  |    0.0496 |    0.0226 |    12.451 | <0.001  | 827 (×1.2) |    53.1% | 53.3% (≈0.176) | 55.0% (≈0.018) |
| rev_hi     |  1496 |  54.5% | <0.001  |    0.0532 |    0.0271 |    12.832 | <0.001  | 1237 (×1.2) |    53.8% | 59.8% (<0.001) | 50.9% (≈0.594) |
| rev_nhi    |  2896 |  56.1% | <0.001  |    0.0599 |    0.0297 |    12.867 | <0.001  | 2231 (×1.3) |    55.7% | 63.7% (<0.001) | 54.5% (<0.001) |
| rev_nlo    |   153 |  64.7% | <0.001  |    0.0497 |    0.0229 |    12.307 | <0.001  | 153 (×1.0) |    64.7% | —             | 64.7% (<0.001) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

### Aggregate by Sign (FY2018–FY2024)

| Sign | FYs | total_n | pooled_DR% | p_pooled | avg_bench_flw | avg_bench_rev | perm_pass | bear_DR range | bull_DR range |
|------|-----|---------|------------|----------|--------------|---------------|-----------|---------------|---------------|
| div_gap    |   7 |    3723 |      54.9% | <0.001   |       0.0508 |        0.0307 |       7/7 | 45.7–68.6%    | 46.5–55.7%    |
| div_peer   |   7 |     740 |      55.1% | ≈0.005   |       0.0562 |        0.0348 |       7/7 | 41.5–100.0%   | 40.8–67.3%    |
| corr_flip  |   7 |     749 |      49.1% | ≈0.635   |       0.0420 |        0.0328 |       7/7 | 50.6–58.2%    | 23.8–63.2%    |
| corr_shift |   7 |    1155 |      50.8% | ≈0.576   |       0.0457 |        0.0365 |       7/7 | 43.2–64.6%    | 40.3–60.2%    |
| str_hold   |   7 |   11588 |      54.8% | <0.001   |       0.0507 |        0.0282 |       7/7 | 46.7–70.0%    | 49.2–57.8%    |
| str_lead   |   7 |    1247 |      47.8% | ≈0.119   |       0.0377 |        0.0281 |       7/7 | —             | 31.8–66.2%    |
| str_lag    |   7 |    2781 |      50.7% | ≈0.483   |       0.0467 |        0.0343 |       7/7 | 29.9–40.7%    | 38.3–59.3%    |
| brk_sma    |   7 |    1874 |      52.6% | ≈0.027   |       0.0494 |        0.0397 |       7/7 | 49.5–68.2%    | 36.4–53.4%    |
| brk_bol    |   7 |    3508 |      52.8% | <0.001   |       0.0482 |        0.0373 |       7/7 | 53.7–71.2%    | 38.1–54.3%    |
| brk_wall   |   7 |    5006 |      53.0% | <0.001   |       0.0396 |        0.0338 |       7/7 | 50.0–65.7%    | 46.2–57.5%    |
| brk_floor  |   7 |    3310 |      51.7% | ≈0.048   |       0.0446 |        0.0387 |       7/7 | 49.6–59.7%    | 47.2–64.9%    |
| brk_kumo_hi |   7 |    5611 |      50.4% | ≈0.566   |       0.0444 |        0.0377 |       7/7 | 44.3–61.3%    | 41.0–54.4%    |
| brk_kumo_lo |   7 |    5001 |      52.3% | ≈0.001   |       0.0479 |        0.0403 |       7/7 | 47.9–71.1%    | 46.9–54.7%    |
| brk_tenkan_hi |   7 |   27042 |      52.7% | <0.001   |       0.0468 |        0.0351 |       7/7 | 49.9–64.1%    | 46.3–56.6%    |
| brk_tenkan_lo |   7 |   25140 |      53.1% | <0.001   |       0.0487 |        0.0354 |       7/7 | 50.2–64.2%    | 47.0–59.0%    |
| chiko_hi   |   7 |    7584 |      51.3% | ≈0.019   |       0.0433 |        0.0363 |       7/7 | 47.3–62.8%    | 46.1–56.3%    |
| chiko_lo   |   7 |    6257 |      51.8% | ≈0.004   |       0.0463 |        0.0366 |       7/7 | 48.8–62.3%    | 46.4–63.7%    |
| rev_lo     |   7 |    8787 |      52.4% | <0.001   |       0.0436 |        0.0340 |       7/7 | 46.1–65.4%    | 48.0–63.3%    |
| rev_hi     |   7 |   10047 |      51.5% | ≈0.003   |       0.0414 |        0.0338 |       7/7 | 49.7–59.8%    | 44.8–56.2%    |
| rev_nhi    |   7 |   15568 |      51.8% | <0.001   |       0.0436 |        0.0357 |       7/7 | 50.6–63.7%    | 44.8–56.7%    |
| rev_nlo    |   7 |    3003 |      48.8% | ≈0.183   |       0.0439 |        0.0323 |       7/7 | —             | 27.9–64.7%    |
| rev_nhold  |   7 |     153 |      43.1% | ≈0.090   |       0.0763 |        0.0197 |       7/7 | —             | 39.9–90.0%    |

**Notes on interpretation**
- pooled_DR% is n-weighted across all FYs; p_pooled is the binomial test on the pooled n.
- perm_pass = FYs where the permutation test passes at p<0.05.
- bear_DR / bull_DR ranges show min–max across FYs.
- Signs consistent across multiple FYs with perm_pass ≥ 4/7 are the most reliable.


---

## Regime-Split Analysis: ADX + Ichimoku Kumo

Generated: 2026-05-18
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
| brk_sma    | choppy (ADX<20)           |    914 |  53.3% |   ≈0.051 |       — |       — |        — |  52.6% |
| brk_sma    | bull (ADX≥20,+DI>−DI)     |    487 |  52.4% |   ≈0.319 |       — |       — |        — |  52.6% |
| brk_sma    | bear (ADX≥20,+DI≤−DI)     |    210 |  57.6% |   ≈0.032 |  0.0946 |  0.0771 |  +0.0219 |  52.6% |
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
| brk_sma    | above (+1)                |   1060 |  52.4% |   ≈0.132 |       — |       — |        — |  52.6% |
| brk_sma    | inside (0)                |    187 |  51.3% |   ≈0.770 |       — |       — |        — |  52.6% |
| brk_sma    | below (−1)                |    319 |  57.7% |   ≈0.007 |  0.0924 |  0.0739 |  +0.0221 |  52.6% |
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

Generated: 2026-05-18  
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
| brk_sma    |   1874 | 0.042–1.000 |  -0.003 |  ≈0.883 | noise (p≥0.05) |
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

Generated: 2026-05-18  
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
| **brk_bol** | high |   902 | +0.028 |  ≈0.400 | 0.725 | +0.006 | +0.048 | 0 | no | noise |
|            | mid  |  1269 | +0.011 |  ≈0.691 | 0.860 | -0.008 | +0.037 | 2 | no | noise |
|            | low  |   814 | +0.080 |  ≈0.022 | 0.117 | +0.063 | +0.095 | 0 | no | borderline |
| **brk_floor** | high |  1289 | +0.172 |  <0.001 | 0.000 | +0.116 | +0.210 | 0 | asc | **strong** |
|            | mid  |  1028 | +0.021 |  ≈0.496 | 0.791 | -0.008 | +0.049 | 1 | no | noise |
|            | low  |   475 | -0.055 |  ≈0.233 | 0.568 | -0.096 | -0.030 | 0 | no | noise |
| **brk_kumo_hi** | high |  1995 | +0.013 |  ≈0.572 | 0.823 | -0.004 | +0.031 | 2 | no | noise |
|            | mid  |  2002 | +0.004 |  ≈0.844 | 0.927 | -0.013 | +0.028 | 1 | no | noise |
|            | low  |   932 | +0.028 |  ≈0.402 | 0.725 | +0.009 | +0.068 | 0 | no | noise |
| **brk_kumo_lo** | high |  1989 | +0.082 |  <0.001 | 0.005 | +0.030 | +0.108 | 0 | no | borderline |
|            | mid  |  1633 | +0.056 |  ≈0.023 | 0.117 | +0.042 | +0.075 | 0 | no | borderline |
|            | low  |   752 | -0.016 |  ≈0.663 | 0.860 | -0.036 | +0.004 | 1 | no | noise |
| **brk_sma** | high |   582 | -0.031 |  ≈0.452 | 0.768 | -0.058 | +0.011 | 1 | no | noise |
|            | mid  |   648 | -0.007 |  ≈0.853 | 0.927 | -0.029 | +0.017 | 1 | no | noise |
|            | low  |   354 | -0.007 |  ≈0.891 | 0.942 | -0.026 | +0.030 | 2 | no | noise |
| **brk_tenkan_hi** | high |  9122 | +0.028 |  ≈0.008 | 0.078 | +0.020 | +0.031 | 0 | no | noise |
|            | mid  |  9056 | +0.019 |  ≈0.066 | 0.285 | +0.014 | +0.025 | 0 | asc | noise |
|            | low  |  4438 | -0.021 |  ≈0.165 | 0.497 | -0.026 | -0.010 | 0 | no | noise |
| **brk_tenkan_lo** | high |  8580 | +0.034 |  ≈0.002 | 0.022 | +0.014 | +0.047 | 0 | asc | noise |
|            | mid  |  8460 | -0.005 |  ≈0.679 | 0.860 | -0.010 | +0.003 | 1 | no | noise |
|            | low  |  4112 | -0.021 |  ≈0.171 | 0.497 | -0.028 | -0.008 | 0 | no | noise |
| **brk_wall** | high |  1530 | +0.028 |  ≈0.282 | 0.607 | +0.015 | +0.042 | 0 | no | noise |
|            | mid  |  1791 | -0.021 |  ≈0.364 | 0.704 | -0.040 | -0.001 | 0 | no | noise |
|            | low  |   980 | +0.092 |  ≈0.004 | 0.044 | +0.066 | +0.130 | 0 | asc | borderline |
| **chiko_hi** | high |  2306 | +0.009 |  ≈0.651 | 0.860 | -0.002 | +0.032 | 2 | asc | noise |
|            | mid  |  2672 | +0.011 |  ≈0.573 | 0.823 | +0.003 | +0.019 | 0 | no | noise |
|            | low  |  1422 | +0.066 |  ≈0.012 | 0.099 | +0.049 | +0.074 | 0 | no | borderline |
| **chiko_lo** | high |  2379 | +0.036 |  ≈0.079 | 0.316 | +0.010 | +0.061 | 0 | no | noise |
|            | mid  |  1949 | +0.038 |  ≈0.091 | 0.325 | +0.014 | +0.062 | 0 | asc | noise |
|            | low  |   958 | +0.023 |  ≈0.470 | 0.773 | +0.005 | +0.044 | 0 | no | noise |
| **corr_flip** | low  |   695 | -0.063 |  ≈0.099 | 0.327 | -0.080 | -0.042 | 0 | no | noise |
| **corr_shift** | mid  |   268 | +0.011 |  ≈0.861 | 0.927 | -0.048 | +0.094 | 2 | no | noise |
|            | low  |   877 | +0.031 |  ≈0.356 | 0.704 | -0.007 | +0.074 | 1 | no | noise |
| **div_gap** | high |   811 | +0.154 |  <0.001 | 0.000 | +0.116 | +0.190 | 0 | asc | moderate |
|            | mid  |  1567 | +0.060 |  ≈0.017 | 0.117 | +0.041 | +0.072 | 0 | no | borderline |
|            | low  |  1310 | -0.006 |  ≈0.832 | 0.927 | -0.022 | +0.007 | 3 | no | noise |
| **div_peer** | high |   223 | +0.003 |  ≈0.962 | 0.963 | -0.036 | +0.047 | 4 | no | noise |
|            | mid  |   279 | -0.074 |  ≈0.220 | 0.560 | -0.088 | -0.041 | 0 | no | noise |
| **rev_hi** | high |  3215 | -0.024 |  ≈0.177 | 0.497 | -0.030 | -0.018 | 0 | no | noise |
|            | mid  |  3469 | -0.007 |  ≈0.676 | 0.860 | -0.013 | +0.003 | 1 | no | noise |
|            | low  |  1883 | +0.004 |  ≈0.846 | 0.927 | -0.026 | +0.038 | 2 | no | noise |
| **rev_lo** | high |  3101 | +0.001 |  ≈0.954 | 0.963 | -0.021 | +0.021 | 3 | no | noise |
|            | mid  |  2920 | +0.014 |  ≈0.437 | 0.765 | -0.012 | +0.030 | 2 | no | noise |
|            | low  |  1455 | +0.024 |  ≈0.362 | 0.704 | +0.013 | +0.041 | 0 | no | noise |
| **rev_nlo** | high |  1996 | +0.025 |  ≈0.260 | 0.588 | -0.077 | +0.076 | 1 | asc | noise |
|            | mid  |   758 | +0.024 |  ≈0.508 | 0.791 | -0.003 | +0.066 | 1 | no | noise |
|            | low  |   249 | -0.082 |  ≈0.199 | 0.530 | -0.132 | -0.058 | 0 | no | noise |
| **str_hold** | high |  2796 | +0.021 |  ≈0.262 | 0.588 | +0.006 | +0.044 | 0 | no | noise |
|            | mid  |  5002 | +0.004 |  ≈0.760 | 0.905 | -0.011 | +0.029 | 3 | no | noise |
|            | low  |  3790 | -0.010 |  ≈0.552 | 0.823 | -0.020 | +0.021 | 1 | desc | noise |
| **str_lag** | high |   850 | +0.065 |  ≈0.057 | 0.265 | +0.047 | +0.079 | 0 | no | noise |
|            | mid  |  1245 | +0.011 |  ≈0.708 | 0.862 | -0.037 | +0.033 | 1 | no | noise |
|            | low  |   666 | -0.089 |  ≈0.021 | 0.117 | -0.125 | -0.065 | 0 | no | borderline |
| **str_lead** | high |   400 | +0.084 |  ≈0.093 | 0.325 | +0.030 | +0.150 | 0 | no | noise |
|            | mid  |   589 | -0.017 |  ≈0.684 | 0.860 | -0.106 | +0.023 | 2 | no | noise |
|            | low  |   258 | -0.003 |  ≈0.963 | 0.963 | -0.060 | +0.054 | 3 | no | noise |

### Quartile EV by cell

EV = DR × mag_flw − (1−DR) × mag_rev. Quartile cells with n < 30 are masked.  

| Sign | corr | Q1 EV (n) | Q2 EV (n) | Q3 EV (n) | Q4 EV (n) |
|------|------|-----------|-----------|-----------|-----------|
| **brk_bol** | high | +0.0068 (226) | +0.0202 (225) | +0.0008 (225) | +0.0185 (226) |
|            | mid  | +0.0148 (318) | +0.0155 (317) | +0.0202 (317) | +0.0162 (317) |
|            | low  | — (0)      | — (0)      | — (0)      | — (0)      |
| **brk_floor** | high | +0.0123 (323) | +0.0156 (322) | +0.0288 (322) | +0.0513 (322) |
|            | mid  | +0.0100 (257) | +0.0092 (257) | +0.0090 (257) | +0.0159 (257) |
|            | low  | +0.0169 (119) | -0.0099 (119) | -0.0014 (118) | +0.0062 (119) |
| **brk_kumo_hi** | high | +0.0061 (499) | -0.0030 (499) | +0.0003 (498) | +0.0127 (499) |
|            | mid  | +0.0140 (501) | +0.0183 (500) | +0.0166 (500) | +0.0241 (501) |
|            | low  | +0.0210 (233) | +0.0044 (233) | +0.0050 (233) | +0.0377 (233) |
| **brk_kumo_lo** | high | +0.0129 (498) | +0.0238 (497) | +0.0209 (497) | +0.0453 (497) |
|            | mid  | +0.0075 (409) | +0.0178 (408) | +0.0198 (408) | +0.0182 (408) |
|            | low  | +0.0116 (188) | +0.0108 (188) | +0.0129 (188) | +0.0149 (188) |
| **brk_sma** | high | — (0)      | — (0)      | — (0)      | — (0)      |
|            | mid  | — (0)      | — (0)      | — (0)      | — (0)      |
|            | low  | — (0)      | — (0)      | — (0)      | — (0)      |
| **brk_tenkan_hi** | high | +0.0136 (2281) | +0.0079 (2280) | +0.0109 (2280) | +0.0222 (2281) |
|            | mid  | +0.0123 (2264) | +0.0140 (2264) | +0.0185 (2264) | +0.0188 (2264) |
|            | low  | +0.0154 (1110) | +0.0110 (1109) | +0.0121 (1109) | +0.0154 (1110) |
| **brk_tenkan_lo** | high | +0.0120 (2145) | +0.0150 (2145) | +0.0200 (2145) | +0.0250 (2145) |
|            | mid  | +0.0169 (2115) | +0.0157 (2115) | +0.0188 (2115) | +0.0192 (2115) |
|            | low  | +0.0186 (1028) | +0.0155 (1028) | +0.0193 (1028) | +0.0134 (1028) |
| **brk_wall** | high | +0.0103 (383) | +0.0150 (382) | -0.0010 (382) | +0.0192 (383) |
|            | mid  | +0.0188 (448) | +0.0147 (448) | +0.0140 (447) | +0.0156 (448) |
|            | low  | -0.0003 (245) | +0.0117 (245) | +0.0134 (245) | +0.0259 (245) |
| **chiko_hi** | high | +0.0043 (577) | +0.0060 (576) | +0.0071 (576) | +0.0134 (577) |
|            | mid  | +0.0116 (668) | +0.0172 (668) | +0.0139 (668) | +0.0183 (668) |
|            | low  | +0.0071 (356) | +0.0019 (355) | +0.0156 (355) | +0.0302 (356) |
| **chiko_lo** | high | +0.0187 (595) | +0.0213 (595) | +0.0160 (594) | +0.0313 (595) |
|            | mid  | +0.0111 (488) | +0.0162 (487) | +0.0184 (487) | +0.0220 (487) |
|            | low  | +0.0142 (240) | +0.0132 (239) | +0.0228 (239) | +0.0211 (240) |
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

Generated: 2026-05-18  
Training: FY2018–FY2024 regime ranking (Ichimoku Kumo × ADX veto)  
Test: FY2025 · classified2024 · 2025-04-01 – 2026-03-31  
Ranking cells: 59 (sign × kumo_state, min_n=30)  

### Regime Cell Detail (sign × kumo_state)

Kumo states: ▲above cloud (+1) · ~inside (0) · ▼below cloud (−1)  
Δ DR = test cell DR − sign-level baseline DR (all events for that sign).

| Sign | kumo | train_bench_flw | train_DR | train_n | test_n | test_DR | Δ DR |
|------|------|-----------------|----------|---------|--------|---------|------|
| div_peer   | ~inside | 0.0654 | 67.0% |      88 |      0 |       — | —      |
| div_peer   | ▼below  | 0.0584 | 57.6% |     172 |      0 |       — | —      |
| brk_kumo_hi | ~inside | 0.0577 | 59.0% |     407 |      0 |       — | —      |
| rev_nlo    | ▼below  | 0.0574 | 62.6% |    1170 |      0 |       — | —      |
| brk_kumo_lo | ▼below  | 0.0573 | 56.3% |    1551 |      0 |       — | —      |
| brk_floor  | ▼below  | 0.0533 | 59.1% |    1173 |      0 |       — | —      |
| chiko_hi   | ~inside | 0.0523 | 60.2% |     777 |      0 |       — | —      |
| brk_sma    | ▼below  | 0.0522 | 56.9% |     311 |      0 |       — | —      |
| brk_sma    | ▲above  | 0.0520 | 51.5% |     809 |      0 |       — | —      |
| str_hold   | ~inside | 0.0520 | 59.8% |    1616 |      0 |       — | —      |
| chiko_lo   | ▼below  | 0.0519 | 55.0% |    1623 |      0 |       — | —      |
| brk_sma    | ~inside | 0.0518 | 51.4% |     181 |      0 |       — | —      |
| brk_tenkan_lo | ▼below  | 0.0517 | 54.8% |    5288 |      0 |       — | —      |
| div_peer   | ▲above  | 0.0509 | 52.7% |     372 |      0 |       — | —      |
| brk_bol    | ▼below  | 0.0503 | 52.5% |     444 |      0 |       — | —      |
| div_gap    | ▼below  | 0.0499 | 56.9% |    1109 |      0 |       — | —      |
| corr_shift | ~inside | 0.0496 | 58.7% |     109 |      0 |       — | —      |
| div_gap    | ▲above  | 0.0492 | 56.7% |    1241 |      0 |       — | —      |
| brk_wall   | ~inside | 0.0492 | 62.0% |     368 |     10 |   80.0% | +20.4% |
| str_hold   | ▼below  | 0.0488 | 56.9% |    4936 |      0 |       — | —      |
| corr_flip  | ▼below  | 0.0478 | 47.4% |     116 |      0 |       — | —      |
| rev_nlo    | ~inside | 0.0476 | 42.8% |     615 |      0 |       — | —      |
| brk_bol    | ▲above  | 0.0474 | 52.3% |    1611 |      0 |       — | —      |
| brk_tenkan_hi | ▼below  | 0.0473 | 57.0% |    4447 |      0 |       — | —      |
| chiko_lo   | ~inside | 0.0470 | 59.6% |     713 |      0 |       — | —      |
| chiko_lo   | ▲above  | 0.0468 | 53.9% |    2115 |      0 |       — | —      |
| rev_lo     | ▼below  | 0.0467 | 55.7% |    2017 |      0 |       — | —      |
| rev_nhi    | ~inside | 0.0464 | 56.2% |     979 |      0 |       — | —      |
| div_gap    | ~inside | 0.0463 | 53.8% |     407 |      0 |       — | —      |
| brk_tenkan_lo | ▲above  | 0.0463 | 53.3% |    8871 |      0 |       — | —      |
| brk_bol    | ~inside | 0.0462 | 51.9% |     270 |      0 |       — | —      |
| brk_tenkan_lo | ~inside | 0.0462 | 56.5% |    2687 |      0 |       — | —      |
| brk_tenkan_hi | ~inside | 0.0459 | 53.6% |    2385 |      0 |       — | —      |
| corr_shift | ▲above  | 0.0457 | 51.5% |     571 |      0 |       — | —      |
| rev_nhold  | ▼below  | 0.0455 | 63.3% |      90 |      0 |       — | —      |
| rev_lo     | ~inside | 0.0455 | 57.7% |     955 |      0 |       — | —      |
| str_lead   | ▼below  | 0.0447 | 62.8% |     479 |      0 |       — | —      |
| str_lag    | ▲above  | 0.0446 | 50.3% |    1546 |      0 |       — | —      |
| rev_nhi    | ▲above  | 0.0444 | 52.0% |    6994 |      0 |       — | —      |
| rev_hi     | ~inside | 0.0443 | 57.2% |     842 |      0 |       — | —      |
| brk_kumo_lo | ▲above  | 0.0438 | 52.8% |    1934 |      0 |       — | —      |
| brk_kumo_hi | ▼below  | 0.0437 | 52.5% |     968 |      0 |       — | —      |
| rev_lo     | ▲above  | 0.0437 | 52.9% |    3342 |      0 |       — | —      |
| brk_tenkan_hi | ▲above  | 0.0427 | 49.8% |   11054 |      0 |       — | —      |
| brk_kumo_lo | ~inside | 0.0426 | 56.7% |     397 |      0 |       — | —      |
| rev_hi     | ▼below  | 0.0425 | 55.5% |    1790 |      0 |       — | —      |
| str_lag    | ▼below  | 0.0421 | 49.1% |     389 |      0 |       — | —      |
| brk_wall   | ▼below  | 0.0421 | 55.1% |     700 |      0 |       — | —      |
| rev_nhi    | ▼below  | 0.0406 | 50.6% |    1980 |      0 |       — | —      |
| chiko_hi   | ▼below  | 0.0405 | 50.9% |     970 |      0 |       — | —      |
| brk_floor  | ▲above  | 0.0405 | 50.0% |    1010 |      0 |       — | —      |
| chiko_hi   | ▲above  | 0.0403 | 49.0% |    3374 |      0 |       — | —      |
| corr_flip  | ▲above  | 0.0402 | 53.5% |     303 |      0 |       — | —      |
| brk_kumo_hi | ▲above  | 0.0395 | 48.5% |    2822 |      0 |       — | —      |
| brk_wall   | ▲above  | 0.0382 | 49.8% |    2199 |    995 |   59.4% | -0.2%  |
| brk_floor  | ~inside | 0.0375 | 56.6% |     311 |      0 |       — | —      |
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
| brk_wall   |    1005 |    59.6% |     1005 |     59.6% |  +0.0% |      100% |
| brk_floor  | 0 | — | — | — | — | — |
| brk_kumo_hi | 0 | — | — | — | — | — |
| brk_kumo_lo | 0 | — | — | — | — | — |
| brk_tenkan_hi | 0 | — | — | — | — | — |
| brk_tenkan_lo | 0 | — | — | — | — | — |
| chiko_hi   | 0 | — | — | — | — | — |
| chiko_lo   | 0 | — | — | — | — | — |
| rev_lo     | 0 | — | — | — | — | — |
| rev_hi     | 0 | — | — | — | — | — |
| rev_nhi    | 0 | — | — | — | — | — |
| rev_nlo    | 0 | — | — | — | — | — |
| rev_nhold  | 0 | — | — | — | — | — |

**Interpretation**: Positive Δ DR means the Kumo+ADX regime filter selected
events with better follow-through outcomes in the out-of-sample year.
Low regime_n% indicates the filter is aggressive; verify test_n is large enough.
## Strategy A/B: brk_wall on/off

Probe run: 2026-05-19.  Walk-forward regime-sign strategy backtest run twice: once WITH brk_wall in the sign set (current shipped state) and once WITHOUT (the state before commit 52bde03).  Same min_dr=0.52 threshold, same ZsTpSl exit, same portfolio cap (≤1 high-corr, ≤3 low/mid-corr).

### Per-FY summary

| FY | with: trades / mean_r / Sharpe / win% | without: trades / mean_r / Sharpe / win% | Δ Sharpe | Δ mean_r |
|----|---|---|---:|---:|
| FY2019 | 0 / — / — / — | 0 / — / — / — | **—** | **—** |
| FY2020 | 0 / — / — / — | 0 / — / — / — | **—** | **—** |
| FY2021 | 31 / -0.68% / -1.06 / 42% | 31 / -0.68% / -1.06 / 42% | **+0.00** | **+0.00pp** |
| FY2022 | 31 / +1.37% / +1.73 / 58% | 31 / +1.37% / +1.73 / 58% | **+0.00** | **+0.00pp** |
| FY2023 | 38 / +3.64% / +6.91 / 76% | 38 / +3.64% / +6.91 / 76% | **+0.00** | **+0.00pp** |
| FY2024 | 36 / +1.05% / +1.44 / 44% | 36 / +1.05% / +1.44 / 44% | **+0.00** | **+0.00pp** |
| FY2025 | 35 / +3.23% / +5.16 / 63% | 35 / +3.23% / +5.16 / 63% | **+0.00** | **+0.00pp** |

### Aggregate (FY-equal-weighted)

- WITH brk_wall:    total trades = 171, avg Sharpe = +2.83, avg mean_r = +1.72%
- WITHOUT brk_wall: total trades = 171, avg Sharpe = +2.83, avg mean_r = +1.72%

- **Δ Sharpe = +0.00** ; **Δ mean_r = +0.00pp**

### Verdict

**brk_wall is roughly neutral on aggregate Sharpe** (|Δ| 0.00 ≤ 0.10).  Sign is harmless but not load-bearing for live strategy performance.  Keep for informational value; don't expect it to shift Sharpe materially.

### Sortino + EV decomposition (added 2026-05-18)

EV = P(win)·E[win] + P(loss)·E[loss].  EV check should ≈ mean_r.  Sortino penalizes only downside variance.

| arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |
|-----|---:|---:|---:|---:|---:|---:|
| WITH brk_wall | +2.83 | **+5.70** | 56.7% | +9.37% | -8.17% | +1.78% |
| WITHOUT brk_wall | +2.83 | **+5.70** | 56.7% | +9.37% | -8.17% | +1.78% |

### Marginal contribution (WITH brk_wall vs WITHOUT)

### Marginal contribution (added 2026-05-18)

Comparing **WITH brk_wall** against **WITHOUT brk_wall** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+0** | WITH brk_wall − WITHOUT brk_wall (turnover impact) |
| WITHOUT brk_wall max drawdown | +74.62% | peak-to-trough on cumulative trade returns |
| WITH brk_wall max drawdown | +74.62% | same metric, expanded arm |
| Δ drawdown | +0.00% | + = drawdown got WORSE under WITH brk_wall |
| Daily-return correlation | **+1.000** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| WITHOUT brk_wall's worst-quintile day mean | -13.07% | A's bad days |
| WITH brk_wall on those same days | -13.07% | does new sign help when A loses? |
| Tail-hedge lift | **+0.00%** | + = WITH brk_wall cushions WITHOUT brk_wall's tail |
| New-trade count (B-only) | 0 | trades introduced by the change |
| New-trade win rate | — | quality of the marginal trades |
## Leave-one-out sweep (regime_sign 2026-05-19)

Probe run: 2026-05-19.  Tests whether removing any individually-negative-Sharpe sign from the regime_sign ranking improves the strategy.  Same min_dr=0.52, same ZsTpSl(2.0,2.0,0.3) exit, same portfolio cap as production.

**Candidates** (selected from aggregate per-sign breakdown in regime_sign_backtest.md, all negative aggregate Sharpe over FY2019-FY2024):

| sign | prior aggregate n | prior Sharpe |
|---|---:|---:|
| rev_nhi    | 11 | −6.75 |
| corr_shift | 17 | −3.36 |
| div_peer   | 11 | −1.86 |
| str_lag    | 11 | −0.84 |

## Aggregate (FY-equal-weighted)

| arm | n | Sharpe | Sortino | mean_r | win% | avg_win | avg_loss |
|-----|---:|---:|---:|---:|---:|---:|---:|
| baseline | 171 | **+2.83** | **+5.70** | +1.72% | 56.7% | +9.37% | -8.17% |
| −rev_nhi | 170 | **+2.91** | **+5.97** | +1.77% | 55.3% | +9.41% | -7.68% |
| −corr_shift | 175 | **+3.45** | **+6.95** | +2.03% | 57.0% | +9.83% | -7.93% |
| −div_peer | 169 | **+3.12** | **+6.22** | +1.87% | 60.1% | +8.65% | -8.09% |
| −str_lag | 169 | **+3.22** | **+6.38** | +1.99% | 57.3% | +9.74% | -8.16% |

### Aggregate deltas vs baseline

| arm | ΔSharpe | ΔSortino | ΔmeanR | Δn_trades |
|-----|---:|---:|---:|---:|
| −rev_nhi | **+0.07** | **+0.27** | **+0.05%** | -1 |
| −corr_shift | **+0.62** | **+1.25** | **+0.30%** | +4 |
| −div_peer | **+0.28** | **+0.52** | **+0.15%** | -2 |
| −str_lag | **+0.38** | **+0.68** | **+0.26%** | -2 |

### Drop `rev_nhi`

#### − rev_nhi — per-FY

| FY | base n | base Sh | arm n | arm Sh | ΔSh | ΔmeanR |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 0 | — | 0 | — | **—** | — |
| FY2020 | 0 | — | 0 | — | **—** | — |
| FY2021 | 31 | -1.06 | 30 | -2.34 | **-1.29** | -0.69% |
| FY2022 | 31 | +1.73 | 33 | +2.65 | **+0.93** | +0.78% |
| FY2023 | 38 | +6.91 | 36 | +8.26 | **+1.35** | +0.39% |
| FY2024 | 36 | +1.44 | 36 | +1.44 | **+0.00** | +0.00% |
| FY2025 | 35 | +5.16 | 35 | +4.53 | **-0.63** | -0.26% |

**Verdict for `−rev_nhi`**: **REJECT**

Pre-registered gate:
- Δ Sharpe (FY-equal-weighted) = +0.07 (✗ ≥ +0.30)
- Δ Sortino                    = +0.27 (✗ ≥ +0.50)
- FYs with non-negative ΔSharpe = 3/5 (✗ ≥ 5)
- FY2024 + FY2025 both non-negative = ✗

#### Marginal contribution: baseline → −rev_nhi

### Marginal contribution (added 2026-05-18)

Comparing **−rev_nhi** against **baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **-1** | −rev_nhi − baseline (turnover impact) |
| baseline max drawdown | +74.62% | peak-to-trough on cumulative trade returns |
| −rev_nhi max drawdown | +76.42% | same metric, expanded arm |
| Δ drawdown | +1.80% | + = drawdown got WORSE under −rev_nhi |
| Daily-return correlation | **+0.852** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| baseline's worst-quintile day mean | -13.07% | A's bad days |
| −rev_nhi on those same days | -10.76% | does new sign help when A loses? |
| Tail-hedge lift | **+2.30%** | + = −rev_nhi cushions baseline's tail |
| New-trade count (B-only) | 27 | trades introduced by the change |
| New-trade win rate | 51.9% | quality of the marginal trades |


### Drop `corr_shift`

#### − corr_shift — per-FY

| FY | base n | base Sh | arm n | arm Sh | ΔSh | ΔmeanR |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 0 | — | 0 | — | **—** | — |
| FY2020 | 0 | — | 0 | — | **—** | — |
| FY2021 | 31 | -1.06 | 32 | +0.55 | **+1.61** | +1.06% |
| FY2022 | 31 | +1.73 | 33 | +0.81 | **-0.92** | -0.78% |
| FY2023 | 38 | +6.91 | 38 | +8.46 | **+1.55** | +0.65% |
| FY2024 | 36 | +1.44 | 36 | +1.44 | **+0.00** | +0.00% |
| FY2025 | 35 | +5.16 | 36 | +6.02 | **+0.86** | +0.58% |

**Verdict for `−corr_shift`**: **REJECT**

Pre-registered gate:
- Δ Sharpe (FY-equal-weighted) = +0.62 (✓ ≥ +0.30)
- Δ Sortino                    = +1.25 (✓ ≥ +0.50)
- FYs with non-negative ΔSharpe = 4/5 (✗ ≥ 5)
- FY2024 + FY2025 both non-negative = ✓

#### Marginal contribution: baseline → −corr_shift

### Marginal contribution (added 2026-05-18)

Comparing **−corr_shift** against **baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **+4** | −corr_shift − baseline (turnover impact) |
| baseline max drawdown | +74.62% | peak-to-trough on cumulative trade returns |
| −corr_shift max drawdown | +57.80% | same metric, expanded arm |
| Δ drawdown | -16.82% | + = drawdown got WORSE under −corr_shift |
| Daily-return correlation | **+0.814** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| baseline's worst-quintile day mean | -13.07% | A's bad days |
| −corr_shift on those same days | -9.27% | does new sign help when A loses? |
| Tail-hedge lift | **+3.79%** | + = −corr_shift cushions baseline's tail |
| New-trade count (B-only) | 43 | trades introduced by the change |
| New-trade win rate | 55.8% | quality of the marginal trades |


### Drop `div_peer`

#### − div_peer — per-FY

| FY | base n | base Sh | arm n | arm Sh | ΔSh | ΔmeanR |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 0 | — | 0 | — | **—** | — |
| FY2020 | 0 | — | 0 | — | **—** | — |
| FY2021 | 31 | -1.06 | 31 | -1.27 | **-0.21** | -0.11% |
| FY2022 | 31 | +1.73 | 29 | +2.11 | **+0.38** | +0.23% |
| FY2023 | 38 | +6.91 | 36 | +7.43 | **+0.52** | -0.01% |
| FY2024 | 36 | +1.44 | 38 | +2.17 | **+0.73** | +0.63% |
| FY2025 | 35 | +5.16 | 35 | +5.16 | **+0.00** | +0.00% |

**Verdict for `−div_peer`**: **REJECT**

Pre-registered gate:
- Δ Sharpe (FY-equal-weighted) = +0.28 (✗ ≥ +0.30)
- Δ Sortino                    = +0.52 (✓ ≥ +0.50)
- FYs with non-negative ΔSharpe = 4/5 (✗ ≥ 5)
- FY2024 + FY2025 both non-negative = ✓

#### Marginal contribution: baseline → −div_peer

### Marginal contribution (added 2026-05-18)

Comparing **−div_peer** against **baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **-2** | −div_peer − baseline (turnover impact) |
| baseline max drawdown | +74.62% | peak-to-trough on cumulative trade returns |
| −div_peer max drawdown | +63.70% | same metric, expanded arm |
| Δ drawdown | -10.92% | + = drawdown got WORSE under −div_peer |
| Daily-return correlation | **+0.754** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| baseline's worst-quintile day mean | -13.07% | A's bad days |
| −div_peer on those same days | -9.43% | does new sign help when A loses? |
| Tail-hedge lift | **+3.64%** | + = −div_peer cushions baseline's tail |
| New-trade count (B-only) | 46 | trades introduced by the change |
| New-trade win rate | 60.9% | quality of the marginal trades |


### Drop `str_lag`

#### − str_lag — per-FY

| FY | base n | base Sh | arm n | arm Sh | ΔSh | ΔmeanR |
|----|---:|---:|---:|---:|---:|---:|
| FY2019 | 0 | — | 0 | — | **—** | — |
| FY2020 | 0 | — | 0 | — | **—** | — |
| FY2021 | 31 | -1.06 | 30 | +1.05 | **+2.10** | +1.42% |
| FY2022 | 31 | +1.73 | 31 | +1.73 | **+0.00** | +0.00% |
| FY2023 | 38 | +6.91 | 38 | +6.91 | **+0.00** | +0.00% |
| FY2024 | 36 | +1.44 | 35 | +1.25 | **-0.19** | -0.09% |
| FY2025 | 35 | +5.16 | 35 | +5.16 | **+0.00** | +0.00% |

**Verdict for `−str_lag`**: **REJECT**

Pre-registered gate:
- Δ Sharpe (FY-equal-weighted) = +0.38 (✓ ≥ +0.30)
- Δ Sortino                    = +0.68 (✓ ≥ +0.50)
- FYs with non-negative ΔSharpe = 4/5 (✗ ≥ 5)
- FY2024 + FY2025 both non-negative = ✗

#### Marginal contribution: baseline → −str_lag

### Marginal contribution (added 2026-05-18)

Comparing **−str_lag** against **baseline** at the per-trade level.

| Metric | Value | Interpretation |
|--------|------:|----------------|
| Δ trade count | **-2** | −str_lag − baseline (turnover impact) |
| baseline max drawdown | +74.62% | peak-to-trough on cumulative trade returns |
| −str_lag max drawdown | +53.28% | same metric, expanded arm |
| Δ drawdown | -21.34% | + = drawdown got WORSE under −str_lag |
| Daily-return correlation | **+0.886** | A vs B per-day returns.  High (>0.7) = same bets; low (<0.3) = real diversification |
| baseline's worst-quintile day mean | -13.07% | A's bad days |
| −str_lag on those same days | -10.91% | does new sign help when A loses? |
| Tail-hedge lift | **+2.16%** | + = −str_lag cushions baseline's tail |
| New-trade count (B-only) | 14 | trades introduced by the change |
| New-trade win rate | 42.9% | quality of the marginal trades |

