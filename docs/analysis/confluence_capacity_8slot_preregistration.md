# Pre-registration — 6→8 slot capacity sweep (backlog item 5)

**Date:** 2026-05-29 · **Type:** capacity lever (structural) · **Status:** frozen before run
**Script:** `src/analysis/confluence_capacity_8slot_null.py`
**Binding:** paired capacity null on the capital-aware book (same machinery that shipped 4→6).

## Hypothesis

4→6 slots shipped (2026-05-23, `confluence_capacity_null`): the 6-slot fill-order band sat above the
4-slot one (Sharpe 1.02 vs 0.89, Δ +0.137, P(Δ>0)=0.865, CI [−0.095,+0.370] grazed 0), adopted on
risk-asymmetry + better maxDD. 6→8 was never tested. **Mechanism that worked 4→6:** more low-corr names
→ lower portfolio variance (real diversification) → tighter, higher Sharpe band + shallower maxDD.

**LOW prior:** Stage-0 found only **~8 low-corr names/day**. The 6-slot book already takes 1 high + 5
low; an 8-slot book (1 high + 7 low) needs 7 low-corr names concurrently — at the breadth ceiling. If the
book is breadth-starved, the extra 2 slots either sit in cash (capital-aware denominator = 8, so
under-investment = return drag) or force in correlated names (false diversification, no variance cut).
Either way the 4→6 mechanism may not extend. It also **raises the manual-execution burden** (the live
plan is a 6-slot book).

## Method (identical to the shipped 4→6 null)

K=200 paired shuffles, FY2018–2025 (matches the current baseline + items 2/6). For each shuffle seed the
**same** within-day fill order is fed to both a **6-slot** (`_MAX_LOW_CORR=5`) and an **8-slot**
(`_MAX_LOW_CORR=7`) book; each marked capital-aware (daily contribution = `r / (1+low)`). Pairing by seed
removes order-luck → Δ = Sharpe(8) − Sharpe(6) is the capacity effect net of fill-order noise. Stitched
Sharpe / return / maxDD; FY2025 OOS reported.

**Breadth diagnostic (the crux for the low prior):** at shuffle 0, per arm, the **mean concurrent held
names per active day** and **total trades**. If the 8-slot book's mean held count is ≪ 8 (≈ the 6-slot's
~6), the extra slots are breadth-starved and the lever is mechanically dead.

## Frozen gate

Unlike 4→6, this lever **raises** manual burden and has a **low prior**, so a near-miss is **not**
adopted (the 4→6 risk-asymmetry argument — reversible one-liner, free option — does not apply when the
change costs execution effort and breadth is at its ceiling).

1. **ACCEPT** iff **P(Δ Sharpe > 0) ≥ 0.95 AND 95% CI-lo > 0** (clean separation, the standing bar).
2. **REJECT** on a near-miss (grazing CI), a flat/negative Δ, or evidence of breadth starvation
   (8-slot mean held ≈ 6-slot mean held → the extra slots add cash drag, not diversification).

## Expected result (stated, not used to pre-judge)

The low prior (~8 low-corr names/day) argues the 8-slot book will be breadth-starved — the extra slots
sit in cash or pull in correlated names — so Δ is likely flat-to-slightly-positive but **not** cleanly
separated, and the maxDD/Sharpe gains that justified 4→6 will be muted. The breadth diagnostic decides
the mechanism either way.
