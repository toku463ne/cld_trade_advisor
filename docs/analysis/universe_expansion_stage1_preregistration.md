# Pre-Registration — Universe Expansion, Stage 1 (break the equal-weight tie)

**Status:** PRE-REGISTERED — frozen before the pipeline rebuild · **Date:** 2026-05-27
**Builds on:** Stage-0 menu-width probe (`src/analysis/universe_menu_width_probe.py`, 2026-05-27,
MENU WIDENS 3/3) · the equal-weight-universe Sharpe **tie** (`project_confluence_buyhold_win`) ·
the fill-order null methodology (`project_confluence_fill_order_null`).
**No-iteration clause:** the tier thresholds, the gates, the selection key, and the OOS split are
frozen below. A failed gate is final for this expansion design; a different tier or key is a NEW
pre-registration, not a re-run. Researcher degrees of freedom are locked in §2 *before* any
rebuilt-data number exists.

---

## 1. The claim under test

Stage 0 showed the 225-name book is **diversification-starved** (~8 genuinely low-corr-to-TOPIX
names/day) and a liquid∩affordable tier offers ~23× more (184/day) with a far better achievable
6-basket correlation (max |ρ| 0.23 → 0.08). The unresolved question is whether that wider *menu*
converts into a real *edge* — because the 225 confluence book only **tied** the equal-weight
universe on Sharpe (selection added nothing beyond passive breadth), and every selection rule died
to the fill-order null for lack of slot contention.

**Stage 1 claim:** on the expanded universe, (A) selective confluence **beats passive equal-weight
of the same universe** (the tie breaks), and (B) a diversification-priority fill **beats the
fill-order null** (real contention finally lets selection bite). Both, out of sample, β-stripped,
net of mid-cap costs — or expansion is not worth the rebuild + live complexity.

---

## 2. Frozen inputs (researcher DoF — locked before rebuilt data exists)

| Input | Frozen value | Why locked here |
|---|---|---|
| **Tier membership** | `raw close ≤ ¥3,333` (¥2M/6/100) **AND** `median trailing-60-bar turnover_value ≥ ¥100M/day`, evaluated as-of each entry day | exactly the Stage-0 thresholds — not re-tuned on rebuilt results |
| **Universe size** | the **full** liquid∩affordable tier (no arbitrary cap; the liquidity floor bounds tradability). Report the realized count. | a cap would be a free parameter |
| **Equity filter** | common stock on Prime/Standard/Growth with a `sector33` code (exclude ETF/REIT/non-equity) | avoid non-stock contaminants |
| **Strategy** | the **shipped** `ConfluenceSignStrategy` (N≥3 bullish set, 6-slot capital-aware book, ZsTpSl exit), **unchanged** | no strategy re-fit; only the universe widens |
| **Selection key (gate B)** | **diversification-priority** = greedy least-correlated to current holdings (trailing-60 ρ), skip-not-queue, the ≤1-high/≤5-low caps. **Pick by correlation, NOT predicted return.** | the project doctrine; a return key is forbidden (root lesson) |
| **OOS split** | discovery = FY2018–FY2024; **blind OOS = FY2025 + FY2026-partial** (never inspected while choosing anything) | the expansion decision must survive a held-out regime |
| **Capital / costs** | ¥2,000,000, 6 slots, integer 100-share lots (sizing.py); cost sweep **0 / 30 / 60 bps** round-trip | mid/small-caps carry real spread — the gate must hold at a realistic floor |

---

## 3. Benchmarks & the β-strip (the honest lens for a 62%-beta strategy)

All curves are the capital-aware ¥2M / 6-slot stitched daily equity curve, then **β-stripped vs
TOPIX** (β = trailing-60-bar). Because the strategy is ~62% beta and the expanded tier tilts
smaller-cap, raw Sharpe gains could be pure beta/size premium — so every gate is on β-stripped
(alpha) terms or on active-minus-passive *within the same universe* (which cancels the universe's
own size tilt).

| Book | Definition |
|---|---|
| `cur_active` | confluence on the **225** universe |
| `cur_passive` | buy-hold **equal-weight 225** (periodic rebalance) |
| `exp_active` | confluence on the **expanded** universe |
| `exp_passive` | buy-hold **equal-weight expanded** universe |

`active − passive` (both β-stripped vs TOPIX) within a universe = the **selection premium net of
that universe's size/beta tilt**. The documented baseline: `cur_active − cur_passive ≈ 0` (the tie).

---

## 4. Binding gates (BOTH must hold to justify expansion)

### Gate A — value: the equal-weight tie breaks
Statistic: `S_A = Sharpe(exp_active β-stripped) − Sharpe(exp_passive β-stripped)` on the stitched
curve. **Paired fill-order null** (K≥1000 shuffles, same order to active & passive per seed; per-FY
blocks preserved). **Gate: `P(S_A > 0) ≥ 0.95` AND 95% CI lower > 0**, holding **OOS** (FY2025+FY2026
share the sign), at the **30 bps** cost level. Context (not a gate): report `cur_active − cur_passive`
(expected ≈ 0) so the diff-in-diff "the wide universe breaks a tie the narrow one couldn't" is visible.

### Gate B — mechanism: selection beats the fill-order null
On the expanded book, statistic: `S_B = Sharpe(diversification-priority fill) − Sharpe(random
fill-order)`, **paired** K≥1000 shuffles (same candidate pool, diversification pick vs random pick of
the 6 slots per seed), β-stripped. **Gate: `P(S_B > 0) ≥ 0.95` AND 95% CI lower > 0**, OOS sign-stable.
This is the test that every prior selection rule failed at 225-name contention; it should now have
room to pass (~1,008 candidates/day vs 6 slots).

**No pre-committed effect size** on either gate — the machinery decides detectability, as in the
sleeve pre-reg.

---

## 5. Secondary diagnostics (reported; do NOT govern the decision)

- **Per-FY** `exp_active − exp_passive` (robustness; no single-FY veto).
- **Cost sensitivity:** gate A at 0 / 30 / 60 bps — flag if it survives only at 0 bps (mid-cap
  cost-fragile).
- **Beta/size decomposition:** raw vs β-stripped Sharpe for `exp_active`; how much of any raw gain is
  small-cap beta (must NOT be the whole story).
- **Drawdown & turnover:** maxDD and trades/yr (expansion should raise trade count materially — the
  contention that was missing).
- **Concurrency / high-corr:** confirm the ≤1-high/≤5-low caps + correlation-diversified fill behave
  on the wider menu (no hidden single-bet concentration).

---

## 6. Decision rule & falsifier

- **EXPAND (proceed to live + keep the rebuilt pipeline)** iff **Gate A AND Gate B** both pass with
  OOS sign-stability at 30 bps.
- **REJECT** otherwise — final for this design. A pass on B but not A = selection beats random but
  not passive breadth (not worth it). A pass on A but not B = breadth helps but not via active
  selection (re-examine as a passive-tilt question, separate pre-reg). Neither = the 225 tie stands
  and universe expansion closes as a harvest path at current sizing.

> **Falsifier:** if the expanded confluence book does not beat equal-weight of the same universe
> (Gate A) with a diversification fill that beats the fill-order null (Gate B), both OOS at 30 bps,
> then a wider menu does not convert to edge — the equal-weight-universe tie is structural, not a
> sample-size artifact, and selection remains unsupported at ~36-trades/yr economics.

---

## 7. Anti-mining / discipline

- **Two gates, fixed up front** (§4). No metric shopping; per-trade EV is not a criterion.
- **Thresholds frozen from Stage 0** (§2) — not re-tuned on rebuilt data.
- **No second tier / key after results** (no-iteration clause). A failed gate ends this design.
- **Fixed K, seed protocol, cost levels, OOS split** stated before the rebuild.
- **Report all FYs and all three cost levels**, no cherry-picking.

---

## 8. Build plan + SAFETY (this phase MUTATES the dev DB)

**Order of operations (each step reversible / backed up):**

1. **BACK UP FIRST:** `scripts/market_data_dump.sh` → `~/db_backups/` (stocks/ohlcv_*/jq_*). The
   rebuild touches `ohlcv_*` + `sign_benchmark` — recoverable from this dump.
2. **Freeze the tier:** materialize the liquid∩affordable code list as-of the data end (read-only
   query mirroring `universe_menu_width_probe`), write it to a checked-in artifact
   (`docs/analysis/universe_expansion_tier.txt`) so membership is auditable and reproducible.
3. **Bridge prices:** load the frozen codes' adjusted OHLCV from `jq_daily_quotes` into the
   `ohlcv_1d_yXXXX` partitions (the strategy reads `ohlcv_1d` via `DataCache`; jq is full-universe).
   Reuse the existing collector/bridge path; do **not** autogenerate Alembic (partitioned-OHLCV
   drift — per CLAUDE.md).
4. **Rebuild signs/regime:** `cluster.py --fiscal-year … --run-corr` (NO `--collect`) + the
   `sign_benchmark` / regime-snapshot rebuild over the expanded `ohlcv_1d`, mirroring the
   confluence rebuild recipe in `project_db_wipe_rebuild`.
5. **Run the gates:** new `src/analysis/universe_expansion_null.py` (~250 lines, mirrors
   `confluence_capacity_null` + `confluence_buyhold`) — builds the four books (§3), β-strips, runs
   the paired nulls (§4), the cost sweep and diagnostics (§5). Read-only against the rebuilt DB.

**HARD SAFETY RULES (from `project_db_wipe_rebuild` — non-negotiable):**
- **NEVER run `pytest --env-file devenv`** — conftest does `Base.metadata.drop_all` on `DATABASE_URL`;
  it wiped the dev DB twice. Tests default to `stock_trader_test`.
- Run the rebuild on **dev** (`--env-file devenv`); btenv/prod untouched until a PASS.
- The rebuild is **additive** to `ohlcv_1d` (new codes) — it must not drop the 225 or `^N225`/`^GSPC`.
  Verify counts before/after.

---

## 9. What a verdict triggers

- **PASS (A∧B)** → universe expansion is real: keep the rebuilt pipeline, expand the live candidate
  menu, and **re-open the parked selection/sizing rules** (RS-rank, prefer_b0, PEAD sleeve, …) — each
  re-tested against the now-contended book under its own pre-reg. The PEAD sleeve specifically gets a
  second life (its reject was explicitly a capacity signpost).
- **FAIL** → restore the dump (§8.1) to return dev to the 225 book; record that the equal-weight tie
  is structural and selection is unsupported at current economics. Remaining lever = sizing, or accept
  the book as a beta-vehicle-with-discipline (the `project_live_trading_plan` framing).
