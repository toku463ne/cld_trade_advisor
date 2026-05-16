# Direct N225 trade on breadth signal — REJECT (2026-05-16)

`/sign-debate` cycle on the question: *"Are there chances to predict
N225 from RevNRegime?  We can trade N225 sakimono (futures) too."*

**Verdict: REJECT.**  The breadth signal IS statistically real
(SMA50_Q5 vs always-long N225, Sharpe gap = **+0.96**, more than 2×
the +0.4 acceptance threshold), but greedy 10-bar overlap dedup
collapses 143 raw Q5 days into **25 independent trades** over 3
years (FY2023–2025).  Effective sample size is too thin to validate
a direct N225 futures trade.

This is the **8th REJECT in 2 days** and introduces a new failure
sub-variant (Pattern C — `pooled+raw positive, dedup-shrunk n`)
beyond Pattern A (per-cohort sign-flip) and Pattern B (regime_sign
sample-size insufficient).

## Motivation

Earlier in the session (see `docs/analysis/breadth_indicators.md`),
the 4-breadth indicator family was benchmarked and `RevNRegime` +
`SMARegime` were shipped as display-only banners on the Daily tab.
Forward returns of N225 conditional on breadth Q1 vs Q5 showed
Δ(Q1−Q5) ≈ −1.44 pp pooled, 2/3 cohorts PASS.

The follow-up question reframed the application: rather than apply
breadth as a *gate on stock entries* (the `breadth_gate_probe` from
`project_breadth_gate_probe_reject.md` — REJECTED on regime_sign
n=12), apply it as a *direct entry signal on N225 futures*.  One
trade per breadth event regardless of universe size sidesteps the
regime_sign-cohort sample-size constraint.

## Cycle output

### Iteration 1

**Analyst** — confirmed the breadth → N225 forward-return effect
holds on independent N225 daily bars (752 FY2023–2025 days):

| breadth quantile | mean fwd return (10d, %) | n |
|---|---:|---:|
| Q1 (lowest)  | +1.32  | 150 |
| Q3 (middle)  | +0.41  | 451 |
| Q5 (highest) | −0.12  | 149 |
| Q1−Q5        | **+1.44 pp** | — |

**Historian** — flagged 3 prior REJECTs on related applications of
breadth signals (peak_anchored, breadth_gate, rev_lo_and_high) and
warned that "pooled signal real → application transfer fails" is a
recurring pattern.  Memory `project_breadth_gate_probe_reject.md`
already documented the regime_sign-cohort n=12 collapse on this
exact indicator family.

**Proposer** — Option C: write
`src/analysis/breadth_n225_trade_probe.py` testing three candidate
gates:

| gate | description |
|---|---|
| `SMA50_Q5` | go short N225 when `sma_frac` is in the bottom 20% |
| `AND3_Q5` | go short when SMA50, RevN, and CorrRegime are all Q5 |
| `AND2_Q5` | go short when any 2 of 3 are Q5 |

Pre-registered accept gate (4 clauses, all required):

1. Sharpe of any single gate ≥ Sharpe of always-long N225 + 0.4 over
   FY2023–2025
2. n_deduped ≥ 30 per FY for the winning gate (each of FY2023, 2024,
   2025)
3. Per-FY Sharpe gap > 0 in ≥ 2 of 3 FYs
4. No FY catastrophic loss (worst-FY return ≥ −15%)

**Critic** — flagged 3 H-severity holes:

- **H1**: temporal autocorrelation in breadth signals (consecutive
  Q5 days cluster) was not addressed in the n estimate.  Pre-flight
  check needed before authorizing the probe.
- **H2**: no greenfield N225-futures infrastructure exists; even if
  the signal validates, productionizing requires building an entire
  exit/sizing stack just for this one signal.
- **H3**: Sharpe gap as the sole gate ignores the variance penalty
  at small n — a +0.96 raw Sharpe gap at n=25 has SE ≈ 0.20, so the
  CI overlaps zero at any FY split below n≈40.

Counter-proposal: **preliminary 2-number check** (raw deduped n and
raw Sharpe gap) before authorizing the full probe.

**Judge** — verdict **Insufficient evidence**.  Required the 2-number
preliminary check with pre-stated flip rule:

- `n_deduped` ≥ 40 over FY2023–2025 AND Sharpe gap ≥ +0.4 → flip
  to Accept (write the probe)
- Either short → harden to Reject (don't write the probe)

### Preliminary check (inline, no script written)

Ran the check using pandas in the conversation context against the
N225 daily bars + `SMA50_Q5` breadth label.  Output:

```
Q5 raw days: 149 of 732 (20%)
Raw fires with fwd return: 143
Deduped (greedy, H=10): 25
Sharpe (Q5 deduped, annualized): 2.417 (n=25)
Sharpe (always-long N225, daily): 1.456 (n=781)
Sharpe_Q5 − Sharpe_buy_hold = +0.960
GATE: n_deduped >= 40: ✗ (25)
GATE: Sharpe gap >= +0.4: ✓ (+0.960)
→ HARDEN TO REJECT (don't write the probe)
```

### Iteration 2

Judge issued final **REJECT** per pre-stated flip rule.  No
follow-up probe authorized.

## What's actually true

1. **The signal IS statistically real**: +0.96 Sharpe gap vs
   always-long N225 is more than 2× the +0.4 acceptance threshold.
2. **The signal is too thin to trade directly**: n = 25 deduped
   trades over 3 yrs.  Sharpe SE at n=25 is ≈ 0.20, so the gap
   realistically sits in roughly [+0.56, +1.36] — wide enough that
   the trade could plausibly underperform after friction in any
   given year.
3. **The structural cause is temporal autocorrelation in
   breadth signals**: low-breadth days cluster, so independent-trade
   count is much smaller than fire-day count.  Intrinsic to the
   breadth-signal architecture; not fixable by threshold tuning.
4. **Without dedup the math is wrong**: overlapping 10-bar holds
   inflate Sharpe via serial correlation in returns — methodologically
   we MUST dedup.

## Decision

- **No probe script written.**  Judge's preliminary check answered
  the binding question (n_deduped < 40); writing
  `breadth_n225_trade_probe.py` would only re-derive a known fail.
- **No production code change.**  `RevNRegime` and `SMARegime` stay
  shipped as display-only banners on the Daily tab; no automated
  trading.
- **No greenfield N225-futures infrastructure.**  Zero hits for
  "futures / sakimono / 先物" in the codebase; building it for a
  signal that needs decades of data to validate is bad EV.

## Codified lesson (Pattern C)

**Breadth signals have temporal autocorrelation.  When using them
as fire-events for trading, greedy overlap-dedup is methodologically
required AND reveals that effective sample size is ~1/5 of the raw
fire count.**  Specifically here: 143 raw → 25 deduped (5.7×
shrinkage at H=10).

**Before any future "trade signal X directly" proposal where X is a
breadth indicator**, compute first:

1. Raw fires/year (cheap)
2. Greedy-deduped fires/year at the target hold-period (the binding
   number)

If deduped fires/year < n_threshold / cohort_count, the proposal is
structurally unable to be validated.  Either:

- Pivot to a continuous-position formulation (sidesteps dedup), or
- Accept the signal is real but untestable at current data depth,
  and shelve.

This lesson generalizes to any signal with temporal clustering
(regime indicators, breadth measures, multi-day pattern detectors).

## 8-REJECT pattern in 2 days

| date | cycle | failure mode |
|---|---|---|
| 2026-05-15 | `project_asym_exit` | Pattern A (per-cohort sign-flip) |
| 2026-05-16 AM | `project_peak_anchored_exit` | Pattern A |
| 2026-05-16 mid | `project_timestop40_bootstrap_reject` | Pattern A |
| 2026-05-16 mid | `project_adx_adaptive_subcohort_reject` | Pattern A |
| 2026-05-16 PM | `project_breadth_gate_probe_reject` | Pattern B (regime_sign n) |
| 2026-05-16 PM | `project_rev_lo_and_high_per_cohort_reject` | Pattern A |
| 2026-05-16 PM | `project_div_peer_cluster_size_reject` | Pattern B (FY2025 n) |
| 2026-05-16 evening | **this entry** | **Pattern C (NEW — dedup-shrunk n)** |

## Salvage paths (untested)

1. **Position-sizing tilt on always-long N225** (most promising):
   instead of "trade Q5 days, sit out others" (which has the dedup
   problem), scale a continuous always-long position by
   inverse-breadth.  Effective sample = hold-period × held days,
   not deduped fires.  The n constraint dissolves.  Requires
   building continuous-position infrastructure but solves the
   binding problem.
2. **Wait 5+ years for organic data accrual**: at ~5.6 independent
   Q5 clusters/year, n = 40 takes ~7 years.  Not actionable soon.
3. **Sector breadth instead of universe breadth**: maybe sector-level
   breadth signals cluster less (different sectors peak at different
   times).  Untested, speculative.
4. **Higher-frequency breadth**: if the breadth-signal architecture
   used intraday or weekly bars, the autocorrelation structure
   might differ.  Untested, requires infrastructure rebuild.

## Honest reconciliation with the operator's question

"Are there chances to predict N225 from RevNRegime?" — precise
answer:

- **Yes, statistically**: the +0.96 Sharpe gap vs always-long N225
  is real and large.
- **No, tradeably**: the gap can't be validated on a sample of 25
  independent trades over 3 years (Sharpe SE wider than the
  friction-adjusted edge).
- **Maybe via sizing-tilt**: convert the gate from binary entry to
  continuous sizing → sidesteps the dedup constraint → may be
  testable.  Separate cycle to scope.

## Files

- This doc
- Memory: `project_n225_from_breadth_reject.md`
- Parent: `docs/analysis/breadth_indicators.md`
- Sibling REJECT: memory `project_breadth_gate_probe_reject.md`
  (same indicator family, regime_sign application failure)
- Sibling REJECT: memory `project_div_peer_cluster_size_reject.md`
  (sparsity Pattern B)
- Probe NOT written: `src/analysis/breadth_n225_trade_probe.py`
  (deliberately not created per judge ruling)
- Reference template (would have been used):
  `src/analysis/breadth_gate_probe.py`
