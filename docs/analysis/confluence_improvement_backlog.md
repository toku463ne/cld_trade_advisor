# Confluence Strategy — Improvement Backlog

**Created:** 2026-05-28 · **Status:** research backlog, nothing committed · **Owner decision required per item**

A prioritized list of the **untested** improvement levers for `ConfluenceSignStrategy` itself (as
opposed to universe-expansion / selection-rule work, which is exhausted). Each item names its
mechanism, the prior evidence, and the **binding test** it must clear. Do **one at a time**, each with
a frozen pre-registration (no batch-running = multiple-comparisons p-hacking).

## Baseline (the thing we're trying to improve)

Capital-aware 6-slot ¥2M book, FY2018–2025 (most recent stitched run):
- **CAGR ~13%** (total +155% over 1,943 trading days), **Sharpe 0.88**, **maxDD −21.8%**.
- (The often-quoted "~2%" is the per-trade `mean_r`, not CAGR.)
- **~62% of return is market beta, ~38% alpha; alpha is NOT significant** (β-stripped mean_r +0.77%,
  t=1.39) **and regime-inverse** (`project_confluence_market_neutral`). So most of the 13% is "long
  Japan equities at β≈0.7"; harvestable alpha is thin.

**Theme:** entry/selection/exit are exhausted. The remaining headroom is **risk-shaping** (Sharpe /
maxDD via sizing & regime conditioning) and **portfolio diversification** — not raw alpha.

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

### 2. Regime-conditional sizing tilt — trim neutral-momentum entries  *(most untapped)*
`project_confluence_phase_regime`: EV is non-monotone in N225 60-bar momentum — **NEUTRAL is the weak
spot** (raw +0.52% / α +0.33% vs bullish +3.31%/+1.20%, bearish +1.31%/+0.57%), survives β-strip. **Trim
(not skip)** weight on neutral-regime entries; keep bull/bear full. Different axis than the rejected
selection rules (changes weights, not which names fill slots) → not pre-killed by the fill-order null.
**Binding:** paired fill-order null on the capital-aware 6-slot book (P(ΔSharpe>0)≥0.95 AND CI-lo>0),
OOS-stable, no effect-size floor.

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

### 4. Volatility-target / risk-parity slot sizing vs equal-weight
Book is equal-weight (deployed-capital) across slots; corr-diversification is enforced in the live UI
for risk but a formal vol-target / risk-parity slot sizing was never **backtested**. Weight slots
inverse to recent vol (or to equalize risk contribution). Goal: Sharpe-via-lower-vol / smaller maxDD,
not higher return. **Binding:** paired fill-order null on portfolio Sharpe + maxDD. *Caveat: integer-lot
granularity at ¥2M/6 slots limits weight precision (`sizing.recommended_lots`).*

### 5. 6→8 slot sweep  *(cheap, low prior)*
4→6 shipped (capacity null Sharpe 1.02 vs 0.89, Δ+0.137, CI grazed 0; adopted on risk-asymmetry). 6→8
never tested. **Low prior:** Stage-0 found only ~8 low-corr names/day → past ~6–8 slots breadth-starved
(forced to add correlated names). Quick paired capacity null at `_MAX_LOW_CORR` 5→7 on the existing 225
book (no rebuild). Also raises manual-execution burden (live plan = 6 slots).

### 6. Portfolio diversification — confluence + uncorrelated overlay  *(structural lever)*
`project_confluence_buyhold_win`: the book only **ties** the equal-weight universe; its edge over the
index is a drawdown cut, not alpha (~62% beta). The biggest risk-adjusted gain is pairing it with an
**uncorrelated** stream, not optimizing the book. Candidate: the **TSMOM long/flat L=12 defensive
overlay** (`docs/analysis/20260528_tsmom_overlay.md` — halves index maxDD over 41yr, breadth-immune,
~uncorrelated to single-name picking). Test blended (confluence + sized overlay) Sharpe/maxDD vs
confluence-only. *NOTE: TSMOM is tail-insurance, not alpha (drags in bull/chop) — judge net portfolio
risk-adjusted improvement, sized as an overlay.*

---

## Exhausted — do NOT re-run (recorded so they aren't re-proposed)

- **Selection / ordering rules** (which candidates fill the 6 slots): RS-rank, corr-greedy,
  prefer_b0/bearish-count, ADX-priority, PEAD vote, PEAD score-boost — all died on the fill-order null
  at ~36–50 trades/yr. The exogeneity of the key does not lower the bar; more contention doesn't either
  (universe expansion refuted). See CLAUDE.md "Selection Rules vs the Fill-Order Null".
- **Individual sign tweaks:** sign-set LOO, drop-tenkan, label-swap, agnostic-count, strength=disp/ATR
  — rejected; brk_sma (low,K=3) + 3 ichimoku _hi already optimized.
- **Fixed-exit swap:** ZsTpSl ≈ best; adx_d8/time40 are portfolio coin-flips. (Regime-conditional exit,
  item 3, is the only re-open path.)
- **2-bar fill:** a manual-execution realism constraint (opening auction), not an optimization knob.
- **Market-neutral confluence:** 62% beta, α t=1.39, short leg doomed by JP momentum failure.

## Discipline
One pre-reg at a time, frozen gate, binding = the **paired fill-order null on the capital-aware 6-slot
book** (per-trade / single-order point estimates do NOT decide). Held-out FY(s); no iteration. The
related cross-strategy backlog (parked rejects) is in memory `project_parked_rejects_revival_triage`
(shelved) and the program map is `docs/analysis/20260528_new_directions.md`.
