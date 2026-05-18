# Follow-ups — things we deferred and must revisit

Canonical "don't forget" list for decisions that were intentionally
shelved.  Each entry must include:

- **What** — the deferred change in one line.
- **Why deferred** — what gate failed / why we couldn't ship now.
- **Trigger to revisit** — concrete condition that, once true, makes
  it worth re-running the cycle (data accrual, infra build,
  upstream change, etc.).
- **Owner** — who's expected to action it (default: operator).
- **Links** — analysis doc / memory entries / probe scripts.

Sorted roughly by recency.  Move items to the **Done** section when
shipped (with a date) or **Dropped** when intentionally abandoned.

---

## Open

### 3. Long-term-high continuation as a new sign — REJECT (both spec variants)

- **What** — Adding `brk_nhi` / similar as a continuation-framed sign
  on N-bar high breakouts, N ∈ {60, 120, 250}.  Two fire-rule variants
  tested:
  - **Loose** — `close[T] > rolling_max(close, N)[T-1]`
  - **Strict** — `low[T] > rolling_max(close, N)[T-1]` (entire bar
    holds above prior resistance, no intraday violation; operator-
    corrected spec)
- **Why deferred (rejected)** — Both variants FAIL the same EV-primary
  gate (need pooled EV ≥+0.020 and DR ≥53%):

  | Variant | N | n_fires | pooled EV | DR | gate |
  |---|---|---|---|---|---|
  | Loose  | 60  | 30,712 | +0.0027 | 50.3% | FAIL |
  | Loose  | 120 | 24,134 | +0.0045 | 51.0% | FAIL |
  | Loose  | 250 | 18,864 | +0.0030 | 50.9% | FAIL |
  | Strict | 60  |  9,460 | +0.0016 | 50.5% | FAIL |
  | Strict | 120 |  7,329 | +0.0031 | 51.1% | FAIL |
  | Strict | 250 |  5,627 | +0.0012 | 50.8% | FAIL |

  Strict gave NO meaningful lift over loose despite being a 3× tighter
  filter — the spec-correction hypothesis (clean breakouts carry the
  edge that loose breakouts don't) is empirically false on this data.
  Same 4 FYs negative across both variants (FY2019, FY2021, FY2022,
  FY2024); same 3 positive (FY2020, FY2023, FY2025).
- **Pattern observed** — Bull/bear N225 regime split dominates the
  per-FY EV sign.  Same level-touch event mean-reverts in mixed
  regimes — validates rev_nhi's "reversal" default framing.  Probe B1
  (sideways breakout) deferred per Path E logic.
- **Trigger to revisit** — Only if a **regime-conditional** framing
  surfaces (e.g., "long-high continuation conditional on N225 ADX>20
  AND +DI>−DI").  Default state is REJECTED across both spec variants;
  don't re-litigate without a new conditioning variable.
- **Owner** — operator.
- **Links**:
  - Probe (loose): `src/analysis/long_high_continuation_probe.py`
  - Probe (strict): `src/analysis/long_high_continuation_strict_probe.py`
  - Reports: `src/analysis/benchmark.md` §§ Long-Term High Continuation Probe (+ Strict)
  - Sign-debate cycle: 2026-05-17 (this session)

---

### 2. Measure rev_peak ∩ rev_nday event overlap (operator-deferred question)

- **What** — Build a pairwise event-overlap table between rev_peak
  sign-types (`rev_lo`, `rev_hi`) and rev_nday sign-types (`rev_nhi`,
  `rev_nlo`) on `(stock_code, fire_date)` joined from `SignBenchmarkEvent`.
  Report: fraction of rev_lo fires that share a date with rev_nlo;
  fraction of rev_hi fires sharing a date with rev_nhi; per-pair
  conditional EV when co-fired vs standalone.
- **Why deferred** — Operator's original question (2026-05-17 sign-debate)
  was "does rev_peak overlap with rev_nday?" The judge accepted Option D
  (hide rev_hi only, UI-only) which is invariant to overlap magnitude,
  but the operator's mechanism question is still unanswered. No pairwise
  sign-overlap script exists in the repo yet — would be a first.
- **Trigger to revisit** — Run a one-off probe (~150 lines, pandas join
  on `SignBenchmarkEvent`) when convenient. If results show:
  - **rev_lo ∩ rev_nlo overlap > 30% on shared (stock, date) fires AND
    co-fired rev_lo Sharpe < standalone rev_nlo Sharpe − 1.0** →
    `rev_lo` is redundant relabeling; reconsider hiding it too
    (extending `_HIDDEN_PROPOSAL_SIGNS` to `{"rev_nhi", "rev_hi", "rev_lo"}`).
  - **Overlap ≤ 30%** → operator's redundancy intuition is empirically
    false; close this followup as Dropped.
- **Owner** — operator.
- **Links**:
  - Sign-debate cycle: 2026-05-17 (this session)
  - Precedent UI-hide: `src/viz/daily.py:80` `_HIDDEN_PROPOSAL_SIGNS`
  - Detectors: `src/signs/rev_peak.py` (zigzag-confirmed swing peaks),
    `src/signs/rev_nday.py` (N-day rolling extremum)
  - Probe template: `src/analysis/regime_sign_no_revnhi_probe.py`
    (for the SignBenchmarkEvent join pattern)

---

### 1. Re-evaluate removing `rev_nhi` from the regime ranking

- **What** — Set `EXCLUDE_SIGNS = frozenset({"rev_nhi"})` in
  `src/analysis/regime_sign_backtest.py` and apply the equivalent
  filter to live `RegimeSignStrategy` instantiation in
  `src/viz/daily.py:_get_run_ids`.  Production change.
- **Why deferred** — Pre-ship bootstrap CI (2026-05-16) gave a
  mixed verdict: per-FY gate PASS (4/5 FYs Δ Sharpe ≥ 0; FY-level
  Δ Sharpe CI [+0.009, +2.32]) but trade-level Δ Sharpe CI was
  [−2.13, +4.77] (straddles 0).  By the pre-registered AND gate
  the proposal REJECTS.  Point estimate +1.23 Sharpe / +0.87 pp
  mean_r is real but n=171/174 over 5 FYs is too thin to certify.
- **Trigger to revisit** — Re-run the bootstrap probe
  (`src/analysis/regime_sign_no_revnhi_bootstrap.py`) when **any
  one** of:
  - FY2026 completes (~30–40 more trades; would push effective
    n to ~210/214 over 6 FYs).
  - The universe expands to a new `classifiedYYYY` stock set
    that materially grows per-FY n (e.g., from ~30/FY to ~50/FY).
  - A revised `rev_nhi` detector (different gate, different params)
    produces a re-benchmarked `SignBenchmarkRun` that materially
    changes the regime-ranking cell EVs.
- **In-the-meantime mitigation (shipped 2026-05-16)** —
  Daily tab hides `rev_nhi` proposal rows entirely via
  `_HIDDEN_PROPOSAL_SIGNS = {"rev_nhi"}` in `src/viz/daily.py`.
  Production ranking is unchanged (backtest still includes rev_nhi);
  only the operator-facing proposals table drops them so the proposal
  list isn't cluttered with rows whose standalone-entry edge is weak.
  First iteration used a "factor-only" badge + dimmed styling but
  rev_nhi rows still cluttered the list — second iteration removes
  them outright.
- **Owner** — operator.
- **Links**:
  - Analysis doc: `docs/analysis/rev_nhi_remove_from_ranking.md`
  - Probe (A/B): `src/analysis/regime_sign_no_revnhi_probe.py`
  - Probe (bootstrap): `src/analysis/regime_sign_no_revnhi_bootstrap.py`
  - Probe report: `src/analysis/regime_sign_no_revnhi_probe.md`
  - Bootstrap report: `src/analysis/regime_sign_no_revnhi_bootstrap.md`
  - Related memory: `project_sign_sector_factor.md`
    (rev_nhi × 銀行 A/B-negative — already excluded).

### 4. Evaluation framework upgrades (2026-05-18 brainstorm)

Operator-proposed extensions to the evaluation rubric.  Verdict + scope
captured here; the three highest-priority items (Sortino + EV
decomposition, BH-FDR, marginal contribution) are being implemented in
the same session and will be removed from this list once shipped.

#### Deferred — worth doing when triggered

**(a) MAE / MFE / time-to-peak path statistics**

- **What** — Per-fire path metrics: Max Adverse Excursion (worst
  drawdown DURING trade), Max Favorable Excursion (best peak), and
  bar-index of MFE.
- **Why deferred** — These optimize EXIT rules, not entry signs.
  Current ZsTpSl(2.0, 2.0, 0.3) is fixed and shipped.  Per-fire DR is
  the entry-side metric.
- **Trigger to revisit** — When we revisit exit rule tuning (e.g.,
  tighter SL for high-MAE cohorts, longer hold for late-MFE cohorts).
  Or if we want to compare ZsTpSl to alternatives at fine granularity.
- **Implementation** — log per-fire (entry_bar_idx, mfe_bar_idx,
  mae_bar_idx, mfe_pct, mae_pct) when running `_first_zigzag_peak`.
  Aggregate to a "path quality" table per sign.
- **Links** — `src/exit/zs_tp_sl.py`, `src/analysis/exit_benchmark.py`.

**(b) Calmar / Omega ratio + pooled CVaR**

- **What** — Beyond Sharpe and Sortino: Calmar = annualized_return /
  max_drawdown; Omega = E[max(r−MAR, 0)] / E[max(MAR−r, 0)] around a
  threshold MAR; CVaR(5%) = mean of worst-5% trade returns.
- **Why deferred** — Calmar needs a continuous equity curve we don't
  currently maintain (our trades are sparse).  CVaR is stable only at
  n ≥ 100 — per-FY n=25-40 is too small.  Omega is theoretically
  elegant but Sortino captures most of the same intuition more cheaply.
- **Trigger to revisit** — When we have a live combined-strategy
  equity curve (Daily-tab P&L view or similar).  CVaR becomes useful
  once we pool across FYs and have n ≥ 200.
- **Implementation** — build a daily equity series from
  ConfluenceSignStrategy trades, compute running max, then Calmar +
  CVaR + Omega are 5-line additions to `_metrics()`.
- **Links** — `docs/evaluation_guide.md` §4.1.

**(c) New regime axes — cross-sectional dispersion, N225 realized vol**

- **What** — Add regime split axes beyond N225 bear/bull (ADX-based):
  - **Cross-sectional dispersion** = std of daily returns across the
    universe.  Low dispersion = "stocks move together" (correlation
    regime).  High dispersion = idiosyncratic moves dominate.
  - **N225 realized vol** (or VXJ index proxy) — high-vol vs low-vol
    regimes likely flip mean-reversion vs continuation behavior.
- **Why deferred** — Each new axis multiplies the regime-cell count
  (currently 9 cells from ADX×Kumo).  Adding 2 more axes → 9 × 2 × 2
  = 36 cells.  Per-cell sample sizes get thin fast.
- **Trigger to revisit** — When we want to refine signs that show
  bimodal per-FY behavior (works some years, fails others — possibly
  vol-regime-conditional).  One focused probe per axis is cheap.
- **Implementation** — extend `n225_regime_snapshots` table with
  realized_vol_20 column; build cross-sectional-dispersion daily
  series from stock returns; join to `SignBenchmarkEvent` via
  fired_at date.  Mirror existing bear/bull tagging logic.
- **Skip variants** — Yen trend already tested 2026-05-14
  (`project_usdjpy_corr_axis` in memory; OOS-failed).

**(d) Bootstrap CI in every A/B report template**

- **What** — Add bootstrap 95% CI on pooled Sharpe + per-FY Sharpe to
  every A/B's output table.  Currently bootstrap is run ad-hoc in
  specific probes (`project_timestop40_bootstrap_reject` etc.).
- **Why deferred** — Not a single missing piece — it's a template
  change across ~6 A/B scripts (`regime_sign_*_ab.py`,
  `confluence_*_ab.py`).  Worth doing in a single sweep when next
  touching A/B scripts.
- **Trigger to revisit** — Next time we write a new A/B script —
  cherry-pick the template upgrade and back-port to existing ones.
- **Implementation** — utility function in `src/analysis/_bootstrap.py`:
  `bootstrap_ci(returns: list[float], stat_fn, n_iter=10000, alpha=0.05)`.
  Call from each `_format_report()`.
- **Links** — `docs/evaluation_guide.md` §6.

**(e) Hierarchical Bayesian consistency — SKIP**

- **What** — Model FY-level Sharpe with shrinkage (PyMC/Stan).
- **Why we WON'T do this** — At n=25-40 trades/FY × 7 FYs, posteriors
  would be dominated by the prior, not the data.  Bootstrap CI gives
  most of the same insight with no model dependency.  Genuine
  over-engineering at our scale.
- **Trigger to revisit** — If/when we have ≥10 FYs of data AND ≥100
  trades/FY (would need universe expansion).

### 5. brk_wall as drawdown-conditional confluence hedge

**Origin** — 2026-05-19 brk_wall re-evaluation under the new marginal
contribution helper.  Unconditional inclusion stays REJECT (Sharpe
−1.40, Sortino −4.26, max-drawdown +17.9pp at N≥3) — see
[[project-brk-wall-k-sweep-reject]].

**The new finding** — Marginal helper revealed a tail-hedge property
that aggregate Sharpe hid:
- Tail-hedge lift = **+4.93%** (on A baseline's worst-quintile days,
  B +brk_wall improves the day mean from −12.12% to −7.18%)
- Daily-return correlation = +0.491 (not pure duplication)
- 135 B-only new trades, win-rate 57.8% (decent quality)

**The gate idea** — Don't include brk_wall always.  Include only when
the confluence baseline is bleeding:
- Gate by recent baseline cumulative drawdown crossing a threshold, OR
- Gate by N225 regime (bear/Kumo-below) where brk_wall's per-fire
  benchmark already shows bear DR 65.7%
- The hope: capture the +4.93pp tail-lift on the small subset of bad
  days WITHOUT paying the +17.9pp drawdown expansion across all days.

**Why deferred (not done now)** —
- Operator chose "log salvage hook only" on 2026-05-19.
- 135 new trades / 7 FYs ≈ 19 trades/FY split across days — gating
  further may shrink to single-digit per-FY n.
- Needs design choice: gate signal (rolling DD vs regime tag),
  threshold, A/B template that handles conditional inclusion.

**Trigger to revisit** —
- If confluence baseline has a fresh-data drawdown episode where we'd
  want a hedge, OR
- When we add new regime axes (followup §4c) — the cross-sectional
  dispersion axis might be the natural gate signal.

**Implementation sketch** —
1. Compute rolling-30-day cumulative return of confluence baseline at
   each trading date.
2. Define `is_bleeding[d] = cum_return_30d[d] < threshold` (start
   with 0.0).
3. In `_run_arm` for arm B, only emit brk_wall candidates whose
   `entry_date` falls on `is_bleeding` days.
4. Compare Sharpe + Sortino + tail-hedge lift A vs B-conditional.

**Links** —
- Reject memory: [[project-brk-wall-k-sweep-reject]]
- Re-eval log: this session, tail of `/tmp/confluence_brk_wall_inclusion_ab.log`
- A/B script (binary): `src/analysis/confluence_brk_wall_inclusion_ab.py`

---

## Done

(none yet)

---

## Dropped

(none yet)
