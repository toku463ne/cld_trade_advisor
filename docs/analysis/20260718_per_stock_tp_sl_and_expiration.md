# Per-Stock TP/SL Weight & Expiration Period — Investigation

**Date:** 2026-07-18
**Status:** COMPLETE — both suspicions resolved **REJECT / NO CHANGE**. Keep global
`ZsTpSl(2.0, 2.0, 0.3)`; leave the exit horizon as-is.
**Origin:** operator suspicion — *"is one general TP/SL rule really best? We could check the
per-stock optimized weight to the zigzag over the last year (like `per_stock_sign_quality`).
I also suspect the expiration period."*

**Scripts (all read-only, reusable):**
- `src/analysis/per_stock_reachability_stage0.py` — A2 premise test
- `src/analysis/exit_expiration_read.py` — B0 expiration read
- `src/analysis/per_stock_tp_first_tilt_null.py` — A2 Stage-1 paired null
- Event cache: `.../scratchpad/per_stock_reach_events.pkl`
- Raw outputs: `src/analysis/out/{reach_stage0,exit_expiration,tp_first_tilt_null}.txt`

**Memory:** [[project_per_stock_reachability_premise]], [[project_exit_expiration_reject]].

---

## TL;DR

Two independent ideas, decomposed and tested against this project's binding methodology
(the paired fill-order null on the capital-aware 6-slot book):

1. **Per-stock TP/SL "weight to the zigzag."** The premise **PASSES strongly** — a stock's
   trailing TP-reachability predicts its forward reachability, all-7-FY monotone, and it is
   genuine *geometry*, not disguised momentum/beta. But the Stage-1 per-stock TP tilt is a
   **coin flip at the 6-slot book — even with a look-ahead oracle** (Δ Sharpe +0.045,
   P=0.58). Decisive: the premise→portfolio gap is the **contention wall**, not estimation
   noise. **REJECT — keep global 2/2.**

2. **Expiration period (`max_bars`).** No variant clears the null; `b26` is an uncertified
   near-miss, shortening hard (`b15`) is worse. Bonus fact: the **live book enforces no
   time-stop at all** (only TP/SL), and that mismatch vs the 40-bar-capped backtest is **not
   a leak** (+2.95pp/trade by holding capped winners). **NO CHANGE.**

Both re-confirm the standing law: at ~36 trades/yr the fill-order/contention wall dominates
any per-trade *exit* refinement — now shown for a **third** independent, unusually-clean signal.

---

## Background — what the current rule already does

TP/SL for both the backtest and live registration is one global rule,
`ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)`. Its band is an **EWA of the stock's own recent
zigzag legs** — so the rule is *already* per-stock and per-time volatility-adaptive. The only
thing a per-stock multiplier could add is per-stock **geometry / reachability**: does *this*
stock travel far in band-units before reversing (deserving a wider `tp_mult`) or reverse near
1× (a tighter one)? That is the precise target of the operator's idea.

Two hard priors framed the whole investigation:
- An **exit change** is certified by the **paired fill-order null** on the 6-slot book, not
  per-trade Sharpe ([[project_confluence_exit_ab_reject]]: `adx_d8` had a real **+0.31pp/trade**
  and still coin-flipped, P=0.535).
- **A premise pass does not lower that bar.** [[project_per_stock_sign_quality_reject]] passed
  its premise (monotone, all-7-FY) and still died at the null.

---

## A2 — Per-stock reachability premise (Stage-0)

**Method.** Per bullish-sign fire (40,070 fires / 221 stocks, pooled — TP/SL is sign-agnostic),
fill `open[F+1]`, band = causal EWA of legs confirmed ≥ `ZZ_SIZE` bars before the fire. Over
H = 20/40 bars compute, in band units:
- `r_fav = MFE / band` (TP-side reach), `r_adv = MAE / band` (SL-side reach)
- **`tp_first`** = 1 if price touches `+2·band` before `−2·band` — the *actual* `ZsTpSl(2/2)`
  path outcome.

Residualize each fire within its year-month across all stocks (strips the calendar/vol regime
the band already rides), then walk each stock in time order: trailing mean of prior fires →
this fire. Decisive: residualized Spearman > 0 with a monotone, all-FY Q1<Q4 forward table.

**Result — PASS, stronger than `per_stock_sign_quality` got.** `tp_first`, residualized, H=40:

| Q (trailing tp_first) | fwd tp_first (residual) |
|---|---|
| Q1 (worst stocks) | **−8.7%** |
| Q2 | −2.2% |
| Q3 | +1.6% |
| Q4 (best stocks) | **+9.5%** |

- Monotone, Spearman **+0.17**, Q4−Q1 **+18.1pp**, **positive in all 7 FYs** (weakest FY2025
  +6.3, FY2022 +8.7). `r_fav` shows the same all-FY monotone pattern.
- Real cross-sectional spread: per-stock `tp_first` runs **p10 0.45 → p90 0.79**.
- Band-normalized ⇒ **geometry, not volatility level** — exactly the residual the global rule
  leaves on the table.

---

## B0 — Expiration period (`max_bars`) read

**Method.** `exit_expiration_read.py` sweeps *only* `max_bars` on `ZsTpSl(2/2/0.3)` —
`b15/b20/b26/b40`(control)/`b_inf`(=10⁶, the live setting) — with tp/sl geometry held identical,
on the production 6-slot book (1 high + 5 low), K=200 paired fill-order null.

**Key fact pinned.** The backtest caps at 40 bars, but the live evaluator
(`crud.evaluate_position_as_of`) enforces **only** tp/sl (status ∈ {tp_hit, sl_hit, hold}) —
there is **no expiration live**. So the operator runs `b_inf` while the benchmark that blessed
`ZsTpSl` ran `b40`.

**The mismatch is not a leak.** On the 3,005 trades where `b40`'s cap fires, removing it (=live)
earns **+3.45% vs +0.50%** — **+2.95pp/trade** by holding ~41 more bars; those capped positions
were mostly still-developing winners (`b_inf` resolves them 49% tp / 25% sl / 26% eod).

**Per-trade monotone, portfolio washes out:**

| arm | mean_r/trade | book Sharpe (null mean) | Δ vs b40 | P(Δ>0) |
|---|---|---|---|---|
| b15 | +1.01% | 0.83 | −0.164 | 0.22 (worse) |
| b20 | +1.23% | 0.98 | −0.020 | 0.51 |
| **b26** | +1.44% | **1.16** | +0.165 | **0.82** (best) |
| b40 (ctrl) | +1.87% | 1.00 | — | — |
| b_inf (=live) | +2.92% | 1.10 | +0.099 | 0.72 |

`b26` and `b_inf` lean positive but **neither clears P≥0.95 / CI>0**; `b15` is clearly worse.
**Verdict: leave the exit horizon as-is.** `b26` is at most an uncertified operator-call.

---

## A2 — Stage-1: per-stock `tp_first` TP-tilt vs global 2/2

**Design (robust option, operator-chosen).** `tp_first` threshold **tiers**, **TP-side only**
(sl fixed 2.0 — the SL-side persistence was ~half as strong, and moving the stop is the
historically-harmful half: timestop40/asym pattern). Pre-registered, keyed on the stock's
**trailing** `tp_first` (H=20) from *prior fires only* (≥45 cal-days back so the window is
closed; ≥3 priors, else untilted):

```
trailing tp_first < 0.50          -> tp_mult 1.0   (choppy: bank the reachable move early)
0.50 <= tp_first <= 0.70          -> tp_mult 2.0   (baseline)
trailing tp_first > 0.70          -> tp_mult 2.5   (trends: let it run)
< 3 prior fires                   -> tp_mult 2.0   (untilted)
```

Arms: **ctrl** (global 2/2), **tilt** (realizable, look-ahead-safe), **tilt_oracle**
(full-sample per-stock `tp_first` = a **look-ahead upper bound**). The oracle is the cheap kill:
if even perfect foresight doesn't separate at the book, the realizable tilt cannot — it is not
estimation noise, the tilt simply doesn't move the book. (Implementation note: the tilt map is
keyed by `candidate.zs_history` — effectively unique per candidate, available at exit-init — and
held **module-global** so `_clone_rule`'s per-position deepcopy stays light. No production
change.) 8,496 candidates, 98% tilted (30% to 1.0, 23% to 2.5).

**Carrier check — PASSED (premise is clean).** The tp_mult tiers are **not** a momentum/beta
proxy: corr-mode mix is ~identical across tiers (high 38–42% / mid 39–40% / low 20–23%). The
Stage-0 geometry finding is real, not disguised beta.

**Part 1 — per-trade, identical entries:**

| arm | mean_r% | DR% |
|---|---|---|
| ctrl | +1.87 | 58.5 |
| tilt | +1.81 | 60.9 |
| tilt_oracle | +1.94 | 59.8 |

Per-tier decomposition (the tell): the **let-run 2.5 tier is right-signed (+0.26pp)**, but the
**bank-early 1.0 tier backfires (−0.40pp)** — tightening TP on choppy stocks gives back reachable
upside (echoes B0's `b15`). The two halves cancel, so the realizable tilt is a per-trade wash.

**Part 2 — the binding paired null (K=200, 6-slot):**

| arm | book Sharpe | Δ vs ctrl | P(Δ>0) | 95% CI |
|---|---|---|---|---|
| ctrl | 1.00 | — | — | — |
| tilt | 1.02 | +0.018 | 0.55 | [−0.38, +0.36] |
| **tilt_oracle** | 1.04 | **+0.045** | **0.58** | [−0.28, +0.39] |

Both coin flips. Per-FY Δ sign-flips bull vs bear (tilt bull −0.05 / bear +0.01; oracle bull
+0.29 / bear −0.45 — the adx_d8/timestop non-stationarity); FY2025 OOS positive (tilt +0.30,
oracle +0.58) but that alone doesn't rescue it.

**Decisive:** even the **oracle** — perfect knowledge of each stock's true `tp_first` —
coin-flips at the book. The premise→portfolio gap is the **contention wall**: reordering TP
levels doesn't change which 6 of ~36 trades/yr fill, or move their book PnL enough to register.

**Do not re-run a widen-only variant** (drop the backfiring 1.0 tier): the oracle already
includes the widen side and coin-flips, and widen-only perturbs even fewer trades (23%) → an
even less winnable null (the milestone-trail power argument). **Per-stock TP/SL is a closed
exit lever.**

---

## Verdict & lessons

- **REJECT** the per-stock TP/SL weight; **NO CHANGE** to the expiration period. Keep global
  `ZsTpSl(2.0, 2.0, 0.3)`; the live no-expiration book is fine as-is.
- **The oracle-upper-bound is the reusable move.** When a per-fire signal is real but a
  selection/exit rule built on it must clear the fill-order null, run a look-ahead oracle arm.
  If the oracle coin-flips, you have *proven* the wall is contention, not estimation — a cheap,
  decisive kill (same logic as the `no_supply`-veto VLA2 arm).
- **Third independent confirmation** that per-trade exit refinements don't become portfolio
  edges at ~36 trades/yr — this time for an *unusually clean* signal (all-7-FY monotone, carrier
  check passed). The Stage-0 per-stock reachability trait is real and the scripts are reusable;
  it is simply not portfolio-harvestable at this trade count. The standing unblocker remains the
  same: more trades (universe expansion), not a better exit.
