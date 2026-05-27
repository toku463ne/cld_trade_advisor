# Design Proposal — Standalone PEAD Sleeve

**Status:** proposal for operator selection (NOT locked) · **Date:** 2026-05-27
**Governs:** the §10 deferred items of `docs/analysis/pead_sleeve_thesis.md` (f1771a8)
**Next step:** operator selects one option per axis → pre-registration → run the §4
selection-alpha null. Committing the *selected* design is the goalpost-lock act.

This proposal fixes the **design** before the null. Each axis lists options with
trade-offs *against the locked thesis*, a recommended default, and what the choice
implies for the binding gate (§4: selection-alpha null, book 4 vs book 3). Nothing
here re-opens the thesis bar — it only decides *what gets tested*.

---

## 0. What the thesis already locked (not up for selection)

- **Long-only.** Beta≈1, "uncorrelated alpha paid for with full market beta." The
  validated signal is long-short (up−down); the sleeve harvests only the **up** leg.
  No shorting (manual long account; the down leg is a separate, unbuilt bet).
- **Shared ¥2M, sleeve displaces confluence.** T1 = ¥0.6M / ~2 slots. The A/B is always
  same-total-capital, sleeve-on vs sleeve-off.
- **Binding gate = selection alpha** (book 4 vs book 3), clustering-aware paired null,
  no pre-committed effect size. Timing (3 vs 2) is diagnostic only.

The design choices below must keep book 3 (β·index over each position's *actual hold
window*) well-defined and must not smuggle in the pro-cyclical timing tilt as if it
were the edge.

---

## 1. Candidate pool — *what makes a name a sleeve candidate today?*

| Opt | Definition | Trade-off vs thesis |
|---|---|---|
| **A (rec)** | Pure signal 1: any **up-revision** (ΔFY-forecast-EPS / price > 0) in the N225 cohort, entered on `tradable_entry_day`. | Exactly what gate 7 validated (+2.51% cohort, n_up=1,792). Zero new parameters. |
| B | Up-revision **above a magnitude threshold** (e.g. top-tercile surprise). | Signal 1's **size-gradient was FLAT/non-monotone** — magnitude does not predict drift. A threshold is an unvalidated free parameter → violates §8 anti-mining. |
| C | Up-revision **minus names confluence already holds** (max independence). | Makes the candidate set depend on the *other* book's live state; complicates book 3 and the pairing. Independence is already a §5 guardrail, not an entry filter. |

**Recommend A.** The sleeve's entire premise is the *standalone, exogenous, cross-
sectionally-validated* earnings signal. Any entry gate beyond "up-revision" is a new
key that the anti-mining clause forbids without its own validation, and B specifically
rides a key (magnitude) the data already showed is flat.

*Entry mechanics inherited from signal 1 (not re-decided):* after-close (≥15:00) shifts
to next session; fill at the open of the tradable entry day (two-bar rule); calendar =
TOPIX dates.

---

## 2. Sign set — *single sign, or confirmation?*

| Opt | Sign set | Trade-off vs thesis |
|---|---|---|
| **A (rec)** | **Single sign**: up-revision alone. | Matches what was validated cross-sectionally. Keeps the sleeve on a *different axis* from confluence (earnings, not price/ichimoku) — the source of the independence the thesis is buying. |
| B | Up-revision **+ ≥2 confluence price signs** (the N≥3 inclusion finding). | That finding (commit 0ef36bb) was the confluence-harvest path, **rejected on the fill-order null**, and was per-trade not portfolio. Re-adding price signs reintroduces the very price-momentum axis we are diversifying away from, and adds the confluence gate's parameters. |

**Recommend A.** The independence guardrail (§5, β-stripped corr < 0.5) is *achievable*
precisely because the sleeve fires on earnings events, not price structure. Bolting
price-confirmation back on would couple it to confluence and defeat the diversification
claim. Single sign is also the only set whose selection alpha book-3 benchmark is clean.

---

## 3. Exit rule + hold horizon — *the choice that defines book 3*

| Opt | Exit | Trade-off vs thesis |
|---|---|---|
| **A (rec)** | **Fixed time-stop at 60 bars** (the validated drift horizon). | The signal IS a 60-bar β-stripped CAR. Parameter-free (horizon inherited, not fitted). Makes book 3 *exact*: each name → β·index over a **deterministic** 60-bar window. H=20 reported as a robustness column (gate 6 confirmed H20 agrees, +0.68%). |
| B | ZsTpSl(2,2,0.3) (confluence's rule). | TP would **cut the drift short** (the drift is the edge); SL interacts with beta≈1 market noise. Bands were fitted to confluence signs, never to PEAD. Makes the hold window *endogenous* → book 3's index leg needs a matching variable window (messier, weaker test). |
| C | ADX / ATR trail. | PEAD is a fixed-horizon fundamental drift, not a measurable trend; trail exits are mis-matched to the mechanism. |

**Recommend A.** The exit should match the measurement that produced the edge. The
decisive point is the gate: a fixed 60-bar hold makes book 3 = Σ βᵢ·(index return over
nameᵢ's 60 bars) — the exact aggregate of the CARs the calibration probe already
computes. Any path-dependent exit blurs that comparison.

**Consequence to surface (see §7):** 60-bar holds + ~2 slots ⇒ low turnover ⇒ a thin
realized bet count. This is the central power tension, addressed honestly below.

### 3a. TP/SL treatment + the registration seam (was an open gap)

CLAUDE.md states new positions get `ZsTpSl(2.0, 2.0, 0.3)` levels at registration. That
is a **UI default, not a universal rule:** `register_position` takes `tp_price/sl_price`
as optional params (default `None`) and stores what's passed; `compute_exit_levels`
(the ZsTpSl call) is invoked only by the two-step `order_position`/`enter_position` UI
flow. And **live TP/SL are advisory previews — there is no auto-execution** (manual
trading; auto-order prohibited). So for the sleeve:

- **Backtest / §4 null (decides deploy):** exit is a *simulator parameter*. Time-stop
  means `TimeStop(60)`; ZsTpSl is never constructed. No seam.
- **Live (manual):** sleeve registrations pass `tp_price=None, sl_price=None` (skip
  `compute_exit_levels`) — a small sleeve-aware branch, overlapping the parked
  Order/Entry/Cancel plan. Build-time, *after* the null passes; not a pre-reg blocker.

**TP vs SL are not symmetric for PEAD:**
- **TP: none, non-negotiable** — any take-profit truncates the 60-bar drift, which *is*
  the edge.
- **SL: a *price-level* stop is actively wrong here.** On a beta≈1 name it fires mostly
  on **market** moves → "sell when the index dips" = the **pro-cyclical timing confound
  §4 strips out**, and it mis-aligns book 3 (the index leg has no stop). If single-name
  disaster protection is wanted (real PEAD failure: a later profit-warning *reverses* the
  up-revision), the only coherent form is a **catastrophic, idiosyncratic (β-stripped /
  alpha-based) stop** — exit only when the stock underperforms β·index by a large
  pre-set margin. That keeps the accepted market exposure but is a **new pre-committed
  parameter** (anti-mining cost) and slightly complicates book 3.

**Recommend: pure 60-bar time-stop, no TP, no SL** (risk control = small sizing +
diversification, not a price stop). **Settled with data, not intuition — probe RAN
2026-05-27** (`src/analysis/pead_sleeve_alpha_stop_probe.py`, n=1,792 cohort up-events):

- Baseline pure time-stop: mean final alpha **+1.91%**, worst-decile **−17.89%**; alpha
  paths dip transiently hard even on winners (median trough −6%, p10 −15.7%).
- Catastrophic alpha-stop sweep θ∈{−8…−20%}: **Δ mean alpha < 0 at EVERY θ** (−0.68% at
  −8% … −0.14% at −20%) and **whipsaw rate ~50% at every θ** — half the stopped names
  recover above the stop by bar 60. The pre-stated rule (Δmeanα>0 AND whip% not
  dominating) **fails** → **no SL.** The stop only compresses the worst decile (+8.7pp at
  −8%) at the cost of mean-alpha leakage; the tiny sizing already owns that tail.
- Mechanism: PEAD drift accrues lumpily and recovers through transient idiosyncratic
  dips, so a stop whipsaws. Idiosyncratic (β-stripped) stop → a negative result is a
  clean "no stop needed", not the market-timing confound. **Exit is LOCKED to pure
  TimeStop(60).**

---

## 4. Slot-fill mechanics — *who fills the slot when more up-revisions fire than slots?*

This is where the sleeve's *selection* lives (and what the §4 gate tests). Cohort
up-revisions ≈ **200/yr** but ~2 slots take ~8/yr ⇒ ~96% skipped ⇒ **real contention**
(unlike confluence's 36/yr — this is the good news: a selection rule *can* bite here).

| Opt | Fill rule | Trade-off vs thesis |
|---|---|---|
| **A (rec)** | **Diversification-priority**: among same-day contenders, fill to minimize pairwise correlation / spread across sectors & reporting days. Pick by **correlation, not predicted return**. | Consistent with the project's root doctrine (selection-for-return is unsupported at this n) and the high-corr rule. The §4 null still feeds **paired fill-order shuffles**, so residual ordering luck is averaged out. |
| B | Surprise-magnitude priority (largest ΔFEPS/price first). | Selection-for-return on a **flat key** (§1 opt B) — the score-booster trap restated. |
| C | First-disclosed / pure fill-order. | Not a selection rule — that's the null's baseline, not a design. |

**Recommend A.** Diversification-priority + a within-sleeve concurrency cap of 2 (= slot
count). Because a 60-bar position from reporting-window N is often still open when
window N+1 fires, the two slots genuinely contend across windows — that contention is
what makes the selection-alpha test meaningful rather than "the sleeve just takes
everything (pure beta-to-signal)."

---

## 5. High-corr / concurrency handling — *the binding clustering risk*

| Opt | Handling | Trade-off vs thesis |
|---|---|---|
| **A (rec)** | Two layers: **(design)** ≤1 high-corr name + sector/reporting-day spread within the 2 slots (CLAUDE.md doctrine); **(null)** block-bootstrap **by reporting window**, so significance is not overstated by earnings clustering. | Directly answers thesis-objection 2 (JP earnings = ~4 windows/yr = false time-diversification). Design-time diversification cuts realized risk; null-time clustering-aware resampling prevents re-certifying regime-timing luck. |
| B | Ignore — sleeve too small to matter. | This is exactly the failure mode the thesis pre-registered against. Rejected. |

**Recommend A.** Note: with beta≈1, two PEAD names with high mutual ρ are *one* logical
bet — the same lesson confluence applies, now on a higher-beta book.

---

## 6. Universe — *cohort, or expand?*

| Opt | Universe | Trade-off vs thesis |
|---|---|---|
| **A (rec)** | **N225 cohort** (225 `ohlcv_1d` codes). | This is the gate-7 BINDING-validated set (+2.51%). Deployable now; data exists; no rebuild. |
| B | Expand (TOPIX / 단元株 affordable tier). | Higher contention & more candidates, but requires the **Stage-0 menu-width probe → pipeline rebuild → re-validation** — a *separate track* per the thesis and memory. Not ready; would delay the sleeve indefinitely. |

**Recommend A** for the first sleeve. Universe expansion is the parallel unblocker
(pending Stage-0 probe); the sleeve does **not** wait on it. If the cohort sleeve fails
the §4 null *only on power* (CI wide but point estimate positive), that is itself
evidence for the expansion track — but per the falsifier, a non-detection is a reject
for *this* design.

---

## 7. Power budget (the honest tension — read before selecting)

Realized distinct bets/yr ≈ `slots × (≈250 trading bars ÷ hold_bars)`:

| slots | hold | turns/yr | bets/yr | over 9 FYs | reporting-window blocks |
|---|---|---|---|---|---|
| 2 | 60 | ~4.2 | **~8** | ~75 | ~4/yr → ~36 blocks |
| 2 | 20 | ~12.5 | ~25 | ~225 | ~4/yr → ~36 blocks |
| 3 | 60 | ~4.2 | ~13 | ~115 | ~4/yr → ~36 blocks |

- **Contention is high** (~200 candidates, ~8 taken) → selection *can* matter. Good.
- **Realized sample is thin and clustered** → the clustering-aware null may simply lack
  power at 2 slots × 60 bars. This is **accepted, not hidden**: the thesis chose *no
  pre-committed effect size* exactly so the machinery decides detectability. A wide CI
  that straddles 0 is a pre-registered REJECT for this design — and a signpost toward
  more slots (T2/capital) or universe expansion, each needing its own pre-reg.
- **The horizon trade-off is real:** H=20 (opt 3-B) triples the bet count but on a
  weaker per-event edge (+0.68% vs +1.52%) and a slightly noisier drift. H=60 is the
  validated, parameter-free choice; do not switch to H=20 *to buy power* — that's
  metric/parameter shopping. If power is the worry, the legitimate lever is **slots
  (capital), not horizon**.

---

## 8. Recommended design (one line per axis) + what it locks for the null

| Axis | Recommended | Locks for §4 null |
|---|---|---|
| Candidate pool | Up-revision (ΔFEPS/price > 0), N225 cohort, signal-1 entry | event set = cohort up-revisions |
| Sign set | Single sign | independence axis intact |
| Exit / horizon | Fixed 60-bar time-stop, **no TP/SL** (alpha-stop probe RAN → rejected at every θ; LOCKED) | book 3 = β·index over deterministic 60-bar windows |
| Slot fill | Diversification-priority, cap 2 | paired fill-order shuffles in the null |
| Concurrency | ≤1 high-corr + sector/window spread | + block-bootstrap by reporting window |
| Universe | N225 cohort (no expansion) | ~75 trades / ~36 blocks over 9 FYs |
| Capital | ¥0.6M / ~2 slots, equal-yen integer lots | budget-constrained book (sizing util) |

This design makes the binding gate a clean question: **did a diversification-picked
basket of cohort up-revision longs, held 60 bars, beat β·index over identical windows,
net of the displaced confluence capital — surviving block-bootstrap by earnings
window?** No timing tilt is credited; no fitted parameter is introduced.

---

## 9. Open decisions for the operator (select to proceed to pre-reg)

1. **Confirm the recommended design** (§8), or override any axis.
2. **Slots at T1: 2 (¥0.6M) as thesis, or 3?** 3 slots ~doubles bets/yr (power) but
   takes ~¥0.9M from confluence — a bigger displacement bet before the edge is proven.
3. **Power stance:** accept the ~75-trade / 36-block reality and let the null rule (rec),
   or treat sub-power as a reason to *defer the sleeve until universe expansion* and run
   the Stage-0 menu-width probe first.

Once selected, I'll write the pre-registration (exact null mechanics, K, seed protocol,
book-3 construction from the existing CAR records, and the build plan reusing
`pead_forecast_revision` pure functions + the `confluence_benchmark` capital-aware book).
