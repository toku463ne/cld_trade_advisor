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
two-bar fill, portfolio cap (≤1 high-corr + ≤3 low/mid), 10-bar
cooldown between re-entries on same stock.

| Strategy | trades | avg Sharpe | avg mean_r | win% |
|---|---:|---:|---:|---:|
| `regime_sign` baseline (currently shipped) | 171 | **+1.33** | +0.77% | varies |
| N ≥ 1 (any bullish sign valid) | 249 | +1.36 | +0.77% | 52% |
| N ≥ 2 (≥2 signs agree) | 222 | **+3.53** | +1.92% | 58% |
| **N ≥ 3 (≥3 signs agree)** | **165** | **+3.80** | **+1.97%** | **59%** |

### Per-FY at N ≥ 3 (the recommended gate)

| FY | trades | Sharpe | mean_r | win% |
|---|---:|---:|---:|---:|
| FY2019 | 12 | **−5.26** | −2.75% | 42% |
| FY2020 | 24 | **+7.95** | +4.47% | 67% |
| FY2021 | 29 | +1.55 | +1.06% | 48% |
| FY2022 | 22 | +3.35 | +1.66% | 64% |
| FY2023 | 23 | **+9.10** | +3.80% | 70% |
| FY2024 | 30 | +1.55 | +0.94% | 57% |
| **FY2025 OOS** | **25** | **+8.37** | **+4.61%** | **68%** |

- 6 of 7 FYs positive Sharpe
- FY2025 OOS is the cleanest win — +8.37 Sharpe on 25 trades with 68% win rate
- Three FYs above +7 Sharpe: FY2020, FY2023, FY2025
- Lone loss: FY2019 at −5.26 on n=12 (small-sample concern, deferred)

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
| `src/analysis/confluence_buyhold_costs.py` | net-of-costs vs ETF / equal-weight universe |
| `src/analysis/confluence_market_neutral.py` | beta/alpha decomposition (62/38) |
| `src/analysis/confluence_slot_order.py` | fill-order permutation null (capstone) |
| `src/analysis/confluence_capacity_null.py` | 6-slot vs 4-slot paired shuffle null |
| `src/analysis/confluence_start_phase_null.py` | start-phase variance + combined phase+order null |
| `src/analysis/confluence_regime_pooling.py` | regime-conditional pooling + per-regime β-stripped alpha + stock-chop cut |
| `src/analysis/confluence_adx_priority.py` | ADX-priority single-arm vs null (looked best, was order luck) |
| `src/analysis/confluence_adx_priority_null.py` | ADX-priority PAIRED null — decisive REJECT |
| `src/analysis/confluence_bearish_select.py` | prefer-fewest-bearish (REJECT, parked) |
| `src/exit/exit_simulator.py` | `day_selector` hook (dynamic holding-aware ordering) |
| `src/analysis/benchmark.md` § Confluence Strategy A/B | Canonical numbers |
| `docs/analysis/probe_vs_canonical_lesson.md` | Methodology safeguard learned this cycle |

## Commit trail

- `8a10ee4` — both confluence probes + brk_wall probe
- `bc758d0` — confluence strategy live backtest report
- `ce5f0b9` — ConfluenceSignStrategy + Daily shadow mode
