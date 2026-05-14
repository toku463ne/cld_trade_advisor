# USDJPY Corr-Axis — OOS Holdout Sanity Check

Generated: 2026-05-14  
Split: train fire_date < 2024-04-01 (28,428 events), test fire_date ≥ 2024-04-01 (20,302 events)  
Tertile cuts fit on **train only**: N225 [+0.404, +0.625]  USDJPY [-0.089, +0.120]  

## Verdict: **FAIL — OOS holdout does not reproduce**

- Discovered cells on train (n≥100, ΔDR≥+3pp, ΔEV≥0): **16**
- OOS reproduction: **10/16** cells keep ΔDR > 0 on test (62%) — gate ≥50%  ✓
- Mean OOS ΔDR over discovered cells: **-1.39pp**
- Pooled test asymmetry DR(U=L)−DR(U=H): **+2.06pp** — gate >0  ✓
- Test-set shuffle p (max-over-cells, 1000 perms): **0.0620** — gate <0.05  ✗

## Per-N225-bucket asymmetry on test set (all signs pooled)

| N225 bucket | n(U=L) | n(U=H) | DR(U=L) | DR(U=H) | DR(U=L)−DR(U=H) |
|-------------|-------:|-------:|--------:|--------:|----------------:|
| L | 3019 | 3492 | 52.6% | 50.4% | +2.23pp |
| M | 1935 | 1957 | 57.6% | 53.2% | +4.33pp |
| H | 2070 | 975 | 53.5% | 56.4% | -2.93pp |

## Discovered cells (train) vs OOS (test)

| sign | N | U | train n | train ΔDR | train ΔEV | test n | test ΔDR | test ΔEV | OOS? |
|------|---|---|--------:|----------:|----------:|-------:|---------:|---------:|:----:|
| rev_nlo | H | L | 155 | +14.62pp | +2.33pp | 280 | +2.91pp | +0.81pp | ✓ |
| str_hold | H | L | 481 | +8.90pp | +0.86pp | 256 | -0.39pp | +2.13pp | ✗ |
| div_gap | M | M | 115 | +7.87pp | +2.37pp | 109 | -6.45pp | -0.29pp | ✗ |
| rev_nhi | L | M | 639 | +7.52pp | +0.89pp | 674 | +3.84pp | +0.98pp | ✓ |
| rev_lo | H | L | 590 | +7.06pp | +0.93pp | 204 | -5.48pp | -1.01pp | ✗ |
| str_hold | M | M | 731 | +4.97pp | +1.16pp | 502 | +0.80pp | +0.82pp | ✓ |
| brk_bol | H | M | 145 | +4.58pp | +0.72pp | 65 | +3.28pp | +2.53pp | ✓ |
| str_lead | M | M | 121 | +4.42pp | +0.94pp | 43 | -28.54pp | -3.89pp | ✗ |
| rev_nlo | H | M | 230 | +4.41pp | +0.48pp | 154 | -3.58pp | -1.44pp | ✗ |
| corr_shift | L | M | 162 | +4.29pp | +0.18pp | 162 | +0.47pp | -1.29pp | ✓ |
| rev_lo | M | L | 547 | +4.23pp | +0.70pp | 209 | +7.49pp | -0.05pp | ✓ |
| rev_hi | H | M | 610 | +4.08pp | +0.65pp | 228 | +1.51pp | -0.56pp | ✓ |
| rev_lo | L | L | 365 | +3.96pp | +0.58pp | 282 | +2.07pp | +0.86pp | ✓ |
| str_hold | L | L | 853 | +3.49pp | +0.86pp | 845 | +5.61pp | +2.08pp | ✓ |
| str_hold | M | L | 725 | +3.12pp | +1.07pp | 413 | +2.43pp | +0.60pp | ✓ |
| brk_bol | M | H | 198 | +3.03pp | +0.75pp | 100 | -8.19pp | -1.13pp | ✗ |

## Test-set shuffle falsifier

Observed max ΔDR (test, n≥100): **+15.31pp**  
Permutation max ΔDR: min=+5.47pp, median=+10.30pp, 95th=+15.74pp, max=+25.15pp  
p-value: **0.0620** (1000 perms)
