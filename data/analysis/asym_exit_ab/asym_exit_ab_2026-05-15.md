# asym_exit_ab — pivot-direction-routed exit-rule A/B

Generated: 2026-05-15

Baseline: `ZsTpSl(2.0,2.0,0.3)` universal (current regime_sign_backtest default).  
Variant: `AdxTrail(d=8.0)` if last confirmed pivot within 20 bars is HIGH; `ZsTpSl(2.0,2.0,0.3)` if LOW; `ZsTpSl(2.0,2.0,0.3)` default otherwise.

## Per-FY

| FY | n | baseline mean_r | baseline sharpe | variant mean_r | variant sharpe | ΔSharpe | route counts (H/L/D) |
|----|---|-----------------|-----------------|----------------|----------------|---------|----------------------|
| FY2019 | 0 | +0.00% | nan | +0.00% | nan | **+nan** | 0/0/0 |
| FY2020 | 0 | +0.00% | nan | +0.00% | nan | **+nan** | 0/0/0 |
| FY2021 | 31 | -0.86% | -1.311 | -0.34% | -0.599 | **+0.712** | 10/21/3 |
| FY2022 | 31 | +2.47% | 3.782 | +0.88% | 1.854 | **-1.929** | 20/17/2 |
| FY2023 | 37 | +2.36% | 4.575 | +4.70% | 7.465 | **+2.890** | 10/22/2 |
| FY2024 | 37 | -0.01% | -0.019 | +2.69% | 4.009 | **+4.029** | 21/11/2 |
| FY2025 | 42 | +1.16% | 1.815 | -0.65% | -1.071 | **-2.886** | 12/25/2 |

## Pre-registered gates

| Gate | Observed | Threshold | Pass? |
|------|----------|-----------|-------|
| G1 mean ΔSharpe FY2019-FY2024 | +1.426 | ≥ +0.10 | ✓ |
| G2 ΔSharpe FY2025 OOS | -2.886 | ≥ +0.05 | ✗ |
| G3 worst FY ΔSharpe (FY2019-FY2024) | -1.929 | ≥ −0.20 | ✗ |
| G4 n_HIGH ≥ 50 AND n_LOW ≥ 50 on FY2025 OOS | H=12, L=25 | ≥ 50 each | ✗ |

## Verdict: **REJECT**

## Notes
- ΔSharpe computed per-FY then averaged; not pooled-Sharpe of concatenated returns.
- Routing uses `detect_peaks(size=5, middle_size=2)` on daily bars; same as project default.
- Recent-pivot lookback = 20 bars at entry date.
- This A/B uses the production `regime_sign` proposals + portfolio constraints (≤1 high-corr, ≤3 low-corr).
- Exit rules are the REAL production rules (`AdxTrail(d=8.0)`, `ZsTpSl(2.0,2.0,0.3)`), not proxies.
