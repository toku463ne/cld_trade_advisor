# Breadth Indicator Family — Comparison and Selection

Cross-cutting comparison of four universe-level breadth indicators against
forward 10-bar `^N225` returns.  The motivating question: now that
`RevNRegime` is shipped, are there other breadth-style indicators worth
adding, and if so which ones provide independent information?

The analysis was conducted on 2026-05-16 using the
`classified2024` universe (219 representative stocks) over the
period 2023-03-01 to 2026-05-15, with cohort splits across
FY2024, FY2025, 2026 YTD, and the aggregate ALL.

---

## Indicators tested

| indicator | what it measures | source |
|-----------|------------------|--------|
| **rev_nhi** | % of universe firing `rev_nhi` (touched prior-20-day high with bearish body) | `src/signs/rev_nday.py` |
| **SMA(50)** | % of stocks closing above their own 50-day SMA | computed inline |
| **Kumo**    | % of stocks closing above their own Ichimoku Kumo cloud | `src/strategy/regime_sign._stock_kumo_series` |
| **CorrRegime** | % of stocks with `|corr to ^N225| > 0.70` over 20-bar rolling window | `src/indicators/corr_regime.py` |

For each indicator the daily breadth fraction is computed across the
universe.  Days are stratified into quintiles by breadth (Q1 = highest
breadth, Q5 = lowest).  Forward 10-bar `^N225` return is averaged per
quintile, and the Δ(Q1 − Q5) is bootstrapped with 10,000 iterations
to obtain a 95 % CI.

PASS criterion: `|Δ(Q1−Q5)| ≥ 1pp` AND `CI does not cross 0`.
Robustness: PASSES in ≥ 2 of `FY2024`, `FY2025`, `2026 YTD`
plus the aggregate.

---

## Per-cohort results

### FY2024 (244 trading days, bear-ish year)

| indicator | Q1 fwd | Q5 fwd | Δ(Q1−Q5) | 95 % CI | verdict |
|-----------|--------|--------|----------|---------|---------|
| rev_nhi   | −2.90 % | +1.93 % | **−4.83 pp** | [−6.62, −3.10] | ✅ PASS |
| **SMA(50)** | **−5.24 %** | **+3.49 %** | **−8.73 pp** | **[−10.68, −6.84]** | ✅ **PASS (strongest)** |
| Kumo      | −2.25 % | +1.52 % | −3.77 pp | [−5.75, −1.82] | ✅ PASS |
| **CorrRegime** | **+1.40 %** | **−1.55 %** | **+2.94 pp** | **[+1.24, +4.66]** | ✅ PASS — **opposite sign** |

### FY2025 (244 trading days, bull year)

| indicator | Q1 fwd | Q5 fwd | Δ(Q1−Q5) | 95 % CI | verdict |
|-----------|--------|--------|----------|---------|---------|
| rev_nhi   | +0.77 % | +3.14 % | −2.38 pp | [−3.95, −0.80] | ✅ PASS |
| SMA(50)   | +1.37 % | +3.13 % | −1.76 pp | [−3.52, −0.02] | ✅ PASS |
| Kumo      | +1.09 % | +3.12 % | −2.04 pp | [−3.79, −0.37] | ✅ PASS |
| CorrRegime| +3.55 % | +2.19 % | +1.36 pp | [−0.25, +3.05] | ✗ FAIL (CI grazes 0) |

### 2026 YTD (76 trading days, small sample)

| indicator | Q1 fwd | Q5 fwd | Δ(Q1−Q5) | 95 % CI | verdict |
|-----------|--------|--------|----------|---------|---------|
| rev_nhi   | +2.21 % | +1.72 % | +0.49 pp | [−3.44, +4.49] | ✗ FAIL |
| SMA(50)   | −1.35 % | +4.98 % | **−6.32 pp** | [−9.43, −3.20] | ✅ PASS |
| Kumo      | −2.68 % | +7.70 % | **−10.39 pp** | [−12.79, −7.86] | ✅ PASS (very strong) |
| CorrRegime| +8.32 % | −2.69 % | **+11.01 pp** | [+8.35, +13.55] | ✅ PASS — opposite sign |

### ALL — aggregate (~720–750 trading days)

| indicator | Q1 fwd | Q5 fwd | Δ(Q1−Q5) | 95 % CI | verdict |
|-----------|--------|--------|----------|---------|---------|
| rev_nhi   | +0.71 % | +2.15 % | −1.44 pp | [−2.37, −0.52] | ✅ PASS |
| **SMA(50)** | **+0.72 %** | **+3.37 %** | **−2.64 pp** | **[−3.57, −1.69]** | ✅ **PASS (strongest)** |
| Kumo      | +1.31 % | +2.94 % | −1.63 pp | [−2.68, −0.61] | ✅ PASS |
| **CorrRegime** | +2.97 % | +1.22 % | **+1.75 pp** | **[+0.79, +2.71]** | ✅ PASS — **opposite sign** |

### Aggregate roll-up

| indicator | aggregate Δ | cohorts passed | best | worst |
|-----------|-------------|----------------|------|-------|
| rev_nhi   | −1.44 pp | 2/3 (FY2024, FY2025) | FY2024 −4.83 | 2026 YTD +0.49 |
| **SMA(50)** | **−2.64 pp** | **3/3** | FY2024 −8.73 | FY2025 −1.76 |
| Kumo      | −1.63 pp | **3/3** | 2026 YTD −10.39 | FY2025 −2.04 |
| CorrRegime | **+1.75 pp** | 2/3 (FY2024, 2026 YTD) | 2026 YTD +11.01 | FY2025 +1.36 |

---

## Two families, one with opposite direction

Three of the four indicators (`rev_nhi`, `SMA(50)`, `Kumo`) point in
the same direction: **high breadth → lower forward N225 returns**
(mean-reversion / compression).

The fourth, `CorrRegime`, points the **opposite** direction: **high
breadth → higher forward N225 returns** (trend continuation /
momentum).

This is not a bug.  The two families measure different things:

| family | what it measures | signal direction |
|--------|------------------|------------------|
| **Extremity breadth** (rev_nhi, SMA(50), Kumo) | How many stocks are extended at multi-day highs / above trend / above their own cloud | High → compression |
| **Lockstep breadth** (CorrRegime) | How many stocks are moving together with the index, regardless of direction | High → continuation |

In a net-bullish environment, "lockstep" mostly means "moving up
together," which predicts further upside.  In a bear-trending
environment, lockstep would mean "moving down together," which would
predict further downside.  The CorrRegime metric is **direction-
agnostic** about breadth but **regime-dependent** about its forward
implication.

### Note on `CorrRegime`'s production use

`CorrRegime` is already wired into the project's portfolio-management
philosophy (per `CLAUDE.md`): block new entries (especially high-corr
ones) when its fraction exceeds the historical 80th percentile.
That rationale is about **false diversification quality** (multiple
high-corr stocks are the same bet), not about forward N225 direction.
The data above does NOT invalidate that use — but it does say that
`CorrRegime` should NOT be repurposed as a reversal-risk gate.  It
answers a different question.

---

## Redundancy analysis within the extremity family

Pearson correlation between the three extremity-breadth series
(common 731 days):

| pair | r |
|------|---|
| rev_nhi vs SMA(50) | +0.486 |
| rev_nhi vs Kumo    | +0.369 |
| **SMA(50) vs Kumo** | **+0.937** |

`SMA(50)` and `Kumo` breadths are 94 % correlated — essentially
measuring the same thing.  Both are "% of stocks above some trend
reference" detectors.  SMA(50) uses a 50-day moving average; Kumo
uses Ichimoku's senkou A/B band.  Different math, near-identical
breadth signal.

### 2×2 conditional: Kumo vs SMA(50)

Each axis split at top quintile (Q1 = HIGH).

| cell | n | mean fwd |
|------|---|----------|
| Kumo=H, SMA=H | 124 | +0.79 % |
| Kumo=H, SMA=L | **22** | +3.79 % |
| Kumo=L, SMA=H | **22** | +0.46 % |
| Kumo=L, SMA=L | 552 | +1.05 % |

The off-diagonal cells have only 22 days each — direct evidence of
the redundancy.  Within `SMA=LOW`, `Kumo=H` shifts forward return by
+2.74 pp (wrong direction, CI [+0.98, +4.41]) but on a 22-day
sample; not actionable.  Within `Kumo=LOW`, `SMA=H` shifts forward
return by −0.60 pp (right direction, CI [−1.82, +0.60], CI crosses 0)
also on a 22-day sample.  **Neither marginal contribution is
decisive, and both rest on the tiny disagreement cells.**

### 2×2 conditional: rev_nhi vs SMA(50)

| cell | n | mean fwd |
|------|---|----------|
| nhi=H, SMA=H | 68 | **+0.034 %** |
| nhi=H, SMA=L | 77 | +0.61 % |
| nhi=L, SMA=H | 78 | +1.35 % |
| nhi=L, SMA=L | 498 | +1.25 % |

Marginal contribution tests (from earlier session):

- Within `SMA=LOW`: Δ(nhi=H vs L) = −0.63 pp, CI [−1.62, +0.32]
  (CI crosses 0, point in expected direction).
- Within `SMA=HIGH`: Δ(nhi=H vs L) = **−1.31 pp, CI [−2.49, −0.09]**
  (statistically significant; rev_nhi **sharpens** SMA(50)).
- Within `nhi=LOW`: Δ(SMA=H vs L) = +0.10 pp, CI [−0.91, +1.07]
  (SMA alone, without rev_nhi confirmation, predicts nothing).

So `rev_nhi` **does** add information to `SMA(50)`, particularly as
a confirmation signal: when both indicators flag HIGH, forward N225
returns compress to near zero (`+0.03 %`).  When only `SMA(50)` flags
high, forward returns remain near baseline (`+1.35 %`).

### Triple-AND check (all three Q1 simultaneously)

| condition | n | mean fwd | P<0 |
|-----------|---|----------|-----|
| All three HIGH | 56 (7.7 %) | **−0.02 %** | 50.0 % |
| None HIGH (baseline) | 482 | +1.16 % | — |
| Δ | — | **−1.18 pp** | CI [−2.19, −0.20] |

The triple-AND identifies a real concentrated-reversal regime, but
since `SMA(50)` and `Kumo` are 94 % correlated, "all three HIGH" is
effectively `(SMA(50) AND rev_nhi)` with extra ceremony.  No
material improvement over the pair.

---

## Selection — ship two, not three

| indicator | role | ship status |
|-----------|------|-------------|
| **SMA(50)** | Primary trend-breadth gate. Strongest standalone signal (aggregate Δ −2.64 pp), passes all individual cohorts (3/3). | **Build `SMA50Regime` next.** |
| **rev_nhi** | Confirmation signal — sharpens SMA(50) at the joint HIGH cell (n=68 days, fwd N225 +0.03 %). | Already shipped as `RevNRegime` (2026-05-16). |
| **Kumo** | Redundant with SMA(50) at r = 0.94. No actionable marginal contribution to either rev_nhi or SMA. | **Skip.** |
| **CorrRegime** | Opposite-direction signal; measures lockstep / false-diversification rather than reversal-risk. | Already in use for portfolio gating (`CLAUDE.md` philosophy). **Do NOT fold into reversal-risk UI.** |

### Operational reading

The actionable reversal-risk signal is `SMA(50) HIGH AND rev_nhi HIGH`
— a 9.3 % subset of trading days where forward N225 returns compress
to near zero.  Either indicator alone produces a much weaker signal:
SMA(50)=HIGH alone gives +1.35 % (essentially baseline) when not
confirmed by rev_nhi.  The two indicators are complementary; the
AND-gate is the actionable form.

---

## Lessons codified

1. **Highly correlated indicators look strong individually but collapse
   in conditional analysis.**  `SMA(50)` and `Kumo` both PASSED 3/3
   cohorts in standalone bootstrap.  Only the r = 0.94 correlation
   between them revealed they're measuring the same thing.  **Always
   compute pairwise correlations before deciding which to ship.**
2. **The strongest standalone signal can be partially driven by its
   correlation with another signal.**  `SMA(50)` aggregate Δ
   appeared as `−2.64 pp` but on the subset where `rev_nhi` does NOT
   flag, the SMA effect drops to `+0.10 pp` (CI [−0.91, +1.07]).
   This is the classic confounding pattern — the joint dependence
   must be measured to know each indicator's true marginal value.
3. **Opposite-direction breadth signals are real and shouldn't be
   force-fit into one framework.**  `CorrRegime` measures something
   genuinely different from `rev_nhi`/`SMA(50)`/`Kumo` and pointing
   the opposite direction is consistent with what it actually
   measures.  Treating it as "another reversal-risk gate" would be
   wrong-direction.
4. **`SMA(50)` and `Kumo` breadths are interchangeable.**  When the
   strategy already uses Kumo for per-stock regime classification,
   the universe-level Kumo breadth doesn't add a new gate.  Two
   different per-stock indicators can collapse into the same breadth
   signal — universe aggregation reveals what stock-level views can
   hide.

---

## Open questions

1. **`SMA50Regime` implementation and dual-banner UI.**  The
   recommended next step.  ~60 lines mirroring `RevNRegime`.  The
   Daily-tab banner should show both flags and highlight when BOTH
   simultaneously HIGH (the actionable AND-gate state).
2. **CorrRegime forward-return bootstrap on a longer / bear-heavy
   cohort.**  The +1.75 pp aggregate effect rests on a bull-dominated
   sample.  If a longer window with more bear regimes inverts the
   sign, the interpretation changes.  FY2018 / FY2019 data recovery
   (tracked separately) would help.
3. **Other sign-types as breadth indicators.**  The generic
   "count fires per universe per day" pattern could be applied to
   `brk_bol`, `str_hold`, `str_lead`, `div_*` etc.  Most will likely
   fail the bootstrap discipline (see codified pattern of 4 REJECTs
   in 2 days from prior cycles), but a few may surprise.
4. **Sizing-tilt vs entry-block A/B.**  Both `RevNRegime` and a
   future `SMA50Regime` are currently display-only.  The natural
   next experiment is an A/B comparing "no gate" vs "skip new entries
   when AND-gate fires" vs "half-size new entries when AND-gate
   fires."  Needs a faithful regime_sign-cohort probe — same shape
   as `src/analysis/peak_price_exit_probe.py`.

---

## Key files

| File | Role |
|------|------|
| `src/indicators/rev_n_regime.py` | `RevNRegime` — rev_nhi breadth (shipped) |
| `src/indicators/corr_regime.py` | `CorrRegime` — lockstep breadth (shipped, different purpose) |
| `src/signs/rev_nday.py` | Per-stock primitive for `RevNRegime` |
| `src/strategy/regime_sign.py` | `_stock_kumo_series` — per-stock Kumo state used by Kumo breadth |
| `docs/analysis/rev_n_regime.md` | Companion writeup specifically for `RevNRegime` |
| `docs/analysis/moving_corr.md` | Underlying primitive for `CorrRegime` |

---

## See also

- `docs/analysis/rev_n_regime.md` — focused writeup of the rev_nhi
  breadth indicator (already shipped).
- `docs/analysis/moving_corr.md` — rolling-correlation primitive.
- `docs/analysis/peak_corr.md` — peak-event correlation analysis.
- Memory `project_adx_adaptive_subcohort_reject.md` — the lesson that
  motivated the bootstrap-CI prerequisite applied throughout this
  analysis.
