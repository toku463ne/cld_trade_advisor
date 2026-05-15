# peak5_exit_selector_probe — Workflow B

Generated: 2026-05-15
Universe: 223 stocks · Fires (after exit replay): 56,848
Discover/Validate/OOS: 33,024 / 16,342 / 7,482

## Verdict: **REJECT**

## Pre-registered falsifier gates

| Gate | Observed | Threshold | Pass? |
|------|----------|-----------|-------|
| HDBSCAN noise frac (Discover, full) | 0.479 | ≤ 0.50 | ✓ |
| Clusters with n≥100 on Discover | 2 | ≥ 3 | ✗ |
| Selector ΔSharpe vs default on Validate | +0.163 | ≥ +0.15 | ✓ |
| Selector ΔSharpe vs default on OOS | +0.442 | ≥ +0.10 | ✓ |

## Default-rule baselines (aggregate Sharpe over slice, no clustering)

| Slice | TIME20 | TRAIL | TPSL | Default(best) |
|-------|--------|-------|------|---------------|
| Discover | -0.169 | -0.438 | -0.208 | **TIME20**=-0.169 |
| Validate | -0.219 | -0.006 | -0.302 | **TIME20**=-0.219 |
| OOS | 0.792 | 2.418 | 1.061 | **TIME20**=0.792 |

## Discovery clusters (Discover slice, n≥100, default rule = TIME20)

| cluster | n | long_frac | TIME20_sh | TRAIL_sh | TPSL_sh | best_rule | best_sh |
|---------|---|-----------|-----------|----------|---------|-----------|---------|
| 1 | 8686 | 0.436 | 0.704 | 0.551 | 1.354 | TPSL | 1.354 |
| 0 | 8525 | 0.592 | -0.844 | -1.417 | -1.583 | TIME20 | -0.844 |

## Selector aggregates

| Slice | n | Selector mean_r | Selector Sharpe | Default mean_r | Default Sharpe | ΔSharpe |
|-------|---|-----------------|-----------------|----------------|----------------|---------|
| Validate | 16342 | -0.03% | -0.055 | -0.12% | -0.219 | **+0.163** |
| OOS | 7482 | +0.72% | 1.234 | +0.49% | 0.792 | **+0.442** |

## Notes
- Exit rules are SIMPLIFIED proxies, not the production `src/exit/` rules.
  TIME20 = exit at fire+20 close. TRAIL = exit on 4×ATR drawdown from peak.
  TPSL = TP at 3×ATR, SL at 2×ATR, 60-bar cap. If clusters meaningfully prefer
  different rules, next step is faithful integration with src/exit/.
- Fires entered at fire_bar (P4-early peak bar). For production use, the same
  shape clusters would need verification at sign-driven entry bars.
- HDBSCAN fit on 5k subsample (corr-distance precomputed); approximate-predict
  for remaining points via nearest-fit-neighbor on corr-distance.