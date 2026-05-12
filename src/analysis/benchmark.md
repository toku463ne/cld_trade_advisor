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

Generated: 2026-05-10  
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
| brk_sma    |   165 |  34.5% | <0.001  |    0.0204 |    0.1017 |    14.600 | <0.001  | 165 (×1.0) |    34.5% | —             | —             |
| brk_bol    |   404 |  45.5% | ≈0.073  |    0.0312 |    0.0482 |    12.881 | <0.001  | 404 (×1.0) |    45.5% | —             | —             |
| rev_lo     |  1188 |  41.1% | <0.001  |    0.0277 |    0.0661 |    13.009 | <0.001  | 966 (×1.2) |    41.6% | —             | —             |
| rev_hi     |  1330 |  50.5% | ≈0.701  |    0.0352 |    0.0433 |    12.947 | <0.001  | 1119 (×1.2) |    51.2% | —             | —             |
| rev_nhi    |  1993 |  45.4% | <0.001  |    0.0287 |    0.0372 |    12.434 | <0.001  | 1522 (×1.3) |    45.5% | —             | —             |
| rev_nlo    |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2020 (2020-04-01 – 2021-03-31) · cluster=classified2019

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   445 |  59.6% | <0.001  |    0.0658 |    0.0264 |    12.663 | <0.001  | 417 (×1.1) |    59.5% | 65.4% (<0.001) | 46.5% (≈0.428) |
| div_peer   |    77 |  62.3% | ≈0.030  |    0.0809 |    0.0264 |    13.273 | <0.001  |  66 (×1.2) |    59.1% | 66.7% (≈0.083) | 64.5% (≈0.106) |
| corr_flip  |    58 |  56.9% | ≈0.294  |    0.0595 |    0.0317 |    13.259 | <0.001  |  58 (×1.0) |    56.9% | 53.3% (≈0.796) | 60.0% (≈0.237) |
| corr_shift |   113 |  58.4% | ≈0.074  |    0.0672 |    0.0279 |    12.929 | <0.001  | 112 (×1.0) |    58.0% | 65.2% (≈0.039) | 53.8% (≈0.535) |
| str_hold   |   800 |  69.5% | <0.001  |    0.0771 |    0.0180 |    11.971 | <0.001  | 520 (×1.5) |    68.7% | 70.0% (<0.001) | —             |
| str_lead   |   104 |  32.7% | <0.001  |    0.0245 |    0.0295 |    10.490 | <0.001  | 104 (×1.0) |    32.7% | —             | 32.7% (<0.001) |
| str_lag    |   296 |  60.8% | <0.001  |    0.0605 |    0.0268 |    12.358 | <0.001  | 295 (×1.0) |    61.0% | —             | 59.6% (≈0.001) |
| brk_sma    |   228 |  55.3% | ≈0.112  |    0.0686 |    0.0319 |    13.096 | <0.001  | 228 (×1.0) |    55.3% | 65.4% (≈0.002) | 44.1% (≈0.217) |
| brk_bol    |   568 |  60.9% | <0.001  |    0.0666 |    0.0287 |    12.819 | <0.001  | 568 (×1.0) |    60.9% | 70.2% (<0.001) | 53.7% (≈0.194) |
| rev_lo     |   946 |  56.9% | <0.001  |    0.0574 |    0.0293 |    12.723 | <0.001  | 781 (×1.2) |    57.6% | 65.0% (<0.001) | 49.4% (≈0.796) |
| rev_hi     |  1168 |  51.6% | ≈0.266  |    0.0522 |    0.0318 |    12.802 | <0.001  | 978 (×1.2) |    52.8% | 59.5% (<0.001) | 44.7% (≈0.007) |
| rev_nhi    |  2496 |  53.9% | <0.001  |    0.0578 |    0.0342 |    12.796 | <0.001  | 1903 (×1.3) |    53.8% | 61.6% (<0.001) | 53.2% (≈0.018) |
| rev_nlo    |   501 |  27.9% | <0.001  |    0.0241 |    0.0412 |    11.583 | <0.001  | 501 (×1.0) |    27.9% | —             | 27.9% (<0.001) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2021 (2021-04-01 – 2022-03-31) · cluster=classified2020

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   635 |  52.1% | ≈0.284  |    0.0462 |    0.0363 |    12.643 | <0.001  | 604 (×1.1) |    51.7% | 45.7% (≈0.257) | 54.0% (≈0.090) |
| div_peer   |   146 |  49.3% | ≈0.869  |    0.0477 |    0.0441 |    12.342 | <0.001  | 137 (×1.1) |    49.6% | 59.3% (≈0.152) | 40.8% (≈0.123) |
| corr_flip  |    41 |  56.1% | ≈0.435  |    0.0362 |    0.0344 |    11.683 | <0.001  |  41 (×1.0) |    56.1% | 55.6% (≈0.637) | 56.5% (≈0.532) |
| corr_shift |   101 |  50.5% | ≈0.921  |    0.0385 |    0.0393 |    12.990 | <0.001  | 100 (×1.0) |    50.0% | 60.0% (≈0.206) | 44.3% (≈0.370) |
| str_hold   |  2082 |  58.2% | <0.001  |    0.0504 |    0.0303 |    12.031 | <0.001  | 1268 (×1.6) |    55.8% | 58.5% (<0.001) | 56.6% (≈0.011) |
| str_lead   |   289 |  47.1% | ≈0.317  |    0.0307 |    0.0301 |    12.405 | <0.001  | 289 (×1.0) |    47.1% | —             | 47.1% (≈0.317) |
| str_lag    |   440 |  43.0% | ≈0.003  |    0.0309 |    0.0486 |    12.395 | <0.001  | 438 (×1.0) |    43.2% | —             | 39.4% (<0.001) |
| brk_sma    |   181 |  50.8% | ≈0.824  |    0.0442 |    0.0422 |    12.370 | <0.001  | 181 (×1.0) |    50.8% | 57.6% (≈0.218) | 47.4% (≈0.574) |
| brk_bol    |   356 |  51.7% | ≈0.525  |    0.0407 |    0.0423 |    12.272 | <0.001  | 356 (×1.0) |    51.7% | 58.8% (≈0.061) | 48.3% (≈0.606) |
| rev_lo     |  1166 |  46.9% | ≈0.035  |    0.0353 |    0.0411 |    12.165 | <0.001  | 959 (×1.2) |    46.9% | 47.4% (≈0.227) | 46.8% (≈0.117) |
| rev_hi     |  1228 |  50.3% | ≈0.819  |    0.0352 |    0.0372 |    12.378 | <0.001  | 1015 (×1.2) |    50.7% | 50.5% (≈0.798) | 50.3% (≈0.877) |
| rev_nhi    |  1815 |  46.6% | ≈0.003  |    0.0353 |    0.0443 |    12.515 | <0.001  | 1443 (×1.3) |    46.2% | 50.8% (≈0.728) | 44.8% (<0.001) |
| rev_nlo    |   647 |  51.9% | ≈0.326  |    0.0479 |    0.0311 |    13.328 | <0.001  | 647 (×1.0) |    51.9% | —             | 51.9% (≈0.326) |
| rev_nhold  |   109 |  45.9% | ≈0.389  |    0.0268 |    0.0317 |    11.661 | <0.001  | 109 (×1.0) |    45.9% | —             | 45.9% (≈0.389) |

#### FY2022 (2022-04-01 – 2023-03-31) · cluster=classified2021

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   550 |  55.5% | ≈0.011  |    0.0430 |    0.0277 |    12.600 | <0.001  | 524 (×1.0) |    55.5% | 55.1% (≈0.177) | 54.7% (≈0.094) |
| div_peer   |   169 |  55.0% | ≈0.191  |    0.0480 |    0.0274 |    12.450 | <0.001  | 145 (×1.2) |    56.6% | 41.5% (≈0.216) | 59.4% (≈0.052) |
| corr_flip  |   139 |  35.3% | <0.001  |    0.0219 |    0.0403 |    12.201 | <0.001  | 139 (×1.0) |    35.3% | 50.8% (≈0.896) | 23.8% (<0.001) |
| corr_shift |   139 |  55.4% | ≈0.203  |    0.0400 |    0.0299 |    12.518 | <0.001  | 137 (×1.0) |    55.5% | 57.4% (≈0.249) | 53.8% (≈0.497) |
| str_hold   |  2257 |  49.9% | ≈0.916  |    0.0375 |    0.0298 |    12.608 | <0.001  | 1287 (×1.8) |    50.8% | 49.0% (≈0.464) | 49.2% (≈0.617) |
| str_lead   |   235 |  36.2% | <0.001  |    0.0219 |    0.0353 |    11.094 | <0.001  | 235 (×1.0) |    36.2% | —             | 36.2% (<0.001) |
| str_lag    |   374 |  57.0% | ≈0.007  |    0.0369 |    0.0301 |    11.206 | <0.001  | 374 (×1.0) |    57.0% | —             | 57.0% (≈0.007) |
| brk_sma    |   174 |  58.0% | ≈0.034  |    0.0462 |    0.0287 |    12.529 | <0.001  | 174 (×1.0) |    58.0% | 50.0% (≈1.000) | 65.2% (≈0.004) |
| brk_bol    |   372 |  52.2% | ≈0.407  |    0.0397 |    0.0350 |    12.481 | <0.001  | 372 (×1.0) |    52.2% | 54.4% (≈0.349) | 51.2% (≈0.709) |
| rev_lo     |  1307 |  50.0% | ≈0.978  |    0.0358 |    0.0281 |    12.492 | <0.001  | 1070 (×1.2) |    50.1% | 46.1% (≈0.052) | 53.4% (≈0.075) |
| rev_hi     |  1381 |  47.6% | ≈0.071  |    0.0310 |    0.0310 |    12.008 | <0.001  | 1115 (×1.2) |    49.3% | 48.8% (≈0.592) | 46.9% (≈0.064) |
| rev_nhi    |  1987 |  50.6% | ≈0.606  |    0.0355 |    0.0328 |    12.165 | <0.001  | 1545 (×1.3) |    49.8% | 54.6% (≈0.039) | 49.2% (≈0.551) |
| rev_nlo    |   594 |  50.2% | ≈0.935  |    0.0446 |    0.0327 |    12.098 | <0.001  | 594 (×1.0) |    50.2% | —             | 50.2% (≈0.935) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2023 (2023-04-01 – 2024-03-31) · cluster=classified2022

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   648 |  55.9% | ≈0.003  |    0.0508 |    0.0256 |    12.267 | <0.001  | 605 (×1.1) |    56.0% | 55.4% (≈0.054) | 55.7% (≈0.040) |
| div_peer   |   167 |  57.5% | ≈0.053  |    0.0589 |    0.0236 |    13.269 | <0.001  | 151 (×1.1) |    57.6% | 56.6% (≈0.336) | 59.0% (≈0.064) |
| corr_flip  |   124 |  61.3% | ≈0.012  |    0.0475 |    0.0242 |    13.000 | <0.001  | 124 (×1.0) |    61.3% | 58.2% (≈0.225) | 63.2% (≈0.029) |
| corr_shift |   185 |  55.7% | ≈0.123  |    0.0469 |    0.0310 |    12.832 | <0.001  | 184 (×1.0) |    56.0% | 50.0% (≈1.000) | 58.7% (≈0.078) |
| str_hold   |  1581 |  49.8% | ≈0.860  |    0.0403 |    0.0299 |    12.103 | <0.001  | 855 (×1.8) |    50.4% | 48.9% (≈0.458) | 52.0% (≈0.393) |
| str_lead   |   150 |  54.7% | ≈0.253  |    0.0409 |    0.0273 |    11.480 | <0.001  | 150 (×1.0) |    54.7% | —             | 54.7% (≈0.253) |
| str_lag    |   411 |  54.0% | ≈0.104  |    0.0590 |    0.0234 |    12.895 | <0.001  | 408 (×1.0) |    53.7% | 30.2% (≈0.002) | 58.3% (≈0.002) |
| brk_sma    |   166 |  53.6% | ≈0.352  |    0.0496 |    0.0320 |    12.645 | <0.001  | 166 (×1.0) |    53.6% | 57.5% (≈0.163) | 48.5% (≈0.808) |
| brk_bol    |   497 |  53.7% | ≈0.097  |    0.0519 |    0.0283 |    13.068 | <0.001  | 497 (×1.0) |    53.7% | 53.2% (≈0.379) | 51.5% (≈0.629) |
| rev_lo     |  1266 |  60.1% | <0.001  |    0.0516 |    0.0248 |    12.529 | <0.001  | 1022 (×1.2) |    59.8% | 57.7% (<0.001) | 62.0% (<0.001) |
| rev_hi     |  1306 |  55.7% | <0.001  |    0.0465 |    0.0266 |    12.497 | <0.001  | 1069 (×1.2) |    55.9% | 54.1% (≈0.074) | 56.2% (<0.001) |
| rev_nhi    |  2732 |  55.8% | <0.001  |    0.0485 |    0.0263 |    12.662 | <0.001  | 2053 (×1.3) |    57.1% | 50.6% (≈0.762) | 56.7% (<0.001) |
| rev_nlo    |   493 |  50.3% | ≈0.893  |    0.0377 |    0.0385 |    11.566 | <0.001  | 493 (×1.0) |    50.3% | —             | 50.3% (≈0.893) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

#### FY2024 (2024-04-01 – 2025-03-31) · cluster=classified2023

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   615 |  58.7% | <0.001  |    0.0471 |    0.0320 |    12.779 | <0.001  | 572 (×1.1) |    58.0% | 68.6% (<0.001) | 47.6% (≈0.433) |
| div_peer   |    98 |  58.2% | ≈0.106  |    0.0540 |    0.0396 |    12.663 | <0.001  |  86 (×1.1) |    62.8% | 54.5% (≈0.546) | 67.3% (≈0.015) |
| corr_flip  |   145 |  52.4% | ≈0.561  |    0.0494 |    0.0292 |    12.241 | <0.001  | 145 (×1.0) |    52.4% | 54.9% (≈0.345) | 48.1% (≈0.785) |
| corr_shift |   212 |  41.0% | ≈0.009  |    0.0371 |    0.0530 |    11.887 | <0.001  | 207 (×1.0) |    40.1% | 41.3% (≈0.078) | 40.7% (≈0.054) |
| str_hold   |  2858 |  57.4% | <0.001  |    0.0465 |    0.0398 |    11.841 | <0.001  | 1498 (×1.9) |    60.3% | 56.7% (<0.001) | 57.8% (≈0.004) |
| str_lead   |   258 |  58.1% | ≈0.009  |    0.0481 |    0.0245 |    11.624 | <0.001  | 258 (×1.0) |    58.1% | —             | 58.1% (≈0.009) |
| str_lag    |   623 |  47.0% | ≈0.138  |    0.0352 |    0.0332 |    12.751 | <0.001  | 621 (×1.0) |    46.9% | —             | 47.0% (≈0.138) |
| brk_sma    |   243 |  54.7% | ≈0.140  |    0.0558 |    0.0342 |    12.827 | <0.001  | 243 (×1.0) |    54.7% | 57.3% (≈0.127) | 52.6% (≈0.544) |
| brk_bol    |   411 |  44.0% | ≈0.016  |    0.0411 |    0.0486 |    12.380 | <0.001  | 411 (×1.0) |    44.0% | 57.7% (≈0.079) | 37.7% (<0.001) |
| rev_lo     |  1293 |  56.4% | <0.001  |    0.0440 |    0.0307 |    12.468 | <0.001  | 1071 (×1.2) |    55.7% | 58.0% (<0.001) | 54.6% (≈0.019) |
| rev_hi     |  1391 |  48.6% | ≈0.296  |    0.0343 |    0.0407 |    12.145 | <0.001  | 1120 (×1.2) |    48.5% | 53.5% (≈0.102) | 45.3% (≈0.007) |
| rev_nhi    |  1649 |  49.4% | ≈0.605  |    0.0396 |    0.0455 |    12.466 | <0.001  | 1323 (×1.2) |    49.0% | 51.7% (≈0.460) | 48.4% (≈0.281) |
| rev_nlo    |   615 |  55.9% | ≈0.003  |    0.0591 |    0.0274 |    11.990 | <0.001  | 615 (×1.0) |    55.9% | —             | 55.9% (≈0.003) |
| rev_nhold  |    10 |  90.0% | ≈0.011  |    0.1295 |    0.0033 |    13.200 | <0.001  |  10 (×1.0) |    90.0% | —             | 90.0% (≈0.011) |

#### FY2025 (2025-04-01 – 2026-03-31) · cluster=classified2024

| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |
|------|---|-----|---|-----------|-----------|----------|--------|------------|----------|-------------|-------------|
| div_gap    |   830 |  50.6% | ≈0.729  |    0.0521 |    0.0359 |    12.552 | <0.001  | 756 (×1.1) |    51.5% | 46.7% (≈0.293) | 51.0% (≈0.663) |
| div_peer   |    22 |  68.2% | ≈0.088  |    0.0746 |    0.0122 |    11.364 | <0.001  |  21 (×1.0) |    66.7% | 100.0% (≈0.008) | 54.5% (≈0.763) |
| corr_flip  |   242 |  45.9% | ≈0.199  |    0.0374 |    0.0371 |    12.541 | <0.001  | 242 (×1.0) |    45.9% | 50.6% (≈0.915) | 43.2% (≈0.092) |
| corr_shift |   297 |  47.5% | ≈0.384  |    0.0399 |    0.0332 |    12.414 | <0.001  | 293 (×1.0) |    47.8% | 46.5% (≈0.428) | 45.3% (≈0.270) |
| str_hold   |  2010 |  51.3% | ≈0.228  |    0.0524 |    0.0213 |    12.448 | <0.001  | 1216 (×1.7) |    51.6% | 46.7% (≈0.022) | 57.5% (<0.001) |
| str_lead   |    66 |  65.2% | ≈0.014  |    0.0583 |    0.0213 |    13.697 | <0.001  |  66 (×1.0) |    65.2% | —             | 65.2% (≈0.014) |
| str_lag    |   401 |  47.4% | ≈0.294  |    0.0556 |    0.0424 |    12.584 | <0.001  | 400 (×1.0) |    47.2% | 41.2% (≈0.104) | 49.1% (≈0.736) |
| brk_sma    |   171 |  54.4% | ≈0.251  |    0.0602 |    0.0364 |    12.298 | <0.001  | 171 (×1.0) |    54.4% | 63.6% (≈0.070) | 49.6% (≈0.928) |
| brk_bol    |   621 |  57.5% | <0.001  |    0.0667 |    0.0304 |    12.762 | <0.001  | 621 (×1.0) |    57.5% | 71.0% (<0.001) | 53.2% (≈0.195) |
| rev_lo     |   986 |  54.5% | ≈0.005  |    0.0499 |    0.0228 |    12.484 | <0.001  | 807 (×1.2) |    53.2% | 53.1% (≈0.202) | 55.2% (≈0.015) |
| rev_hi     |  1462 |  54.7% | <0.001  |    0.0534 |    0.0273 |    12.815 | <0.001  | 1207 (×1.2) |    53.8% | 59.5% (<0.001) | 51.2% (≈0.458) |
| rev_nhi    |  2896 |  56.1% | <0.001  |    0.0599 |    0.0297 |    12.867 | <0.001  | 2231 (×1.3) |    55.7% | 63.7% (<0.001) | 54.5% (<0.001) |
| rev_nlo    |   153 |  64.7% | <0.001  |    0.0497 |    0.0229 |    12.307 | <0.001  | 153 (×1.0) |    64.7% | —             | 64.7% (<0.001) |
| rev_nhold  |     0 |   0.0% | ≈1.000  |         — |         — |         — | <0.001  |   0 (×1.0) |     0.0% | —             | —             |

### Aggregate by Sign (FY2018–FY2024)

| Sign | FYs | total_n | pooled_DR% | p_pooled | avg_bench_flw | avg_bench_rev | perm_pass | bear_DR range | bull_DR range |
|------|-----|---------|------------|----------|--------------|---------------|-----------|---------------|---------------|
| div_gap    |   7 |    3723 |      54.9% | <0.001   |       0.0508 |        0.0307 |       7/7 | 45.7–68.6%    | 46.5–55.7%    |
| div_peer   |   7 |     740 |      55.1% | ≈0.005   |       0.0562 |        0.0348 |       7/7 | 41.5–100.0%   | 40.8–67.3%    |
| corr_flip  |   7 |     749 |      49.1% | ≈0.635   |       0.0420 |        0.0328 |       7/7 | 50.6–58.2%    | 23.8–63.2%    |
| corr_shift |   7 |    1047 |      50.1% | ≈0.926   |       0.0449 |        0.0357 |       7/7 | 41.3–65.2%    | 40.7–58.7%    |
| str_hold   |   7 |   11588 |      54.8% | <0.001   |       0.0507 |        0.0282 |       7/7 | 46.7–70.0%    | 49.2–57.8%    |
| str_lead   |   7 |    1102 |      48.1% | ≈0.206   |       0.0374 |        0.0280 |       7/7 | —             | 32.7–65.2%    |
| str_lag    |   7 |    2545 |      50.6% | ≈0.565   |       0.0464 |        0.0341 |       7/7 | 30.2–41.2%    | 39.4–59.6%    |
| brk_sma    |   7 |    1328 |      52.0% | ≈0.138   |       0.0493 |        0.0439 |       7/7 | 50.0–65.4%    | 44.1–65.2%    |
| brk_bol    |   7 |    3229 |      53.1% | <0.001   |       0.0483 |        0.0373 |       7/7 | 53.2–71.0%    | 37.7–53.7%    |
| rev_lo     |   7 |    8152 |      52.2% | <0.001   |       0.0431 |        0.0347 |       7/7 | 46.1–65.0%    | 46.8–62.0%    |
| rev_hi     |   7 |    9266 |      51.3% | ≈0.013   |       0.0411 |        0.0340 |       7/7 | 48.8–59.5%    | 44.7–56.2%    |
| rev_nhi    |   7 |   15568 |      51.8% | <0.001   |       0.0436 |        0.0357 |       7/7 | 50.6–63.7%    | 44.8–56.7%    |
| rev_nlo    |   7 |    3003 |      48.8% | ≈0.183   |       0.0439 |        0.0323 |       7/7 | —             | 27.9–64.7%    |
| rev_nhold  |   7 |     119 |      49.6% | ≈0.927   |       0.0781 |        0.0175 |       7/7 | —             | 45.9–90.0%    |

**Notes on interpretation**
- pooled_DR% is n-weighted across all FYs; p_pooled is the binomial test on the pooled n.
- perm_pass = FYs where the permutation test passes at p<0.05.
- bear_DR / bull_DR ranges show min–max across FYs.
- Signs consistent across multiple FYs with perm_pass ≥ 4/7 are the most reliable.


---

## Regime-Split Analysis: ADX + Ichimoku Kumo

Generated: 2026-05-10
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
| div_gap    | choppy (ADX<20)           |   1133 |  55.8% |   <0.001 |  0.0794 |  0.0649 |  +0.0156 |  54.8% |
| div_gap    | bull (ADX≥20,+DI>−DI)     |    939 |  53.5% |   ≈0.037 |  0.1021 |  0.0757 |  +0.0194 |  54.8% |
| div_gap    | bear (ADX≥20,+DI≤−DI)     |    571 |  55.0% |   ≈0.019 |  0.0863 |  0.0612 |  +0.0199 |  54.8% |
| div_peer   | choppy (ADX<20)           |    282 |  56.0% |   ≈0.049 |  0.0842 |  0.0686 |  +0.0170 |  57.2% |
| div_peer   | bull (ADX≥20,+DI>−DI)     |    132 |  57.6% |   ≈0.098 |       — |       — |        — |  57.2% |
| div_peer   | bear (ADX≥20,+DI≤−DI)     |     42 |  64.3% |   ≈0.088 |       — |       — |        — |  57.2% |
| corr_flip  | choppy (ADX<20)           |    314 |  44.3% |   ≈0.048 |  0.0714 |  0.0593 |  -0.0014 |  48.0% |
| corr_flip  | bull (ADX≥20,+DI>−DI)     |    265 |  51.3% |   ≈0.713 |       — |       — |        — |  48.0% |
| corr_flip  | bear (ADX≥20,+DI≤−DI)     |     71 |  52.1% |   ≈0.813 |       — |       — |        — |  48.0% |
| corr_shift | choppy (ADX<20)           |    487 |  52.8% |   ≈0.239 |       — |       — |        — |  50.1% |
| corr_shift | bull (ADX≥20,+DI>−DI)     |    440 |  47.7% |   ≈0.365 |       — |       — |        — |  50.1% |
| corr_shift | bear (ADX≥20,+DI≤−DI)     |    120 |  48.3% |   ≈0.784 |       — |       — |        — |  50.1% |
| str_hold   | choppy (ADX<20)           |   4770 |  52.3% |   ≈0.002 |  0.0813 |  0.0573 |  +0.0152 |  52.7% |
| str_hold   | bull (ADX≥20,+DI>−DI)     |   1611 |  50.5% |   ≈0.727 |       — |       — |        — |  52.7% |
| str_hold   | bear (ADX≥20,+DI≤−DI)     |   2325 |  55.0% |   <0.001 |  0.0857 |  0.0870 |  +0.0079 |  52.7% |
| str_lead   | choppy (ADX<20)           |    455 |  35.8% |   <0.001 |  0.0723 |  0.0595 |  -0.0123 |  48.1% |
| str_lead   | bull (ADX≥20,+DI>−DI)     |     73 |  26.0% |   <0.001 |  0.0909 |  0.0473 |  -0.0114 |  48.1% |
| str_lead   | bear (ADX≥20,+DI≤−DI)     |    574 |  60.6% |   <0.001 |  0.0731 |  0.0528 |  +0.0235 |  48.1% |
| str_lag    | choppy (ADX<20)           |   1382 |  52.2% |   ≈0.101 |       — |       — |        — |  50.6% |
| str_lag    | bull (ADX≥20,+DI>−DI)     |    833 |  50.4% |   ≈0.835 |       — |       — |        — |  50.6% |
| str_lag    | bear (ADX≥20,+DI≤−DI)     |    330 |  43.9% |   ≈0.032 |  0.0780 |  0.0800 |  -0.0105 |  50.6% |
| brk_sma    | choppy (ADX<20)           |    670 |  54.3% |   ≈0.028 |  0.0960 |  0.0744 |  +0.0182 |  52.0% |
| brk_sma    | bull (ADX≥20,+DI>−DI)     |    300 |  50.3% |   ≈0.954 |       — |       — |        — |  52.0% |
| brk_sma    | bear (ADX≥20,+DI≤−DI)     |    190 |  62.1% |   ≈0.001 |  0.1009 |  0.0783 |  +0.0330 |  52.0% |
| brk_bol    | choppy (ADX<20)           |   1432 |  52.5% |   ≈0.061 |       — |       — |        — |  53.1% |
| brk_bol    | bull (ADX≥20,+DI>−DI)     |   1120 |  55.4% |   <0.001 |  0.1047 |  0.0724 |  +0.0258 |  53.1% |
| brk_bol    | bear (ADX≥20,+DI≤−DI)     |    248 |  56.5% |   ≈0.049 |  0.0900 |  0.0791 |  +0.0164 |  53.1% |
| rev_lo     | choppy (ADX<20)           |   3901 |  52.5% |   ≈0.002 |  0.0816 |  0.0667 |  +0.0112 |  52.2% |
| rev_lo     | bull (ADX≥20,+DI>−DI)     |   1759 |  54.1% |   <0.001 |  0.0904 |  0.0552 |  +0.0235 |  52.2% |
| rev_lo     | bear (ADX≥20,+DI≤−DI)     |   1297 |  58.5% |   <0.001 |  0.0786 |  0.0697 |  +0.0171 |  52.2% |
| rev_hi     | choppy (ADX<20)           |   4148 |  49.3% |   ≈0.376 |       — |       — |        — |  51.3% |
| rev_hi     | bull (ADX≥20,+DI>−DI)     |   2566 |  51.3% |   ≈0.186 |       — |       — |        — |  51.3% |
| rev_hi     | bear (ADX≥20,+DI≤−DI)     |   1205 |  59.2% |   <0.001 |  0.0805 |  0.0688 |  +0.0195 |  51.3% |
| rev_nhi    | choppy (ADX<20)           |   4293 |  52.1% |   ≈0.006 |  0.0798 |  0.0724 |  +0.0069 |  53.6% |
| rev_nhi    | bull (ADX≥20,+DI>−DI)     |   4504 |  54.3% |   <0.001 |  0.0987 |  0.0672 |  +0.0229 |  53.6% |
| rev_nhi    | bear (ADX≥20,+DI≤−DI)     |    467 |  60.6% |   <0.001 |  0.0759 |  0.0599 |  +0.0224 |  53.6% |
| rev_nlo    | choppy (ADX<20)           |    900 |  36.4% |   <0.001 |  0.0718 |  0.0773 |  -0.0230 |  53.3% |
| rev_nlo    | bull (ADX≥20,+DI>−DI)     |      0 |      — |        — |       — |       — |        — |  53.3% |
| rev_nlo    | bear (ADX≥20,+DI≤−DI)     |    955 |  69.2% |   <0.001 |  0.0992 |  0.0496 |  +0.0534 |  53.3% |
| rev_nhold  | choppy (ADX<20)           |      0 |      — |        — |       — |       — |        — |  49.6% |
| rev_nhold  | bull (ADX≥20,+DI>−DI)     |      0 |      — |        — |       — |       — |        — |  49.6% |
| rev_nhold  | bear (ADX≥20,+DI≤−DI)     |    119 |  49.6% |   ≈1.000 |       — |       — |        — |  49.6% |

### Ichimoku Kumo Regime Split

Kumo state (N225 close vs cloud boundaries at each fired_at date):
- **above (+1)**: close > upper cloud boundary — bullish trend confirmed
- **inside (0)**: close within cloud — transitioning / no clear trend
- **below (−1)**: close < lower cloud boundary — bearish trend confirmed

| Sign | Kumo | n | DR% | p | mag_flw | mag_rev | EV | vs_all |
|------|---|---|-----|---|---------|---------|----|--------|
| div_gap    | above (+1)                |   1648 |  52.2% |   ≈0.072 |       — |       — |        — |  54.8% |
| div_gap    | inside (0)                |    376 |  57.7% |   ≈0.003 |  0.0858 |  0.0600 |  +0.0241 |  54.8% |
| div_gap    | below (−1)                |    619 |  59.8% |   <0.001 |  0.0888 |  0.0647 |  +0.0270 |  54.8% |
| div_peer   | above (+1)                |    292 |  52.1% |   ≈0.520 |       — |       — |        — |  57.2% |
| div_peer   | inside (0)                |     72 |  72.2% |   <0.001 |  0.1017 |  0.0611 |  +0.0565 |  57.2% |
| div_peer   | below (−1)                |     92 |  62.0% |   ≈0.028 |  0.0946 |  0.0501 |  +0.0396 |  57.2% |
| corr_flip  | above (+1)                |    485 |  49.1% |   ≈0.716 |       — |       — |        — |  48.0% |
| corr_flip  | inside (0)                |     67 |  44.8% |   ≈0.464 |       — |       — |        — |  48.0% |
| corr_flip  | below (−1)                |     98 |  44.9% |   ≈0.363 |       — |       — |        — |  48.0% |
| corr_shift | above (+1)                |    781 |  49.0% |   ≈0.616 |       — |       — |        — |  50.1% |
| corr_shift | inside (0)                |    112 |  58.9% |   ≈0.072 |       — |       — |        — |  50.1% |
| corr_shift | below (−1)                |    149 |  49.0% |   ≈0.870 |       — |       — |        — |  50.1% |
| str_hold   | above (+1)                |   3708 |  46.2% |   <0.001 |  0.0818 |  0.0765 |  -0.0033 |  52.7% |
| str_hold   | inside (0)                |   1651 |  58.2% |   <0.001 |  0.0904 |  0.0559 |  +0.0293 |  52.7% |
| str_hold   | below (−1)                |   3347 |  57.1% |   <0.001 |  0.0833 |  0.0556 |  +0.0237 |  52.7% |
| str_lead   | above (+1)                |    413 |  36.3% |   <0.001 |  0.0709 |  0.0591 |  -0.0119 |  48.1% |
| str_lead   | inside (0)                |    244 |  45.9% |   ≈0.224 |       — |       — |        — |  48.1% |
| str_lead   | below (−1)                |    417 |  61.4% |   <0.001 |  0.0719 |  0.0556 |  +0.0227 |  48.1% |
| str_lag    | above (+1)                |   1800 |  49.8% |   ≈0.906 |       — |       — |        — |  50.6% |
| str_lag    | inside (0)                |    321 |  51.7% |   ≈0.577 |       — |       — |        — |  50.6% |
| str_lag    | below (−1)                |    344 |  48.3% |   ≈0.553 |       — |       — |        — |  50.6% |
| brk_sma    | above (+1)                |    728 |  54.0% |   ≈0.035 |  0.1024 |  0.0737 |  +0.0213 |  52.0% |
| brk_sma    | inside (0)                |    144 |  50.0% |   ≈1.000 |       — |       — |        — |  52.0% |
| brk_sma    | below (−1)                |    268 |  59.3% |   ≈0.003 |  0.0960 |  0.0703 |  +0.0283 |  52.0% |
| brk_bol    | above (+1)                |   2031 |  53.8% |   <0.001 |  0.0992 |  0.0766 |  +0.0180 |  53.1% |
| brk_bol    | inside (0)                |    261 |  53.3% |   ≈0.322 |       — |       — |        — |  53.1% |
| brk_bol    | below (−1)                |    408 |  53.2% |   ≈0.216 |       — |       — |        — |  53.1% |
| rev_lo     | above (+1)                |   4038 |  52.8% |   <0.001 |  0.0845 |  0.0624 |  +0.0152 |  52.2% |
| rev_lo     | inside (0)                |    913 |  58.5% |   <0.001 |  0.0791 |  0.0611 |  +0.0209 |  52.2% |
| rev_lo     | below (−1)                |   1828 |  55.3% |   <0.001 |  0.0827 |  0.0680 |  +0.0154 |  52.2% |
| rev_hi     | above (+1)                |   5316 |  49.6% |   ≈0.612 |       — |       — |        — |  51.3% |
| rev_hi     | inside (0)                |    778 |  57.7% |   <0.001 |  0.0771 |  0.0591 |  +0.0195 |  51.3% |
| rev_hi     | below (−1)                |   1613 |  54.8% |   <0.001 |  0.0767 |  0.0650 |  +0.0126 |  51.3% |
| rev_nhi    | above (+1)                |   7346 |  53.3% |   <0.001 |  0.0903 |  0.0716 |  +0.0146 |  53.6% |
| rev_nhi    | inside (0)                |    857 |  56.2% |   <0.001 |  0.0913 |  0.0609 |  +0.0247 |  53.6% |
| rev_nhi    | below (−1)                |   1061 |  54.0% |   ≈0.010 |  0.0773 |  0.0602 |  +0.0141 |  53.6% |
| rev_nlo    | above (+1)                |    717 |  46.0% |   ≈0.036 |  0.0716 |  0.0773 |  -0.0088 |  53.3% |
| rev_nlo    | inside (0)                |    486 |  47.3% |   ≈0.257 |       — |       — |        — |  53.3% |
| rev_nlo    | below (−1)                |    652 |  65.8% |   <0.001 |  0.0933 |  0.0448 |  +0.0461 |  53.3% |
| rev_nhold  | above (+1)                |      0 |      — |        — |       — |       — |        — |  49.6% |
| rev_nhold  | inside (0)                |     44 |  18.2% |   <0.001 |       — |       — |        — |  49.6% |
| rev_nhold  | below (−1)                |     75 |  68.0% |   ≈0.002 |  0.0741 |  0.0776 |  +0.0256 |  49.6% |


---

## Score Calibration: Does sign_score Predict Outcomes?

Generated: 2026-05-10  
Events: multi-year runs (FY2018–FY2024, run_ids ≥ 47).  
signed_return = trend_direction × trend_magnitude (+ when sign follows, − when reverses).  
ρ: Spearman correlation between sign_score and signed_return.  
Per-quartile rows with n < 50 are shown but their stats are masked.  

### Summary

| Sign | n | score range | ρ | p(ρ) | verdict |
|------|---|-------------|---|------|---------|
| div_gap    |   2643 | 0.065–0.950 |  +0.037 |  ≈0.055 | noise (p≥0.05) |
| div_peer   |    456 | 0.171–1.000 |  +0.029 |  ≈0.534 | noise (p≥0.05) |
| corr_flip  |    650 | 0.095–0.859 |  -0.030 |  ≈0.438 | noise (p≥0.05) |
| corr_shift |   1047 | 0.502–1.000 |  +0.039 |  ≈0.207 | noise (p≥0.05) |
| str_hold   |   8706 | 0.416–1.000 |  -0.026 |  ≈0.016 | **inverted** (ρ<0) |
| str_lead   |   1102 | 0.354–0.794 |  +0.047 |  ≈0.120 | noise (p≥0.05) |
| str_lag    |   2545 | 0.084–0.902 |  +0.012 |  ≈0.541 | noise (p≥0.05) |
| brk_sma    |   1328 | 0.002–1.000 |  +0.047 |  ≈0.086 | noise (p≥0.05) |
| brk_bol    |   3229 | 0.500–1.000 |  +0.031 |  ≈0.080 | noise (p≥0.05) |
| rev_lo     |   8152 | 0.000–1.000 |  +0.009 |  ≈0.410 | noise (p≥0.05) |
| rev_hi     |   9266 | 0.000–1.000 |  -0.013 |  ≈0.211 | noise (p≥0.05) |
| rev_nhi    |   9264 | 1.000–1.000 |       — |       — | n/a (constant) |
| rev_nlo    |   1855 | 0.251–0.945 |  +0.103 |  <0.001 | informative |
| rev_nhold  |    119 | 0.224–1.000 |  +0.039 |  ≈0.677 | noise (p≥0.05) |

### Quartile Breakdown

DR = direction-rate; mag_flw / mag_rev = mean trend_magnitude when the trend follows / reverses;
EV = DR × mag_flw − (1−DR) × mag_rev (expected return per trade in that quartile).
If the score is informative we expect EV(Q4) ≫ EV(Q1).

| Sign | Quartile | score range | n | DR% | mag_flw | mag_rev | EV |
|------|----------|-------------|---|-----|---------|---------|----|
| **div_gap**    | Q1 | 0.065–0.128   |   661 | 53.4% |  0.0818 |  0.0674 |  +0.0123 |
|                | Q2 | 0.128–0.185   |   661 | 55.2% |  0.0791 |  0.0610 |  +0.0164 |
|                | Q3 | 0.185–0.292   |   663 | 54.0% |  0.0983 |  0.0720 |  +0.0199 |
|                | Q4 | 0.293–0.950   |   658 | 56.5% |  0.0958 |  0.0717 |  +0.0230 |
| **div_peer**   | Q1 | 0.171–0.319   |   114 | 53.5% |  0.0899 |  0.0569 |  +0.0216 |
|                | Q2 | 0.320–0.440   |   114 | 60.5% |  0.0919 |  0.0588 |  +0.0324 |
|                | Q3 | 0.441–0.662   |   114 | 59.6% |  0.0861 |  0.0746 |  +0.0213 |
|                | Q4 | 0.667–1.000   |   114 | 55.3% |  0.1127 |  0.0709 |  +0.0305 |
| **corr_flip**  | Q1 | 0.095–0.201   |   163 | 50.3% |  0.0841 |  0.0570 |  +0.0140 |
|                | Q2 | 0.201–0.281   |   162 | 48.1% |  0.0716 |  0.0704 |  -0.0020 |
|                | Q3 | 0.281–0.433   |   162 | 48.1% |  0.0739 |  0.0681 |  +0.0003 |
|                | Q4 | 0.434–0.859   |   163 | 45.4% |  0.0921 |  0.0624 |  +0.0077 |
| **corr_shift** | Q1 | 0.502–0.701   |   262 | 48.5% |  0.0879 |  0.0727 |  +0.0052 |
|                | Q2 | 0.702–0.858   |   262 | 45.8% |  0.0937 |  0.0680 |  +0.0061 |
|                | Q3 | 0.860–0.968   |   261 | 55.6% |  0.0882 |  0.0754 |  +0.0155 |
|                | Q4 | 0.968–1.000   |   262 | 50.8% |  0.0765 |  0.0762 |  +0.0013 |
| **str_hold**   | Q1 | 0.416–0.637   |  2177 | 52.9% |  0.0805 |  0.0516 |  +0.0183 |
|                | Q2 | 0.637–0.774   |  2176 | 52.7% |  0.0846 |  0.0593 |  +0.0165 |
|                | Q3 | 0.774–0.867   |  2185 | 52.6% |  0.0923 |  0.0681 |  +0.0163 |
|                | Q4 | 0.867–1.000   |  2168 | 52.5% |  0.0795 |  0.0840 |  +0.0019 |
| **str_lead**   | Q1 | 0.354–0.445   |   276 | 47.8% |  0.0687 |  0.0575 |  +0.0028 |
|                | Q2 | 0.445–0.519   |   275 | 45.5% |  0.0789 |  0.0552 |  +0.0058 |
|                | Q3 | 0.520–0.589   |   275 | 51.6% |  0.0655 |  0.0565 |  +0.0065 |
|                | Q4 | 0.590–0.794   |   276 | 47.5% |  0.0817 |  0.0536 |  +0.0106 |
| **str_lag**    | Q1 | 0.084–0.397   |   641 | 47.6% |  0.0914 |  0.0715 |  +0.0060 |
|                | Q2 | 0.401–0.561   |   644 | 49.7% |  0.0981 |  0.0657 |  +0.0157 |
|                | Q3 | 0.562–0.692   |   663 | 53.7% |  0.0782 |  0.0681 |  +0.0105 |
|                | Q4 | 0.693–0.902   |   597 | 51.3% |  0.0869 |  0.0746 |  +0.0082 |
| **brk_bol**    | Q1 | 0.500–0.619   |   808 | 52.0% |  0.0962 |  0.0812 |  +0.0110 |
|                | Q2 | 0.620–0.754   |   807 | 52.2% |  0.0992 |  0.0789 |  +0.0140 |
|                | Q3 | 0.754–0.938   |   807 | 52.8% |  0.0930 |  0.0751 |  +0.0136 |
|                | Q4 | 0.938–1.000   |   807 | 55.3% |  0.0943 |  0.0724 |  +0.0197 |
| **rev_lo**     | Q1 | 0.000–0.245   |  2038 | 52.0% |  0.0787 |  0.0725 |  +0.0061 |
|                | Q2 | 0.245–0.491   |  2038 | 51.4% |  0.0827 |  0.0719 |  +0.0076 |
|                | Q3 | 0.491–0.740   |  2038 | 52.7% |  0.0818 |  0.0746 |  +0.0078 |
|                | Q4 | 0.740–1.000   |  2038 | 52.6% |  0.0826 |  0.0726 |  +0.0090 |
| **rev_hi**     | Q1 | 0.000–0.244   |  2317 | 52.5% |  0.0792 |  0.0722 |  +0.0073 |
|                | Q2 | 0.244–0.501   |  2316 | 51.7% |  0.0814 |  0.0708 |  +0.0079 |
|                | Q3 | 0.501–0.747   |  2316 | 50.2% |  0.0805 |  0.0672 |  +0.0069 |
|                | Q4 | 0.747–1.000   |  2317 | 50.8% |  0.0791 |  0.0684 |  +0.0065 |
| **rev_nlo**    | Q1 | 0.251–0.368   |   464 | 52.6% |  0.0712 |  0.0668 |  +0.0058 |
|                | Q2 | 0.369–0.459   |   464 | 50.4% |  0.0753 |  0.0636 |  +0.0065 |
|                | Q3 | 0.459–0.599   |   463 | 52.3% |  0.0999 |  0.0708 |  +0.0184 |
|                | Q4 | 0.599–0.945   |   464 | 58.0% |  0.1112 |  0.0708 |  +0.0347 |
| **rev_nhold**  | Q1 | 0.224–0.540   |    30 | — | — | — | — |
|                | Q2 | 0.554–0.805   |    51 | 43.1% |  0.0695 |  0.0525 |  +0.0001 |
|                | Q3 | 0.816–0.816   |    32 | — | — | — | — |
|                | Q4 | 0.951–1.000   |     6 | — | — | — | — |

---

## FY2025 Out-of-Sample Backtest

Generated: 2026-05-10  
Training: FY2018–FY2024 regime ranking (Ichimoku Kumo × ADX veto)  
Test: FY2025 · classified2024 · 2025-04-01 – 2026-03-31  
Ranking cells: 34 (sign × kumo_state, min_n=30)  

### Regime Cell Detail (sign × kumo_state)

Kumo states: ▲above cloud (+1) · ~inside (0) · ▼below cloud (−1)  
Δ DR = test cell DR − sign-level baseline DR (all events for that sign).

| Sign | kumo | train_bench_flw | train_DR | train_n | test_n | test_DR | Δ DR |
|------|------|-----------------|----------|---------|--------|---------|------|
| div_peer   | ~inside | 0.0654 | 67.0% |      88 |      1 |    0.0% | -68.2% |
| div_peer   | ▼below  | 0.0584 | 57.6% |     172 |      1 |    0.0% | -68.2% |
| rev_nlo    | ▼below  | 0.0574 | 62.6% |    1170 |      0 |       — | —      |
| brk_sma    | ▼below  | 0.0559 | 58.7% |     264 |      4 |  100.0% | +45.6% |
| brk_sma    | ▲above  | 0.0541 | 54.0% |     563 |    165 |   53.9% | -0.4%  |
| str_hold   | ~inside | 0.0520 | 59.8% |    1616 |    439 |   58.8% | +7.4%  |
| div_peer   | ▲above  | 0.0509 | 52.7% |     372 |     20 |   75.0% | +6.8%  |
| rev_nhold  | ▼below  | 0.0504 | 68.0% |      75 |      0 |       — | —      |
| div_gap    | ▼below  | 0.0499 | 56.9% |    1109 |     27 |   70.4% | +19.8% |
| brk_bol    | ▼below  | 0.0494 | 53.4% |     384 |     24 |   50.0% | -7.5%  |
| div_gap    | ▲above  | 0.0492 | 56.7% |    1241 |    753 |   48.7% | -1.9%  |
| str_hold   | ▼below  | 0.0488 | 56.9% |    4936 |     19 |   94.7% | +43.4% |
| brk_bol    | ~inside | 0.0486 | 53.3% |     244 |     17 |   52.9% | -4.5%  |
| brk_sma    | ~inside | 0.0482 | 50.7% |     142 |      2 |    0.0% | -54.4% |
| corr_shift | ~inside | 0.0479 | 58.4% |     101 |     11 |   63.6% | +16.2% |
| corr_flip  | ▼below  | 0.0478 | 47.4% |     116 |      0 |       — | —      |
| rev_nlo    | ~inside | 0.0476 | 42.8% |     615 |      0 |       — | —      |
| brk_bol    | ▲above  | 0.0475 | 52.2% |    1451 |    580 |   57.9% | +0.4%  |
| rev_nhi    | ~inside | 0.0464 | 56.2% |     979 |    124 |   47.6% | -8.5%  |
| div_gap    | ~inside | 0.0463 | 53.8% |     407 |     50 |   68.0% | +17.4% |
| rev_lo     | ▼below  | 0.0458 | 55.3% |    1826 |      2 |   50.0% | -4.5%  |
| corr_shift | ▲above  | 0.0455 | 50.3% |     495 |    286 |   46.9% | -0.6%  |
| rev_lo     | ~inside | 0.0450 | 58.0% |     878 |     35 |   71.4% | +17.0% |
| str_lag    | ▲above  | 0.0446 | 50.5% |    1399 |    401 |   47.4% | +0.0%  |
| rev_hi     | ~inside | 0.0445 | 57.2% |     755 |     23 |   73.9% | +19.3% |
| rev_nhi    | ▲above  | 0.0444 | 52.0% |    6994 |   2685 |   57.0% | +0.9%  |
| str_lead   | ▼below  | 0.0441 | 61.4% |     417 |      0 |       — | —      |
| rev_lo     | ▲above  | 0.0433 | 52.5% |    3089 |    949 |   53.8% | -0.6%  |
| rev_hi     | ▼below  | 0.0416 | 54.7% |    1590 |     23 |   65.2% | +10.6% |
| rev_nhi    | ▼below  | 0.0406 | 50.6% |    1980 |     87 |   40.2% | -15.8% |
| corr_flip  | ▲above  | 0.0402 | 53.5% |     303 |    238 |   45.8% | -0.1%  |
| rev_hi     | ▲above  | 0.0371 | 48.0% |    3900 |   1416 |   54.2% | -0.5%  |
| str_lead   | ~inside | 0.0358 | 45.9% |     244 |      0 |       — | —      |
| corr_flip  | ~inside | 0.0345 | 46.1% |      76 |      4 |   50.0% | +4.1%  |

### Sign Summary: All Events vs Regime-Accepted Events

Regime-accepted = (sign, kumo) cell present in training ranking AND ADX veto passes.  
regime_n% = fraction of total events retained by the regime filter.

| Sign | total_n | total_DR | regime_n | regime_DR | Δ DR | regime_n% |
|------|---------|----------|----------|-----------|------|-----------|
| div_gap    |     830 |    50.6% |      830 |     50.6% |  +0.0% |      100% |
| div_peer   |      22 |    68.2% |       22 |     68.2% |  +0.0% |      100% |
| corr_flip  |     242 |    45.9% |      242 |     45.9% |  +0.0% |      100% |
| corr_shift |     297 |    47.5% |      297 |     47.5% |  +0.0% |      100% |
| str_hold   |    2010 |    51.3% |      458 |     60.3% |  +8.9% |       23% |
| str_lead   |      66 |    65.2% |        0 |         — |      — |        0% |
| str_lag    |     401 |    47.4% |      401 |     47.4% |  +0.0% |      100% |
| brk_sma    |     171 |    54.4% |      171 |     54.4% |  +0.0% |      100% |
| brk_bol    |     621 |    57.5% |      621 |     57.5% |  +0.0% |      100% |
| rev_lo     |     986 |    54.5% |      986 |     54.5% |  +0.0% |      100% |
| rev_hi     |    1462 |    54.7% |     1462 |     54.7% |  +0.0% |      100% |
| rev_nhi    |    2896 |    56.1% |     2896 |     56.1% |  +0.0% |      100% |
| rev_nlo    |     153 |    64.7% |        0 |         — |      — |        0% |
| rev_nhold  | 0 | — | — | — | — | — |

**Interpretation**: Positive Δ DR means the Kumo+ADX regime filter selected
events with better follow-through outcomes in the out-of-sample year.
Low regime_n% indicates the filter is aggressive; verify test_n is large enough.

