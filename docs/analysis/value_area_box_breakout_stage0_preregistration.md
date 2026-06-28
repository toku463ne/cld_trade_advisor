# Stage-0 Pre-registration — `value_area_box_breakout`

**Date:** 2026-06-28
**Status:** RUN — **REJECT** (G3 fails, G4 fails hard; see Verdict at bottom)
**Origin:** port of the `density_pullback` idea from `cld_bittrade` (BTC/JPY 1h, shipped).
The crypto strategy: price consolidates inside a tight ~1-week **value-area box** (POC ±
coverage band of a time-at-price profile), closes through an edge, then a **limit at the
broken edge fills only on the retest**. This Stage-0 asks whether the *concept* — not the
crypto-tuned artifact — carries a candidate-level edge on **N225-universe daily bars**,
tested under this project's rules. See chat assessment 2026-06-28 for why the strategy
does **not** transfer drop-in (bar-counted params, 24/7 fill model, short-dependence, vol
character all break).

## What is genuinely new vs the existing breakout signs

We already have **6** breakout detectors: `brk_sma`, `brk_bol`, `brk_floor`, `brk_kumo`,
`brk_tenkan`, `brk_wall`. The novel primitive here is the **value-area box**: a band
[VAL, VAH] built from the *causal rolling volume-at-price profile* (reuse
`vap_node_sr_stage0.py`'s `_vap_strength` machinery — same trailing-window time/volume-at-
price bins), covering `coverage` (≈0.70) of the window's traded mass, **gated tight**
(band width ≤ `max_band_pct` of price = a real consolidation, not a trend). The trigger is
`close[T]` crossing above **VAH** (long only — see below). None of the 6 existing signs key
on a volume-profile value area, so the box edge is a structurally different level from an
SMA/Bollinger/Ichimoku/swing-high line.

**This is the make-or-break question, learned the hard way:**
- `brk_prev2peaks` died Stage-0 because only **6–9%** of fires were "fresh" (~91% collinear
  with the existing 5 breakout signs) → DOA for confluence. [[brk_prev2peaks_stage0_reject]]
- `accum_volume` died because orthogonality is **necessary but not sufficient** — fresh%
  rose but DR(fresh) ≤ DR(co-fired). [[accum_volume_stage0_reject]]

So freshness is **G1** and fresh-cohort edge is **G2**, both pre-registered below.

## Long-only

The crypto version is bidirectional; its SHORT leg is half the engine. This project is
**long-only retail** (6-slot Confluence, かぶミニ — [[live_trading_plan]]) and the whole
short sleeve is CLOSED ([[short_sleeve_map]]). Stage-0 tests **VAH up-breakouts only**.
Down-breakouts of VAL are out of scope.

## Prior-lowering context (not pre-falsifying)

`vap_node_sr_stage0.py` found heavy volume nodes (HVN) act as **absorption/overhang, not
support** on JP daily, and that **loud-volume = exhaustion** recurs across all 4 dekidaka
studies. A value-area box *is* a heavy node; breaking **out** of it into clear air is a
different question (does leaving a defended zone run?), so it is not pre-falsified — but the
"retest of the broken edge holds" leg inherits a **lowered prior**. The retest variant is
*also* directly warned by [[confluence_limit_entry_reject]]: 指値 entry lost to market-at-
open because non-fills are the winners (adverse selection). Therefore the retest is tested
as a *variable*, not assumed beneficial (G4).

---

## Design

**Universe / data.** `ohlcv_1d`, all non-`^` codes, FY2016–FY2025. Liquidity floor
`avg_turnover ≥ ¥30M` (copy `TURN_MIN`). Per-stock `COOLDOWN` dedupe. Two-bar fill: signal
on bar T, **fill at `open[T+1]`**. Winsorize forward returns at ±0.60. Horizons h5/h10/h20.
Reuse `vap_node_sr_stage0.py` wholesale for the profile, FY tagging, stats, dedupe.

**Box construction (causal, as of bar T).** From the trailing `PROFILE_W` bars' volume-at-
typical-price profile, take the value area [VAL, VAH] covering `COVERAGE` of mass (POC-
centered expansion). **Tight-box gate:** `(VAH−VAL)/close[T] ≤ MAX_BAND_PCT` (a
consolidation). **Inside-prior gate:** the prior `BASE_MIN` closes sat inside [VAL, VAH]
(an actual base, not a single touch).

**Trigger (LONG).** `close[T] > VAH` AND `close[T−1] ≤ VAH_{T−1}` (a fresh upside resolution).

**Two entry arms (the core comparison).**
- **`market`** (control): fill at `open[T+1]`.
- **`retest`** (the density_pullback fill): rest a limit at VAH; fill at VAH only if
  `low` touches VAH within `LIMIT_WINDOW` bars after T; else cancel (no trade). Record the
  non-fill cohort's forward return separately to measure adverse selection (the
  limit_entry-reject diagnostic).

**Parameter sweep (translated to daily, not crypto bars).**

| knob | meaning | sweep |
|---|---|---|
| `PROFILE_W` | box lookback (crypto 168×1h≈1wk) | 40 / 60 / 90 trading days |
| `COVERAGE` | value-area mass | 0.70 (fixed; sens 0.60/0.80) |
| `MAX_BAND_PCT` | tightness gate | 0.06 / 0.09 / 0.12 |
| `BASE_MIN` | bars inside box before break | 5 / 10 |
| `LIMIT_WINDOW` | retest wait (crypto=6×1h) | 3 / 6 bars |

Report the **baseline** = all up-breakouts at the loosest gate, so cohort edges read as
excess over the breakout population (not over cash).

---

## Pre-registered gates (Stage-0 → Stage-1)

All binary, decided before looking at the binding null. A Stage-0 PASS is **G1 ∧ G2 ∧ G3**;
G4 decides *which arm* (and whether the "retest" framing survives at all).

- **G1 — Orthogonality.** Fresh fraction ≥ **20%**, where "fresh" = no `brk_sma / brk_bol /
  brk_floor / brk_kumo / brk_tenkan / brk_wall` fire on the same stock within ±2 bars of T.
  *Fail ⇒ DOA for confluence (the brk_prev2peaks death).*
- **G2 — Fresh-cohort edge, right-signed.** On the fresh cohort: DR > baseline DR **and**
  mean_r > 0 at **both** h10 and h20. *Orthogonality alone is not enough (accum_volume).*
- **G3 — Not beta.** Fresh-cohort h10 mean_r positive in ≥ **5/8** FY, and **not**
  concentrated in the up-N225 years only (check the down-N225 FYs are not the losers — the
  alpha tell from [[confluence_market_neutral]]).
- **G4 — Does the retest earn its keep?** `retest` arm mean_r ≥ `market` arm mean_r at h10
  (non-inferiority), **and** the non-fill cohort is *not* systematically the winners
  (adverse-selection check). *Expected outcome: retest LOSES (limit_entry reject +
  density_pullback's own "misses breakouts that never retest"). If so, the idea collapses to
  "a 7th breakout sign" and must stand on G1∧G2∧G3 with the `market` arm alone.*

**If Stage-0 passes:** the **binding** test is the standard one and is **not** part of
Stage-0 — the **paired fill-order null** (K=200 shuffles, capital-aware 6-slot book,
P(ΔSharpe>0) ≥ 0.95 AND 95% CI lower bound > 0). Every selection/ordering rule this cycle
has died there; a fresh *member* sign for confluence is judged instead by whether it lifts
the confluence A/B (`scripts/rebenchmark_sign.sh` + confluence null), but the candidate-
level Stage-0 here is the gate to even build the detector. See CLAUDE.md "Methodology —
Selection Rules vs the Fill-Order Null" and [[confluence_fill_order_null]].

## Kill conditions (don't waste a Stage-1 on these)

- G1 fails (fresh% < 20) → it's a collinear restatement of existing breakouts. Stop.
- G2 fails right-signed → the box edge is not a better level than an SMA. Stop.
- G3 fails → pure N225 beta, like every "ride the breakout" reject. Stop.
- Only G4 fails → drop the retest, keep only if `market` arm clears G1∧G2∧G3.

## Deliverable

- Script: `src/analysis/value_area_box_breakout_stage0.py` (reuses `vap_node_sr_stage0.py`).
- Run: `PYTHONPATH=. uv run --env-file devenv python -m src.analysis.value_area_box_breakout_stage0`
- Read-only. Output: standalone edge panel (per-arm × horizon × FY), G1 freshness/
  orthogonality panel, G4 retest-vs-market + non-fill adverse-selection panel.

---

## Verdict (run 2026-06-28, 222-name benchmarked universe, 3971 primary fires)

**REJECT.** Primary config `profile_w=60, max_band_pct=0.09, base_min=5, limit_window=6`.

| gate | result | reading |
|---|---|---|
| **G1 orthogonality** | **PASS** | **41.0%** fresh (gate 20%). The value-area box edge is a *genuinely different level* from the 6 upside `brk_*` signs — unlike `brk_prev2peaks` (6–9% fresh, DOA). This is the one real positive. |
| **G2 fresh edge** | **PASS-on-technicality / FAIL-in-spirit** | Fresh cohort h10 +0.10% / h20 +0.38% (both >0), DR just above baseline — but *excess vs baseline is ≈0* (h10 −0.04pp) and the fresh cohort is **not better than the co-fired cohort** (h10 0.10 vs 0.17). Orthogonal but **not additive** — the exact `accum_volume` failure (orthogonality necessary, not sufficient). |
| **G3 not-beta** | **FAIL** | Fresh cohort **4/10 FY** positive (gate ≥5/8), and positives are **up-N225 years only** (FY2017/20/23/25 +; FY2021 −1.2, FY2022 −0.8, FY2024 −1.0 all −). Textbook "ride the breakout = N225 beta," and the fresh cohort is *more* beta-like, not less. |
| **G4 retest** | **FAIL (hard)** | Retest fill-rate 72%. The **non-filled** fires (limit never touched → breakouts that ran straight up) are the **massive winners**: h10 **+2.74%**, h5 +2.46%, DR 73–83%. The **filled** trades are duds (market h10 **−0.85%**, retest h10 −0.15%). The limit systematically **selects the losers** — replicates `confluence_limit_entry_reject` and `density_pullback`'s own "misses breakouts that never retest." |

**The density_pullback thesis is backwards on JP daily.** Its edge is "wait for the
pullback to the broken edge"; here the pullback names are the *failed* breakouts and the
edge lives entirely in the names that **don't** retest (+2.74% h10) — momentum continuation,
not pullback. That cohort is (a) only knowable *after* the no-retest window closes
(unselectable in advance) and (b) exactly what the incumbent `brk_*` signs already chase.

**Net:** the box edge is real geometry (G1) but carries **no additive, non-beta,
selectable edge** at the candidate level. Stage-0 PASS required G1 ∧ G2 ∧ G3 → fails at G3.
G4 independently kills the "retest" framing that was the whole point of the port. Do **not**
build the detector; do **not** advance to the fill-order null. The crypto-1h `density_pullback`
does not transfer to N225 ETF / N225-universe daily bars, by mechanism, not just by tuning.

Script: `src/analysis/value_area_box_breakout_stage0.py` (reuses `vap_node_sr_stage0.py`).
