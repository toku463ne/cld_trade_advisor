# Investment Thesis — Standalone PEAD Sleeve

**Status:** draft for commit · **Date:** 2026-05-26 · **Author:** operator (calibrated with Claude)
**Supersedes:** the Route-1/Route-2 worst-year-rescue framing (refuted — see §2)

## 1. Thesis

The absolute forecast-revision signal (signal 1, ACCEPT, +2.51% N225-cohort) is a **real,
exogenous, cross-sectionally-validated alpha** that the confluence book cannot harvest — every
attempt to wire it into the 6-slot book (vote, score-booster) died to the fill-order null, not
because the key was wrong but because ~36 trades/yr has too little slot contention to reward
*any* selection key. A **standalone sleeve** is the structurally distinct harvest path: its own
candidate pool, its own capital, no reordered shared book, so the fill-order null does not
pre-kill it.

We are betting that a small PEAD sleeve adds **uncorrelated selection alpha** to the portfolio.
We are **not** betting on downside protection. Calibration (§2) shows PEAD up-revision longs run
**beta ≈ 1.0–1.1** and were *more* hurt than N225 in the worst confluence year (FY2019, −4.34%
vs −1.58%, negative alpha). The honest proposition: **uncorrelated selection alpha, paid for with
full market beta.**

## 2. What calibration changed (so the goalposts are fixed before results)

- **Worst-year rescue is refuted.** Beta≈1, no hedge effect; the sleeve would have *amplified*
  FY2019. Worst-year is demoted to a do-no-harm diagnostic, never a primary criterion.
- **The alpha is real but regime-shaped.** Pooled down-year alpha +1.84% (carried by FY2022's
  weak-tide +4.32%); non-down-year alpha +3–6% (FY2021/FY2026). The edge lives in normal / up /
  weak-tide markets.
- **A beta-1 sleeve mechanically lifts Sharpe via added market exposure.** Any naive improvement
  could be leverage, not alpha. The null must control for this (§4).
- **PEAD up-fires are pro-cyclical**, so the sleeve's market-exposure *timing* is a confound to
  strip, not a feature to bank — see §4. The validated edge is *selection* (which names), not
  *timing* (when to be exposed).

(Evidence: `src/analysis/pead_forecast_revision.py` [signal 1 ACCEPT],
`src/analysis/pead_updrift_vs_n225.py` [the beta≈1 / worst-year / down-year-alpha calibration].)

## 3. Capital & tiers (pre-committed)

Single shared ¥2,000,000 account. The sleeve is funded by **displacing confluence capital**, so
the honest A/B is always *same total capital, sleeve-on vs sleeve-off*.

| Tier | Trigger | Allocation |
|---|---|---|
| **T0 — Validation** | now | ¥0 live; backtest + Daily-tab shadow only |
| **T1 — Probe** | passes §4 primary in backtest | ¥0.6M sleeve / ¥1.4M confluence (~30%); ~2 sleeve slots |
| **T2 — Scale** | ≥~1yr T1 with **execution discipline held through a drawdown** (not P&L) | revisit allocation; never auto |
| **Migration** | sleeve beats confluence on the **same ¥2M** under the same null | separate, higher bar (§7) |

Scaling gates on *discipline*, not returns — consistent with the live-trading-plan (62% beta +
path variance means 1yr can't prove edge). The ¥0.6M / ~2-slot T1 sizing is a placeholder so the
A/B has a concrete capital split to model; the design proposal may refine it.

## 4. Primary criterion — the only binding gate

A **clustering-aware paired null on *selection alpha***, on the capital-aware stitched equity
curve across all complete FYs. We decompose the sleeve's contribution against a nested benchmark
ladder so that leverage and exposure-timing are stripped, and only the validated (selection)
component governs the deploy decision:

| # | Book (all blended, same ¥2M) | Isolates |
|---|---|---|
| 1 | confluence ¥2M (sleeve-off) | baseline |
| 2 | confluence + constant-ETF to the sleeve's *average* beta | removes **leverage / avg exposure** |
| 3 | confluence + ETF held **on the sleeve's actual signal-days, at each position's beta** | adds the sleeve's **exposure schedule** to a dumb index |
| 4 | confluence + the real PEAD sleeve | full sleeve |

- **(4 vs 3) = selection alpha → the BINDING gate.** Did real PEAD names beat the index over
  *identical exposure windows at identical beta*? Book 3 is the portfolio aggregate of the
  beta-stripped CARs (replace each PEAD name with β·index over its hold window) — same event
  records the calibration probe used, no new data.
- **(3 vs 2) = timing alpha → DIAGNOSTIC ONLY**, reported skeptically. Pro-cyclical and
  regime-fragile; explicitly **not** part of the deploy decision.
- **(2 vs 1) = leverage / avg-beta → never credited** as a PEAD edge.

**Null mechanics:** block-bootstrap by reporting window (resample Japanese earnings windows with
replacement — respects the temporal clustering that makes i.i.d. trade bootstraps lie); **paired**
(identical resampled blocks *and* identical fill-order shuffles fed to both arms per seed); **K ≥
1000** resamples. Statistic: ΔSharpe (book 4 − book 3) on the stitched curve. **Gate:**
`P(ΔSharpe > 0) ≥ 0.95` **AND** 95% CI lower bound `> 0`. **No pre-committed effect size** — if
the machinery can't detect it at this capital, it isn't deployable.

If the sleeve clears the timing test (3 vs 2) but fails the selection test (4 vs 3), it is
**rejected as a PEAD-alpha sleeve** — it would be a pro-cyclical market-timing strategy wearing a
PEAD coat, warranting its own separate pre-registration with timing-appropriate skepticism, not
deployment under this thesis.

## 5. Secondary guardrails (must hold; guardrails, not the decision)

- **Independence:** beta-stripped (alpha) daily-return correlation between sleeve and confluence
  **< 0.5**. Raw correlation is not tested — it is mostly shared market beta and would fail a real
  alpha sleeve.
- **OOS direction:** FY2025 and the FY2026-partial blended ΔReturn share the sign of the
  full-sample estimate. Diagnostic (single-FY noise), not pass/fail.

## 6. Do-no-harm diagnostic

The worst confluence FY's blended return must not be **materially** worse than sleeve-off (bound:
≥ −1.5pp). Reported and monitored; a breach prompts review, not automatic rejection — the primary
null already governs the decision. (This exists only because FY2019 proved the sleeve *can*
amplify a bad year.)

## 7. Migration bar (distinct from the additive bar)

T1/T2 test whether the sleeve **adds** uncorrelated selection alpha. *Migration* — letting the
sleeve replace or dominate confluence — requires it to **beat confluence on the same ¥2M**
(sleeve-only vs confluence-only) under the identical §4 selection-alpha null. This test is **not
run** unless the additive test passes first, and is a separate pre-registration.

## 8. Anti-mining / pre-registration discipline

- **Lock the design before the null.** Candidate pool, sign set, exit rule, slot count, high-corr
  handling, and universe are fixed in the design-proposal phase and frozen before the null is
  computed.
- **One primary metric, one null.** No metric shopping; per-trade EV is explicitly *not* the
  criterion (it already passed and isn't the binding question).
- **No second formulation after seeing results** — the no-iteration clause applied throughout this
  project. A failed null is final for that design; a new design requires a new pre-registration.
- **The nested-benchmark ladder (§4)** blocks the two most likely false positives (leverage and
  pro-cyclical timing).
- **Report all FYs**, no cherry-picking; fixed K and seed protocol stated up front.

## 9. Falsifier (one line)

> If the clustering-aware paired null on **selection alpha** (book 4 vs book 3) does not reach
> `P(Δ>0) ≥ 0.95` with a 95% CI lower bound `> 0`, the sleeve is rejected — the PEAD selection
> alpha does not survive portfolio construction at deployable capital.

## 10. Deliberately *not* decided here (→ design proposal)

Candidate pool definition · sign set (signal-1 absolute revision is the anchor) · exit rule and
hold horizon · slot pool mechanics · high-corr / concurrency handling · whether the universe stays
at the N225 cohort or expands. These are the design-proposal's job; the thesis only fixes the
*business decision and the bar*.
