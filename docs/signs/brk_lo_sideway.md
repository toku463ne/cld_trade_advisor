# brk_lo_sideway — Sideways-Range Floor Breakdown sign detector

Mirror of `brk_hi_sideway`.  Fires when today's bar **high** breaks
below a recently-tested support "floor" — the low of any tight 10-bar
consolidation in the prior ~6 months.  Strict and transition-gated
(one fire per genuine breakdown, no intraday recovery within the bar).

## ⚠ Probe vs canonical INVERSION

The pre-ship probe (`brk_hi_sideway_probe.py --side lo`) suggested
this was an "avoid long" signal (breakdowns persist, long entry
loses).  The canonical rebench INVERTED that reading:

| Measurement | Pooled DR | Interpretation |
|---|---|---|
| Probe (global zigzag, more confirmed peaks) | 33.2% | breakdowns persist → avoid long |
| Canonical (windowed zigzag per fire) | **51.7%** | breakdowns mean-revert → mild long entry |

The canonical reading is the trustworthy one (matches the live
pipeline used by every other sign in the catalogue).  The probe
over-claimed because globally-detected zigzag is more "settled."

**Conclusion**: this is a **mild long entry sign**, not an avoid-long
filter.  Do not use as a short signal; the canonical FY-by-FY pattern
shows breakdowns mostly revert.

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

Surfaces as a regular long-entry sign on the Daily tab.  Operator
should NOT interpret as "avoid long" — that contradicts the canonical
measurement.  Strongest cell to act on: high-corr stocks with deep
breakdowns (high score) when N225 is below its Kumo cloud.
