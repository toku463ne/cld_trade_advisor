# Confluence Strategy — multi-sign agreement beats single-sign ranking (2026-05-17, deep-dive 2026-05-21→23)

**Verdict: SHIP** — `ConfluenceSignStrategy` deployed in Daily-tab
shadow mode alongside `RegimeSignStrategy`. A six-day deep-dive
(2026-05-21→23) confirmed the net economics, ruled out every per-name
**selection** rule (fill-order luck dominates), and left **capacity**
(more slots) as the only un-ruled-out lever. See the
[2026-05-21 → 05-23 deep-dive](#2026-05-21--05-23-deep-dive-economics-selection-capacity-ui)
section below.

Operator hypothesis: "Individual signs decay quickly, but state-change
signs and multi-sign agreement could last longer.  N bullish signs
agreeing on direction should be a stronger bet than any single sign."

The data confirmed it — once measured properly.  Live ZsTpSl backtest
at N≥3 confluence: **Sharpe +3.80 vs `RegimeSign` baseline +1.33
(~3×), 6 of 7 FYs positive**, FY2025 OOS at +8.37 Sharpe.  First
positive backtest result in the May 2026 sign-review run.

## The framework

For each stock × trading day, count the number of bullish signs
currently "valid" using each sign's own `valid_bars` (matches
detector defaults):

| Sign | valid_bars |
|---|---:|
| str_hold | 3 |
| brk_bol | 3 |
| str_lead, str_lag, brk_sma, rev_lo, rev_nlo | 5 |

A sign that fired on Monday counts toward confluence on Mon–Wed
(str_hold/brk_bol) or Mon–Fri (others).  Fire if count ≥ N.

**brk_wall is EXCLUDED** from the bullish set — its inclusion diluted
the v2 probe uplift by −0.83pp (see [brk_wall / brk_floor](brk_wall_brk_floor.md)).

## Three iterations to the right framing

### v1 — same-day confluence (REJECT)

`src/analysis/bullish_confluence_probe.py` measured confluence on the
calendar day signs co-fire.  Only **13 stock-dates in 7 FYs had ≥3
bullish signs firing the same day**.  Signs detect at different
timescales — str_hold (5-day window), brk_sma (20-bar), rev_lo
(zigzag-confirmed) — and almost never trigger on the same calendar
day.  Confluence-as-defined was structurally rare.

### v2 — validity-windowed confluence (operator's key insight)

Operator: *"each sign has 'valid days' — why don't we use that to
widen the time?"*

`src/analysis/bullish_confluence_v2_probe.py` credits each fire for
its full validity window.  Result per-fire (using benchmark.md
`trend_direction` convention):

| Bucket | n_train | DR (train) | EV (train) | EV (FY2025) | mean signs/day |
|---|---:|---:|---:|---:|---:|
| 1 sign | 84,449 | 55.1% | +0.89% | +2.59% | 1.00 |
| 2 signs | 16,260 | 60.1% | +1.79% | +4.07% | 2.00 |
| **≥3 signs** | **1,746** | **64.3%** | **+3.20%** | **+5.02%** | 3.08 |

- All 7 FYs positive uplift at ≥3 confluence
- Pooled training EV[≥3] − EV[1] = **+2.31pp**
- FY2025 OOS uplift = **+2.44pp**

PASS at per-fire level.  But this uses the trend_direction proxy,
which the same session demonstrated can overstate live performance
(see [Probe vs Canonical](probe_vs_canonical_lesson.md)).  The real
test is live ZsTpSl backtest.

### v3 — live ZsTpSl strategy backtest (BIG WIN)

`src/analysis/confluence_strategy_backtest.py` runs the confluence
gate as an actual entry rule with all of: ZsTpSl(2.0, 2.0, 0.3) exit,
two-bar fill, portfolio cap (≤1 high-corr + ≤5 low/mid since 2026-05-23;
was ≤3 — see §Capacity), 10-bar
cooldown between re-entries on same stock.

| Strategy | trades | avg Sharpe | avg mean_r | win% |
|---|---:|---:|---:|---:|
| `regime_sign` baseline (currently shipped) | 171 | **+1.33** | +0.77% | varies |
| N ≥ 1 (any bullish sign valid) | 249 | +1.36 | +0.77% | 52% |
| N ≥ 2 (≥2 signs agree) | 222 | **+3.53** | +1.92% | 58% |
| **N ≥ 3 (≥3 signs agree)** | **165** | **+3.80** | **+1.97%** | **59%** |

### Per-FY at N ≥ 3 (the recommended gate)

**CANONICAL per-FY benchmark — regenerated 2026-05-23** on the rebuilt data (full
Nikkei universe, OHLCV 2017–2026, 8 FY clusters, fresh sign_benchmark) at the
shipped **6-slot** book. Reports BOTH the original per-trade Sharpe (large, NOT
annualized) and the real **capital-aware book** metrics. Source:
`src/analysis/confluence_benchmark.py`.

| FY | trades | mean_r | win% | per-trade Sh | hold | ‖ | **book Sharpe** | total | maxDD |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| FY2018 | 47 | +0.22% | 45% | +0.36 | 28 | ‖ | +0.15 | +1% | −23% |
| FY2019 | 57 | −0.42% | 47% | −0.67 | 23 | ‖ | −0.20 | −5% | −27% |
| FY2020 | 47 | +3.53% | 66% | +5.05 | 29 | ‖ | +1.49 | +29% | −10% |
| FY2021 | 52 | −1.29% | 42% | −2.07 | 27 | ‖ | −0.64 | −12% | −25% |
| FY2022 | 41 | +2.85% | 73% | +5.62 | 33 | ‖ | +1.21 | +21% | −8% |
| FY2023 | 59 | +3.06% | 71% | +5.63 | 23 | ‖ | +1.95 | +33% | −8% |
| FY2024 | 51 | +1.18% | 59% | +1.76 | 26 | ‖ | +0.50 | +8% | −21% |
| **FY2025 OOS** | 55 | +2.77% | 62% | +4.59 | 25 | ‖ | **+1.38** | +29% | −13% |

- **Stitched all-FY capital-aware book: Sharpe +0.72, total +149%, maxDD −27%,
  6/8 FYs book-positive.** This is the real number; +0.72 sits in the fill-order
  null band (~+0.6..+1.2, mean +0.89; see §Capacity). The per-trade Sharpe column
  is ~4× larger and is NOT an annualized book Sharpe — do not read +4.59 as annual.
- **FY2019 is no longer the −5.26 outlier** — that was an n=12 small-sample
  artifact of the old dev DB; on the full universe it's n=57, a mild −0.67
  per-trade / −0.20 book loss. Losses concentrate in **FY2019 + FY2021** (choppy /
  bearish years).
- The deterministic (sorted entry_date) book Sharpe is ONE fill-order draw;
  judge live results against the band, not a single FY (see §Start-phase).

<details><summary>Superseded 2026-05-17 table (per-trade Sharpe, 4-slot, partial DB — kept for history)</summary>

| FY | trades | Sharpe | mean_r | win% |
|---|---:|---:|---:|---:|
| FY2019 | 12 | −5.26 | −2.75% | 42% |
| FY2020 | 24 | +7.95 | +4.47% | 67% |
| FY2021 | 29 | +1.55 | +1.06% | 48% |
| FY2022 | 22 | +3.35 | +1.66% | 64% |
| FY2023 | 23 | +9.10 | +3.80% | 70% |
| FY2024 | 30 | +1.55 | +0.94% | 57% |
| FY2025 OOS | 25 | +8.37 | +4.61% | 68% |

</details>

### Why N=3 over N=2

| Gate | Pooled Sharpe | Per-FY consistency |
|---|---|---|
| N≥2 | +3.53 | 4 of 7 FYs positive |
| N≥3 | +3.80 | **6 of 7 FYs positive** |

N≥3 has fewer trades but materially better per-FY consistency.  The
extra robustness matters more than the trade-count gain.

## What shipped

### `src/strategy/confluence_sign.py`

`ConfluenceSignStrategy` class mirroring the `RegimeSignStrategy`
public interface (`propose`, `propose_range`, `from_config`).

```python
ConfluenceSignStrategy.from_config(
    stock_set = "classified2024",
    start     = ...,
    end       = ...,
    n_gate    = 3,             # default per backtest finding
)
```

Each proposal's `sign_type` is the label
`conf{N}:{sorted_constituent_signs}`, e.g.
`conf3:brk_sma,rev_lo,str_hold`.  This distinguishes confluence
proposals from regime proposals in the Daily table.

### `src/viz/daily.py` — shadow mode

Both strategies run on Refresh.  Proposals concatenated: regime first,
then confluence.  Existing UI renders both naturally; confluence rows
visible by their `conf{N}:...` label.

`_CONFLUENCE_N_GATE = 3` constant at the top of `daily.py` for easy
tuning.  Confluence cache invalidates alongside regime cache.

## Caveats and known unknowns

1. **FY2019 outlier** — Sharpe −5.26 on n=12.  Too small to dismiss,
   too small to weight heavily.  Worth investigating if the universe
   ever expands and n grows to 30+; until then, the 6/7-FY positive
   pattern stands.

2. **Confidence band now measured (2026-05-23).**  The +2.47 per-trade
   Sharpe gap was a per-fire pooled statistic; the capital-aware annualized
   Sharpe is **~0.6–1.2** (fill-order permutation null, mean +0.89). The
   headline +0.84 is a p44 draw of that null. No per-name selection rule
   clears it — see the deep-dive section.

3. **Hold time ~22 days** at N≥3.  Much longer than typical regime_sign
   holds.  Different capital efficiency profile; per-trade Sharpe
   may not translate to annualized Sharpe one-for-one.

4. **Sparse fire rate.**  N≥3 produces ~24 trades/year (1-2 per week
   typically; sometimes zero).  Does NOT replace regime_sign — they
   COMPLEMENT each other on the Daily tab.

5. **Shadow mode only.**  No live decision is forced; operator picks
   per-row.  Decision to flip confluence to primary (or drop regime)
   should wait until accumulated discretionary data + bootstrap CI.

## 2026-05-21 → 05-23 deep-dive (economics, selection, capacity, UI)

Six days after ship, the strategy was stress-tested along four axes:
**net economics**, **per-name selection**, **structural capacity**, and
**daily operability**. Net conclusion: the strategy is real and shippable,
its realized outcome is **dominated by slot-fill luck, not by any selection
rule**, and the only un-ruled-out lever is **capacity** (more slots) — pending
the operator's willingness to run a larger book.

### Bullish set expanded 7 → 10 (already in the catalogue)

The bullish set used by the gate now includes three ichimoku `_hi` signs added
on 2026-05-18 — `brk_kumo_hi`, `brk_tenkan_hi`, `chiko_hi` — plus the
`brk_sma` default change (close,K=5 → low,K=3). Current set:

```
str_hold, str_lead, str_lag, brk_sma, brk_bol,
rev_lo, rev_nlo, brk_kumo_hi, brk_tenkan_hi, chiko_hi
```

### Economics — net of costs (`confluence_buyhold_costs.py`)

Capital-aware 4-slot equity curve, FY2017–FY2025 stitched, vs a Nikkei ETF:

- **Beats the index** (+256.9% / Sharpe +0.84 / maxDD −29.9% vs N225
  +180.6% / +0.68 / −32.8%) but **ties the equal-weight universe**
  (+253.2% / +0.86 / −36.0%) — a Sharpe wash, only a drawdown edge.
- **Net-wins vs the ETF at all realistic costs**; break-even is **~34 bps**
  round-trip at ~38 trades/yr.
- **62% of the headline return is beta, 38% is alpha**
  (`confluence_market_neutral.py`, trailing-60-bar β ≈ 0.73). The alpha is
  **regime-inverse**: the biggest raw years evaporate market-neutral
  (FY2020 +4.33→−3.05) while bearish FY2024 *improves* (+6.49→+8.74). The
  strategy is a **beta vehicle with conditional alpha**, not market-neutral.

### Selection rules — all REJECT, and why (THE methodological capstone)

The decisive realization: `run_simulation` **already enforces the slot cap**
(≤1 high-corr + ≤3 low-corr) and **SKIPS — does not queue — when full**. A
prior round of "ranking" scripts fed that already-filtered output into a
*second* selector, double-counting the cap and fabricating an "n-thin" trap.
Corrected: ~20 valid stocks/day (~1200/FY) compete for 4 slots, 97% skipped,
~36 filled/yr.

Because the book skips, the realized trade set is **one fill-order path**, and
that order is an **unmeasured variance source**. The correct null is therefore
a **fill-order permutation null**, not a day-resampling bootstrap. Built a
`day_selector` hook in `run_simulation` (no-op default, 13/13 exit tests pass)
and a 200-shuffle null (`confluence_slot_order.py`):

| Arm | Sharpe | total | maxDD | pctile in null | perm p |
|---|---:|---:|---:|---:|---:|
| **shuffle null** | mean +0.89 (sd 0.19, p5 +0.60, p95 +1.20) | mean +335% | mean −27% | — | — |
| baseline (= shipped +0.84) | +0.84 | +257% | −29.9 | **p44** | — |
| RS-high (relative strength) | +0.93 | +366% | −24.4 | p59 | 0.41 |
| corr-greedy (least-corr-to-holdings) | +0.99 | +398% | −26.4 | p73 | 0.27 |
| prefer_b0 (fewest bearish, `confluence_bearish_select.py`) | +1.12 | — | −20.8 | ≈p89 | ≈0.11 |

- **The order-luck band is WIDER than every selection effect measured** (RS
  +0.09, corr-greedy +0.15, prefer_b0 +0.29 are all ≤1.5 sd of this null).
- The **shipped +0.84 is a slightly UNLUCKY p44 draw** — true central tendency
  is +0.88 / +335%.
- **RS = random (p59)**, confirming the RS reject for the right reason.
- **No selection rule clears the null** at ~36 trades/yr — not RS, not
  bearish-count, not corr-greedy. prefer_b0 is closest but still p≈0.11.
- Bearish "N bullish + 1 bearish" / "n_bearish < 2": the per-fire bowl shape
  did **not** replicate on the filled book; the named gate (`veto_b2`) is the
  weakest variant (p=0.29). **Operator decision: clear fail — not implemented,
  not surfaced in the UI.**

**Practical picking guidance:** diversification (least-correlated-to-holdings)
is the **best-supported heuristic** (mechanism + best draw) — surfaced in the
Daily slot-corr panel — but expect a wide band and no large edge. Do **not**
rank by relative strength (= random).

### Capacity — the best lead, near-miss (`confluence_capacity_null.py`)

Capacity is **structural, not selection**, so it can certify at current n if
the 6-slot fill-order distribution sits above the 4-slot one. 200 **paired**
shuffles (same within-day order fed to both books):

| config | Sharpe mean | sd | p5 | p50 | p95 | ret mean | DD mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4-slot (production) | +0.89 | 0.19 | +0.60 | +0.88 | +1.20 | +335% | −27% |
| **6-slot** | **+1.02** | **0.17** | +0.72 | +1.04 | +1.32 | +374% | **−24%** |

Paired Δ Sharpe mean **+0.137**, **P(Δ>0) = 0.865** (173/200), but 95% CI
**[−0.095, +0.370] grazes 0 → NOT separated at 95%**. Unlike the selection
rules, capacity **moves the whole band** with a real mechanism (more low-corr
names → lower variance) and **improves drawdown**. **Risk asymmetry favors
adoption**: one-line `_MAX_LOW_CORR=3→5`, reversible, better DD even if the
Sharpe gain is noise. **VERDICT: lean-yes / operator's call**, gated on
willingness to run a **6-concurrent-position book** (more capital + manual
tracking) — that operational cost, not the statistics, is the binding question.
Not auto-shipped (below 95%).

**Costs + per-FY check (`confluence_capacity_costs.py`, 2026-05-23).** Two
adoption objections resolved:

| cost (bps) | 4-slot Sharpe | 6-slot Sharpe | 4-slot ret | 6-slot ret | paired ΔSharpe | P(Δ>0) |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0.89 | 1.02 | 335% | 374% | +0.137 | 0.865 |
| 20 | 0.81 | 0.93 | 277% | 310% | +0.128 | 0.855 |
| 34 | 0.75 | 0.87 | 241% | 271% | +0.122 | 0.845 |

- **Costs = clean PASS — the edge is cost-invariant.** Turnover-per-capital is
  identical (4-slot 36 tr/yr ÷ 4 = 9; 6-slot 54 ÷ 6 = 9), so round-trip costs hit
  both arms equally and the Δ barely moves (+0.137 → +0.122 from 0→34 bps). The
  +48% more trades is exactly offset by +50% more slots (smaller positions). 6-slot
  net return beats 4-slot at every cost level. **Turnover is not a reason to hesitate.**
- **Per-FY = moderate but asymmetric.** 6-slot wins **5/9** FYs (below a 6/9 bar)
  but with big wins (+0.41 to +0.82) vs small losses (≤−0.48); **FY2025 OOS +0.81**;
  bull-mean Δ +0.78, **bear-mean Δ −0.00 (flat — no sign-flip**, unlike the exit
  arms). Gains are bull-loaded (more slots → more capital → more beta) plus the
  drawdown improvement (diversification).
- **Verdict unchanged: lean-yes / operator-call.** The costs objection is removed
  and the per-FY profile is favorable-asymmetric, but the paired CI still grazes 0
  at every cost (not 95%-separated). The binding question stays operational.

### What shipped from the deep-dive (decision-support only, no gating)

- **Daily strategy switch** — RadioItems toggle between regime and confluence
  proposals (`daily-strategy-switch`, `filter_proposals_by_strategy`).
- **Confluence-label wrapping** — `conf{N}:…` labels wrap to multiple lines in
  the chart title / fired annotation (`_fmt_sign_label`).
- **slot-corr diversification panel** — ρ(20) of each candidate vs every open
  position (`_slot_corr`); red ≥0.6, green ≤0.3. This is the picking rule.
- **Per-position exit advice** — ADX-trail HOLD/EXIT verdict + bars held +
  distance-to-TP/SL + ZigZag-exit flag in each position card (`_exit_advice`,
  mirrors `adx_trail_d8.0`).
- **`sign_type` widened varchar(30) → 255** (`Position`, `ReviewedCandidate`)
  + Alembic migration `f4b1d8c3a907` — confluence labels (~64 chars) no longer
  truncate on Register/Skip. **dev migrated; btenv/prodenv still need
  `alembic upgrade head`.**
- **Unit tests** — `tests/strategy/test_confluence_sign.py` (21 tests) + fixed
  pre-existing RankEntry/EV test drift; `stock_trader_test` DB documented in
  `README.md`. 165 tests pass.

### New evaluation pattern (now in the rubric)

For any **slot-constrained, skip-not-queue** backtest, the **fill-order
permutation null** is the correct benchmark for selection/ordering
interventions — wider and more honest than a day-resampling bootstrap because
it captures the path-dependence the bootstrap ignores. And: if a simulator's
trade output is sparse vs the raw signal, check for an **internal cap that
SKIPS rather than queues** — re-applying that cap externally fabricates a
"no-pressure" / "n-thin" artifact. See `evaluation_criteria.md` §5.12.

### Start-phase variance + regime-conditional pooling (operator follow-up)

Two operator questions extended the null: *"you don't trade every day — doesn't
reshuffling the days give more samples?"* and *"if each parallel world trades on
different days, aren't they under different regimes?"* Both produced findings.

**Start-phase null (`confluence_start_phase_null.py`).** The within-day shuffle
only permutes *same-day* slot competitors; it does not span the **start phase**.
Since slots are sticky, beginning the book on a different date cascades into a
different admit/skip trajectory. Sweeping the start offset 0→35 trading days:

| start offset (days) | trades | Sharpe | total return |
|---:|---:|---:|---:|
| 0 (= shipped) | 326 | +0.84 | +257% |
| 15 | 315 | **+1.01** | +359% |
| 25 | 290 | +0.67 | +152% |
| 35 | 271 | **+0.53** | +100% |

- **Start date alone swings Sharpe 0.48 / return 259pp** with trade count nearly
  flat (326→271) — *not* the "less time left" confound. The shipped offset-0 is
  mid-pack, not special. So the wide fill-order band is **mostly regime-timing
  variance** (sticky slots land over different regimes) — the timing-side view
  of the 62%-beta result. **Operational consequence:** you won't adopt on a FY
  boundary, so judge live results against the phase band, not a single path.
- Combined phase+order null is **wider** than order-only (sd 0.16→0.19); the
  selection arms reject more clearly (corr-greedy perm p 0.20→0.23).

**Regime-conditional pooling (`confluence_regime_pooling.py`).** The legitimate
"more samples" win: parallel worlds admit different *subsets* of the fixed
~1,200/FY pool, so the **union** binned by entry regime gives many more distinct
trades per bucket. Pooling 161 worlds/FY → **4,656 distinct trades vs 284 on the
single path (~16× per regime bucket)**. Per-trade EV by N225 trailing-60-bar
momentum is **non-monotonic — the weak spot is the middle, not the bear**:

| regime (N225 60-bar) | distinct n | DR | raw ret | mean α (β-stripped) | avg β |
|---|---:|---:|---:|---:|---:|
| bearish (≤ −0.1%) | 1,554 | 57.0% | +1.31% | +0.57% | 0.77 |
| neutral (−0.1%→+8.1%) | 1,554 | 53.8% | +0.52% | **+0.33%** | 0.82 |
| bullish (> +8.1%) | 1,548 | 63.7% | +3.31% | +1.20% | 0.72 |

- DR > 50% in every regime (positive edge everywhere). Raw favors up-trends =
  the **beta face** (β share 64% in bullish). **The neutral weakness survives
  the beta-strip** — worst on raw *and* alpha — so it is **genuine
  signal-quality regime dependence**, the session's first real sizing-tilt
  candidate.
- **Caveats that keep it parked:** (1) **trim, not skip** — neutral alpha is
  still *positive* (+0.33%, DR 52%); (2) a per-regime sizing tilt is still a
  **market-timing rule** and must clear the *same* fill-order/phase null at the
  **portfolio** level that every selection rule failed — per-trade alpha ≠
  portfolio sizing edge; (3) this conditions on **local entry momentum**, a
  *different* axis than the FY-level regime-inverse alpha in
  `confluence_market_neutral.py` — do not conflate.
- **Statistical honesty:** the 16× are distinct but *not independent* (≈43% of
  the candidate pool admitted in some world) — valid for regime-conditional EV
  point estimates, **not** for overall significance. Worlds replay the same
  fixed history; you cannot out-sample it.

**Stock-chop cut — "avoid sideways/choppy stocks?" → REFUTED.** Operator follow-up:
since ~20 candidates fire per day, screen out the choppy ones. Binning the same
4,656 pooled trades by the **stock's own ADX14 at entry** (not the N225 regime
above):

| stock state | n | avg ADX | DR | raw ret | β-stripped α |
|---|---:|---:|---:|---:|---:|
| choppy (low ADX) | 1,552 | 13.0 | 58.8% | +1.71% | **+0.67%** |
| mid | 1,552 | 18.4 | 56.3% | +1.42% | **+0.45%** |
| trending (high ADX) | 1,552 | 27.9 | 59.3% | +2.01% | **+0.98%** |

- **Choppy entries are the *second-best* cohort, not the worst** — filtering them
  out would delete a winning bucket. Same non-monotonic "extremes work, middle is
  mush" shape: the weak spot is **mid-ADX** (≈18, α +0.45%).
- **Mechanism:** the bullish set mixes *reversal* signs (`rev_lo`, `rev_nlo`,
  `str_hold` — shine in low-ADX) and *breakout* signs (`brk_*`, `str_lead/lag` —
  shine in high-ADX); the mid-ADX tape serves neither cleanly.
- **Verdict: do not add a chop/ADX filter.** The only hint is "trim mid-ADX" —
  the *opposite* of the proposal, modest (all buckets positive), and still a
  selection rule facing the portfolio null. Consistent with the 2026-05-19
  `trend_score` no-op: the ≥3-bullish gate is already trend-aware.

**ADX-priority picking (high>low>mid) → REJECT, and a methodology lesson.** The
EV gradient tempted using ADX rank as the slot-pick tiebreak. Two tests:

| test | result |
|---|---|
| single-arm vs fill-order null (`confluence_adx_priority.py`) | Sharpe **1.15** / +564% / DD −23.5 / pctile **92** / perm **p=0.080**; per-FY **6/9**; FY2025 **OOS Δ +1.22** — *looked like the best lead of the session* |
| **paired null** (`confluence_adx_priority_null.py`, 200 paired shuffles) | Δ Sharpe **+0.029**, 95% CI **[−0.396, +0.420]**, **P(Δ>0)=0.545** — *coin flip* |

The single-arm score was **order luck**: that one deterministic ordering was a
favorable draw, mis-attributed to ADX. The paired test (same fill order, with vs
without the ADX tiebreak) isolates the effect and finds **~0**. **Lesson:** a
single-arm-vs-null percentile conflates a rule with its lucky draw; for any
ordering/selection rule the **paired null is decisive** (the capacity test did
this right). The per-fire EV gap is real but does not become a portfolio picking
edge — ADX-priority joins RS / corr-greedy / prefer_b0 as a portfolio-level
reject, and is **not** surfaced in the UI (§5.11: a picking hint implies an edge
that isn't there).

**Exit-rule A/B (/sign-debate 2026-05-23) → REJECT, keep ZsTpSl.** The exit rule
had never been A/B'd on the confluence book — the least-explored, non-selection
lever. `confluence_exit_ab.py` ran a paired fill-order null with **adx_d8 as the
headline** (holds ~27 ≈ ZsTpSl, minimal occupancy confound), TimeStop(40) as a
diagnostic, plus a **same-trade-set decomposition** (exit quality on *identical*
entries, no slot cap).

| arm | per-trade mean_r (identical entries) | DR | paired Δ Sharpe | P(Δ>0) | FY2025 OOS Δ |
|---|---:|---:|---:|---:|---:|
| ZsTpSl (control) | +1.25% | 56.0 | — | — | — |
| adx_d8 (headline) | +1.56% | 54.0 | **+0.021** [−0.48,+0.46] | **0.535** | **−0.24** |
| time40 (diagnostic) | +1.95% | 55.7 | −0.124 | 0.265 | +0.14 |

adx_d8 captures **+0.31pp more per-trade EV on identical entries** ("win bigger,
lose more often" — DR 54 vs 56), but at the portfolio it's a **coin flip**
(P=0.535), fails the **FY2025 OOS hard gate** (−0.24), and **bull/bear sign-flips**
(+0.65 / −0.25, FY2024 −1.66) — the same regime non-stationarity that sank
TimeStop(40) in `project_timestop40_bootstrap_reject`. So the exit lever joins the
selection family: real per-trade signal, washes out in fill-order luck at ~36
trades/yr. **Exit *and* selection are now both exhausted; capacity (6-slot) is the
only intervention that moved the whole band.** (Build note: `AdxTrail` needs
`_add_adx` on the caches or it silently degenerates to `TimeStop(40)`.)

## 2026-05-30 — same-day confluence revisited (two paired nulls, both REJECT)

Operator revisited the validity-window choice with two distinct questions. The v1
"same-day" reject (§Three iterations) was a *structural-rarity* argument on the per-fire
proxy; these are the **binding capital-aware paired-null** versions on the current 10-sign
6-slot book. Both come back NOT separated.

### Q1 — generate candidates from same-day fires only? (candidate-generation rule)

Force every sign's `valid_bars = 0`, so a fire counts toward the ≥3 gate **only on its fire
date** (`confluence_sameday_ab.py` single draw → `confluence_sameday_null.py` paired null,
K=200, same seed both arms):

| arm | Sharpe mean | band [p5,p95] | sd | ret | maxDD |
|---|---:|---:|---:|---:|---:|
| WINDOWED (production) | +0.91 | [0.65, 1.18] | 0.16 | +254% | −27% |
| SAMEDAY (valid_bars=0) | +0.86 | [0.72, 1.02] | 0.09 | +167% | −24% |

- Paired **Δ Sharpe −0.048**, 95% CI **[−0.388, +0.338]**, **P(Δ>0)=0.37** — NOT separated,
  point estimate leans *negative*.
- The single-draw A/B had *flipped sign* between books (ew +0.72→+0.88 but budget +0.88→+0.83)
  and per-FY year to year — the fill-order-luck fingerprint; pairing it out reverses the "+0.16".
- SAMEDAY sheds ~17% of trades (409→338) → **−87pp stitched return** (P(Δ>0)=0.155) for no
  Sharpe. Its tighter band / shallower DD (+3.6pp) is the *fewer-candidates → less order
  sensitivity* effect, **not alpha**. **Verdict: keep WINDOWED.**

### Q2 — among WINDOWED candidates, PRIORITIZE same-day ones for the 6 slots? (ordering rule)

Same pool, promote sameday-qualifying candidates (≥3 signs fired that exact `entry_date`)
ahead of carried-only ones within each competition day, random tiebreak inside each tier
(`confluence_sameday_priority_null.py`, K=200 paired):

| arm | Sharpe mean | band [p5,p95] | ret | maxDD |
|---|---:|---:|---:|---:|
| random fill (null) | +0.91 | [0.65, 1.18] | +254% | −27% |
| sameday-priority | +0.92 | [0.67, 1.17] | +255% | −27% |

- Pool is only **9% sameday-qualifying / 91% carried** (1,030 / 10,940). Paired **Δ Sharpe
  +0.007**, 95% CI **[−0.274, +0.249]**, **P(Δ>0)=0.53** — coin flip.
- Same PEAD-score-booster mechanism: most days have no sameday candidate to promote, and when
  they do, reordering rarely changes *which 6 names* fill. Joins RS-rank / corr-greedy /
  prefer_b0 / ADX-priority / PEAD-vote-booster in the selection-rule graveyard.

**Picking guidance (unchanged, now reinforced):** do **not** prioritize by sameday-freshness
*or* by bullish-sign count (production's current sort key, itself unverified vs random fill).
Prioritize by **correlation / diversification** — the only axis with evidence. See memory
`project_confluence_sameday_null_reject.md`.

### Q3 — 指値/逆指値 entry instead of market-at-open? (entry-execution rule)

Operator: replace the two-bar market fill (T+1 open) with a 指値 (buy-limit below) or 逆指値
(buy-stop above) good for the sign's validity window — cheaper entry, skip names that gap
away, and validity gives a few days to fill if price comes back. This is an **entry-execution**
change (not selection), so it could in principle move the whole band — Stage-0 probe first
(`confluence_limit_entry_stage0.py`): identical WINDOWED signals, common +20-bar exit (only
entry varies), 5-day fill window, daily-bar fill convention (no intraday look-ahead),
limit=stop=close[T]; non-fills counted as cash.

| mode | fill% | cond. mean (filled) | **all-cand mean (cash=0)** |
|---|---:|---:|---:|
| MKT open[T+1] (production) | 100% | +0.98% | **+0.98%** |
| LIM 指値 @ close[T] | 89% | +1.01% | **+0.90%** |
| STOP 逆指値 @ close[T] | 91% | +0.91% | **+0.84%** |

**Both lose to market-at-open.** Adverse-selection diagnostic — baseline (MKT) return of the
candidates each mode FAILED to fill:

- **LIM 指値 non-fills +3.94%** vs filled +0.61% → the buy-limit **skips the winners** (names
  that ran up never return to the limit). The +0.03pp cheaper fill can't offset. The
  "avoid opposite-side moves" intuition **backfires for a bullish signal** — the up-move away
  from you is where the gains are.
- **STOP 逆指値 non-fills −1.74%** vs filled +1.23% → breakout-confirmation correctly skips
  **losers** (right directionality) but you pay *up* at the stop trigger, and that execution
  cost eats the benefit (+0.84% all-in < +0.98%).
- The validity window does its job (fill rate ~89–91% vs much less for a 1-day order) but
  cannot rescue the selection problem.

**Verdict: keep market-at-open.** Neither clears the Stage-0 gate → no Stage-1 portfolio null
warranted. *Untried low-prior thread:* 逆指値 as a pure entry **filter** (skip if no
breakout-confirm within the window, but still fill at the next open, not at the stop price) —
it had the right directionality but is a momentum gate that would still face the portfolio
fill-order null. See memory `project_confluence_limit_entry_reject.md`.

## What the data lesson is

- Single-sign feature additions (str_hold candle / gap probe, brk_wall
  / brk_floor, long-term high continuation) repeatedly failed live
  benchmark gates this session.
- Multi-sign **agreement** with the right window definition produced
  the breakthrough.
- The operator's intuition about "state-change signs" was on the right
  track; the implementation needed the validity-windowed framework
  before the signal became visible.
- This is the only positive ship-decision from the May 2026 cycle
  (which generated 10+ REJECTs of single-sign tweaks).

## Files

| Path | Role |
|---|---|
| `src/strategy/confluence_sign.py` | Production strategy class |
| `src/viz/daily.py` | Daily-tab shadow-mode wiring |
| `src/analysis/bullish_confluence_probe.py` | v1 (same-day, REJECT) |
| `src/analysis/bullish_confluence_v2_probe.py` | v2 (validity-windowed, PASS at per-fire level) |
| `src/analysis/confluence_strategy_backtest.py` | v3 live ZsTpSl backtest |
| `src/analysis/confluence_benchmark.py` | canonical per-FY benchmark (per-trade + capital-aware, 6-slot, FY2018-2025) |
| `src/analysis/confluence_buyhold_costs.py` | net-of-costs vs ETF / equal-weight universe |
| `src/analysis/confluence_market_neutral.py` | beta/alpha decomposition (62/38) |
| `src/analysis/confluence_slot_order.py` | fill-order permutation null (capstone) |
| `src/analysis/confluence_capacity_null.py` | 6-slot vs 4-slot paired shuffle null |
| `src/analysis/confluence_capacity_costs.py` | 6-slot costs (cost-invariant) + per-FY robustness |
| `src/analysis/confluence_start_phase_null.py` | start-phase variance + combined phase+order null |
| `src/analysis/confluence_regime_pooling.py` | regime-conditional pooling + per-regime β-stripped alpha + stock-chop cut |
| `src/analysis/confluence_adx_priority.py` | ADX-priority single-arm vs null (looked best, was order luck) |
| `src/analysis/confluence_adx_priority_null.py` | ADX-priority PAIRED null — decisive REJECT |
| `src/analysis/confluence_exit_ab.py` | exit-rule A/B (adx_d8/time40 vs ZsTpSl) — REJECT |
| `src/analysis/confluence_bearish_select.py` | prefer-fewest-bearish (REJECT, parked) |
| `src/analysis/confluence_sameday_ab.py` | same-day-only vs windowed candidate gen (single-draw A/B) |
| `src/analysis/confluence_sameday_null.py` | same-day-only PAIRED null — REJECT, keep windowed |
| `src/analysis/confluence_sameday_priority_null.py` | same-day-priority ordering PAIRED null — REJECT |
| `src/analysis/confluence_limit_entry_stage0.py` | 指値/逆指値 vs market-at-open entry (Stage 0) — REJECT, adverse selection |
| `src/exit/exit_simulator.py` | `day_selector` hook (dynamic holding-aware ordering) |
| `src/analysis/benchmark.md` § Confluence Strategy A/B | Canonical numbers |
| `docs/analysis/probe_vs_canonical_lesson.md` | Methodology safeguard learned this cycle |

## Commit trail

- `8a10ee4` — both confluence probes + brk_wall probe
- `bc758d0` — confluence strategy live backtest report
- `ce5f0b9` — ConfluenceSignStrategy + Daily shadow mode
