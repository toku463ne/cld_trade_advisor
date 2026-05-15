# long_short_asym_exit_probe — 9-pair A/B

Generated: 2026-05-15
Fires: 56,848 (Discover 33,024 / Validate 16,342 / OOS 7,482)

## Verdict: **ACCEPT — asymmetric pair beats universal across slices**

## Pre-registered gates

| Gate | Observed | Threshold | Pass? |
|------|----------|-----------|-------|
| G1 Discover ΔSharpe (pair − universal) | +0.337 | ≥ +0.10 (and pair must be asymmetric) | ✓ |
| G2 Validate Sharpe vs Validate universal | -0.008 vs -0.006 | ≥ -0.056 (−0.05 slack) | ✓ |
| G3 OOS Sharpe vs OOS universal | 3.892 vs 2.418 | ≥ 2.468 (+0.05 required) | ✓ |
| G4 n_long ≥ 1000 AND n_short ≥ 1000 in all slices | min nL=4195, min nS=3250 | ≥ 1000 | ✓ |

## Selected pair (best on Discover)
- **long_rule = TRAIL**
- **short_rule = TPSL**
- Symmetric? False
- Discover Sharpe: 0.168  ·  Validate: -0.008  ·  OOS: 3.892

Universal-best on Discover: **TIME20/TIME20** (Sharpe -0.169)

## Full 9-pair grid

### Discover

| long\short | TIME20 | TRAIL | TPSL |
|------------|--------|-------|------|
| **TIME20** | -0.169 *(symm)* | -0.704 | -0.106 |
| **TRAIL** | 0.078 | -0.438 *(symm)* | 0.168 |
| **TPSL** | -0.259 | -0.848 | -0.208 *(symm)* |

### Validate

| long\short | TIME20 | TRAIL | TPSL |
|------------|--------|-------|------|
| **TIME20** | -0.219 *(symm)* | 0.067 | 0.083 |
| **TRAIL** | -0.262 | -0.006 *(symm)* | -0.008 |
| **TPSL** | -0.589 | -0.218 | -0.302 *(symm)* |

### OOS

| long\short | TIME20 | TRAIL | TPSL |
|------------|--------|-------|------|
| **TIME20** | 0.792 *(symm)* | 0.123 | 1.995 |
| **TRAIL** | 2.905 | 2.418 *(symm)* | 3.892 |
| **TPSL** | -0.233 | -1.038 | 1.061 *(symm)* |

## Notes
- Exit-rule proxies (TIME20, TRAIL 4×ATR, TPSL 3/2 ATR 60-bar cap) are simplified;
  not production `src/exit/` rules. Direction of signal portable, absolute Sharpe not.
- Fires are peak-anchored (P4-early). Generalization to sign-driven entries untested.
- Per-pair n's are roughly half of the slice total (one rule each for longs/shorts).
