# Confluence Strategy — Improvement Backlog

**Created:** 2026-05-28 · **Status:** research backlog, nothing committed · **Owner decision required per item**

A prioritized list of the **untested** improvement levers for `ConfluenceSignStrategy` itself (as
opposed to universe-expansion / selection-rule work, which is exhausted). Each item names its
mechanism, the prior evidence, and the **binding test** it must clear. Do **one at a time**, each with
a frozen pre-registration (no batch-running = multiple-comparisons p-hacking).

## Progress (as of 2026-05-29)

| # | Lever | Axis | Status |
|---|---|---|---|
| 1 | Oracle-ceiling probe | diagnostic | ✅ **done** — exit is the headroom axis; drawdown is exit-driven |
| 2 | Regime-conditional **sizing tilt** (neutral-momentum trim) | weights | ✅ **PASS (operator call)** — first lever to clear BOTH binding nulls (fill-order + phase); a drawdown lever (−4pp), thin Sharpe edge |
| 3 | Regime-conditional **exit** | exit (market regime) | ⛔ **REJECT** — regime-inverse trap; no clean drawdown win |
| 4 | Vol-target / risk-parity slot sizing | weights | ⛔ **REJECT** — inverse-vol mildly HURTS; EW already ≈ risk-parity on a high-β correlated book |
| 5 | 6→8 slots | capacity | open (cheap, low prior) |
| 6 | Confluence + uncorrelated overlay | portfolio | ⛔ **REJECT** — TSMOM sleeve is long-equity beta (ρ +0.61), not uncorrelated in-window; blend dilutes Sharpe, maxDD worse |
| 7 | Per-stock **β-stripped alpha stop** | exit (idiosyncratic) | ⛔ **REJECT** — all variants lose Sharpe/CAGR, whipsaw ~50%; exit 2×2 now fully settled |

**META-PATTERN (durable, earned across items 3 + the TSMOM entry gate):** anything that de-risks the
book off the **MARKET regime** fails — both the TSMOM *entry* gate (`confluence_tsmom_gate_probe`) and
the regime-conditional *exit* (item 3) died the same way, cutting into the **bear-regime recovery where
confluence's alpha lives** (regime-INVERSE alpha; FY2024 is the canary). The drawdown is exit-driven
(oracle) but only capturable by **per-stock** peak timing, which ZsTpSl already approximates. → The
surviving lever is the **conditional-EV** sizing tilt (item 2), NOT market-regime conditioning and NOT a
single-index diversification overlay (item 6 now REJECT — the only breadth-immune overlay candidate, the
TSMOM sleeve, is long-equity beta in-window, ρ +0.61, so it dilutes rather than diversifies). Item 2 is
the cleanest shot because it keys off the **NEUTRAL** momentum regime (the EV weak spot), *not* bear — so
it does not fight the regime-inverse alpha.

**UPDATE 2026-05-29 — the weights axis splits: pure-risk sizing (item 4) DEAD, conditional-EV sizing
(item 2) is the WINNER.** (a) Inverse-vol / risk-parity slot weighting (item 4) *mildly hurts* (Δ Sharpe
−0.044, P=0.21; cuts return −41pp, maxDD flat) — on a high-β (~0.7) **positively-correlated** long book
equal-weight is **already ≈ risk-parity** and the higher-vol names carry the return. (b) But the
**conditional-EV tilt (item 2) — trim NEUTRAL-momentum entries — PASSES both binding nulls** (fill-order
Δ +0.120 P=1.00; phase+order Δ +0.123 P=0.99, 8/8 phases positive), cutting maxDD −4pp. **Lesson: the
right sizing axis is EV-conditional (tilt by where forward EV actually differs), NOT risk-conditional
(tilt by vol).** And it survives where every market-REGIME *gate* died (items 3 / TSMOM) precisely because
it trims NEUTRAL while keeping bear full → preserves the regime-inverse bear-recovery alpha. After a
month of rejects across selection / exit / market-regime / pure-risk-sizing, the **conditional-EV weights
tilt is the one surviving improvement** — a drawdown lever with a thin Sharpe tailwind, pending operator
sign-off (CI-lo +0.007 thin, OOS flat, lot-granularity untested).

## Baseline (the thing we're trying to improve)

Capital-aware 6-slot ¥2M book, FY2018–2025 (most recent stitched run):
- **CAGR ~13%** (total +155% over 1,943 trading days), **Sharpe 0.88**, **maxDD −21.8%**.
- (The often-quoted "~2%" is the per-trade `mean_r`, not CAGR.)
- **~62% of return is market beta, ~38% alpha; alpha is NOT significant** (β-stripped mean_r +0.77%,
  t=1.39) **and regime-inverse** (`project_confluence_market_neutral`). So most of the 13% is "long
  Japan equities at β≈0.7"; harvestable alpha is thin.

**Theme:** entry/selection/exit are exhausted (the **exit 2×2 is now fully settled** — item 7's per-stock
alpha stop REJECT closes the last cell), **market-regime risk-shaping is closed** (item 3 + TSMOM entry
gate, see meta-pattern), and **single-index diversification is closed too** (item 6 REJECT — the TSMOM
overlay is long-equity beta, not uncorrelated, in this window). The **sole surviving lever in the entire
backlog** is **weights-axis EV-conditional sizing** (item 2, the winner; pure-risk sizing item 4 is dead)
— not raw alpha, not market-regime gates, not an overlay, not an exit. **Only item 5 (6→8 slots, cheap /
low-prior) remains untested.**

**Realizable ceiling:** every ex-ante rule tried lands inside the fill-order null band (Sharpe median
0.89, p5 0.60, **p95 1.20**); the shipped book (+0.88) is a *median* draw. p95 is luck, not edge. So
"pick better entries from this pool" is ≈ capped at the current Sharpe — see item 1 to quantify the
perfect-foresight ceiling.

---

## Backlog (priority order)

### 1. Oracle-ceiling probe — quantify per-axis headroom  *(DONE 2026-05-28 — `confluence_oracle_ceiling_probe.py`)*
Perfect-foresight upper bounds, capital-aware 6-slot, FY2018–2025:

| book | Sharpe | CAGR | maxDD | Δ Sharpe |
|---|---|---|---|---|
| baseline (ZsTpSl, prod) | 0.88 | 12.9% | −21.8% | — |
| oracle SELECTION | 2.85 | 56.8% | −17.8% | +1.97 |
| **oracle EXIT (within hold)** | **4.34** | 59.1% | **−6.4%** | **+3.46** |
| oracle EXIT (+60-bar) | 4.23 | 107.8% | −11.7% | +3.35 |
| oracle BOTH | 5.08 | 96.1% | −6.6% | +4.20 |

**Findings:** (a) **EXIT timing is the largest-headroom axis (+3.46 Sharpe), bigger than SELECTION
(+1.97).** (b) **The strategy's drawdown is almost entirely exit-driven** — perfect exit timing within
the *same* hold windows collapses maxDD −21.8% → **−6.4%**. (c) Both gaps are *perfect-foresight*, and
the realizable nulls say neither is capturable by the rules tried: SELECTION is dead (fill-order null),
and the fixed-exit swap was a portfolio coin-flip (exit A/B). So **return** improvement is unlikely. (d)
But (b) reframes the value of exit work: a causal regime-conditional exit that de-risks faster in bear
regimes is the lever most likely to **cut the −22% drawdown** — a *risk* win, not a return win.

**Reprioritization:** item 3 (regime-conditional exit) is now elevated — but its value proposition is
**drawdown reduction**, so its binding test should weight maxDD, not just Sharpe. Items 2 (sizing tilt)
and 4 (vol-target) remain the return/risk axes the oracle did *not* measure (weights, not selection/exit).

### 2. Regime-conditional sizing tilt — trim neutral-momentum entries  *(✅ DONE 2026-05-29 — PASS, operator call. `confluence_evtilt_null.py` + `confluence_evtilt_phase_null.py`, pre-reg `confluence_evtilt_sizing_preregistration.md`)*
**RESULT — the first backlog lever to clear BOTH binding nulls.** Trim neutral-N225-60bar-momentum
entries to τ=0.5 weight (keep slot filled, bull/bear full); frozen tercile cutoffs from the prior pooled
run (bear ≤ −0.1% < neutral ≤ +8.1% < bull). Both arms applied to the SAME fills per shuffle (perfect
pairing — only the weight differs).

| null | EW Sharpe | TILT-DL Sharpe | Δ Sharpe | P(Δ>0) | 95% CI | Δ maxDD |
|---|---|---|---|---|---|---|
| fill-order (K=200) | 0.911 / −27.3% | 1.031 / −23.2% | +0.120 | **1.000** | [+0.037,+0.207] | +4.14pp (100%) |
| **phase+order (200 worlds)** | 0.878 / −27.2% | 1.001 / −23.0% | **+0.123** | **0.990** | **[+0.007,+0.262]** | +4.17pp (100%) |

- **Clears the gate in BOTH nulls** — including the combined phase+order null that the standing CLAUDE.md
  caveat names as the binding test for a regime-keyed rule (the fill-order null pairs only on within-day
  order → blind to regime-timing luck; the phase null sweeps start offset → trims DIFFERENT neutral
  periods). Per-offset deterministic Δ is **positive at 8/8 start phases** (+0.087..+0.223) — regime-
  timing luck would have made some phases negative. It did not.
- **Clears the 95% CI where the shipped 6-slot capacity null did NOT** (capacity was P=0.865, CI included
  0, adopted on risk-asymmetry; this is P=0.99, CI-lo +0.007 clears). By the project's own bar this is a
  *stronger* statistical case than a rule already in production.
- **Primarily a DRAWDOWN lever** (−27%→−23%, +4pp, rock-solid 100% across both nulls); the Sharpe gain is
  real but **thin** (CI-lo grazes 0 in the wider band) and **concentrated in the weak FYs** (FY2021 +0.38,
  FY2022 +0.28; strong FYs ~flat; **OOS FY2025 −0.024**, flat-negative — but FY2025 is bull-heavy with
  little neutral exposure to trim, so the rule is ~inactive, not failing). τ monotone (0.25→+0.173 >
  0.5→+0.120 > 0.75→+0.061) — coherent dose-response, not a tuned point.
- **Why it escapes the item-3 trap:** it trims the **NEUTRAL** regime (the β-stripped EV weak spot), keeping
  **bull AND bear full** → it does NOT cut the bear-regime recovery where the regime-inverse alpha lives
  (the mechanism that killed the market-regime exit + TSMOM entry gate). Structurally distinct.

**LOT-GRANULARITY CHECK (2026-05-29, `confluence_evtilt_lots_null.py`) — caveat (3) RESOLVED: realizable.**
On the realistic integer-lot budget book (¥2M / 6 slots / 100-sh lots, affordability skip + cash drag,
mirrors `confluence_benchmark.py` bw path), "trim neutral to τ=0.5" = buy `floor(0.5·base_lots)` lots.
Granularity bite is real — 50% of neutral names (mean base_lots 3.15) round to **0 lots**, so realized
τ_eff = 0.394 (deeper than nominal) — but the edge SURVIVES: EW-LOT Sharpe 0.916/maxDD −21.1% → TILT-LOT
1.042/−17.0%, paired Δ Sharpe **+0.126, P(Δ>0)=0.980, CI [+0.005,+0.252]**, Δ maxDD **+4.13pp shallower
in 99.5%** of shuffles, **Δ return FLAT (+0.5pp)** = pure risk improvement at zero return cost on the real
book. (OOS FY2025 Δ −0.114, worse than idealized — the aggressive trim cashes working neutral names in a
bull year = the insurance premium it recoups in FY2021/FY2022.)

**CUTOFF CROSS-VALIDATION (2026-05-29, `confluence_evtilt_cutoffcv_null.py`) — the in-period-cutoff caveat
RESOLVED.** The `/sign-debate` judge initially DEFERred (the maxDD claim is load-bearing yet the cutoffs
were fit in-period on the tape containing the FY2021/FY2022 drawdowns; the per-FY edge concentrates there).
Pre-registered falsifier: re-derive terciles on **train FY2018–22**, freeze, score the integer-lot null on
**held-out FY2023–25**. Result: train cutoffs = bear ≤ −1.64% < neutral ≤ +4.06% < bull (**materially
different** from the in-period −0.10%/+8.10% → genuine OOS cutoff test). Held-out **Δ maxDD +4.51pp
shallower (−17.3%→−12.8%), P(shallower)=0.995** (gate ≥+2pp & ≥90% → PASS); **Δ Sharpe +0.128 ≥ 0** (gate
PASS) but CI [−0.107,+0.401] wide / not significant (3 FYs, 35 neutral fills); Δ return −5.3pp (insurance
premium). **The drawdown edge is FORWARD-STABLE, not cutoff overfit.**

**`/sign-debate` VERDICT: ACCEPT (confidence M)** (full record: `confluence_evtilt_sign_debate_verdict.md`) — adopt as a live manual sizing GUIDELINE, scoped strictly
as a **DRAWDOWN lever**: drawdown claim accepted (significant, OOS-stable); **no Sharpe-improvement claim**
(held-out CI wide); **no return claim** (held-out −5.3pp). **Binding surfacing condition:** integer rounding
makes the real instruction **BIMODAL** — "in a NEUTRAL N225-60bar regime, **SKIP cheap neutral-regime names
entirely, HALF-SIZE expensive ones**; buys ~4.5pp shallower drawdown at a ~5pp return cost (held-out n=35,
Sharpe not significant)" — the guideline text must say *that*, not "buy half lots." The live book is manual
so there is NO exit_simulator constant to flip — adoption = surfacing the N225-60bar regime + the bimodal
half-lot recommendation in the Daily tab / live plan. **Forward falsifier:** if FY2026 (first true
post-adoption OOS year) closes with tilt-lot maxDD ≤ EW-lot maxDD (Δ ≤ 0), withdraw the guideline.

**REMAINING CAVEATS:** (1) the Sharpe tailwind is real in-sample but NOT significant out-of-sample — this is
a drawdown-for-return TRADE (~−5pp return for ~4.5pp shallower DD), not a free lunch. (2) the edge
concentrates in weak/drawdown FYs (insurance premium; flat-to-negative in calm/bull years). This is the
strongest, most-validated lever the backlog has produced (clears fill-order + phase + integer-lot + held-out
cutoff-CV nulls) — the weights axis (item 2) is the survivor where selection, exit, market-regime, and
pure-risk sizing (item 4) all died. **Implementation (Daily-tab + live-plan text) is user-authorized work,
NOT yet done.**

_(original) `project_confluence_phase_regime`: EV is non-monotone in N225 60-bar momentum — **NEUTRAL is
the weak spot** (raw +0.52% / α +0.33% vs bullish +3.31%/+1.20%, bearish +1.31%/+0.57%), survives β-strip.
**Trim (not skip)** weight on neutral-regime entries; keep bull/bear full. Different axis than the rejected
selection rules (changes weights, not which names fill slots) → not pre-killed by the fill-order null.
**Binding:** paired fill-order null on the capital-aware 6-slot book (P(ΔSharpe>0)≥0.95 AND CI-lo>0),
OOS-stable, no effect-size floor._

### 3. Regime-conditional exit — bull vs bear  *(⛔ DONE 2026-05-28 — REJECT, `confluence_regime_exit_probe.py`)*
**RESULT:** exit each trade at the earlier of ZsTpSl or the first N225-bear bar (sweep: sma50/sma100/
mom60neg/dd5/dd10), post-hoc on the same entries. **No clean drawdown win** — every trigger costs Sharpe
(−0.14 to −0.72) for a modest maxDD cut (best +4.5pp, −21.8%→−17.2% via mom60neg, but −0.29 Sharpe;
cheapest dd10 −0.14 Sharpe for only +1.6pp). **The regime-inverse trap bites** (FY2024 Sharpe 0.64→−1.79
sma50 / −0.75 mom60neg) — exiting on bear cuts the bear-regime recovery where confluence's alpha lives,
same mechanism as the rejected TSMOM ENTRY gate. **MECHANISM (durable):** the oracle's drawdown headroom
is PER-STOCK peak timing (idiosyncratic), not a market signal; the per-stock causal exit (ZsTpSl) is
already ~optimized (exit A/B), and a MARKET-regime overlay collides with the regime-inverse alpha → exit-
side risk-shaping via market regime is CLOSED. Discovery gate (material maxDD cut AND Sharpe≈baseline AND
FY2024 intact) met by none → no escalation. _Original framing below._

_(original) Regime-conditional exit — bull vs bear, held-out bull FY (the open exit door):_
`project_confluence_exit_ab_reject` rejected ZsTpSl→adx_d8/time40 (portfolio coin-flip Δ+0.021 P=0.535)
but explicitly said *re-open only via a regime-conditional exit with a held-out bull FY* — because
adx_d8 vs ZsTpSl **sign-flips bull vs bear**. Condition the exit (rule or ZsTpSl params) on N225 trend
regime at entry. **Binding:** paired fill-order null on the 6-slot book **+ a held-out bull FY** (the
prior reject was single-order luck / lacked a bull holdout). **Value proposition is DRAWDOWN
reduction, not return** (oracle item 1: drawdown is exit-driven, maxDD −22%→−6% under perfect exit;
but causal exit swaps don't beat ZsTpSl on Sharpe) → the binding test should weight **maxDD / CDaR**,
not just Sharpe. *Build note: AdxTrail needs `_add_adx()` or it degenerates to TimeStop(40).*

### 4. Volatility-target / risk-parity slot sizing vs equal-weight  *(⛔ DONE 2026-05-29 — REJECT, `confluence_voltarget_null.py`)*
**RESULT:** K=200 paired fill-order null, FY2018–2025, 6-slot equal-weight idealized book. Both
re-weighting arms were applied to the **same fills per shuffle** (perfect pairing — only the slot
weight differs), trailing-20-bar entry vol, no lookahead. Pre-reg:
`confluence_voltarget_sizing_preregistration.md`.

| arm | Sharpe (mean) | ret | maxDD | Δ Sharpe vs EW | P(Δ>0) | 95% CI |
|---|---|---|---|---|---|---|
| EW (baseline) | 0.911 | +254% | −27.3% | — | — | — |
| **IV-RP** (inverse-vol risk-parity, same gross) | 0.868 | +213% | −27.4% | **−0.044** | **0.205** | [−0.134, +0.067] |
| VT (vol-target, gross-scaled, diag) | 0.846 | +212% | −26.6% | −0.065 | 0.155 | [−0.171, +0.054] |

**Gate FAILED hard** (need P≥0.95 AND CI-lo>0): IV-RP Δ Sharpe is **negative**, P(Δ>0)=0.205, CI
straddles 0; maxDD unchanged (−0.09pp). Not merely "within noise" — inverse-vol **mildly HURTS**:
it cuts return −41pp (P(Δret>0)=0.075) while barely touching portfolio vol/DD. VT (gross-scaled
deleverage) shaves maxDD a hair (+0.75pp) but also loses Sharpe → a pure leverage trade, not edge.
(OOS FY2025 *alone* leaned + for both arms, +0.13/+0.16, but the pooled verdict is the binding one.)

**MECHANISM (durable):** on a **high-β (≈0.7), positively-correlated long book**, the higher-vol
confluence breakouts carry **more of the return**, and because the held names co-move, down-weighting
them by single-name vol sacrifices return **without** a compensating portfolio-variance reduction
(single-name vol dispersion ≠ portfolio vol when ρ is high). **Equal-weight is already ≈ risk-parity**
for correlated names (similar risk contribution per name). So the weights-axis risk-shaping that *isn't*
market-regime-conditioned still fails — for a different reason than items 3/TSMOM (correlation, not the
regime-inverse trap). **Sizing as a pure risk knob is closed; the remaining live levers are diversification
(item 6, add an uncorrelated stream) and the conditional-EV sizing tilt (item 2, which keys off the
NEUTRAL-momentum EV weak spot — a different premise than de-risking by vol).**

_(original) Book is equal-weight (deployed-capital) across slots; corr-diversification is enforced in
the live UI for risk but a formal vol-target / risk-parity slot sizing was never **backtested**. Weight
slots inverse to recent vol (or to equalize risk contribution). Goal: Sharpe-via-lower-vol / smaller
maxDD, not higher return. **Binding:** paired fill-order null on portfolio Sharpe + maxDD. Caveat:
integer-lot granularity at ¥2M/6 slots limits weight precision (`sizing.recommended_lots`)._

### 5. 6→8 slot sweep  *(cheap, low prior)*
4→6 shipped (capacity null Sharpe 1.02 vs 0.89, Δ+0.137, CI grazed 0; adopted on risk-asymmetry). 6→8
never tested. **Low prior:** Stage-0 found only ~8 low-corr names/day → past ~6–8 slots breadth-starved
(forced to add correlated names). Quick paired capacity null at `_MAX_LOW_CORR` 5→7 on the existing 225
book (no rebuild). Also raises manual-execution burden (live plan = 6 slots).

### 6. Portfolio diversification — confluence + uncorrelated overlay  *(⛔ DONE 2026-05-29 — REJECT, `confluence_overlay_blend_null.py`, pre-reg `confluence_overlay_blend_preregistration.md`)*
**RESULT — REJECT, both gates fail, exactly the pre-registered failure mode.** Blended the production
6-slot book with a *parallel* TSMOM long/flat L=12 index sleeve (no-leverage fixed split, `f` of ¥2M to
the sleeve), K=200 paired fill-order null. NOT the rejected entry-gate (this never changes which names
confluence buys; it splits capital).

| arm | Sharpe | ret | maxDD | Δ Sharpe vs conf | P(Δ>0) | 95% CI | Δ maxDD |
|---|---|---|---|---|---|---|---|
| confluence-only (f=0) | 0.911 | +254% | −27.3% | — | — | — | — |
| **blend f=0.30 (primary)** | 0.830 | +177% | −27.9% | **−0.081** | **0.015** | [−0.156, −0.012] | **−0.61pp (worse)** |
| blend f=0.20 | 0.865 | +201% | −27.6% | −0.046 | 0.015 | [−0.092, −0.004] | −0.24pp |
| blend f=0.50 | 0.732 | +133% | −29.2% | −0.179 | 0.015 | [−0.323, −0.047] | −1.86pp |

**CRUX (the whole thesis dies here): pooled ρ(confluence_daily, overlay_daily) = +0.605.** The TSMOM
sleeve is **long-equity beta 70% of the time** → it co-moves with the ~0.7-beta book, so it is **NOT an
uncorrelated stream in this window**. Standalone overlay Sharpe +0.37 / maxDD −39% (worse than the book).
Blending therefore just **dilutes** toward a lower-Sharpe, in-window-correlated asset: monotone-worse in
`f` (more overlay = lower Sharpe, larger return drag −53/−77/−121pp), and maxDD **worsens** (P(shallower)
only 0.235). **Per-FY maxDD:** small cuts in calm/up FYs (FY2018 +3.3pp, FY2023 +1.9pp) but **WORSE in the
years that matter** — FY2024 **−1.2pp** (the documented good-in-bearish year) and ~no-op on FY2019
(+0.2pp on a −29% loss year, because TSMOM was *long going in*). **MECHANISM (durable):** the 41-yr TSMOM
drawdown edge lives in *sustained* bears (1990s lost decades, GFC) **outside** FY2018–25; inside the
window the index bears are V-recoveries (COVID) and chop (2022/2025) where TSMOM **whipsaws** and is long
into the sharp legs → it protects nothing the β-0.7 book actually suffers. OOS FY2025 leans + (+0.074)
but the pooled verdict binds and the blend loses badly overall. **Closes the single-index-TSMOM
diversification-overlay lever.** (A genuinely *uncorrelated* stream — not long-equity-beta — could still
diversify, but no such breadth-immune candidate is on the map; PEAD/value were breadth-killed.)
_Original framing below._

_(original)_ `project_confluence_buyhold_win`: the book only **ties** the equal-weight universe; its edge
over the index is a drawdown cut, not alpha (~62% beta). The biggest risk-adjusted gain is pairing it with
an **uncorrelated** stream, not optimizing the book. Candidate: the **TSMOM long/flat L=12 defensive
overlay** (`docs/analysis/20260528_tsmom_overlay.md` — halves index maxDD over 41yr, breadth-immune,
~uncorrelated to single-name picking). Test blended (confluence + sized overlay) Sharpe/maxDD vs
confluence-only. *NOTE: TSMOM is tail-insurance, not alpha (drags in bull/chop) — judge net portfolio
risk-adjusted improvement, sized as an overlay.*

### 7. Per-stock β-stripped ALPHA stop — idiosyncratic exit  *(⛔ DONE 2026-05-29 — REJECT, `confluence_alpha_stop_probe.py`, pre-reg `confluence_alpha_stop_preregistration.md`)*
**RESULT — REJECT (no escalation), the predicted failure mode.** The last open cell of the exit 2×2 is now
closed:

| | raw-price | β-stripped (alpha) |
|---|---|---|
| **per-stock** | exhausted (ZsTpSl ≈ best; ~14 raw-price variants, CI [−2.89,+4.74]) | ⛔ **REJECT (this probe)** |
| **market-regime** | — | ⛔ REJECT (item 3, regime-inverse trap) |

Post-hoc exit override on the ¥2M 6-slot baseline (401 trades, FY2018–2025): exit at the earlier of ZsTpSl
or a per-stock β-stripped cumulative-alpha stop (β = trailing 60-bar vs ^N225, pre-entry). Six variants —
LEVEL `α_cum ≤ −θ` (θ∈{5,8,12}%) and TRAIL `α_cum ≤ peak−X` (X∈{5,8,12}%):

| variant | Sharpe | CAGR | maxDD | nStop | whip% | Δ Sharpe |
|---|---|---|---|---|---|---|
| baseline | 0.88 | 12.9% | −21.8% | — | — | — |
| lvl05 | 0.70 | 7.6% | −24.1% | 154 | 47% | −0.18 |
| lvl12 (lightest) | 0.81 | 11.4% | −21.8% | 34 | 53% | −0.07 |
| trl05 | 0.75 | 7.5% | −20.1% | 226 | 52% | −0.13 |
| trl08 | 0.66 | 7.7% | −28.1% | 133 | 45% | −0.22 |

**Every variant LOSES Sharpe (−0.07..−0.22) AND CAGR (−1.5..−5.4pp); whipsaw ~45–53% (a coin flip — half
the stops cut names that recovered, exactly the PEAD-sleeve prior); maxDD mostly WORSE** (confirms the
caveat — the −22% DD is beta-driven, an alpha stop cannot touch it). **MECHANISM (per-FY):** the stops
crater **FY2019** (the sole sustained bear, base −0.09 → −0.98/−1.15) and hurt the post-COVID **FY2020**
recovery (2.10 → 0.79) — they churn breakout **pullbacks that recover**. Lighter stops (lvl12, 34 fires)
approach a no-op (Δ −0.07) but still whip 53% / lose return; heavier stops churn more. **No operating point
helps → no escalation.** With this, the exit 2×2 is **fully settled** and the only backlog survivor is the
conditional-EV sizing tilt (item 2). _Original framing below._

Idea: exit a held name on its own **β-stripped cumulative-alpha** erosion (level stop < −X%, or alpha-
trailing stop X% below the alpha peak), β from a pre-entry window. **STRUCTURAL FACT:** `ExitContext`
(what an `ExitRule` sees) carries only the stock's own `close/high/low/adx/±DI/peak_adx/zs_history` —
**no market/index series** — so no current exit rule can be alpha/beta-based; this needs a **post-hoc
probe** (like `confluence_regime_exit_probe`) or an extended context. **Why it's distinct & appealing:**
stripping the market means it does NOT fight the regime-inverse alpha (unlike the rejected market-regime
exit — it fires only on names breaking down *idiosyncratically*). **CAVEAT (reframes its axis):** the
−22% drawdown is **beta-driven** (β≈0.7 long book); an alpha stop strips the market so it will **not** cut
that drawdown — it's a **RETURN/alpha** lever (cut idiosyncratic losers), closer in spirit to item 2 than
to drawdown. **PRIOR:** the only alpha-stop tested anywhere (`pead_sleeve_alpha_stop_probe`, PEAD sleeve
β≈1) **whipsawed** (~50%, Δmean-alpha<0 at every θ) — but a different cohort, so untested on confluence
breakouts. **Binding:** paired fill-order null on the 6-slot book + explicit whipsaw-rate check.

---

## Exhausted — do NOT re-run (recorded so they aren't re-proposed)

- **Selection / ordering rules** (which candidates fill the 6 slots): RS-rank, corr-greedy,
  prefer_b0/bearish-count, ADX-priority, PEAD vote, PEAD score-boost — all died on the fill-order null
  at ~36–50 trades/yr. The exogeneity of the key does not lower the bar; more contention doesn't either
  (universe expansion refuted). See CLAUDE.md "Selection Rules vs the Fill-Order Null".
- **Individual sign tweaks:** sign-set LOO, drop-tenkan, label-swap, agnostic-count, strength=disp/ATR
  — rejected; brk_sma (low,K=3) + 3 ichimoku _hi already optimized.
- **Fixed-exit swap:** ZsTpSl ≈ best; adx_d8/time40 are portfolio coin-flips.
- **Market-REGIME conditioning (entry OR exit):** the regime-conditional exit (item 3,
  `confluence_regime_exit_probe`) and the TSMOM entry gate (`confluence_tsmom_gate_probe`) both REJECT —
  de-risking off the market regime cuts into the regime-inverse bear-recovery (FY2024). Do not re-propose
  market-trend entry/exit gates. (Per-stock idiosyncratic exit ≠ this; that's ZsTpSl, already optimized.)
- **2-bar fill:** a manual-execution realism constraint (opening auction), not an optimization knob.
- **Market-neutral confluence:** 62% beta, α t=1.39, short leg doomed by JP momentum failure.

## Discipline
One pre-reg at a time, frozen gate, binding = the **paired fill-order null on the capital-aware 6-slot
book** (per-trade / single-order point estimates do NOT decide). Held-out FY(s); no iteration. The
related cross-strategy backlog (parked rejects) is in memory `project_parked_rejects_revival_triage`
(shelved) and the program map is `docs/analysis/20260528_new_directions.md`.
