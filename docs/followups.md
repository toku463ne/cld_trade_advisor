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

---

## Done

(none yet)

---

## Dropped

(none yet)
