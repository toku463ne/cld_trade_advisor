# RegimeSign Strategy — benchmark & fill-order null (2026-05-29)

**Verdict: KEEP in the Daily-tab UI.** RegimeSign is *not* the weak sister
to Confluence. On the binding metric — fill-order-robust portfolio Sharpe on
the capital-aware 6-slot book — RegimeSign scores **+1.03**, on par with
Confluence's ~+1.02. The apparent gap (RegimeSign +1.33 vs Confluence +3.80)
was an artifact of FY-equal-weighted *per-trade* Sharpe and disappears once the
portfolio is modeled with slot contention and capital weighting.

Operator question: *"Calculate benchmark of regime_sign strategy. If not good
enough, consider removing it from the UI."* — Answer: good enough, keep it.

## Configuration

- Entry: `RegimeSignStrategy` (Kumo gate + ADX veto + sign rank), backtest mode
- Exit: `ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)`
- Fill: two-bar rule (signal on T, fill at T+1 open)
- Book: ≤1 high-corr + ≤5 low/mid-corr = 6 slots
  (`exit_simulator._MAX_HIGH_CORR=1`, `_MAX_LOW_CORR=5`)
- Walk-forward: each FY ranks on the prior years' `SignBenchmarkRun` universe
  (cumulative, max 5 yrs); FY2025 is true out-of-sample (`classified2024`)

## 1. Per-trade backtest (refreshed 2026-05-29)

`src/analysis/regime_sign_backtest.md` — 357 trades over FY2019–FY2025.

| FY | trades | mean_r | win% | Sharpe\* |
|----|--:|--:|--:|--:|
| FY2019 | 58 | −1.73% | 39.7% | −2.61 |
| FY2020 | 46 | +5.34% | 58.7% | +6.72 |
| FY2021 | 50 | +1.35% | 52.0% | +2.10 |
| FY2022 | 46 | +1.20% | 54.3% | +2.12 |
| FY2023 | 50 | +4.35% | 72.0% | +7.69 |
| FY2024 | 51 | +1.07% | 52.9% | +1.52 |
| **FY2025 (OOS)** | 56 | **+3.43%** | 67.9% | +5.62 |
| **Aggregate** | 357 | **+2.05%** | 56.6% | 3.10 |

\* per-trade Sharpe. **6 of 7 FYs positive**, only FY2019 (thinnest/earliest
universe) negative; out-of-sample FY2025 is one of the strongest years.

The sign set has grown materially since the stale `+1.33` record (commit
bc758d0 / 78f4344, 171 trades): the strong new carriers are `brk_sma`
(+6.81%), `rev_hi` (+6.07%), `str_lead` (+6.13%), `corr_flip` (+2.66%).

By corr_mode (aggregate): low/mid 298 trades +2.19% (the genuine-diversification
sleeve, positive every FY except FY2019); high 59 trades +1.34% (weak in down
years — FY2021 −2.91%, FY2024 −2.63% — but capped at ≤1 slot).

**Caveat:** per-trade Sharpe is *not* the portfolio metric. Per CLAUDE.md it
routinely overstates because it ignores slot contention and capital weighting.
The 3.10 headline is not the number to trust — section 2 is.

## 2. Fill-order null — the binding metric

`src/analysis/regime_sign_fill_order_null.py` — 200 within-day fill-order
shuffles through the 6-slot capital-aware book, stitched daily portfolio
returns, FY2019–FY2025. The book SKIPS (does not queue) when full, so the
realized trade set is one path that depends on fill order; the shuffle null is
the order-luck confidence band.

| arm | Sharpe | total return | maxDD | pctile | perm p |
|-----|--:|--:|--:|--:|--:|
| **shuffle null** | **+1.03** (sd 0.09)<br>p5 +0.90 · p50 +1.03 · p95 +1.19 | +223% [+174, +285] | −30% [−32, −28] | — | — |
| baseline (shipped entry-date order) | **+1.01** | +210.9% | −28.4% | 40% | 0.595 |

Reading:
- **Capital-aware portfolio Sharpe ≈ +1.0**, not +3.10. The per-trade headline
  overstated ~3×, exactly as warned.
- **Robustly positive** — even the unlucky p5 draw is +0.90; order luck never
  flips the sign. Tight band (sd 0.09).
- **The shipped order sits at p40 (perm p 0.595)** — a typical draw, neither
  lucky nor cherry-picked.

## 3. Head-to-head with Confluence

| metric | RegimeSign | Confluence (N≥3) |
|--------|--:|--:|
| per-trade Sharpe (FY-avg) | +1.33 *(stale)* / 3.10 *(2026-05-29 pooled)* | +3.80 |
| **fill-order-null portfolio Sharpe (6-slot)** | **+1.03** | **~+1.02** |

Sharpe is invariant to the per-slot capital divisor (a constant scaling cancels
in mean/std), so the 6-vs-4-slot divisor difference vs `confluence_slot_order`
does not distort the comparison; the FY span differs slightly (RegimeSign
FY2019–2025 vs Confluence FY2017–2025). Confluence's +1.02 is its capacity-null
6-slot figure (see [confluence fill-order null](#) / memory
`project_confluence_fill_order_null`).

**The per-trade / FY-avg gap vanishes at the portfolio level.** Both sister
strategies deliver ~+1.0 fill-order-robust portfolio Sharpe and surface
somewhat different cohorts in the Daily table. There is no case to remove
RegimeSign from the UI.

## Reproduce

```bash
# per-trade backtest (writes src/analysis/regime_sign_backtest.md)
uv run --env-file devenv python -m src.analysis.regime_sign_backtest

# fill-order null (prints the section-2 table, read-only)
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_fill_order_null
```

## Code notes

- `regime_sign_backtest.build_fy_candidates()` / `FyCandidateSet` were extracted
  from `run_fy` so the null reuses the *exact* shipped candidate pool; `run_fy`
  and `main` behavior are unchanged.
- All metrics use the production `run_simulation` 6-slot book; live trading is
  manual (the live plan is the 6-slot equal-weight Confluence book — see memory
  `project_live_trading_plan`).
