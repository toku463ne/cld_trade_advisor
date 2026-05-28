# Event-Driven Catalysts — Result

**Date:** 2026-05-28 · **Status:** discovery probe complete, REJECT (as testable) · **Type:** read-only analysis
**Script:** `src/analysis/event_driven_probe.py`

## Verdict

The event-driven category was the map's *best small-capital fit* — the premise being a **large per-name
edge** so a *few* catalyst names suffice (breadth-immune), unlike PEAD/value. That premise **fails on the
catalysts we can test from data in hand.** Dividend signaling (initiation/hike/cut/omission) and a buyback
proxy (net-share reduction) show **no large edge**, and the big catalysts run **the wrong direction**
(initiation −1.89% / omission +1.85% on the wide universe — backwards vs signaling theory); the spread is
insignificant (t=1.27), cohort big-catalysts are n-thin (initiation n=5, omission n=2), and the buyback
proxy *under*performs the no-change group. **REJECT.**

Crucially, the genuinely large-edge event-driven catalysts the literature relies on — **index
reconstitution, tender offers, announcement-timed buybacks** — are **untestable here** (no index-membership
history, no corporate-action / TDnet table, and the `/fins/dividend` endpoint is premium-gated). So this is
a **data-availability** result as much as a signal result: what we *can* see has no deployable edge; what
*might* work, we cannot see.

---

## 1. Hypothesis & method

Event-driven edges fit small capital when the per-name reaction is large and near-certain (e.g. index
funds *must* buy an addition), so a handful of events beats the breadth wall that sinks PEAD/value. We
tested the catalysts observable from `jq_*`:

- **Dividend signaling** — at each FY-results disclosure, compare the new annual DPS (`DivAnn`) to the
  prior FY's: **initiation** (0→+), **hike** (>+10%), **flat**, **cut** (<−10%), **omission** (+→0). The
  signaling literature predicts initiation/hike drift up, cut/omission drift down.
- **Buyback proxy** — YoY change in net shares outstanding (`shares_outstanding_fy − treasury_shares_fy`):
  **buyback** (≥1% reduction), **issuance** (≥1% increase), **none**.
- **Anchor & measure:** event = the FY disclosure announcing the new dividend / share count
  (`disclosed_date`, after-close → next session); **β-stripped 60-bar CAR** vs TOPIX (β over the 60 bars
  before entry); signed groups; **N225 cohort gate** (the binding deployment test). Mirrors the PEAD
  forecast-revision study structure.

The key question is **magnitude on the cohort**: a large drift on the big catalysts (initiation/omission)
would be a deployable few-names edge; a small or wrong-signed/thin one is not.

## 2. Data — what we have, what's missing

| catalyst | testable? | source |
|---|---|---|
| Dividend signaling (initiation/hike/cut/omission) | **yes** | `jq_statements.dividend_per_share_annual` (DivAnn, ingested 2026-05-28) |
| Buyback proxy (net-share reduction) | **yes (coarse)** | `jq_statements.shares_outstanding_fy / treasury_shares_fy` (annual, not announcement-timed) |
| Forecast-dividend revision (announcement-timed) | not yet | needs `FDivAnn` ingested (one more column + re-collect) |
| Index reconstitution (Nikkei/TOPIX add/delete) | **no** | no index-membership history in DB |
| Tender offers / announcement buybacks | **no** | no corporate-action / TDnet table; `/fins/dividend` premium-gated |
| PEAD (earnings/forecast-EPS surprise) | done | validated +2.51% cross-sectionally, unharvestable at ¥2M (separate arc) |

16,804 catalyst events built (1,186 on the 225 cohort).

## 3. Result — dividend signaling (β-stripped 60-bar CAR)

| group | wide n | wide CAR | cohort n | cohort CAR |
|---|---|---|---|---|
| initiation | 329 | **−1.89%** | 5 | −2.06% |
| hike | 5,166 | +1.31% | 473 | +0.97% |
| flat | 9,187 | +0.10% | 541 | −0.31% |
| cut | 1,366 | +0.23% | 105 | −0.96% |
| omission | 224 | **+1.85%** | 2 | +6.49% |

- **(initiation+hike) − (cut+omission):** +0.66% wide (Welch t=1.27, insignificant); +1.75% cohort
  (t=1.49, insignificant).
- **Big catalysts run backwards:** initiations drift *down* (−1.89%), omissions drift *up* (+1.85%) on
  the wide universe — the opposite of dividend signaling. Only `hike` is coherent (+1.31%) but PEAD-sized.
- **Cohort big catalysts are n-thin** (initiation n=5, omission n=2) → noise; the cohort omission +6.49% is
  two events.

## 4. Result — buyback proxy

| group | wide n | wide CAR | cohort n | cohort CAR |
|---|---|---|---|---|
| buyback | 2,810 | +0.26% | 382 | **−0.65%** |
| none | 11,384 | +0.67% | 703 | +0.33% |
| issuance | 2,610 | −0.20% | 101 | +0.22% |

Buybacks **underperform the no-change group** on both universes (cohort −0.65% vs +0.33%) — wrong direction
/ no edge. Expected: the proxy is an *annual* share-count change, not an announcement, so it carries little
catalyst information.

## 5. Per-FY — dividend catalyst spread (wide universe)

FY2017 +4.29% · FY2018 +0.93% · FY2019 −0.19% · FY2020 +1.25% · FY2021 −0.91% · FY2022 **−3.54%** ·
FY2023 +0.11% · FY2024 +2.17% · FY2025 +2.56%. **Sign-flips across years** — no stable edge.

## 6. Why it fails

The realized YoY dividend / share-count change is **anticipation- and mean-reversion-contaminated**, not a
forward catalyst: a firm that *initiates* a dividend has typically already had a strong year and run up
(then mean-reverts down → the −1.89%); a firm that *omits* has typically already crashed (then rebounds →
the +1.85%). The forward-looking, announcement-timed surprise (the *forecast* dividend revision, `FDivAnn`)
is the cleaner signal — but even that is the same family as PEAD's validated-but-breadth-bound
forecast-EPS revision, so the prior on a *deployable* result is low.

## 7. What would be needed to revisit

- **Forecast-dividend revision:** ingest `FDivAnn` (one column + re-collect statements, as was done for
  `DivAnn`) and run the announcement-timed version. Expect PEAD-family behavior (real but breadth-bound).
- **Index reconstitution / corporate actions:** requires an external membership-history / TDnet source not
  in the project. This is the catalyst class with the genuinely large per-name edge — the only path to a
  truly breadth-immune event-driven strategy at ¥2M — but it is a data-acquisition project, not a re-run.

## 8. Reproduce

```bash
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.event_driven_probe
```
See also the territory map `docs/analysis/20260528_new_directions.md` (§4).
