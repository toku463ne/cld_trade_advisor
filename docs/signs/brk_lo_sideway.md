# brk_lo_sideway — Sideways-Range Floor Breakdown sign detector

Mirror of `brk_hi_sideway`.  Fires when today's bar **high** breaks
below a recently-tested support "floor" — the low of any tight 10-bar
consolidation in the prior ~6 months.  Strict and transition-gated
(one fire per genuine breakdown, no intraday recovery within the bar).

## ⚠ Informational-only (not a usable entry trigger)

The operator's original intent was to use this as a **SHORT entry
signal** (price broke down → go short).  The pre-ship probe supported
this:

| Direction | Probe DR (for that direction) | Probe verdict |
|---|---|---|
| SHORT (1−DR) | **66.8%** | strong short edge — ship it |
| LONG (DR) | 33.2% | bad for long |

The canonical rebench INVERTED this reading:

| Direction | Canonical DR | Canonical EV pooled |
|---|---|---|
| SHORT (1−DR) | **48.3%** | ≈−0.44% |
| LONG (DR) | 51.7% | ≈+0.44% |

**Neither direction has a usable edge.**  The probe overestimated the
short edge by ~19pp because it used globally-confirmed zigzag (which
sees the full continuation of a real bear leg) rather than the per-fire
windowed detection the canonical pipeline uses (which catches
mean-reversion within 35 bars).

**Listed in `_HIDDEN_PROPOSAL_SIGNS` in `src/viz/daily.py`** — does
not generate proposal rows on the Daily tab.  The sign is kept in the
catalogue because:
1. The underlying event ("broke a sideways-range floor today") is
   real and operator-meaningful as context;
2. Score calibration ρ=+0.172 in high-corr cohort is the strongest
   score-EV correlation in the entire sign catalogue (worth preserving
   the events for future structural analysis).

## Fire rule

```
sideways range at bar i (10 trading days):
    window = highs[i-K+1 : i+1], lows[i-K+1 : i+1], closes[i-K+1 : i+1]
    (max(window.highs) - min(window.lows)) / mean(window.closes) <= θ

floor[T] = min(tight_window_low[j] for j in [T-lookback, T-K-1])

fire[T] = (high[T] < floor[T-1]) AND (high[T-1] ≥ floor[T-1])
```

Parameters (same as brk_hi_sideway):
- **K        = 10** trading-day sideways window length
- **θ        = 0.05** (range / mean tightness)
- **lookback = 120** bars (~6 months for finding floors)

Score = `min((floor - close) / floor, 0.05) / 0.05` — normalised
breakdown depth, saturates at 5%.  Higher score = deeper breakdown.

Validity = 5 trading days.

## Benchmark (canonical pipeline, 2026-05-17)

| FY | n | DR | perm_p | bear DR / p | bull DR / p |
|----|--:|--:|--:|--:|--:|
| FY2019 | 511 | 36.4% | 1.000 | — | — |
| FY2020 | 317 | 54.3% | 0.052 | **59.7% / 0.018** | 47.2% / 0.475 |
| FY2021 | 666 | 53.0% | 0.062 | 51.9% / 0.483 | 54.2% / 0.133 |
| FY2022 | 541 | 59.9% | **<0.001** | 54.6% / 0.138 | **64.9% / <0.001** |
| FY2023 | 331 | 50.2% | 0.502 | 49.6% / 0.927 | 50.5% / 0.891 |
| FY2024 | 672 | 55.2% | **0.002** | **57.7% / 0.003** | 52.3% / 0.425 |
| FY2025 OOS | 272 | 51.5% | 0.339 | 52.5% / 0.584 | 50.7% / 0.871 |
| **Pooled FY18–24** | **3,310** | **51.7%** | — | — | — |

**Perm-pass 2/7** (FY2022, FY2024).  Same tier as `brk_sma`, `brk_bol`,
`brk_hi_sideway` — marginal headline, with regime-concentrated
strength.

### Score calibration (the unique finding)

| Cell | n | ρ | p | Quartile EV range |
|---|---|---|---|---|
| high-corr | 1,289 | **+0.172** | **<0.001** | +1.23% → +5.13% (top quartile) |
| mid-corr | 1,028 | +0.021 | 0.496 | noise |
| low-corr | 475 | −0.055 | 0.233 | noise |

**brk_lo_sideway × high-corr has the strongest score-to-EV correlation
in the entire catalogue** (+0.172, p<0.001).  Deeper breakdowns in
high-corr stocks produce LARGER mean-reversion EV.  Rank by score
WITHIN high-corr cohort.

### Regime placement (Kumo × ADX, ranked by EV)

| Cell | EV | DR | n | Note |
|---|---|---|---|---|
| ▼below | +0.053 | **59.1%** | 1,173 | **strongest cell** — bear regime |
| ▲above | +0.041 | 50.0% | 1,010 | flat |
| ~inside | +0.038 | 56.6% | 311 | small n |

The Kumo-below cell carries the consistent edge — same pattern as
brk_hi_sideway concentrating in Kumo-inside/below, both signs work
best when N225 is below its Kumo cloud.

## FY2019 outlier

FY2019 standalone shows DR=36.4% (the "breakdowns persist" pattern the
operator hypothesized) but `perm_p=1.000` — no signal vs shuffled
baseline.  This is the only FY where canonical agrees with the probe's
avoid-long framing; in FY2020–FY2025 the pattern flipped.  Either
FY2019 was a uniquely bearish year that strongly extended breakdowns,
or the small n=511 produced a coincidental cluster.  Not load-bearing
for the production interpretation.

## Operational note

Does NOT surface on the Daily proposals table.  Available via the
sign catalogue / regime analysis tables for inspection.  If the
operator decides to read brk_lo_sideway as a "context indicator" on
a stock they were already considering, the strongest signal is the
combination: high-corr stock + Kumo below + high score (deep
breakdown).  Even there the canonical EV is modest (~+5pp top quartile)
and not enough to justify a standalone trigger.

The original SHORT-entry intent is NOT supported by canonical
measurement.  Do not short on this sign.
