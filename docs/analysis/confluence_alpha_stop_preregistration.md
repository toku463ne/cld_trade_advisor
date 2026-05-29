# Pre-registration — Per-stock β-stripped ALPHA stop (backlog item 7)

**Date:** 2026-05-29 · **Type:** exit lever, the untested 2×2 quadrant · **Status:** frozen before run
**Script:** `src/analysis/confluence_alpha_stop_probe.py`
**Binding:** paired fill-order null on the 6-slot book **+ explicit whipsaw-rate check** (CLAUDE.md +
backlog item 7). This script is the **discovery screen** that gates escalation to the full null.

## Hypothesis & why it is distinct

The confluence exit space is a 2×2 {per-stock, market-regime} × {raw-price, β-stripped alpha}. Three
cells are settled: per-stock raw-price = **exhausted** (ZsTpSl ≈ best, ~14 variants); market-regime =
**REJECT** (item 3, regime-inverse trap). The **open cell is per-stock β-stripped alpha**: exit a held
name on erosion of its OWN cumulative alpha (β from a pre-entry window), not on the market.

**Why it does not pre-die like item 3:** stripping the market means it fires only on names breaking down
**idiosyncratically** — it does NOT de-risk off the market regime, so it does not fight the regime-inverse
bear-recovery alpha that killed the market-regime exit + TSMOM gate.

**CAVEAT that frames the axis (stated up front):** the book's −22% drawdown is **beta-driven** (β≈0.7 long
book). An alpha stop strips the market, so it will **NOT** cut that drawdown. This is a **RETURN/alpha
lever** (cut idiosyncratic losers), closer to item 2 than to a drawdown lever. Judge it on Sharpe/CAGR.

**PRIOR (the realistic failure mode):** the only alpha-stop tested anywhere
(`pead_sleeve_alpha_stop_probe`, PEAD sleeve β≈1) **whipsawed** — Δmean-alpha < 0 at every θ, names
recovered through transient alpha dips. Different cohort (PEAD drift, β≈1) so untested on confluence
breakouts, but it sets the prior: transient alpha dips that recover ⇒ a stop churns and loses.

## Method (post-hoc exit override, mirrors `confluence_regime_exit_probe`)

Reconstruct the production capital-aware ¥2M 6-slot book (budget path, `_MAX_LOW_CORR=5`), FY2018–2025.
On the baseline filled trades, override each trade's exit to the **earlier of** its production ZsTpSl exit
or the first bar its alpha-stop triggers. Freed slots are **NOT** re-filled (conservative on return; same
entry set across arms → clean apples-to-apples).

**β (frozen):** trailing **60-bar** daily-return regression of the stock on **^N225**, measured strictly
**before** `entry_date` (β = cov/var; skip the stop for that trade if < 40 usable return pairs).

**Alpha path:** anchored at `entry_date` close. For each hold bar `d`,
`α_cum[d] = (s[d]/s[entry] − 1) − β·(n225[d]/n225[entry] − 1)`.

**Two stop families (sweep):**
- **LEVEL** `lvlNN`: exit at first bar with `α_cum ≤ −NN%`. θ ∈ {−5%, −8%, −12%}.
- **TRAIL** `trlNN`: track running peak of `α_cum`; exit at first bar with `α_cum ≤ peak − NN%`. X ∈ {5%, 8%, 12%}.

Exit executes at that bar's close (`cmap[hit]`), matching the regime-exit probe's convention.

## Whipsaw-rate check (binding sub-gate)

For every alpha-stop exit, compare `α_cum` at the stop bar (`a_hit`) to `α_cum` at the **baseline** exit
date (`a_base`): **WHIPSAW** if `a_base > a_hit` (the name recovered — premature stop); **HELPED** if
`a_base < a_hit`. Report `whip%` per variant. A stop that whipsaws ≥ 50% is churning recoveries.

## Frozen discovery gate (decides escalation vs REJECT)

A variant escalates to the paired fill-order null **iff ALL**:
1. **Return lever:** stitched **Sharpe ≥ baseline + 0.05** OR stitched **CAGR ≥ baseline + 1.0pp**.
2. **Whipsaw:** `whip% < 50%`.
3. **No regime-inverse damage:** FY2024 Sharpe ≥ baseline FY2024 − 0.30 (the canary; an alpha stop
   *should* leave it intact since it strips the market — if it guts FY2024 that is itself a finding).

If **no** variant clears all three → **REJECT** (no escalation), as item 3 was handled. If one clears,
escalate to the K=200 paired fill-order null on the 6-slot book (return-judged) in a follow-up.

## Expected result (stated, not used to pre-judge)

Two forces point to REJECT: (a) the prior (PEAD alpha-stop whipsawed — breakout pullbacks recover);
(b) the axis caveat (it can't cut the beta-driven drawdown, so its only hope is a return lift, which the
whipsaw prior argues against). The gate decides; this is the falsifiable bet.
