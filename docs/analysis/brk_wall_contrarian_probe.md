# brk_wall contrarian probe — Stage 1 cohort EV table

Probe run: 2026-05-19.  Tests whether brk_wall fires on extreme-bearish-breadth days deliver materially better outcomes than on normal/extreme-bullish days.

- **Sign**: `brk_wall` (K=10 production fires, all multi-year runs)
- **Conditioning signal**: SMA(50) breadth = fraction of `classified2024` universe whose close > own SMA(50)
- **bearish_extreme**: breadth percentile ≤ 20 (bottom quintile)
- **bullish_extreme**: breadth percentile ≥ 80 (top quintile)
- **normal**: in between

**Signed mean** = E[trend_direction × trend_magnitude] (proxy for long-side mean return per fire if you blindly go long every fire).  DR > 50% and signed_mean > 0 together = sign predicts upward follow-through.

## Pooled across all FYs

| cohort | n | DR | mag_flw | mag_rev | signed_mean |
|--------|---:|---:|---:|---:|---:|
| **bearish_extreme** |   316 | 58.2% | +8.71% | +7.83% | +1.80% |
| normal |  2997 | 52.2% | +7.58% | +7.32% | +0.45% |
| **bullish_extreme** |  1693 | 53.5% | +7.71% | +6.22% | +1.23% |
| ALL |  5006 | 53.0% | +7.70% | +6.98% | +0.80% |

## Per-FY breakdown

| FY | cohort | n | DR | signed_mean |
|----|--------|---:|---:|---:|
| FY2019 | bearish_extreme |  100 | 47.0% | -2.85% |
| FY2019 | normal |  292 | 40.8% | -5.91% |
| FY2019 | bullish_extreme |  288 | 53.8% | +0.80% |
| FY2020 | bearish_extreme |   22 | 63.6% | +6.82% |
| FY2020 | normal |  303 | 52.8% | +1.41% |
| FY2020 | bullish_extreme |  317 | 52.1% | +1.46% |
| FY2021 | bearish_extreme |   70 | 58.6% | +1.80% |
| FY2021 | normal |  515 | 52.4% | -0.29% |
| FY2021 | bullish_extreme |   59 | 30.5% | -3.86% |
| FY2022 | bearish_extreme |   42 | 57.1% | +2.55% |
| FY2022 | normal |  531 | 49.7% | +0.04% |
| FY2022 | bullish_extreme |   60 | 26.7% | -3.17% |
| FY2023 | bearish_extreme |   18 | 33.3% | -0.22% |
| FY2023 | normal |  441 | 56.7% | +2.27% |
| FY2023 | bullish_extreme |  419 | 53.7% | +1.75% |
| FY2024 | bearish_extreme |   64 | 81.2% | +7.43% |
| FY2024 | normal |  433 | 50.6% | -0.21% |
| FY2024 | bullish_extreme |   27 | 33.3% | -2.93% |
| FY2025 | normal |  482 | 58.3% | +3.86% |
| FY2025 | bullish_extreme |  523 | 60.8% | +2.20% |

## Verdict shape (lift vs ALL pooled)

- **bearish_extreme** (316 fires): DR Δ +5.2pp / signed_mean Δ +1.00pp
- **bullish_extreme** (1693 fires): DR Δ +0.5pp / signed_mean Δ +0.43pp

## Interpretation

- **PASS (contrarian-bullish real)**: bearish_extreme shows DR ≥ ALL+5pp AND signed_mean ≥ ALL+0.5pp AND per-FY direction consistent in ≥ 5/8 FYs.
- **REJECT (no contrarian effect)**: bearish_extreme close to ALL or worse.  Hypothesis dies here, no need to run Stage 2 A/B.
- **PARTIAL (weak signal)**: lift present but per-FY noisy or n too small.  Document as not-actionable; revisit on universe expansion.
