# Sign / Strategy Evaluation Criteria

This document defines how empirical evidence is judged when reviewing a sign or
strategy change. **All agents in `.claude/agents/` reference this file** so
their reasoning shares one rubric.

When in doubt, the question is always: **does the evidence justify the change,
on the dimensions that matter for live trading?**

---

## 1. Evidence Sources (in order of weight)

| # | Source | What it measures | Where it lives |
|---|--------|------------------|----------------|
| 1 | Multi-year benchmark FY2018–FY2024 | Aggregate DR and event volume across 7 fiscal years | `src/analysis/benchmark.md` § Multi-Year Benchmark |
| 2 | OOS FY2025 backtest | Out-of-sample directional rate, regime-gated | `src/analysis/benchmark.md` § FY2025 OOS |
| 3 | Regime analysis (ADX × Kumo) | Cell-level DR / mag_flw / mag_rev / EV with p-value gate | `src/analysis/benchmark.md` § Regime Analysis |
| 4 | Score calibration | Spearman ρ between `sign_score` and signed return + Q1–Q4 EV | `src/analysis/benchmark.md` § Score Calibration |
| 5 | Theoretical/visual rationale | The reason we expected the change to help | code comments, session notes |

Empirical evidence (rows 1–4) **outweighs** theoretical rationale (row 5). A
visually compelling pattern that does not show up in multi-year DR is not
sufficient to keep a change.

---

## 2. Core Metrics

- **DR** — directional rate. Fraction of events that move in the expected
  direction over the evaluation horizon.
- **n_events** — sample size of fired events.
- **mag_flw / mag_rev** — average magnitude of the move when the event went
  forward vs reversed.
- **EV** — expected value: `dr × mag_flw − (1 − dr) × mag_rev`. This is the
  primary ranking metric (replaced raw DR / `bench_flw` in 2026-05).
- **perm_pass** — permutation-test pass rate (DR vs shuffled baseline).
- **Spearman ρ** — score-to-return rank correlation.

---

## 3. Materiality Thresholds

A change is **material** only if at least one of these holds:

| Dimension | Material if … |
|-----------|---------------|
| DR change | abs(ΔDR) ≥ **1.0 pp** at n ≥ 1 000 events, or ≥ **2.0 pp** at smaller n |
| n_events  | Drop of ≥ 50% — must be justified by a DR or EV lift, else the filter is cutting signal not noise |
| EV        | Sign change (positive ↔ negative) or magnitude change ≥ 0.3 pp |
| OOS FY2025 | Regime-gated Δ DR ≥ 3 pp vs all-events DR |
| Calibration | Spearman ρ flips sign, or moves between [-0.05, +0.05] and outside |

Changes below these thresholds are noise; do not act on them in isolation.

---

## 4. Decision Matrix

For a sign change being evaluated against rebench results:

| DR change | n change | Verdict |
|-----------|----------|---------|
| ↑ ≥ 1 pp | stable / mild drop | **Keep** |
| ↑ ≥ 1 pp | drop ≥ 50% | **Keep** if strategy can tolerate the lower fire rate; check OOS FY2025 |
| flat | drop ≥ 50% | **Revert** — filter cut volume without quality lift |
| ↓ ≥ 1 pp | any | **Revert or isolate** — find which gate within the change caused the regression |
| ↓ < 1 pp | stable | **Watch** — re-evaluate after one more FY of data |

---

## 5. Common Failure Modes (the Critic's checklist)

1. **Sample-size illusion**: n < 100 events in a cell is too noisy to read DR
   from. Aggregate before drawing a conclusion.
2. **Regime overfit**: a gate trained on a bull-heavy window will look great
   in-sample and fail in bear regimes (or vice versa).
3. **Definition drift**: the analysis used to motivate a gate often uses a
   slightly different definition than the gate as implemented. Re-check.
4. **Filter masquerading as signal**: cutting events 65% and keeping DR flat
   means the filter caught nothing useful — and may have removed real signal
   by chance.
5. **Score worship**: if Spearman ρ ≈ 0, `sign_score` is noise and should not
   be in the ranking key — ordering by `EV` alone is more honest.
6. **Compounded gates**: stacking state-machine + persistence + hysteresis +
   crossover gates makes any single bad gate hard to detect. Add gates one
   at a time and rebench after each.
7. **Forward-looking leakage**: a regime label that uses any data from after
   `fire_date` will inflate DR. Confirm regime snapshot is built strictly
   from history.

---

## 6. When to Revert vs Investigate

- **Revert wholesale** when:
  - The change combined multiple gates and DR fell ≥ 3 pp.
  - There is no clear path to isolating which gate is responsible without
    several rebench cycles.
  - The change's theoretical motivation was already weak.

- **Investigate first** when:
  - One known gate looks suspicious (e.g. persistence threshold too strict).
  - The new event volume is healthy and only DR is off.
  - Sub-population behavior may explain the aggregate (a single regime cell
    is dragging things down).

---

## 7. What the Judge Weighs

Final verdicts are one of:

- **Accept** — empirical evidence clears § 3 thresholds and decision matrix
  in § 4 favors the change. Confidence: H / M / L.
- **Reject** — change regresses on the primary dimension (DR or EV) and § 4
  recommends revert.
- **Insufficient evidence** — sample too small, evidence conflicts across
  sources, or a known gap (calibration not run, OOS FY incomplete). Specify
  what would resolve it.

The judge MUST state confidence (H / M / L) and the single piece of
evidence that would flip the verdict — this keeps the rubric falsifiable.

---

## 8. Iterated Debate Protocol

When a sign/strategy decision is non-trivial, the agent cycle can run
multiple times via the `/sign-debate <topic>` slash command (see
`.claude/commands/sign-debate.md` for the full spec).

Per iteration: `analyst → proposer → critic → judge`. If the judge
returns "Insufficient evidence," the **Next action** field is executed
autonomously — running an existing analysis script, reading the relevant
benchmark section, or writing a small new one-off script — and the
resulting evidence is fed into the next iteration. The cycle stops on
Accept, Reject, max iterations (default 3), or when the next action
falls outside the autonomous scope (e.g. modifying detector code or
running a full rebench).

This protocol exists so single-round "we need decomposition data first"
verdicts do not stall the workflow on the user's manual intervention.
The judge still produces falsifiers; the harness just resolves the
falsifier autonomously when it can.
