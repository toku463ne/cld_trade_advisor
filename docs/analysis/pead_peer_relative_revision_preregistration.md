# PEAD — Peer-Relative Forecast-Revision Surprise (Pre-Registration)

**Status:** pre-registered 2026-05-25, *before* any peer-relative measure is fit. This is an
**independent signal** from the absolute management-forecast-revision surprise
(`pead_forecast_revision_preregistration.md`, ACCEPT). It is pre-registered separately, with
its own gates, so it must stand on its own and — critically — must prove it adds drift *beyond*
the absolute measure (gate 7). **Two peer definitions** (sector vs trailing-correlation) are
registered as a pre-specified pair and run in parallel; the data, not methodological
preference, decides which (if either) carries the drift — see "Peer definition".

## Provenance & anti-mining stance (read first)
This hypothesis was **generated** by an exploratory observation that the ΔEPS=0 reaffirmation
group (≈58% of the absolute study's pairs) shows non-trivial forward-return dispersion when
contextualised by other firms' contemporaneous revision behaviour. That observation is a
**hypothesis generator only**. To avoid the garden-of-forking-paths trap (we are, by
construction, returning to a residual of a prior study), this document fixes the peer
definition, reference, surprise, outcome, binning, and accept/reject gates up front. **No
parameter below was tuned on the observed dispersion**; the window, peer-count floor, and
binning are chosen on a-priori / robustness grounds and stated before the driver is written.
The code must not deviate from this spec without a new pre-registration.

## Why this definition
A management forecast carries information *relative to expectations*. The absolute measure
(`ΔFEPS/P`) treats a reaffirmation (ΔFEPS=0) as "no news". But if a firm **holds** guidance
while its sector peers are **raising**, the firm is relatively lagging (bad); if it holds while
peers are **cutting**, it is relatively resilient (good). The same logic sharpens non-zero
revisions: a +5% raise into a sector that raised +10% is a relative *disappointment*. So we
commit to **one** peer-relative measure: own revision surprise minus a peer-group reference,
and we test its drift on a **sector-neutral (peer-β-stripped)** outcome so the result is
idiosyncratic alpha, not sector rotation.

## Peer definition — TWO pre-registered variants, decided by data
We register **two** peer definitions as an independent, pre-specified pair, differing **only**
in how "peer" is defined; everything else (reference, surprise, outcome, binning, gates) is
identical. This is deliberate: `sector33` mis-classifies **conglomerates** (Hitachi, Sony G,
SoftBank G, the trading houses) whose fundamentals do not track their nominal-sector peers —
and those names sit in the **N225 deployment cohort**, exactly where gate 8 bites. A
trailing-correlation peer set tracks a conglomerate's *real* economic exposure better, at the
cost of being price-derived and less stable. Rather than pick on methodological preference, we
fix both up front and let the gates decide which peer concept (if either) carries the drift.

- **Variant A — sector (fundamental peers).** Peer group of stock i = all other listed codes
  sharing i's **`sector33_code`** (`jq_listed`), excluding i. **Sector17 fallback:** if fewer
  than the peer-count floor of same-`sector33` peers have a usable trailing revision, fall back
  to the same-`sector17_code` group; if that also falls short, **exclude** the event. Fallback
  is pre-registered, not discretionary.
- **Variant B — trailing-correlation (economic peers, conglomerate-robust).** Peer group of
  stock i = the **K = 20** codes with the highest **120-trading-day** daily-return correlation
  to i, estimated on the window **ending the bar before entry** (strictly trailing → no
  look-ahead), among priced names with a correlation **≥ 0.30**. If fewer than the peer-count
  floor clear the 0.30 floor *and* have a usable trailing revision, **exclude** the event. K,
  the window, and the 0.30 floor are fixed here and are NOT tuned on any result.

Both variants use the same **peer-count floor (≥ 3 usable peer revisions)**, the same reference,
surprise, outcome, binning, and gates below. Read "peer group" in those sections as the
variant's peer group.

### Multiple peer definitions — anti-mining handling
Registering two variants doubles the chance one clears by luck. This is controlled, not
ignored: (1) every parameter of both variants is fixed in this document before any fit;
(2) passing requires clearing the **entire** gate stack — pooled monotone + t, OOS on a complete
FY, double-β-spec robustness, the incremental gate 7, AND the binding N225-cohort gate 8 — which
a lucky pooled t cannot satisfy on its own; (3) **both verdicts are reported**; we do **not** run
both and christen the better one "the signal." If exactly one variant clears, that is evidence
about *which peer concept* carries the drift (and we note it). If both clear, they may be
complementary, but any joint use is A/B'd as correlated votes (see "Relationship to signal 1").
If neither clears, reject. We do not iterate to a third peer definition without a new pre-reg.

## Peer reference construction (look-ahead-safe)
For an event on stock i with tradable entry day `t` (after-close-shifted; see timing):
1. Each peer j contributes its **most recent absolute revision surprise** (`ΔFEPS_j / P_j`,
   identical definition to signal 1) whose disclosure is **strictly before `t`** and within a
   trailing window **W = 90 calendar days** of `t`. Only peer revisions already public by the
   entry day are used → no look-ahead.
2. **Peer reference `R_i` = the MEDIAN** of those peer surprises (median, not mean: robust to a
   single peer's outlier revision; one earnings season per sector fits inside 90d).
3. **Peer-count floor:** require **≥ 3** distinct peer surprises in the window (else apply the
   sector17 fallback, then exclude). Pre-registered floor; not tuned.

## Peer-relative surprise (single, pre-registered)
`rel_i = own_surprise_i − R_i`, where `own_surprise_i = ΔFEPS_i / P_i` (signal-1 definition;
`P_i` = adjusted close on the last trading day strictly before `t`). Positive = the firm's
revision is **better than its peers'**. For a reaffirmation (own=0), `rel_i = −R_i`: holding
while peers cut (R<0) ⇒ rel>0 (relatively good); holding while peers raise (R>0) ⇒ rel<0.

### Exclusions (pre-registered, not tunable later)
- Own event excluded if `own_surprise` is undefined (signal-1 exclusions: no same-FY prior
  forecast, null FEPS either side, missing/zero price, cross-accounting-basis pair).
- Excluded if the peer-count floor is unmet under both sector33 and the sector17 fallback.
- `rel` winsorized at the pooled 0.5% / 99.5% tails.

## Event timing (two-bar fill, no look-ahead)
Identical to signal 1: after-close (`disclosed_time ≥ 15:00`) shifts the effective day to the
next trading day; entry = first trading day on/after the effective day. Drift measured from
entry. Trading-day set = TOPIX dates (`jq_trading_calendar.holiday_division` is unpopulated).

## Drift / outcome metric (sector-neutral is mandatory)
- **Primary:** **peer-β-stripped** cumulative abnormal return over **H = 60** trading bars from
  entry. Abnormal return = `r_stock − β_peer · r_peerportfolio`, where `r_peerportfolio` is the
  equal-weight daily return of stock i's peer group (the same sector33/17 set used for the
  reference, priced names only), and `β_peer` is estimated from the **trailing 60 daily
  returns ending the bar before entry** (look-ahead-safe). This makes the outcome
  *sector-relative by construction* — it measures whether i out-drifts its peers, matching the
  peer-relative surprise.
- **β-spec robustness (gate 5):** also compute a **double β-strip**
  `r_stock − β_mkt · r_TOPIX − β_peer · r_peerportfolio`; the (Q5−Q1) must keep the same sign,
  proving the result is not an artifact of the single-factor choice or of market beta.
- **Secondary (robustness only):** H = 20; and the plain TOPIX-β-stripped CAR (reported so we
  can see how much of any drift is sector tilt vs idiosyncratic).
- Report raw and both β-stripped CARs.

## Cross-sectional scope — discovery universe vs deployment cohort
Identical split to signal 1. **Discovery universe** = full J-Quants universe (power + size
gradient only). **Deployment cohort** = the N225 names we hold (codes in `ohlcv_1d`, mapped via
`to_yf_code`). A pooled result is never fallen down to N225; the binding deployment decision is
gate 8.

### Size-gradient diagnostic (reported always)
(Q5−Q1) peer-β-stripped 60-bar CAR per TOPIX `ScaleCat` bucket — same as signal 1, to show
whether any effect is small-cap-concentrated or present in the tradable large caps.

## Binning
The peer-relative surprise is **continuous** (own − peer median), so the ΔEPS=0 mass point that
forced signed terciles in signal 1 **does not recur** (rel=0 only if own exactly equals the
peer median — rare). Therefore bin into **value quintiles** Q1 (most peer-negative) … Q5 (most
peer-positive). *Degeneracy fallback:* if, on the realised data, any quintile holds < 5% of
events (an unforeseen mass point), fall back to signed terciles {rel<0 / rel=0 / rel>0} —
declared here so the choice is not made to favour a result.

**Pre-specified reaffirmation cut (the named hypothesis):** restricted to **own_surprise = 0**
events, bin the peer reference `R_i` into terciles (peers-cutting / peers-flat / peers-raising)
and test that peer-β-stripped drift is monotone DECREASING in `R` (reaffirm-while-peers-cut =
best, reaffirm-while-peers-raise = worst). This is gate 7b.

## Accept gates (ALL must hold; else REJECT)
Gates 1–6 establish existence on the discovery universe (power). Gate 7 is the **independence /
incremental** gate (this signal must beat the absolute measure, or it is redundant). Gate 8 is
the **binding deployment** gate (N225 cohort).

1. **Monotone:** Spearman(quintile, mean peer-β-stripped 60-bar CAR) > 0 **and** Q5 mean > Q1.
2. **Long-short:** (Q5 − Q1) peer-β-stripped 60-bar CAR > 0, naive Welch t > **2.0** (report
   Newey-West; naive flagged as an upper bound).
3. **Sample:** ≥ **1000** pooled events (after the peer-context exclusion); each quintile ≥ 100.
4. **OOS:** most-recent **complete** fiscal year held out (`fy_end + ~135d ≤ data end`;
   truncated trailing FY excluded + reported as a watch-item) — (Q5 − Q1) keeps the **same
   sign** and > 0.
5. **β-spec robust (sector-neutrality):** (Q5 − Q1) keeps the same sign under the double
   β-strip (TOPIX + peer). The signal must NOT be explainable as market or pure sector beta.
6. **Horizon robustness:** sign of (Q5 − Q1) agrees at H = 20 and H = 60.
7. **Incremental over the absolute measure (BINDING for independence):**
   - **7a (conditional):** within each absolute-surprise group {down / reaffirm / up}, the
     peer-relative (Q5 − Q1) — or tercile (T3 − T1) where a group is thin — is > 0 with the
     same sign. I.e. *conditional on the firm's own revision, peer-relative still sorts drift.*
   - **7b (the named hypothesis):** in the **reaffirmation subgroup** (own = 0), peer-β-stripped
     drift is monotone in the peer-reference terciles with (peers-cut − peers-raise) > 0, n ≥
     **300** in the subgroup and ≥ 60 per extreme tercile.
   If 7a fails, the peer measure adds nothing beyond signal 1 and is **rejected as
   redundant**, regardless of gates 1–6.
8. **Deployment (N225 cohort, BINDING):** restricted to the deployment cohort, the peer-β-
   stripped (Q5 − Q1) 60-bar CAR is > 0 with the **same sign** as pooled. Floor relaxed to the
   cohort's reach: ≥ **200** events, ≥ 40 per extreme group (tercile fallback permitted). If the
   cohort cannot reach this floor over 10 years, report **n-thin / untestable for our book** —
   do **not** substitute the pooled number.

## Falsifier (single line)
If the peer-β-stripped (Q5 − Q1) 60-bar CAR ≤ 0, **or** it flips sign OOS, **or** it does not
survive the double β-strip (gate 5), **or** it fails the incremental gate 7a (no drift beyond
the absolute measure), **or** it is ≤ 0 / sign-flipped on the N225 deployment cohort → the
peer-relative revision signal is rejected and not wired to any sign/strategy.

## Relationship to signal 1 (independence requirement)
This signal is accepted **only if** it clears gate 7 — i.e. it carries drift *beyond* the
absolute revision surprise. If both signals are accepted, any confluence wiring must treat them
as **potentially-correlated votes** (a peer-relative up-surprise on a stock whose absolute
revision is also up is one bet, not two) and A/B them jointly, not assume additivity. The
absolute signal's binding-cohort result (+2.51%) is **not** evidence for this one.

## Data dependency
J-Quants Standard 10-yr backfill: `jq_statements` (revisions), `jq_daily_quotes` (adjusted
closes for own + peer-portfolio returns), `jq_listed` (`sector33_code` / `sector17_code` for
peers + `ScaleCat`), `jq_topix` (market β / secondary strip). Same backfill as signal 1.

## Implementation
`src/analysis/pead_peer_relative_revision.py` (to be written) — peer-set construction (both
variants), peer-reference, peer-relative surprise, peer-β-strip, and binning logic in pure,
unit-tested functions (`tests/test_pead_peer_relative_revision.py`); a thin DB driver (`run()`)
assembles the per-event table **for both peer variants in one pass** and reports, per variant,
the three views (pooled quintiles, size gradient, N225 cohort) plus the gate-7 incremental
tables, side by side with both verdicts. Reuse signal-1 pure functions where identical
(`Disclosure`, `pair_same_fy_revisions`, `tradable_entry_day`, `revision_surprise`, `beta`,
`beta_stripped_car`, `doc_basis`). This document is the spec; the code must not deviate without
a new pre-registration.
