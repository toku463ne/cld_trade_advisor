# Reversal-Breadth Regime Indicator (`RevNRegime`)

A cross-sectional regime gate built on top of the existing `rev_nday`
sign.  For each trading date, computes the **fraction of universe
stocks** that fired `rev_nhi` (touched their prior-N-day high with a
bearish closing body) on that date.  When this breadth fraction sits
above its historical 80th percentile, the market is in a "high
reversal-risk" regime — many stocks are exhausted at multi-day highs,
and forward index returns are systematically dampened.

The signal is intended as a **position-sizing / entry-gate context
flag**, not a directional bet.  It does not turn the index short; it
flags days where upside is statistically smaller than baseline.

---

## Motivation

The investigation began with an observation about `rev_nhi` proposals
dominating the Daily-tab candidate list (78% of 2,506 proposals across
2026 YTD).  A sequence of empirical checks established that **the
per-stock edge of rev_nhi above the universe baseline is essentially
zero** (net edge of 0.07 / 0.27 pp across FY2024 / FY2025), but the
signal carries strong **regime-level** information:

- After a `rev_nhi` fire on any stock in FY2024 (bear-ish year), mean
  forward 10-bar return was **−1.4 %** per fire.
- After a `rev_nhi` fire on any stock in FY2025 (bull year), mean
  forward 10-bar return was **+1.5 %** per fire.

The 3 pp swing across regimes meant the *firing density itself* tells
you what regime you are in, even though the *individual pick* adds no
value.  This re-framing — from per-stock trade signal to universe-level
breadth indicator — is what `RevNRegime` operationalises.

---

## Empirical validation

Two pre-registered tests were run on FY2024 + FY2025 + 2026 YTD
(~3 years, 771 trading days, universe = 219 representative stocks from
`classified2024`).

### Test 1 — N225-level signal

Apply `RevNDayDetector` directly to `^N225` (not stocks).  Forward
10-bar N225 return on days when the index itself fires the signal,
compared to baseline (all usable days).

| signal | n fires | mean fwd 10-bar ret | vs baseline | 95 % CI | verdict |
|--------|--------:|---------------------:|-------------:|---------|---------|
| `rev_nhi` on N225 | 88 | +0.915 % | −0.234 pp | [−1.05, +0.56] | FAIL (CI crosses 0) |
| `rev_nlo` on N225 | 12 | +2.430 % | +1.281 pp | [+0.19, +2.28] | technically passes but n=12 too small |

The N225-level signal is weak.  rev_nhi shows no edge at the index
level; rev_nlo is directionally correct but fires too rarely to be
actionable.

### Test 2 — Universe breadth indicator

For each day, compute the fraction of universe firing the detector.
Stratify trading days into quintiles by that fraction.  Report forward
10-bar N225 return per quintile.  Hypothesis: top quintile (highest
breadth) yields the most-negative forward return for `rev_nhi`, and
the most-positive for `rev_nlo`.

#### `rev_nhi` breadth (quintile split, full window)

| quintile | n days | mean breadth % | fwd N225 10-bar mean | P(<0) |
|----------|-------:|---------------:|----------------------:|------:|
| Q1 (top) | 150 | **23.4 %** | **+0.70 %** | 42 % |
| Q2 | 150 | 11.9 % | +0.81 % | 42 % |
| Q3 | 150 | 7.4 % | +0.79 % | 42 % |
| Q4 | 150 | 4.1 % | +1.42 % | 37 % |
| Q5 (bottom) | 150 | 1.5 % | **+2.15 %** | 29 % |

Bootstrap Δ(Q1 − Q5) = **−1.44 pp**, 95 % CI [−2.37, −0.52],
p(Δ > 0) = 0.001.  **PASSES.**

The pattern is **monotonic**: from Q5 (1.5 % breadth) to Q1 (23 %
breadth), forward 10-bar N225 returns slope downward (+2.15 % →
+0.70 %) and bearish-bar probability rises (29 % → 42 %).

#### Per-cohort robustness — `rev_nhi`

| cohort | n_days | Q1 fwd ret | Q5 fwd ret | Δ(Q1 − Q5) | 95 % CI | verdict |
|--------|-------:|-----------:|-----------:|------------:|---------|---------|
| FY2024 (bear-ish) | 244 | **−2.90 %** | +1.93 % | **−4.83 pp** | [−6.62, −3.10] | ✅ PASS |
| FY2025 (bull) | 244 | +0.77 % | +3.14 % | **−2.38 pp** | [−3.95, −0.80] | ✅ PASS |
| 2026 YTD | 76 | +2.21 % | +1.72 % | +0.49 pp | [−3.44, +4.49] | ✗ FAIL (n=15 / quintile) |
| ALL | 750 | +0.70 % | +2.15 % | **−1.44 pp** | [−2.37, −0.52] | ✅ PASS |

`rev_nhi` breadth passes in 2 of 3 individual cohorts AND the
aggregate.  The 2026 YTD fail is most plausibly a sample-size issue —
each quintile has only 15 days, and the CI is ±4 pp wide.  The
critical positive is that the signal holds in **both** the bear-ish
FY2024 and the bull FY2025 — it is not a regime-specific artifact.

#### Per-cohort robustness — `rev_nlo`

| cohort | n_days | Q1 fwd ret | Q5 fwd ret | Δ(Q1 − Q5) | 95 % CI | verdict |
|--------|-------:|-----------:|-----------:|------------:|---------|---------|
| FY2024 | 244 | +1.29 % | −2.53 % | **+3.82 pp** | [+1.93, +5.82] | ✅ PASS |
| FY2025 | 244 | +2.96 % | +2.34 % | +0.62 pp | [−1.12, +2.36] | ✗ FAIL |
| 2026 YTD | 76 | +4.07 % | +4.10 % | −0.03 pp | [−3.26, +3.50] | ✗ FAIL (sign wrong) |
| ALL | 750 | +1.81 % | +0.86 % | +0.95 pp | [−0.04, +1.95] | ✗ FAIL (CI grazes 0) |

`rev_nlo` breadth passes only in FY2024 (the bear-ish year).  In FY2025
and 2026 YTD the signal is either flat or pointing the wrong direction,
and the aggregate CI grazes zero from below.  This is **not** strong
enough to ship as a regime indicator.

---

## What ships, what doesn't

| side | ships? | rationale |
|------|--------|-----------|
| `rev_nhi` (default) | **Yes** | PASSES in 2/3 cohorts + aggregate; works in both bear and bull years; aggregate ΔSharpe-shape effect statistically significant. |
| `rev_nlo` | **No** | Passes only in FY2024; aggregate CI grazes zero; FY2025 / 2026 directionally inconsistent. Implementation supports `side="lo"` for future re-validation. |

This follows the codified discipline of **"ship what has bootstrap-CI
evidence across cohorts; document but don't ship the rest"** that
emerged from the 2026-05-15 / 2026-05-16 sequence of four prior
exit-rule REJECTs ([[project-asym-exit]], [[project-peak-anchored-exit]],
[[project-timestop40-bootstrap-reject]], [[project-adx-adaptive-subcohort-reject]]).

---

## Practical reading of the signal

The signal **compresses returns**, it does not invert them.  Even in
the most-bearish-breadth quintile (Q1), aggregate forward returns are
still positive (+0.70 %), just much smaller than baseline (+1.15 %).
In bear regimes (FY2024) the compression can flip Q1 outright negative
(−2.9 %).  Practical operator reading:

| breadth quintile | typical fraction at N-day highs | what to do |
|------------------|---------------------------------|------------|
| Q1 (top) | 16–29 % | **High reversal risk.**  Reduce new-entry sizing or skip. Especially restrictive in bear regimes (FY2024 Q1 forward N225 = −2.9 %). |
| Q2 – Q4 | 3–17 % | Normal regime. No sizing tilt. |
| Q5 (bottom) | < 2 % | **Low reversal risk.**  Tailwind for momentum entries — full sizing. |

This is the same shape as the existing `CorrRegime` indicator
(`src/indicators/corr_regime.py`): a quintile gate the strategy reads
at proposal time to tilt position sizing or block entries.

---

## Why `rev_nhi` and not `rev_nlo` — three hypotheses

The asymmetry between the two sides is striking.  Three (untested)
explanations for why `rev_nhi` carries usable breadth signal while
`rev_nlo` doesn't:

1. **Asymmetric market behaviour.**  Bottoms in equity markets tend to
   be sharper and shorter than tops; "many stocks at multi-day lows"
   is a rarer, more transient state than "many stocks at multi-day
   highs."  Breadth detection has less to work with on the down side.
2. **Bull-regime bias in the test window.**  FY2025 and 2026 YTD
   (2 of 3 cohorts) are net-bullish.  In a bull regime, "stocks at
   N-day highs" is a meaningful relative event but "stocks at N-day
   lows" is a noisy outlier event — exactly the regime where `rev_nlo`
   would need to prove itself.
3. **The N-day window is the wrong scale for lows.**  The detector
   uses the same N=20 for both sides.  Lows may need a tighter or
   wider window to capture meaningful exhaustion.  Untested.

---

## Key files

| File | Role |
|------|------|
| `src/indicators/rev_n_regime.py` | `RevNRegime` class — breadth computation + percentile cutoff |
| `src/signs/rev_nday.py` | `RevNDayDetector` — per-stock primitive |
| `src/indicators/corr_regime.py` | Template the indicator mirrors |
| `src/viz/daily.py` | `_get_revn_regime`, `_revn_banner`, `_regime_card` — UI integration |

---

## UI integration (display-only)

The Daily-tab "N225 Regime" card now includes a small reversal-risk
row:

```
Reversal Risk: HIGH ▲   breadth 24.5 %  (cutoff 17.8 %, P89)
```

or

```
Reversal Risk: normal   breadth 8.3 %   (cutoff 17.8 %, P42)
```

This is **display-only**.  The indicator does not:

- Filter or rank proposals.
- Block any sign type.
- Tilt position sizing automatically.
- Show up in the proposal-detail panel.

Those wiring points are deliberately left as future work — see Open
Questions below.

---

## Open questions

1. **Sizing-tilt or entry-gate wiring.**  Should the strategy
   automatically reduce position size or refuse new entries when
   `is_high()` fires?  Needs a separate A/B before ship.
2. **Per-corr-mode behaviour.**  Does the signal work uniformly across
   high / mid / low-corr stocks, or does it concentrate in one corr
   regime?  Sub-segmentation untested.
3. **rev_nlo formulation alternatives.**  Different N values, different
   percentile cutoff, or a hybrid (e.g. asymmetric N + body-filter
   tweak) might recover a usable signal on the down side.  Untested.
4. **FY2018 / FY2019 recovery.**  The dev DB has no `^N225` data prior
   to 2020-05-11.  Re-running the cohort robustness test on a longer
   window would tighten CIs and confirm whether the signal predates the
   2020s bull regime.  Tracked separately as a data-recovery task.
5. **Sign-by-sign breakdown.**  Does this signal also work for other
   sign types' breadth (e.g. `str_hold` breadth, `brk_bol` breadth)?
   The construction is generic; the test was rev_nday-specific.

---

## Reproducing the result

The empirical test that produced this finding was a one-shot inline
script run on 2026-05-16.  It used:

- Cohorts: FY2024, FY2025, 2026 YTD, ALL (union).
- Universe: 219 stocks from `classified2024` (the live trading set).
- Bootstrap: 10,000 iterations, seed `20260516`.
- Horizon: 10 bars forward (N225 close-to-open over next 10 trading days).
- Quintiles ranked by descending breadth (Q1 = highest breadth).

The full output tables for both `rev_nhi` and `rev_nlo` cohort splits
appear in the session log; the bootstrap CIs and decision logic in
this document are the verbatim figures from that run.  Future
re-validation should use the same script shape with an extended
window once FY2018 / FY2019 OHLCV is recovered.

---

## See also

- `docs/analysis/moving_corr.md` — rolling-correlation indicator that
  feeds `CorrRegime`.
- `docs/analysis/peak_corr.md` — peak-event correlation analysis.
- `docs/signs/rev_nday.md` — per-stock `rev_nhi` / `rev_nlo` signs.
- `src/indicators/corr_regime.py` — sibling regime indicator
  (templates `RevNRegime`).
- Memory: `project_adx_adaptive_subcohort_reject.md` — the lesson that
  motivated the bootstrap-CI prerequisite and the "ship-what-survives"
  discipline applied here.
