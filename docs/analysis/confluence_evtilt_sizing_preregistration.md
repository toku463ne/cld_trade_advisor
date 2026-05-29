# Pre-registration — Conditional-EV sizing tilt (trim neutral-momentum entries) — backlog item 2

**Frozen:** 2026-05-29 · **Status:** pre-registered, frozen before running · **One pre-reg at a time**
**Script:** `src/analysis/confluence_evtilt_null.py` (read-only, no DB writes)

## Hypothesis
`project_confluence_phase_regime` (the `confluence_regime_pooling.py` run, 4,656 distinct pooled trades)
found per-trade EV is **non-monotone** in N225 trailing-60-bar momentum, with **NEUTRAL the weak spot**,
and the weakness **survives β-strip** (so it is signal-quality, not missing beta):

| N225 60-bar regime | raw mean_r | α (β-stripped) | α DR |
|---|---|---|---|
| bearish (≤ −0.1%)  | +1.31% | +0.57% | 53.2% |
| **neutral (≤ +8.1%)** | **+0.52%** | **+0.33%** | **52.1%** |
| bullish (> +8.1%)  | +3.31% | +1.20% | 57.3% |

Neutral EV is **positive but lowest** → the prior analysis concluded **"trim, not skip."** Trimming
weight on neutral-regime entries (keeping the slot filled) should raise book Sharpe IF neutral's
**risk-adjusted** contribution is below the book average. This is the **weights axis** — it changes how
much capital a filled name gets, NOT which names fill the 6 slots — so it is **not pre-killed by the
fill-order null** (unlike skip/veto/reorder selection rules). It conditions on **local entry momentum**,
a different axis than the FY-level regime-inverse alpha (`project_confluence_market_neutral`) and from
the market-regime *exit* (item 3, rejected) — do not conflate.

## Frozen regime definition (no outcome-fitting)
- Regime of a fill = N225 **trailing-60-bar momentum** (`close[t]/close[t−60] − 1`) on its `entry_date`.
- **Frozen tercile cutoffs from the prior independent pooled run:** `bearish ≤ −0.001 (−0.1%)`,
  `neutral ≤ +0.081 (+8.1%)`, `bullish > +0.081`. These are cutoffs on the **momentum distribution**
  (not fit to trade outcomes); freezing the prior numeric values is the no-lookahead choice for a rule
  that would be applied live. Fills with < 60 prior bars (unclassifiable) get **full weight** (no trim).
- Bucket counts on the filled book are reported as a sanity check (the trim must actually bite).

## Arms (all applied to the SAME fills per shuffle → perfect pairing)
Per shuffle seed, `run_simulation` runs **once** at the production 6-slot book (`_MAX_LOW_CORR = 5`).
Fills are identical across arms; arms differ ONLY in the per-position daily weight. `τ = 0.5` (frozen
primary trim factor):

- **EW (baseline):** `w_p = 1/6` per held day (shipped book).
- **TILT-DL (PRIMARY — deleverage, the literal item-2 spec "trim neutral, keep bull/bear full"):**
  neutral-entry names `w_p = (1/6)·τ`; bull/bear/unclassified `w_p = 1/6`. **Gross floats down** on
  neutral-heavy days (freed capital → cash). This is the literal proposal and carries the verdict.
- **TILT-RD (SECONDARY — same-gross redistribution):** among the held set H, relative weight `τ` for
  neutral-entry names and `1` for the rest, normalized so daily gross `= |H|/6` (identical to EW). Tilts
  the book **toward** bull/bear entries with no leverage change — a stronger variant; diagnostic only.

## Universe / data
FY2018–FY2025, classified universe (~225), 6-slot capital-aware **equal-weight idealized** book (the
`r / n_slots` series the benchmark + capacity/vol-target nulls use — NOT the integer-lot budget book, to
avoid the lot-granularity confound). FY2025 = OOS holdout. **No DB changes.**

## Binding test (frozen gate)
Paired fill-order null, **K = 200** shuffles, same within-day fill order fed to both arms each seed:

1. **PRIMARY (Sharpe):** TILT-DL vs EW — `P(Δ Sharpe > 0) ≥ 0.95` **AND** 95% CI lower bound on
   `Δ Sharpe` `> 0`. (Standard methodology gate; per-trade / single-order point estimates do NOT decide.)
2. **maxDD / return:** reported. Since neutral EV is positive, a deleverage trim is expected to cut
   return; the test is whether it improves **risk-adjusted** (Sharpe) — a return-only drop with flat
   Sharpe is the VT failure mode and is a **reject**.
3. **OOS:** FY2025 paired Δ must not sign-flip hard against the pooled result.

**Accept** = gate 1 passes. **Lean/operator-call** = whole TILT band shifts up with favourable risk
asymmetry but CI grazes 0 (capacity-null precedent). **Reject** = TILT Δ Sharpe within fill-order noise
(CI straddles 0 / P < 0.95).

## Pre-registered priors / caveats
- **Guarded prior.** The prior pooled finding was per-TRADE EV; every per-trade signal this cycle that
  was real per-trade has died at the PORTFOLIO fill-order null (PEAD score-boost is the canonical case —
  key correct, rule didn't matter). The pooling memo itself flagged a sizing tilt "must clear the SAME
  fill-order null at the PORTFOLIO level that every selection rule failed." So the honest base rate is
  REJECT; the distinguishing hope is that this is a **weights** change (not slot membership), which is a
  genuinely different axis the null has not yet closed.
- **τ, cutoffs, and the regime variable are frozen** — no iteration after seeing results. A τ sensitivity
  curve (0.25 / 0.75) may be shown but is **non-binding** (the gate is τ = 0.5).
- **Realizability:** fractional trims aren't exact at ¥2M / 6 slots / 100-sh lots; a positive result needs
  a lot-granularity follow-up. This probe scores the idealized book.
