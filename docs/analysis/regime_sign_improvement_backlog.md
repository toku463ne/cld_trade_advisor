# RegimeSign Strategy — Improvement Backlog

**Created:** 2026-05-29 · **Status:** research backlog, nothing committed · **Owner decision required per item**

A prioritized list of the **untested** improvement levers for `RegimeSignStrategy` itself. Companion to
`docs/analysis/confluence_improvement_backlog.md` (the sister strategy) and the benchmark write-up
`docs/analysis/regime_sign_strategy.md`. Each item names its mechanism, the prior evidence, and the
**binding test** it must clear. Do **one at a time**, each with a frozen pre-registration (no batch-running
= multiple-comparisons p-hacking).

> **Read this first.** Unlike the confluence backlog (where most items have been *run*), this backlog is
> mostly **untested priors** — but RegimeSign already has a *long* graveyard of entry/sign/selection A/Bs
> (see "Exhausted" below), almost all killed by the **n-thin trap** (~50 trades/FY can't separate arms on
> per-trade bootstrap CI). Those rejects used the **wrong metric** (per-trade / per-FY bootstrap), but the
> confluence work proves that re-testing such rules on the *correct* metric (the paired fill-order null)
> does **not** revive them — fill-order luck dominates at this trade count. So the high-prior levers here
> are the **non-selection** ones: sizing (weights), diversification (a genuinely uncorrelated stream),
> and exit — the same axes that survived longest on confluence.

## Progress (as of 2026-05-29)

| # | Lever | Axis | Status |
|---|---|---|---|
| 1 | Oracle-ceiling probe (per-axis headroom) | diagnostic | ⬜ **untested** — run first to prioritize selection vs exit vs sizing |
| 2 | Regime-conditional **EV sizing tilt** (neutral-momentum trim) | weights | ⬜ **untested — HIGHEST PRIOR** (the sole confluence survivor; does it transfer?) |
| 3 | **Blend RegimeSign + Confluence** into one book | portfolio | ⬜ **untested — strongest NEW idea** (two ~+1.0 books; if daily-return ρ is low this is *real* diversification, unlike the TSMOM overlay) |
| 4 | `min_dr` cutoff sweep | selection/ranking | ⬜ **untested** but low prior (selection axis → fill-order null) |
| 5 | Regime-conditional / β-stripped **exit** | exit | ⬜ **untested** but low prior (confluence items 3+7 both REJECT) |
| 6 | 6→8 slot sweep | capacity | ⬜ **untested** but low prior (confluence item 5 REJECT, manual-burden) |
| 7 | Vol-target / risk-parity slot sizing | weights | ⬜ **untested** but low prior (confluence item 4 REJECT) |

**META-PATTERN inherited from the confluence backlog (durable, do not re-litigate):** (a) **selection/
ordering rules die on the fill-order null** at this trade count — the exogeneity of the key does not lower
the bar, and more contention (universe expansion) does not either. (b) **Market-REGIME risk-shaping
(entry or exit gates) fights the regime-inverse alpha** — both strategies earn their alpha in bear-regime
recoveries (FY2024 is the canary), so anything that de-risks off a bearish market signal cuts the very
returns it's trying to protect. (c) The **one surviving lever** across the entire confluence backlog was
the **EV-conditional sizing tilt** (item 2) — tilt by *where forward EV differs* (the NEUTRAL-momentum
weak spot), not by vol and not by market regime. This backlog's job is to find out which of those
durable lessons transfer to RegimeSign and which RegimeSign-specific levers (item 3) are new.

## Baseline (the thing we're trying to improve)

Capital-aware 6-slot book (≤1 high-corr + ≤5 low/mid), FY2019–FY2025 — see
`docs/analysis/regime_sign_strategy.md`:
- **Fill-order-null portfolio Sharpe +1.03** (sd 0.09, p5 +0.90, p95 +1.19); shipped order at p40, +210.9%
  total return, maxDD −28.4%. **This is the binding number** — on par with Confluence's ~+1.02.
- Per-trade headline (Sharpe 3.10, +2.05% mean_r, 357 trades, 6/7 FYs positive) **overstates** — per-trade
  Sharpe ignores slot contention and capital weighting (CLAUDE.md).
- Like Confluence, RegimeSign is a **high-β long book** (low/mid-corr sleeve is the genuine-diversification
  core, positive every FY except FY2019; high-corr sleeve weak in down years, capped at ≤1 slot). Assume the
  same ~60% beta / thin regime-inverse alpha until measured otherwise.

**Realizable ceiling:** the shipped order sits at the **p40** of the fill-order null (band p5 +0.90 →
p95 +1.19, sd 0.09). The band is *tight* (much tighter than confluence's sd 0.19), so "pick better entries
from this pool" has very little headroom — p95 is +1.19, only +0.16 over the shipped +1.03, and it is luck
not edge. This is a *strong* prior that **selection/ranking tweaks (item 4) are near-dead on arrival.**

---

## Backlog (priority order)

### 1. Oracle-ceiling probe — quantify per-axis headroom  *(⬜ untested — adapt `confluence_oracle_ceiling_probe.py`)*
Run the same perfect-foresight upper-bound probe (oracle SELECTION / oracle EXIT-within-hold / oracle
EXIT+60bar / oracle BOTH) on the RegimeSign 6-slot book to learn **which axis has headroom** before
spending pre-registrations. On confluence this showed EXIT timing is the largest-headroom axis (+3.46
Sharpe) and that the drawdown is almost entirely exit-driven (maxDD −22%→−6% under perfect exit), while
SELECTION headroom (+1.97) is real but un-capturable (fill-order null). **Expected RegimeSign result:**
similar shape, but with a *tighter* selection null (sd 0.09 vs 0.18) the selection headroom is likely even
less realizable. **Value:** pure diagnostic — reprioritizes items 2/5/7. **No binding gate** (it measures
ceilings, doesn't propose a rule). Cheap; do it first.

### 2. Regime-conditional EV sizing tilt — trim neutral-momentum entries  *(⬜ untested — HIGHEST PRIOR; adapt `confluence_evtilt_null.py` + `confluence_evtilt_phase_null.py`)*
**The sole confluence backlog survivor — test whether it transfers.** On confluence, EV is non-monotone
in N225 60-bar momentum (NEUTRAL is the β-stripped EV weak spot: raw +0.52% / α +0.33% vs bullish
+3.31%/+1.20%, bearish +1.31%/+0.57%); trimming NEUTRAL-momentum entries to τ=0.5 (keep slot filled, bull/
bear full) cleared **both** binding nulls (fill-order Δ +0.120 P=1.00; phase+order Δ +0.123 P=0.99) and cut
maxDD ~4pp — a drawdown lever with a thin Sharpe tailwind, ACCEPTED as a manual sizing guideline.
**Question for RegimeSign:** does the same NEUTRAL-momentum EV trough exist for RegimeSign entries? (It is a
*different entry cohort* — sign-ranked Kumo/ADX-gated, not ≥3-confluence — so the EV-by-momentum profile
must be re-measured, not assumed.) **Why it escapes the regime-inverse trap:** it trims NEUTRAL, keeping
bull AND bear full → does not cut the bear-recovery alpha. **Stage 0:** pool RegimeSign trades by N225
60-bar momentum tercile, β-strip, check for a NEUTRAL trough (regime-pooling, as in
`project_confluence_phase_regime`). **Stage 1 (only if Stage 0 shows the trough):** paired fill-order null
on the 6-slot book — **P(ΔSharpe>0)≥0.95 AND CI-lo>0** — PLUS the combined phase+order null (the binding
test for any regime-keyed rule), held-out-FY-stable, **+ integer-lot realizability** at ¥2M/6 slots (the
confluence tilt nearly died on lot granularity; on RegimeSign's typically-cheaper low-corr names it may bite
harder — half a 1-lot name = 0 lots = a SKIP, the bimodal "skip cheap / half-size expensive" rule). If both
sister strategies share the same NEUTRAL-trim rule, the Daily-tab banner already built for confluence
(`_sizing_regime_banner`, `src/portfolio/sizing.py`) covers RegimeSign rows too.

### 3. Blend RegimeSign + Confluence into one capital-aware book  *(⬜ untested — strongest NEW, RegimeSign-specific idea)*
**The most interesting lever, and one confluence's backlog could not have:** we now have **two strategies
that each score ~+1.0 fill-order-null portfolio Sharpe** and surface *different cohorts* (RegimeSign =
sign-ranked Kumo/ADX entries; Confluence = ≥3-bullish-sign agreement). Confluence's item 6 (uncorrelated
overlay) REJECTED because the only breadth-immune candidate (TSMOM) turned out to be long-equity beta
(ρ +0.61) → it diluted rather than diversified. **A second equity strategy is also long-beta, so the same
risk applies** — BUT if the two books' *daily-return* correlation is meaningfully below 1 (different entries,
different exits firing on different days), a blended book gets a real diversification lift (Sharpe rises
even between two equal-Sharpe correlated assets as long as ρ<1). **Stage 0 (cheap, decisive):** compute
pooled ρ(regime_daily, confluence_daily) on the stitched 6-slot equity curves, FY2019–2025, and the
overlap fraction (how often both fire on the same name/day). If ρ ≳ 0.85 the books are near-redundant →
blending adds nothing (and the honest conclusion may be "run only the stronger one" — informs the
keep/remove question). If ρ ≲ 0.7 there is diversification to harvest. **Stage 1 (if ρ low):** paired
fill-order null on a *merged* candidate pool feeding ONE 6-slot book (both strategies' candidates compete
for the same slots, deduped, corr-cap enforced) vs each strategy alone — binding gate P(ΔSharpe>0)≥0.95 AND
CI-lo>0 vs the **better** single strategy, held-out-FY-stable. **Caveat:** a merged book changes the live
workflow (one book, two candidate sources) and may concentrate if both fire on the same name — the corr-cap
must treat shared names as one logical bet (CLAUDE.md). **This also directly answers "should both stay in
the UI"**: if ρ is high they're redundant (drop one); if low they're complementary (keep both, consider
blending).

### 4. `min_dr` cutoff sweep  *(⬜ untested — low prior, selection axis)*
RegimeSign excludes (sign, kumo) cells with historical DR ≤ `MIN_DR=0.52` from the ranking
(`regime_sign_backtest.py`). This is a **selection knob** — it changes which candidates qualify for slots.
Sweep `min_dr ∈ {0.50, 0.52, 0.55, 0.58, 0.60}`. **Low prior:** (a) it is a selection/ranking rule, and
selection rules die on the fill-order null at this trade count; (b) the RegimeSign null band is *tight*
(sd 0.09), so there is almost no realizable selection headroom (see "Realizable ceiling"). **Binding:**
paired fill-order null on the 6-slot book (P≥0.95, CI-lo>0), held-out FY. **Most likely outcome:** every
cutoff lands inside the null band = no separation. Run only if item 1's oracle SELECTION headroom comes
back surprisingly large.

### 5. Regime-conditional / β-stripped exit  *(⬜ untested — low prior)*
RegimeSign uses the same `ZsTpSl(2.0/2.0/0.3)` exit as confluence. On confluence both exit-side risk-shaping
levers REJECTED: regime-conditional exit (item 3, regime-inverse trap — exiting on bear cuts the bear
recovery) and per-stock β-stripped alpha stop (item 7, whipsaw ~50%, the −22% DD is beta-driven so an alpha
stop can't touch it). **Also already rejected ON RegimeSign specifically:** the asymmetric long/short
peak-anchored exit (`project_asym_exit` — OOS −2.886, n=30–42/FY too small) and the TimeStop(40) swap
(`project_timestop40_bootstrap_reject`). **Prior is strongly negative.** Only re-open via a *causal*
exit with a held-out bull FY and a drawdown-weighted (not Sharpe-only) gate, and only if item 1 shows
RegimeSign's drawdown is exit-driven AND larger headroom than confluence's. *Build note: AdxTrail needs
`_add_adx()` or it degenerates to TimeStop(40).*

### 6. 6→8 slot sweep  *(⬜ untested — low prior)*
Confluence's item 5 (6→8) REJECTED: Δ +0.035 not separated (P=0.64), breadth NOT starved (filled 7.92/
day) but diminishing diversification + return drag, and it raises the manual-execution burden (live plan =
6 slots). **Same low prior for RegimeSign**, with an extra concern: RegimeSign's low-corr universe per day
is thin (its diversification core depends on genuinely-uncorrelated names; past ~6 slots it would be forced
to add correlated names = false diversification, CLAUDE.md). **Binding:** paired capacity null
(`_MAX_LOW_CORR` 5→7) on the RegimeSign pool. Quick to run (no rebuild) but do not adopt a near-miss given
the manual burden.

### 7. Volatility-target / risk-parity slot sizing  *(⬜ untested — low prior)*
Confluence's item 4 REJECTED: on a high-β positively-correlated long book, equal-weight is **already ≈
risk-parity** (similar risk contribution per correlated name), and down-weighting higher-vol names by
single-name vol sacrifices return without cutting portfolio variance (single-name vol dispersion ≠
portfolio vol when ρ is high). **Same structural prior for RegimeSign** — but RegimeSign's low-corr sleeve
is *less* internally correlated than confluence's breakout names, so the "EW already ≈ risk-parity"
argument is weaker and inverse-vol *might* help more here. Still low prior; rank below item 2 (EV-conditional
sizing beat pure-risk sizing on confluence). **Binding:** paired fill-order null on Sharpe + maxDD.

---

## Exhausted — do NOT re-run (RegimeSign-specific graveyard)

Recorded so they aren't re-proposed. Almost all died on the **n-thin trap** (per-trade / per-FY bootstrap
CI too wide at ~50 trades/FY) — and the confluence work shows re-testing them on the fill-order null does
**not** revive selection-type rules.

- **Sign-set pruning (leave-one-out):** all 4 negative-Sharpe candidates (rev_nhi, corr_shift, div_peer,
  str_lag) REJECT individually (`regime_sign_leaveoneout_sweep`, commit 4cbba5c). The **combined drop** of
  corr_shift+div_peer+str_lag PASSED the FY-level CI [+0.47,+2.89] / 5-of-5 FYs but FAILED the trade-level
  bootstrap AND-gate at n=170 (`regime_sign_combined_drop_bootstrap`, 66c0214) → **UI-hidden only**
  (production ranking unchanged). rev_nhi/rev_hi/brk_floor also UI-hidden (2026-05-16). Revisit only if
  universe expansion lifts effective n — but expansion is REFUTED for selection (CLAUDE.md), so effectively
  closed.
- **Bearish-count veto** ("skip when bearish ≥ 2"): REJECT on RegimeSign (`regime_sign_bearish_veto_ab`,
  bf2869e) — the confluence bowl-shape did not transfer; cross-strategy shape INVERTED.
- **Breadth gate** (skip/half-size on AND-HIGH reversal-risk days): REJECT (`breadth_gate_probe`, 6c3e151)
  — n=12 AND-gate trades; universe-level breadth signal (n=750 days) does not transfer to ~12 regime_sign
  entries (`project_breadth_gate_probe_reject`).
- **rev_lo bearish-body filter** stratified by breadth regime: ambiguous → REJECT (`rev_lo_filter_regime_ab`,
  `project_rev_lo_and_high_per_cohort_reject`).
- **N225 trend_score ceiling/floor gate** (skip high-trend-score fires for anti-trend signs): both REJECT
  (`regime_sign_trend_score_ceiling_ab`, `trend_score Stage 1 path A`, 14bdb13). trend_score is a no-op
  filter on this cohort.
- **brk_wall inclusion:** literally zero strategy-level impact (78f4344) / REJECT confirmed (03ea6c3) — the
  added (sign, kumo) cell just displaces better-EV cells in the ranking. **brk_wall × confluence_count**
  Stage 0 also REJECT (39a3913).
- **Asymmetric long/short (peak-anchored) exit & TimeStop(40):** REJECT on regime_sign
  (`project_asym_exit`, `project_timestop40_bootstrap_reject`) — n=30–42/FY too small, OOS sign-flips.
- **Sector confidence bonus:** the 3 certified cells (rev_nhi×銀行, str_hold×不動産, rev_nlo×電機・精密) are
  **shipped, env-gated** (`regime_sign._CERTIFIED_SECTOR_BONUS`, `RS_SECTOR_FACTOR`, probe dee9197). Not a
  backlog item — already adopted as an optional factor.
- **Selection/ordering rules generally** (RS-rank, corr-greedy, ADX-priority, prefer_b0, PEAD vote/boost):
  killed on confluence's fill-order null; the RegimeSign null band is even tighter → same verdict applies.
- **Universe expansion:** REFUTED as a selection unblocker (CLAUDE.md / `project_jquants_pead_universe`).
- **2-bar fill:** a manual-execution realism constraint (opening auction), not an optimization knob.

## Discipline

One pre-reg at a time, frozen gate, binding = the **paired fill-order null on the capital-aware 6-slot
book** (per-trade / single-order / per-FY-bootstrap point estimates do NOT decide — they are what produced
the entire graveyard above). Held-out FY(s); no iteration. **Run order:** item 1 (diagnostic) → item 3
Stage 0 (ρ check, decisive and cheap, also answers the keep-both-in-UI question) → item 2 (the highest-prior
*improvement*). Items 4–7 only if item 1 surprises. Program map: `docs/analysis/20260528_new_directions.md`;
companion backlog: `docs/analysis/confluence_improvement_backlog.md`.
