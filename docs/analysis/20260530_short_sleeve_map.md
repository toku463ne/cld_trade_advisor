# Short-Selling Sleeve — Territory Map (2026-05-30)

Map only — no probe run, nothing committed to the book. Purpose: separate what is
genuinely untried from what is already refuted, and rank the untried by prior so the
next pre-reg (if any) starts from the strongest candidate. Short-selling is available
(Rakuten 制度信用, borrow ~1.1%/yr) — see `project_program_direction_2026`.

## Framing — the short leg has TWO roles, and they have opposite priors

**Role A — short leg of a cross-sectional factor (ALPHA).** Long cheap / short expensive,
long low-vol / short high-vol, etc. The short leg *adds* the part of a factor premium that
a long-only book structurally cannot reach. This is where the live questions are.

**Role B — short the index as a BETA HEDGE / tail tool (RISK).** Cancel the confluence
book's ~62% beta to cut its (beta-driven) −21.8% drawdown. **Pre-refuted — do not re-open:**
- The market-neutral diagnostic (`project_confluence_market_neutral`) is binding: stripping
  beta from confluence leaves an alpha that is **insignificant (t=1.39) and regime-INVERSE**.
  A short hedge is a beta-strip → it throws away 62% of return for a non-edge. MN-Confluence
  was already answered NO.
- TSMOM short-index leg is **dead** (`tsmom_index_probe`: L/S Sharpe 0.30 = buy-hold, maxDD
  worse — run over in rallies). The only working index-timing tool is TSMOM **long/flat**
  (tail insurance, no borrow), and even that underperforms in the 2016–26 regime.
- ∴ the sleeve is really about **Role A only**. Any "short the index to feel safer" proposal
  is the refuted "go cash when the market is weak" trap in a costlier wrapper.

## The binding wall (inherited from MN-PEAD, `project_jquants_pead_universe`)

MN-PEAD was the top pick and was **REFUTED as a deployable book**. Its three failure modes
are the gates every Role-A candidate must clear:

1. **Gross-spread Sharpe must be high enough to survive realization.** A real cross-sectional
   spread, realized as a continuous overlap-hold book, *sheds* Sharpe (PEAD K=∞ gross only
   +0.44 on the 225). Need a high standalone L/S Sharpe to have headroom.
2. **Turnover × cost must not flip the sign.** MN **doubles** turnover; ~6%/yr round-trip cost
   flipped PEAD-MN negative. Event/PEAD-family signals are high-turnover → killed. **Low-turnover
   factors (value, low-vol) are the structural escape.**
3. **Breadth + borrowability at ¥2M.** IR ≈ IC·√breadth; a MN book needs ~6 names **per side**.
   PEAD's short leg **leaked into mid/small-caps that are unborrowable** in 制度信用. The short
   leg must live in **borrowable (large-cap) names**.

Binding test for any candidate = the `mn_pead_feasibility_probe` template: K=6/side,
**point-in-time** universe (no ever-qualifies look-ahead — that artifact already burned the
value long-tilt, `value_turnover_tier_probe`), net of 30bps + 1.1%/yr borrow, Sharpe CI must
exclude 0, **and the short names must be borrowable**.

## Ranked candidates (untried → refuted)

### 1. MN-Value (long cheap / short expensive) — FRONT-RUNNER, untried
The single explicitly-named "only untried value angle." Why it has the best prior of any
short candidate:
- **The L/S premium is already CONFIRMED in-data** (not assumed): value decile L/S Sharpe
  **0.84, CAGR +12.2%, t=2.46** with dividends (`value_tilt_discovery_probe` Gate A). Nearly
  **2× the PEAD gross spread** → clears wall #1 with headroom PEAD never had.
- **Value is LOW-turnover** (monthly/quarterly rebalance) vs PEAD's event-driven churn → the
  ~6%/yr cost that flipped PEAD-MN is **much milder** → best shot at wall #2.
- **The short leg is borrowable.** Value-short = low-B/M / high-E-multiple = expensive *large-cap
  growth* names → in the borrowable large-cap tier, **unlike PEAD-short which leaked to
  unborrowable small-caps** (wall #3). This is the key structural advantage and the first thing
  to verify.
- The reason value FAILED deployment was *exactly* that the long-only slice can't capture a
  cross-sectional premium (`project_program_direction_2026` dir 2) — i.e. it **needs the short
  leg**. The short sleeve is the missing half, not a new bet.

**Binding probe:** clone `mn_pead_feasibility_probe` with the value signal (equity-derived B/M
+ E/P, point-in-time, total-return). K=6/side, net 30bps + 1.1% borrow. Gates: net Sharpe CI
> 0; short names in the borrowable large-cap set; survives FY2025 (the recent value drawdown —
the live OOS risk). **Prior: best of the sleeve, but still a coin-flip — the breadth wall (6/side)
is structural and may repeat the MN-PEAD reject even with 2× Sharpe + lower cost.**

### 2. MN-low-vol / BAB (long low-β / short high-β) — untried, second prior
- Low-vol/BAB is a **documented Japan winner** (Blitz–van Vliet) and is **beta-neutral by
  construction** (the cleanest hedge-free MN structure).
- **Not yet confirmed in-data** (unlike value) → needs a discovery step first (vol-decile L/S
  significance, dividends/survivorship-robust) BEFORE feasibility. Lower prior than value purely
  because value is already validated here and BAB is not.
- **Risk:** the short leg = high-vol names → borrow may be costlier/harder, and high-vol shorts
  get run over in bull legs (the TSMOM-short failure mode). Beta-neutrality mitigates but does
  not remove this.
- Naturally **negatively correlated with value's exposures** → if both survive, a value+low-vol
  MN combo could add the diversification that value+momentum did NOT (v+m corr only +0.17 here).

### 3. MN-Quality (long high-ROE / short junk) — untried, lowest of the live three
- Data available (profit/equity ROE, equity-to-asset). Quality/profitability premium exists in
  JP but is **typically value-correlated** → likely subsumed by candidate #1, low marginal value.
- Park behind value; only revisit if value survives and a *diversifying* second factor is wanted
  and low-vol (#2) fails.

### 4. Short-index beta-hedge / tail overlay — CLOSED (Role B, pre-refuted)
See framing above. MN-Confluence = NO (t=1.39 regime-inverse alpha); TSMOM-short dead. Tail
protection is better served by TSMOM **long/flat** (no borrow, no bleed in chop). Do not propose.

### 5. Momentum short (short losers) — DEAD
JP cross-sectional momentum **fails** (Asness; replicated in-data, L/S −0.04). Shorting the
losers of a failed factor has no edge and gets run over in recoveries (FY2020 archetype). Closed.

## Cross-cutting constraints (carry into any pre-reg)

- **Breadth is the falsifier.** Test ~6 names/side net of cost, NOT an institutional-breadth
  Sharpe. MN-PEAD proved a real premium can die purely on 6/side breadth.
- **Borrowability gate is first-class.** Confirm the short names are 制度信用-borrowable
  (large-cap). An unborrowable short leg = no book. This is candidate #1's whole edge over PEAD.
- **Point-in-time universe only.** The ever-qualifies "frozen tier" is look-ahead for a *selection*
  universe (it inflated the value long-tilt to a phantom +4.2%/yr). Use as-of membership.
- **Cost stack:** 30bps round-trip × (MN doubles turnover) + 1.1%/yr borrow. Low-turnover signals
  only — this is why value/low-vol are live and PEAD/event are not.
- **Survivorship hits the SHORT leg hardest** — delisted names are disproportionately the *winning*
  shorts that vanish from the data → MN backtests overstate the short-leg P&L. Discount magnitude.
- **FY2025 was a value drawdown** → deploying value-MN now = post-rotation entry; OOS-FY2025
  survival is a hard gate, not a nice-to-have.

## Recommended first probe (if the operator wants to proceed)

**MN-Value feasibility** — the `mn_pead_feasibility_probe` template with the validated value
signal. One read-only probe answers the whole sleeve question: if value (2× PEAD's gross Sharpe,
~⅕ the turnover, borrowable short leg) **still** can't clear 6/side net of cost, then the breadth
wall is truly universal and the short sleeve is closed at ¥2M — and low-vol/quality won't rescue
it. If it clears, it is the first market-neutral book with a real shot, and low-vol becomes the
diversifying follow-up.

## RESULT (2026-05-30) — MN-Value feasibility = REJECT, central hypothesis FALSIFIED

`src/analysis/mn_value_feasibility_probe.py` (read-only). The map's whole case for MN-Value
over MN-PEAD was "value-short = expensive **large-cap** growth → borrowable." **The data
falsifies both halves — borrowability and alpha are anti-located:**

| universe | decile L/S ceiling | K=6 net Sharpe | short-Borrow% | per-FY | time-CI / P(>0) |
|---|---|---|---|---|---|
| **225 large-cap (borrowable)** | **+0.04 (t=0.11)** | **−0.33** (gross −0.23) | 100% | 4/9, FY20 −52% / FY18 −35% | [−1.14,+0.41] / 0.21 |
| **mid-cap tier (premium lives here)** | +0.67 (t=1.98) | +0.39 (gross +0.50) | **0%** | 5/9, all on FY24 +83% | [−0.11,+1.01] / 0.93 |
| **wide, short-borrowable-only (realizable)** | — | — | — | **can't be formed** ("too few") | — |

1. **In the borrowable large-cap (225) universe there is NO value premium to harvest** — decile
   ceiling Sharpe +0.04 (t=0.11); the deployable K=6 MN book is *negative* (net −0.33), with
   FY2020 −52% / FY2018 −35%. Value does not live in large-caps.
2. **The premium lives in the mid-cap tier** (decile +0.67, K=6 net +0.39) — but there the **short
   leg is 0% borrowable** (expensive names = mid-cap growth, not 貸借銘柄). The realizable
   borrowable-short book **cannot even be formed**.
3. Even ignoring borrowability, the mid-cap +0.39 **fails the binding gate anyway**: time-CI
   [−0.11, +1.01] straddles 0 (P=0.93 < 0.95) and leans entirely on FY2024 (+83%); OOS FY2025
   −16% and FY2026 −13% are both negative (the post-rotation entry risk the map flagged, realized).

**MN-Value dies the same structural way MN-PEAD did: the premium lives where you can't borrow the
short leg; where you can borrow, there's no premium.** The binding wall is **borrowability**, not
just breadth — and it is anti-correlated with where cross-sectional alpha lives (mid/small-cap).

**Implication for the rest of the sleeve.** This borrowability/alpha anti-location is *structural*
and size-driven, so it likely generalizes to MN-Quality (size-tilted, value-correlated) → treat as
**closed**. The one candidate it need NOT kill is **MN-low-vol/BAB**, whose short leg is *high-β
names* (not a size bucket) and so could include borrowable large-caps — but its prior is now sharply
lower, and it is not yet even confirmed in-data. **Net: the short sleeve is effectively closed; the
sole remaining thread is a low-vol/BAB discovery + borrowable-short check, low prior.**

## RESULT (2026-05-30) — MN-low-vol / BAB feasibility = REJECT. Short sleeve CLOSED.

`src/analysis/mn_lowvol_feasibility_probe.py` (same engine, price-derived signal). Both the BAB
(β-ranked) and LOW-VOL (σ-ranked) long-low/short-high books fail, and the "high-β short leg might
be borrowable" hypothesis is **falsified**:

| signal / universe | decile ceiling | K=6 net Sharpe | short-Borrow% | resid β | time-CI P(>0) |
|---|---|---|---|---|---|
| BAB · 225 borrowable | −0.56 (t−1.64) | −0.57 | 100% | −1.40 | 0.009 |
| BAB · wide tier | −0.15 | −0.67 | ~10% | −1.32 | — |
| **low-vol · 225 borrowable** | **−0.93 (t−2.72)** | −1.42 | 100% | −1.30 | 0.000 |
| low-vol · wide tier | +0.14 (t0.42) | +0.06 | **0%** | −0.4…−0.8 | — |

- **In the borrowable large-cap universe both signals are NEGATIVE** — low-vol significantly so
  (decile −0.93, t=−2.72): high-vol large-caps *out*performed low-vol over FY2018–26 (post-COVID
  high-β rally), the academic prior *inverts* here. 1/9 positive FYs, FY2021 −64% / FY2026 −77%.
- **The only positive sliver** (wide-tier low-vol, K=6 net **+0.06**) is economically nil,
  statistically insignificant (decile t=0.42), partly embedded short-β (resid β −0.4…−0.8), AND its
  short leg is **0% borrowable** — the exact MN-Value wall.
- The map's open question — "high-β/high-vol short leg isn't a size bucket, could be borrowable" —
  is answered NO: short-Borrow% = 0% on the wide tier (high-vol names are also mid/small-cap).

**Conclusion — the entire short-selling sleeve is CLOSED at ¥2M.** Role B (beta-hedge) was
pre-refuted; Role A (factor short leg) is now refuted across value, quality (by generalization),
BAB, and low-vol. The structural law for this account: **borrowability is anti-located with
cross-sectional alpha — the premia live in mid/small-caps whose short legs aren't 貸借銘柄, and the
borrowable large-cap universe carries no (or inverted) premium.** No factor short leg is harvestable
at ¥2M retail borrow. (Caveat: naive dollar-neutral books carry large negative resid β; a proper
beta-neutralized BAB needs leverage infeasible at ¥2M — but the balanced cohort *decile* is already
significantly negative, so beta-neutralizing does not rescue it.)

## What would make this map wrong
- Value-MN nets a Sharpe-CI > 0 at 6/side with a borrowable short leg surviving FY2025 → the
  breadth wall is **not** universal; the sleeve opens.
- Borrow turns out available on mid/small-caps (changes the MN-PEAD wall-#3 verdict too).
- A genuinely low-turnover, **non-value-correlated** factor (low-vol) is confirmed in-data →
  a two-factor MN combo could beat the breadth wall via diversification where one factor can't.
