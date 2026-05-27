# Pre-Registration — Standalone PEAD Sleeve (selection-alpha null)

**Status:** PRE-REGISTERED — frozen before the null is computed · **Date:** 2026-05-27
**Governs:** `docs/analysis/pead_sleeve_thesis.md` (f1771a8, goalposts) +
`docs/analysis/pead_sleeve_design_proposal.md` (design, operator-selected 2026-05-27)
**No-iteration clause:** this design is final. A failed null is final *for this design*;
any change (slots, horizon, universe, sign set, exit, fill rule) is a NEW pre-registration,
not a re-run. Results do not reopen any choice below.

---

## 1. Locked design (frozen — not re-decidable post-result)

| Axis | Locked value |
|---|---|
| Candidate pool | N225-cohort up-revisions only: ΔFY-forecast-EPS / price **> 0**, signal-1 pairing & entry. No magnitude gate. |
| Sign set | **Single sign** (up-revision). No price-confirmation. |
| Exit / horizon | **`TimeStop(60)`** — fixed 60-bar hold. No TP, no SL. (alpha-stop probe rejected it at every θ, 2026-05-27.) |
| Slot fill | 2 slots, **diversification-priority**, **skip-not-queue**, eligibility = the event's own `tradable_entry_day` only. |
| Concurrency | ≤1 high-corr + least-correlated greedy pick; null block-bootstraps by reporting window. |
| Universe | **N225 cohort** (225 `ohlcv_1d` codes). No expansion. |
| Capital | **¥0.6M sleeve / ¥1.4M confluence** of one ¥2.0M account; equal-yen ¥0.3M/slot; integer 100-share lots; affordability-skip if 1 lot > ¥0.3M. |
| Power stance | Accept ~75 trades / ~36 reporting-window blocks over 9 FYs; **let the null rule** (no effect-size floor; a wide CI is a clean reject). |

**Entry mechanics (inherited):** after-close (≥15:00) → next session; fill at the **open**
of the entry day (two-bar rule); calendar = TOPIX dates.

**Diversification key (pre-committed):** when a free slot has >1 eligible up-event on the
same day, fill the contender that **minimizes the maximum |trailing-60-bar daily-return
correlation| to currently-held sleeve names** (greedy least-correlated; an empty book → any).
Ties broken by the fill-order shuffle seed. Selection is for **diversification, not
predicted return** (size/magnitude is not a key — it was flat in validation).

---

## 2. Data & scope

- **Complete FYs FY2017–FY2025 (9)** = the test set. FY2026 is partial → **watch-item only**,
  reported, never in a gate (truncated, early-FY-biased).
- Cohort = the 225 `ohlcv_1d` codes (`to_yf_code(local_code) ∈ cohort`).
- β = trailing-60-bar daily-return beta vs **TOPIX** (signal 1's market series; guarantees
  date alignment). Per position, computed at entry.
- `cbt._MULTIYEAR_MIN_RUN_ID = 0` (default 47 drops early-FY confluence runs id 9–46).

---

## 3. The benchmark ladder (capital-aware, same ¥2.0M everywhere)

All four books share the **identical ¥1.4M confluence slice** (same trades, same fill-order
shuffle per seed) — it cancels in the binding comparison. Only the ¥0.6M slice differs:

| # | ¥0.6M slice | Isolates |
|---|---|---|
| 1 | (none — confluence runs the full ¥2.0M) | **baseline** (sleeve-off) |
| 2 | index (TOPIX-ETF proxy) held **continuously at constant effective exposure** = avg position-β × avg invested-fraction | **leverage / avg exposure** |
| 3 | index held **only on the sleeve's actual hold-days, at each filled position's β & weight** | + the sleeve's **exposure schedule** |
| 4 | the **real PEAD sleeve** (locked design §1) | full sleeve |

- Per filled position, **book-4 daily return − book-3 daily return = the β-stripped alpha**
  of that name over its window — i.e. (3) replaces each real name with `βᵢ · index` over the
  *identical* 60-bar hold, same weight. Book 3 is the portfolio aggregate of the per-position
  β-stripped CARs the calibration probe already computes — **no new data**.
- The index proxy is the **TOPIX series** (used as the ETF stand-in; the sleeve's β is vs
  TOPIX, so this is the matched hedge leg). Stated up front; not swapped post-result.
- **Convention is identical across books** (open-fill, same windows, same betas), so any
  close-vs-open or basis convention cancels in (4 vs 3).

---

## 4. The binding null — selection alpha (4 vs 3)

**Statistic:** `ΔSharpe = Sharpe(book 4) − Sharpe(book 3)`, annualized, on the **full
blended ¥2.0M daily stitched equity curve** (NOT per-trade Sharpe — the root lesson). Measured
at the portfolio level on purpose: a real-but-tiny alpha slice that cannot move ¥2.0M Sharpe
is, by the thesis, not deployable — that is the power stance, honestly priced.

**Two paired randomization layers, same seed to book 4 and book 3:**

1. **Fill-order shuffle** — randomizes tie-order in the diversification-priority slot fill,
   so residual ordering luck in *which* names fill is averaged out. Book 3 fills the
   **same** names' β·index legs (paired).
2. **Clustering-aware resample** — **circular block bootstrap of the ¥0.6M slice's daily
   return series**, block length **L = 60 trading days** (one reporting cycle + the 60-bar
   hold — keeps an earnings cluster and its drift together). Same resampled day-indices
   applied to both books (paired). This is the machinery that refuses to overstate
   significance from earnings clustering.

**K = 2,000** combined seeds (seed = 0 … 1999; base RNG `np.random.default_rng(seed)`).

**Gate (BINDING):** `P(ΔSharpe > 0) ≥ 0.95` **AND** the 2.5th-percentile of the ΔSharpe
distribution `> 0`. No pre-committed effect size.

**Robustness (reported, NOT gates):** the same null at L ∈ {20, 120} to show the verdict is
not an artifact of block length.

---

## 5. Diagnostics (reported; (3 vs 2) and below NEVER govern the decision)

- **(3 vs 2) = timing alpha** — pro-cyclical, regime-fragile; reported skeptically. If the
  sleeve passes (3 vs 2) but **fails (4 vs 3)**, it is **rejected as a PEAD-alpha sleeve** —
  it would be a market-timing strategy in a PEAD coat, requiring its own pre-registration.
- **(2 vs 1) = leverage / avg-beta** — never credited as PEAD edge.

---

## 6. Secondary guardrails (must hold; guardrails, not the decision)

- **Independence:** β-stripped (alpha) daily-return correlation between the sleeve slice
  (book4 − book3 daily) and confluence's β-stripped daily returns **< 0.5**. Raw correlation
  is not tested (both are long-beta). Must hold.
- **OOS direction:** FY2025 (most-recent complete) and FY2026-partial blended ΔReturn
  (book 4 − book 1) share the sign of the full-sample estimate. **Diagnostic** (single-FY
  noise), not pass/fail.
- **Do-no-harm:** the worst confluence FY's blended **RAW** (β-inclusive) return under
  book 4 ≥ book 1 − **1.5pp**. On raw return by design (harm is lived P&L; the deploy
  decision tests alpha — intentional asymmetry). Breach → review, not auto-reject.

---

## 7. Decision rule & falsifier

- **DEPLOY to T1 (¥0.6M / 2 slots)** iff the §4 binding gate passes **and** §6 independence
  holds. The §6 OOS/do-no-harm diagnostics inform but do not veto.
- **REJECT** otherwise — final for this design (no-iteration clause). A reject with a positive
  point estimate but CI through 0 is a signpost toward **more slots (capital)** or **universe
  expansion**, each a separate pre-registration — not a re-run here.

> **Falsifier (thesis §9):** if the clustering-aware paired null on selection alpha
> (book 4 vs book 3) does not reach `P(Δ>0) ≥ 0.95` with a 95% CI lower bound `> 0`, the
> sleeve is rejected — the PEAD selection alpha does not survive portfolio construction at
> deployable capital.

---

## 8. Anti-mining / discipline

- **One primary metric, one null** (§4). Per-trade EV is explicitly NOT the criterion (it
  already passed; not the binding question).
- **No second formulation after results** (no-iteration clause, §1 header).
- **Fixed up front:** K = 2,000; seeds 0…1999; block length L = 60 (robustness L∈{20,120});
  index proxy = TOPIX; capital split ¥0.6M/¥1.4M; FY set FY2017–FY2025.
- **All FYs reported**, no cherry-picking. FY2026 partial shown as watch-item.
- The ladder (§3) blocks the two likely false positives (leverage, pro-cyclical timing) by
  construction.

---

## 9. Build plan (to run the null)

New script `src/analysis/pead_sleeve_null.py` (~250 lines, mirrors
`confluence_capacity_null.py` / `confluence_pead_boost_null.py` structure):

1. **Event set + per-position alpha** — reuse `pead_forecast_revision` pure fns
   (`Disclosure, pair_same_fy_revisions, revision_surprise, tradable_entry_day, beta,
   beta_stripped_car`). Cohort up-events, entry day, per-position β & 60-bar β·index leg
   (= book-3 position return) and real return (= book-4 position return).
2. **¥0.6M sleeve sim** — 2 slots, diversification-priority greedy least-correlated fill,
   skip-not-queue, `TimeStop(60)`, integer-lot sizing via `src/portfolio/sizing.py`
   (`recommended_lots(0.6M, price, n_slots=2)`, `position_weight`). Produces the daily
   return series for book 4 and book 3 slices.
3. **¥1.4M confluence slice** — reuse `confluence_benchmark` capital-aware book at
   `_BUDGET = 1_400_000` (set `cbt._MULTIYEAR_MIN_RUN_ID = 0`); identical & paired across
   books 2–4.
4. **Books 1/2** — book 1 = confluence at full ¥2.0M; book 2 = ¥1.4M confluence + ¥0.6M
   constant-exposure index (diagnostic).
5. **Null** — for seed 0…1999: paired fill-order shuffle + circular block bootstrap (L=60)
   of the blended daily curve; ΔSharpe(4−3). Report P(Δ>0), 2.5/50/97.5 percentiles, the
   §5 diagnostics, §6 guardrails, per-FY table, and the L∈{20,120} robustness rows.
6. **Read-only** — no DB writes, no production-code changes. (The live sleeve registration
   branch — tp/sl=None, sleeve tag, separate slot counter — is a *build-time* task gated on
   a PASS, overlapping the parked Order/Entry/Cancel plan; NOT part of this null.)

---

## 10. What a verdict triggers

- **PASS** → T1 deploy plan: sleeve-aware registration (no TP/SL, sleeve tag, separate
  2-slot counter in the Daily tab), ¥0.6M allocation, shadow-then-live; T2/migration per
  thesis §3/§7 (separate, higher bars).
- **FAIL** → record as the third PEAD-harvest attempt outcome; the remaining sanctioned
  paths are universe expansion (Stage-0 menu-width probe, pending) and sizing — each its
  own pre-registration. PEAD selection alpha stays validated cross-sectionally (signal 1)
  but unharvested at this capital.

---

## 11. RESULT — REJECT (run 2026-05-27, `src/analysis/pead_sleeve_null.py`)

n=1,792 cohort up-revisions → 1,252 affordable (¥0.3M/slot ceiling drops ~30%); ~8 filled/yr
over 8 FYs (FY2018–FY2025; FY2017 had no confluence stock-set). Stitched 1,943 trading days,
K=2,000 paired seeds.

**Binding gate (4 vs 3) — FAIL at every block length:** L=60 mean ΔSharpe **+0.054, P(Δ>0)
0.699, 95% CI [−0.145, +0.257]**; L=20 +0.058 / 0.717 / [−0.150,+0.267]; L=120 +0.054 / 0.697 /
[−0.157,+0.264]. Required `P≥0.95 AND CI-lower>0` → not met. Point curves: book1 +0.884 /
book2 +0.816 / book3 +0.844 / book4 +0.896.

**This is the pre-registered "signpost" reject, not "signal is fake":** point estimate
positive and stable; the sleeve's names beat β·index in ~70% of resamples (consistent with
signal 1's +2.51% cross-sectional edge); but the harvest is too small to move ¥2.0M portfolio
Sharpe above fill-order + clustering noise at ¥0.6M / 2 slots / ~8 bets-yr.

**Independence guardrail PASSED, notably: β-stripped sleeve-vs-confluence corr +0.006** — the
diversification premise was correct; the PEAD alpha is genuinely orthogonal to confluence,
just sub-power at this capital.

**Diagnostics (non-binding):** (2 vs 1) leverage −0.068 (displacing ¥0.6M of confluence costs
Sharpe — the structural cost of any sleeve, correctly not charged to PEAD); (3 vs 2) timing
+0.028 (small). Do-no-harm breached (worst-FY −15.1pp FY2020) and full-sample raw Δ −5.36pp,
but both are book4-vs-full-¥2.0M-confluence = dominated by the displacement, not a PEAD defect;
the binding gate governs.

**VERDICT: REJECT — final for this design (no-iteration clause).** Signpost → capacity
(slots/capital) or universe expansion, each a new pre-registration. Signal 1 remains a
validated cross-sectional edge; it is unharvested at deployable capital via this sleeve.
