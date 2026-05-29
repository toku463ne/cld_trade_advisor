# Pre-registration — Volatility-target / risk-parity slot sizing (backlog item 4)

**Frozen:** 2026-05-29 · **Status:** pre-registered, frozen before running · **One pre-reg at a time**
**Script:** `src/analysis/confluence_voltarget_null.py` (read-only, no DB writes)

## Hypothesis
The 6-slot confluence book is **equal-weight** across slots (each held name contributes `r / 6` to the
daily book). Equal-weight over-allocates *risk* to the most volatile held names. Re-weighting slots
**inverse to recent realized volatility** (risk-parity) should lower portfolio variance and shave
maxDD with little return cost → a **risk-adjusted (Sharpe / maxDD)** win, not a return win.

This is the **weights axis**, distinct from selection/ordering (which decides *which* names fill the
slots — exhausted) and from exit timing. It does **not** condition on the market regime, so it does not
fight the regime-inverse alpha (the killer of items 3 + the TSMOM entry gate).

## Arms (all applied to the SAME fills per shuffle → perfect pairing)
For every shuffle seed, `run_simulation` is run **once** at the production 6-slot book
(`_MAX_LOW_CORR = 5`). The resulting fills (which names, when, exit) are **identical** across arms; the
arms differ ONLY in how each held name's daily return is weighted into the book:

- **EW (baseline):** `w_p = 1/6` per held day (the shipped book).
- **IV-RP (PRIMARY — inverse-vol risk-parity, same gross):** among the set H of names held that day,
  `w_p = (|H|/6) · iv_p / Σ_{j∈H} iv_j`, where `iv_p = 1 / vol_p`. Gross exposure each day is **identical
  to EW** (`|H|/6`); only the split among held names changes. This isolates the pure risk-parity effect
  from any leverage decision. **This arm carries the verdict.**
- **VT (SECONDARY — vol-target, gross-scaled):** `w_p = (1/6) · clip(vt / vol_p, 0.5, 2.0)`, `vt` = the
  per-FY median entry vol. Gross floats (deleverages in high-vol books). Reported as a diagnostic only;
  it is a *leverage* decision that is awkward to realize on a manual integer-lot ¥2M book, so it does
  **not** carry the verdict.

`vol_p` = trailing **20-bar** stdev of the stock's daily pct returns, measured **strictly before**
`entry_date` (no lookahead), frozen for the hold. Window = **20** (matches the project's ρ(20)
convention); 60-bar is explicitly NOT tested this pre-reg. Names with insufficient pre-entry history
fall back to the per-FY median vol.

## Universe / data
FY2017–FY2025, classified universe (~225), 6-slot capital-aware equal-weight idealized book (the same
`r / n_slots` series the capacity null and benchmark use — NOT the integer-lot budget book, to avoid
the lot-granularity confound). FY2025 is the OOS holdout. **No DB changes.**

## Binding test (frozen gate)
Paired fill-order null, **K = 200** shuffles, same within-day fill order fed to both arms each seed:

1. **PRIMARY (Sharpe):** IV-RP vs EW — `P(Δ Sharpe > 0) ≥ 0.95` **AND** the 95% CI lower bound on
   `Δ Sharpe` `> 0`. (Standard methodology gate; per-trade / single-order point estimates do NOT decide.)
2. **maxDD (value-prop axis):** report paired `Δ maxDD`; a clean win needs maxDD reduced (Δ maxDD ≥ 0,
   i.e. shallower) without the Sharpe gate failing. A maxDD cut that *costs* Sharpe is NOT an accept (it
   would just be deleveraging, available trivially by holding cash).
3. **OOS:** FY2025 paired Δ must not sign-flip hard against the pooled result.

**Accept** = gate 1 passes AND maxDD not worsened. **Lean / operator-call** = whole IV-RP band shifts up
with favourable risk asymmetry but CI grazes 0 (the capacity-null precedent). **Reject** = IV-RP Δ band
centred on / straddling 0 (within fill-order noise), the expected outcome given that re-weighting within
a ~5-name held set is a second-order effect and prior weight-axis precision is limited by integer lots.

## Pre-registered priors / caveats
- **Low prior on a Sharpe win.** Inverse-vol within a ~4–5-name held set is a small redistribution;
  equal-weight is already near risk-parity when held vols are similar. The honest expectation is a
  near-zero Δ inside the fill-order band (REJECT), with at most a small maxDD shave.
- **Realizability:** fractional risk-parity weights are not exactly realizable at ¥2M / 6 slots / 100-sh
  lots (`sizing.recommended_lots`). This probe scores the *idealized* book; a positive result would need
  a follow-up lot-granularity check before any live use.
- No iteration on window / cap / vt definition after seeing results — those are frozen above.
