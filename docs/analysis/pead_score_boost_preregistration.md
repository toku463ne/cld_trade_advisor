# PEAD as a Score Booster on Price-Sign Candidates (Pre-Registration)

**Status:** pre-registered 2026-05-25, *before* any computation. Third harvest of the PEAD
forecast-revision signal, after: signal 1 (absolute revision) = cross-sectional ACCEPT (+2.51%
N225 cohort, `pead_forecast_revision_results.md`); pead_up **confluence-VOTE = REJECT** on the
fill-order null (`confluence_pead_null.py`) — the vote *added* ~20% more candidates that flooded
the 6 slots and displaced baseline trades.

## The reframe (why this is structurally different from the rejected vote)
The vote failed because it treated an **information** signal as an **initiation** signal: a
60-bar drift window can't be an entry trigger, and adding it as a confluence vote injected new
candidates into a slot-constrained book. This harvest treats PEAD as **confirmation, not
initiation**:
- **The trigger stays price-based.** Price signs generate the candidate pool exactly as today
  (10-sign confluence, N≥3). **PEAD adds ZERO new candidates.**
- **PEAD only reweights existing candidates.** A candidate whose stock had a recent up-revision
  is *boosted*; when slot contention exists, boosted candidates are preferred for the limited
  slots. The long holding period is then a *consequence* of entering a confirmed name, not a
  timing assumption.

This converts PEAD from a candidate-adder (which the null rejected) into a **fill-order
priority rule** on a fixed pool — exactly the object the paired fill-order null adjudicates.

## Honest prior — this is a selection/ordering rule, a category that has a 100% rejection rate here
Every confluence **selection** rule tested this cycle died against the fill-order null:
RS-rank (p=random), corr-greedy (p73, not sig), bearish-count/prefer_b0 (p≈0.08 near-miss),
**ADX-priority (single-arm looked great, paired null Δ +0.029 P=0.545 = coin flip)**. "Prefer
boosted candidates in contention" is the SAME object as ADX-priority, with a different priority
key. The **only** reason it earns a fresh test: the priority key is **exogenous and
cross-sectionally validated** (an up-revision carries a documented +2.51% cohort forward
differential), whereas every rejected key was **price-endogenous** to the candidate pool (RS,
corr, ADX, bearish-count are all functions of the same price data that generated the
candidates). That is a genuinely different prior — but it does **not** lower the bar. The bar is
the identical strict null below.

## Definitions (single, pre-registered)
- **Candidate pool (unchanged):** the shipped 10-sign confluence book at N≥3, 6-slot caps
  (≤1 high-corr + ≤5 low-corr), ZsTpSl(2.0/2.0/0.3) exit, two-bar fill. Identical to
  `confluence_benchmark.py` / arm A of `confluence_pead_null.py`. Run id ≥ 0
  (`cbt._MULTIYEAR_MIN_RUN_ID = 0`).
- **Boost flag (look-ahead-safe):** a candidate with entry day `d` (price-sign entry) is
  **boosted** iff its stock had an **up-revision** (signal-1 "up" group, ΔFEPS > 0, same-FY
  pairing) whose **tradable entry day falls in the trailing 60 trading bars `[d−60, d]`**. The
  up-revision is already public by `d` (its entry day is the after-close-shifted tradable day),
  so no look-ahead. 60 bars = the pre-registered PEAD drift horizon (still live at entry).
- **Priority rule (HARD boosted-first — the maximal-effect / upper-bound test):** within the
  candidates competing for slots, boosted candidates are filled **before** non-boosted ones,
  preserving the underlying (shuffled) order *within* each group (a stable sort by `not
  boosted`). Hard priority is the **upper bound** of any boost: a candidate-score boost large
  enough to always win contention reduces to boosted-first; a smaller boost is a soft tiebreak
  with a *weaker* effect. **If the upper bound does not clear the null, no calibrated boost can**
  — so hard priority is the single decisive test. A soft-tiebreak variant is reported for colour
  only and does **not** get its own pass.

## Test — paired fill-order null on the capital-aware 6-slot book (binding methodology)
Mirrors `confluence_pead_null.py` / `confluence_adx_priority_null.py`. **K = 200** shuffles, all
9 FYs (FY2017–FY2025; FY2025 = OOS). For each seed `k`:
- shuffle the **single shared candidate pool** into random order `O_k`;
- **Arm A (random):** `run_simulation(O_k)` → capital-aware daily returns (r / 6);
- **Arm B (boost):** stable-sort `O_k` boosted-first → `run_simulation` → capital-aware returns;
- **Δ_k = Sharpe(B) − Sharpe(A)**.
Seed-pairing feeds both arms the **same** realization of fill-order randomness on the **same**
pool, so Δ isolates the boost-priority effect net of order luck. Stitch FYs to one daily series
per arm per seed; report Sharpe / return / maxDD distributions and the paired Δ distribution.

## Accept gates (ALL must hold; else REJECT)
1. **BINDING null:** paired Δ Sharpe (boost − random): **P(Δ > 0) ≥ 0.95 AND 95% CI lower bound
   > 0.** This is the exact bar capacity was held to and every selection rule failed. A
   favourable point estimate with a CI that grazes 0 is a REJECT (the prefer_b0 / ADX-priority
   outcome).
2. **Whole-band shift, not a fat tail:** Sharpe(B) at p5 **and** p50 ≥ Sharpe(A) at the same
   percentile — the distribution shifts up (as capacity did), not just a lucky upper tail.
3. **Per-FY robustness:** paired Δ mean > 0 in **≥ 6/9** FYs **and** OOS **FY2025** Δ mean > 0.
   (Selection rules were per-FY coin-flips; require consistency.)
4. **Mechanism — boosted trades are genuinely better:** among *filled* trades, boosted trades'
   realised mean_r and win% exceed non-boosted's, in the direction implied by the +2.51% cohort
   edge. Confirms the boost selects better outcomes, not noise. (Reported; must be positive.)
5. **Structural invariants (faithfulness; must hold by construction):** (a) the candidate pool
   is byte-identical across arms — **zero new candidates**; (b) the *filled-trade count* and
   turnover are ≈ equal across arms (boost changes *which* fill, not *how many*), so the edge is
   **cost-invariant**. If filled-trade count differs materially the design leaked candidates —
   fix before judging.

Gate 1 is binding; 2–4 are robustness (a pass on 1 with failures on 2–4 is suspicious → treat
as not-ship); 5 is the design sanity check. The backtest universe **is** the N225 deployment
cohort (the 225 `ohlcv_1d` names), so the null is on-cohort by construction — no separate
cohort gate.

## Falsifier (single line)
If the paired Δ Sharpe band includes 0 (P(Δ>0) < 0.95 **or** 95% CI lower bound ≤ 0), the PEAD
score-boost is rejected — same fate as ADX-priority and every price-endogenous selection rule —
and not wired to slot allocation.

## No-second-formulation clause (anti-mining)
We test **hard boosted-first** (the upper bound) on the **60-bar / up-group** definition fixed
above. If it fails the null, we do **not** then try: soft tiebreak, a different lookback, T3-of-
absolute vs signed-up, a continuous score weight, or a smaller boost — any of which would be
fishing for a pass. A reformulation requires a new pre-registration with a stated reason.

## Relationship to prior PEAD work
Independent of the rejected confluence-vote (different mechanism: reweight, not add). Uses
signal 1's up-revision definition as the priority key but is **not** evidence-borrowed: the
+2.51% cohort result is cross-sectional EV, not portfolio-book EV, and this gate re-earns the
portfolio claim from scratch. Orthogonal to the peer-relative pre-reg
(`pead_peer_relative_revision_preregistration.md`), which is a separate signal-definition study.

## Data dependency & implementation
Reuses the loaded backfill (`jq_statements` for up-revisions, `ohlcv_1d` confluence book,
`sign_benchmark` fires). `src/analysis/confluence_pead_boost_null.py` (to be written) mirrors
`confluence_pead_null.py`: build the single baseline pool, tag boosted candidates via
`_build_pead_up_fires` (filtered to up-revisions, trailing-60-bar membership), run the paired
boosted-first-vs-random shuffle null, report the gates above. Pure boost-tagging logic
unit-tested. This document is the spec; the code must not deviate without a new pre-registration.
