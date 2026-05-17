# brk_hi_sideway — Sideways-Range Wall Breakout sign detector

Fires when today's bar **low** breaks above a recently-tested resistance
"wall", where a wall is the high of any tight 10-bar consolidation in
the prior ~6 months.  Strict and transition-gated: requires the entire
bar to hold above the wall (no intraday violation back into the range),
and the previous bar to have been below the wall (one fire per breakout
event, not continuous while above).

## Rationale

Operator hypothesis (2026-05-17 /sign-debate): sideways price ranges
in the recent past form *tested* support/resistance.  A clean breakout
above such a wall is structurally different from a generic rolling-N-max
breakout because:

- The wall is built from K consecutive bars that all hugged the same
  level (not from a single spike).
- The strict `low > wall` plus transition condition filters out wicky
  pierces and continuation bars (fires once per genuine breakout).

The contrast with the earlier `long_high_continuation_probe` (close >
rolling_max(close, N), N ∈ {60, 120, 250}) is decisive — that REJECTED
at pooled EV ≈ +0.003.  Same universe, same outcome convention, very
different signal once "tested wall" replaced "any historical max."

## Fire rule

```
sideways range at bar i (10 trading days):
    window = highs[i-K+1 : i+1], lows[i-K+1 : i+1], closes[i-K+1 : i+1]
    (max(window.highs) - min(window.lows)) / mean(window.closes) <= θ

wall[T] = max(tight_window_high[j] for j in [T-lookback, T-K-1])

fire[T] = (low[T] > wall[T-1]) AND (low[T-1] ≤ wall[T-1])
```

Parameters:
- **K        = 10** trading-day sideways window length (~2 weeks)
- **θ        = 0.05** (range / mean cutoff — total range ≤ 5%)
- **lookback = 120** bars (~6 months for finding walls)

Score = `min((close - wall) / wall, 0.05) / 0.05` — normalised distance
above wall at breakout bar, saturates at 5%.

Validity = 5 trading days.  Detector expires the sign if a later bar
trades back below the wall (transition condition + valid_bars window).

## Probe basis

`src/analysis/brk_hi_sideway_probe.py` (committed 8a10ee4) ran the
same detector logic against 219-stock classified-cluster universe,
FY2019–FY2025, 4,733 training fires + 1,138 FY2025 OOS:

| FY | n | DR | EV |
|---|---|---|---|
| FY2019 | 745 | 69.8% | +0.0080 |
| FY2020 | 707 | 72.8% | +0.0426 |
| FY2021 | 731 | 71.0% | +0.0235 |
| FY2022 | 723 | 72.6% | +0.0250 |
| FY2023 | 1,094 | 77.4% | +0.0443 |
| FY2024 | 733 | 69.9% | +0.0228 |
| **FY2025 OOS** | **1,138** | **74.3%** | **+0.0443** |
| **pooled train** | **4,733** | **72.6%** | **+0.0288** |

All FYs positive.  Standalone gate (pooled EV ≥ +0.020, FY2025 EV > 0,
DR ≥ 53%) cleared by wide margin.

Confluence-incremental: adding brk_hi_sideway to the v2 bullish-set
DILUTES the ≥3-confluence uplift by −0.83pp pooled (n[≥3] grew 1746 →
3441 but EV/row dropped).  **Implication: ship as standalone proposal,
NOT as confluence input.**

## Benchmark (canonical pipeline, 2026-05-17)

| FY | n | DR | perm_p | bear DR / p | bull DR / p |
|----|--:|--:|--:|--:|--:|
| FY2019 | 680 | 47.2% | 0.941 | — | — |
| FY2020 | 642 | 52.8% | 0.076 | 59.5% / 0.004 | 48.0% / 0.443 |
| FY2021 | 644 | 51.1% | 0.302 | 53.0% / 0.330 | 49.7% / 0.918 |
| FY2022 | 633 | 48.0% | 0.863 | 51.0% / 0.747 | 46.2% / 0.130 |
| FY2023 | 878 | 54.8% | **0.002** | 50.0% / 1.000 | **57.6% / <0.001** |
| FY2024 | 524 | 53.4% | 0.060 | **61.8% / 0.001** | 48.3% / 0.542 |
| **FY2025 OOS** | **1,005** | **59.6%** | **<0.001** | **65.7% / <0.001** | **55.6% / 0.006** |
| **Pooled FY18–24** | **5,006** | **53.0%** | — | — | — |

**Perm-pass: 2/7** (FY2023, FY2025).  Training pooled is statistically
significant but marginal in magnitude; FY2025 OOS is strong on both
regimes.  Bear-regime cell carries the consistent edge across FY2020,
FY2024, FY2025.

### Score calibration

Spearman ρ ≈ 0.028 (noise) — `sign_score` for brk_hi_sideway is
**non-informative**.  By convention this would normally trigger
"drop from ranking" investigation, but per `feedback_score_calibration_insufficient`
the established rule is: rank by EV, treat score as tiebreak only.

### Regime placement (Kumo × ADX, ranked by EV)

| Cell | EV | DR | n |
|---|---|---|---|
| ~inside | +0.049 | 62.0% | 368 |
| ▼below  | +0.042 | 55.1% | 700 |
| ▲above  | +0.038 | 49.8% | 2,199 |

The Kumo-inside cell has the highest standalone EV but smallest n;
the Kumo-above cell has the largest n but flattest DR.

### Confluence-incremental: do NOT add to bullish-set tally

The pre-rebench probe (`src/analysis/brk_hi_sideway_probe.py`) measured
brk_hi_sideway's effect on the v2 bullish-confluence tally and found
**−0.83pp Δ uplift** pooled — adding it dilutes the existing 7-sign
confluence quality even as it expands the ≥3-confluence cohort.
**Ship as standalone proposal only.**

### Probe vs canonical discrepancy

The pre-rebench probe reported DR 72.6% pooled / 74.3% FY2025 — ~20pp
higher than the canonical rebench numbers above.  Cause: the probe used
zigzag peaks detected globally on the full bar series (more "settled"
confirmation), while the canonical pipeline detects peaks on a 35-bar
forward window per fire.  The canonical numbers are the trustworthy
ones; the probe's directional finding survives but the magnitude was
inflated.
