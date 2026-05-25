# PEAD — Peer-Relative Forecast-Revision Surprise (Results)

**Verdict: REJECT (both peer variants) — fails gate 7 (incremental / independence over the
absolute revision measure).** Run 2026-05-26 on the J-Quants Standard 10-yr backfill
(`stock_trader_dev`). Spec: `pead_peer_relative_revision_preregistration.md`. Reproduce:

```bash
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_peer_relative_revision
```

Driver: `src/analysis/pead_peer_relative_revision.py` (`run()`). Builds, for **both** peer
definitions in one pass, the peer-relative surprise `rel = own_surprise − peer_median`, and a
**peer-β-stripped** 60-bar CAR (sector-neutral by construction). Pure logic unit-tested in
`tests/test_pead_peer_relative_revision.py` (5/5).

## Binning note (spec compliance)
`rel` carries a **mass point at exactly 0** — same root cause as signal 1's ΔEPS=0 reaffirmations:
when a firm reaffirms (own = 0) and its peer-median surprise is also 0, `rel = 0`. ~56% of events
land on it. Value-percentile quintiles (and value terciles) therefore **degenerate** (the 33rd and
67th percentiles both fall on 0, collapsing the middle bin to empty). Per the pre-registered
degeneracy fallback, binning is **signed terciles `{rel<0 / rel=0 / rel>0}`**, identical in spirit
to signal 1. (An initial run used value-percentile terciles by mistake — non-compliant with the
spec, produced an empty middle bin and an artifactual gate-3 fail; corrected to signed terciles
before recording. No buggy result was committed.)

## What passes — the peer-relative surprise IS a real standalone sorter
Unconditionally, both variants clear gates 1–6 **and** the binding N225-cohort gate 8:

| | Variant A (sector33+17) | Variant B (corr top-20) |
|---|---|---|
| n events | 72,687 | 54,761 |
| (pos−neg) peer-β-stripped 60-bar CAR | **+1.36%** (t +6.32) | **+1.19%** (t +5.01) |
| Spearman(bin, CAR) | +1.000 | +1.000 |
| OOS FY2025 (pos−neg) | +0.94% | +1.39% |
| double-β strip (TOPIX+peer) | +1.19% | +1.09% |
| H20 sign | +0.48% | +0.65% |
| **N225 cohort (gate 8, BINDING)** | **+1.40%, t +2.93** | **+1.49%, t +3.00** |

Taken alone this looks deployable — sector-neutral drift, survives a double β-strip, present in the
tradable N225 cohort. **If signal 1 did not already exist, this would read as a winner.**

## Why it is REJECTED — gate 7 (independence) fails: it is not incremental to signal 1
Gate 7a conditions on the firm's **own** revision group (down / reaffirm / up — i.e. signal 1) and
asks whether peer-relative *still* sorts drift inside each group. It does not, consistently:

| own group | Variant A (pos−neg) | Variant B (pos−neg) |
|---|---|---|
| down | **−1.90%** (t −1.18) ✗ | **−0.71%** (t −0.50) ✗ |
| reaffirm | +1.82% (t +1.32) | **−0.29%** (t −0.52) ✗ |
| up | +1.31% (t +1.14) | +1.53% (t +2.02) |

Variant A fails on the **down** cell (wrong sign); variant B fails on **down and reaffirm**. The
mechanism is collinearity: `rel = own − R` is dominated by `own`, so the unconditional +1.36% spread
is mostly a **repackaging of signal 1's up−down differential**, not new information. Once you hold
the own revision fixed, the peer context adds nothing of consistent sign. Per the pre-registration —
*"if 7a fails, the peer measure adds nothing beyond signal 1 and is rejected as redundant, regardless
of gates 1–6"* — this is decisive.

### Reaffirmation hypothesis (gate 7b) — the original generator, also rejected
The named hypothesis ("reaffirm-while-peers-cut" beats "reaffirm-while-peers-raise"):
- **Variant A: PASS direction but thin** — (cut − raise) +1.82%, yet the peers-cutting cell is only
  **n=182** (vs 41,524 reaffirm events; almost no reaffirmation happens while the sector net-cuts).
- **Variant B: FAIL** — (cut − raise) −0.29% (cut −1.02% vs raise −0.73%).
With gate 7a already failing and 7b thin/inconsistent across variants, the reaffirmation effect is
not a robust, independent edge.

## Decision
**Neither peer concept carries drift independent of the absolute revision surprise → the
peer-relative signal is REJECTED.** Per the multiple-comparison / no-iteration clause, we do **not**
proceed to a third peer definition without a new pre-registration. The exhausted-independence result
is itself informative: the deployable PEAD edge is the **absolute** forecast-revision signal (signal
1, ACCEPT, +2.51% cohort); peer-relativity is the same bet wearing a sector-neutral coat.

## Robustness / honesty notes
- The double-β strip used **univariate** betas (own vs peer-portfolio, own vs TOPIX) subtracted
  separately, not a joint 2-factor regression — a stated approximation for the gate-5 sign check; it
  is not the binding gate and the verdict turns on gate 7, not gate 5.
- Variant B's corr-peer set is price-derived and recomputed per event on a strictly-trailing 120-day
  window (no look-ahead); ~33% of events were excluded for failing the ≥3-peers-≥0.30-corr floor.
- Welch t-stats are **naive** (no Newey-West / no clustering by event-date or sector) → upper bounds
  on significance. Gate 7 fails regardless, so this does not change the verdict.
