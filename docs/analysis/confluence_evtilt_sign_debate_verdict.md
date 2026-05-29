# /sign-debate verdict — Conditional-EV sizing tilt (confluence backlog item 2)

**Date:** 2026-05-29 · **Verdict: ACCEPT (confidence M)** · **Scope: live manual sizing GUIDELINE, drawdown lever only**
**Topic:** adopt the conditional-EV sizing tilt — on a `ConfluenceSignStrategy` entry whose ^N225 trailing-60-bar
momentum is in the NEUTRAL tercile (bear ≤ −0.1% < neutral ≤ +8.1% < bull), buy ~half lots
(`floor(0.5·recommended_lots)`); keep bull/bear entries at full lots.

This file records the debate outcome so the decision is reconstructable without the transcript. The rule's
evidence lives in `confluence_improvement_backlog.md` item 2; the pre-registration in
`confluence_evtilt_sizing_preregistration.md`; the scripts in `src/analysis/confluence_evtilt_*null.py`.

---

## Final verdict

**ACCEPT (confidence M)** — adopt as a live **manual sizing guideline**, scoped **strictly as a drawdown
lever**. The drawdown claim is accepted (significant, forward-stable across four paired nulls including a
held-out cutoff cross-validation). **No Sharpe-improvement claim** (held-out CI wide, not significant). **No
return claim** (held-out return ≈ −5pp — an insurance premium paid in calm/bull years, recouped in drawdowns).

Confidence is **M not H** because the two empirical sources disagree on the load-bearing dimension: the maxDD
edge is significant and OOS-stable, but the Sharpe dimension (the n=3-FY / 35-neutral-fill held-out source) has
a CI spanning zero. Per `docs/evaluation_criteria.md` §7, H requires both axes to agree; only drawdown does.

### Binding adoption conditions (from the judge)
1. **Drawdown framing only.** Surface it as a drawdown-for-return *trade* (~4.5pp shallower maxDD for ~5pp
   return cost), never as a return or Sharpe improvement.
2. **State the BIMODAL live instruction.** Integer-lot rounding (mean base_lots 3.15 at ¥333k/slot, 50% of
   neutral names round to 0) means the real rule is **"in a NEUTRAL N225-60bar regime: SKIP cheap neutral-regime
   names entirely, HALF-SIZE expensive ones"** — NOT a smooth "size neutral fills at ~⅓". The UI text must say
   this, with the n=35 held-out caveat.
3. **No code path to unwind.** The live book is manual — there is no `exit_simulator` constant. Adoption =
   surfacing the regime + half-lot recommendation in the Daily tab / live plan (shipped 2026-05-29).

### Forward falsifier (withdraw the guideline if this triggers)
If **FY2026** — the first true post-adoption out-of-sample year — closes with the tilt-lot book showing maxDD
**equal-or-deeper** than the equal-lot book on the same fills (Δ maxDD ≤ 0), the drawdown edge has sign-flipped
out of sample → withdraw. (Re-run `confluence_evtilt_lots_null.py` extended to FY2026.)

---

## Why it passed where a month of levers failed

- **Different axis.** Every rule that died on the fill-order null (RS-rank, corr-greedy, prefer_b0, ADX-priority,
  PEAD vote, PEAD score-boost) changed *which* names fill the 6 slots. This changes *how much capital a filled
  name gets* — a weights axis the null had not closed. First per-trade-EV finding this cycle to clear the
  portfolio null (not coin-flip like PEAD score-boost).
- **Escapes the regime-inverse trap.** Items 3 (regime-conditional exit) and the TSMOM entry gate died by
  de-risking in the BEAR regime, cutting the bear-recovery alpha (FY2024 0.64→−1.79). This trims **NEUTRAL** and
  keeps **bear full**, so it never de-risks the bear tail. Mechanically distinct.
- **Beats the precedent bar.** The shipped 6-slot capacity bump was P=0.865, CI [−0.095,+0.370] (grazed 0),
  adopted on risk-asymmetry. This is P≥0.98 across three nulls and *clears* the 95% CI — statistically stronger
  than a rule already in production.

---

## Evidence trail

### Iteration 1 — verdict: Insufficient evidence (DEFER)
- **Analyst:** predominantly a maxDD edge (−4.1pp; +4.14/+4.17/+4.13pp shallower at 100%/100%/99.5% of draws
  across fill-order / phase+order / integer-lot nulls) with a THIN Sharpe tailwind (Δ +0.120/+0.123/+0.126,
  P 1.000/0.990/0.980, CI-lo +0.037/+0.007/+0.005). Lot-book return FLAT. Per-FY edge concentrated in FY2021
  (+0.38) + FY2022 (+0.28); OOS FY2025 −0.024/−0.114. **The cutoff choice (in-period terciles) is the one
  unaddressed gap** — 8/8 phase positivity + τ dose-response hold the cutoffs fixed, so they prove start-timing
  robustness but NOT cutoff robustness.
- **Historian:** first sizing tilt ever run to a portfolio null; weights-vs-selection distinction sound;
  statistically stronger than the shipped capacity bump; genuinely sidesteps the item-3 trap; escapes the
  PEAD-score-boost graveyard. Risk-conditional sizing (vol-target, item 4) was REJECTED the same sitting →
  lesson "EV-conditional, not risk-conditional, sizing."
- **Proposer:** lean-ADOPT, cutoff-CV as fast-follow; DEFER-pending-cutoff-CV the fallback.
- **Critic:** one **H-severity** hole — in-period cutoffs × 2-FY-concentrated maxDD; the rule's mechanism is
  "deleverage before the FY2021/FY2022 drawdowns" and the cutoffs were chosen on a distribution that *contains*
  those drawdowns. The three nulls resample fill-order/phase but NOT price history, so they can't retire it.
  **The held-out cutoff-CV is a BLOCKER, not a fast-follow.** (Plus M holes: single-path maxDD framing,
  integer-rounding making the live instruction bimodal, ~117-neutral-fill thin cell.)
- **Judge:** Insufficient evidence → DEFER. Next action = run the held-out cutoff-CV.

### Iteration 2 — verdict: ACCEPT (M)
- **Falsifier run** (`confluence_evtilt_cutoffcv_null.py`): re-derived terciles on **train FY2018–2022** =
  bear ≤ −1.64% < neutral ≤ +4.06% < bull — **materially different** from the in-period −0.10%/+8.10% (FY2018–22
  was a lower-momentum window), so the test FYs get genuinely different regime labels = a real OOS cutoff test.
  Scored on **held-out FY2023–2025**, integer-lot ¥2M book, K=200: EW-LOT 1.329/maxDD −17.3% → TILT-LOT
  1.457/−12.8%; **Δ maxDD +4.51pp shallower, P(shallower)=0.995** (gate ≥+2pp & ≥90% → PASS); **Δ Sharpe +0.128
  ≥ 0** (gate PASS) but CI [−0.107,+0.401] wide / not significant; Δ return −5.3pp.
- **Judge:** both pre-registered ACCEPT conditions met and no maxDD sign-flip → the in-period-cutoff objection
  is retired; the drawdown edge is forward-stable. **ACCEPT (M)**, drawdown-only scope, bimodal-surfacing
  condition binding.

---

## Follow-up A/B — floor (skip) vs take-1-lot (2026-05-29, `confluence_evtilt_floor_min1_ab.py`)

Operator question: the bimodal rule SKIPS the mid-priced (¥1,667–3,333, base==1 lot) neutral names (~50% of
neutral fills); since the source finding said "trim, not skip" (neutral EV is positive), is it better to
still TAKE 1 lot for them? A 3-arm paired null (EW / FLOOR=skip / MIN1=take-1-lot; MIN1 differs from FLOOR
ONLY on base==1 neutral names) answered: **KEEP FLOOR.** Taking 1 lot gives back ~half the drawdown benefit —
FLOOR cuts maxDD +4.1pp/+4.5pp vs EW (full / held-out), MIN1 only +1.9pp/+1.6pp; MIN1 − FLOOR ΔmaxDD
−2.27pp/−2.90pp (FLOOR shallower in 96–97% of shuffles), Δret +5.4pp/+1.0pp. MIN1's edge over EW is weak OOS
(Sharpe +0.024, P=0.63). **The skip is load-bearing for the drawdown lever, not an artifact** — it is the
deeper deleverage the τ dose-response rewards. MIN1 is only a milder, more return-friendly operating point.
Shipped rule unchanged.

## Open questions / residual risk
- Held-out **Sharpe is not significant** (3 FYs, 35 neutral fills) — only the maxDD claim is. Accepted on that basis.
- The Sharpe gain is concentrated in weak/drawdown FYs; it is an insurance premium in calm/bull years (held-out
  return −5pp). This is a drawdown-for-return trade, not a free lunch.
- Forward falsifier (FY2026) above is the live withdrawal trigger.

## Implementation (shipped 2026-05-29, commit 90f5376)
- `src/portfolio/sizing.py`: `n225_momentum_regime()`, `neutral_trim_lots()`, frozen cutoffs (τ=0.5).
- `src/viz/daily.py`: `_n225_mom60()` + Register-panel hint (confluence rows only) showing the regime and the
  half-lot / skip recommendation.
- `tests/test_portfolio_sizing.py`: regime + trim-lots unit cases.
- Live-plan note: memory `project_live_trading_plan`.
