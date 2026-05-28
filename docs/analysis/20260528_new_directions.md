# Program Direction Reassessment — Map of the Territory

**Date:** 2026-05-28 · **Status:** advisory map, no commitment · **Capital:** ¥2,000,000, manual execution
**Trigger:** universe expansion REJECTED (contention-is-the-wall thesis refuted); PEAD validated but
unharvested via N225; long-only was never a deliberate constraint; **short-selling now available**
(Rakuten margin, 制度信用 borrow ~1.1%/yr).

Infrastructure assumed: J-Quants 10-yr backfill, TOPIX-wide `ohlcv_1d`, `jq_statements` fundamentals
(book value, EPS, equity, profit, sales, forecast EPS), PostgreSQL + Python, manual execution.

---

## 0. The binding constraint: BREADTH, not strategy

Almost every documented L/S equity edge (PEAD, value, momentum, BAB, quality) is a **cross-sectional
diversification play** — a *small per-name mean* realized by holding *many* names so idiosyncratic
risk averages out. Grinold's fundamental law: `IR ≈ IC × √breadth`, breadth = names × rebalances/yr.

At **¥2M / 単元株 lots / manual execution → ~6–12 names total.** This is the same wall the project
hit all month — the "capacity wall" was never specific to confluence; it is the structural reality of
small-capital manual cross-sectional investing. **Short-selling helps two ways** (removes long-only
beta dependence → true market-neutral; harvests *both* legs of a spread) **but does NOT fix breadth.**
A 6-long/6-short book carries huge idiosyncratic variance vs any factor's spread → modest, very noisy
realized Sharpe regardless of factor quality.

**Reframed question for every direction:** does it need breadth, or is the per-name edge large enough
that a few names suffice? Documented factor Sharpes (~0.4–0.8) are at *full institutional breadth*
(100s of names); realized Sharpe at ~6–12 names is lower and swings wildly year-to-year.

---

## 1. Market-neutral PEAD — long up-revision / short down-revision

**⛔ REFUTED by feasibility probe (2026-05-28) — NOT worth a pre-reg.** Was the top pick; the
breadth/cost probe killed it before any pre-reg. (Original rationale retained below for the record.)

> **PROBE RESULT** (`src/analysis/mn_pead_feasibility_probe.py`, read-only): a dollar/β-neutral
> long-up / short-down book, 60-bar hold, ≤K slots/side, 200-draw selection bootstrap + L=60 time
> block-bootstrap, on BOTH the validated 225 cohort and the wide ~2,785 tier.
> - **Machinery check PASSES:** 225-cohort (up−down) β-stripped 60-bar CAR = **+2.47%** (up +1.46% /
>   down −1.01%) — matches the validated +2.51%. The signal is real.
> - **But the deployable book FAILS on two walls, and COST is the bigger one:**
>   - **225, K=6/side:** net@30bps Sharpe **−0.12** (sel-band [−0.37,+0.15]), net −2.2%/yr, time-CI
>     P(Sharpe>0)=0.25, per-FY 3/7 positive (FY2019 −31.7%, OOS FY2025 −11.6%). K=∞ (hold all
>     1,224/829, no breadth limit) is only gross +0.44 / net −0.06 — the spread realized as a
>     continuous overlap-hold book is **intrinsically low-Sharpe** (high cross-sectional dispersion).
>     **Even at 0 transaction cost (borrow only), K=6 net = +0.15** → below the ~0.3 deploy bar.
>   - **Wide tier:** spread smaller (+1.99%); K=∞ gross +1.30 (diversification works) but net only
>     +0.32; **K=6 net −0.30** (worse than 225). Needs **K≥20/side (40 names, beyond ¥2M manual)** to
>     reach net ≈0 — and the wide short leg is largely **unborrowable** (mid/small-caps not 制度信用
>     貸借銘柄). **→ the "size-agnostic PEAD beats the breadth wall via a wider menu" hope is REFUTED.**
>   - **Cost is decisive & robust:** MN doubles turnover (both legs, quarterly full rebalance ≈4.2×/yr
>     → ~5%/yr @30bps + 1.1% borrow ≈ **6%/yr**), which alone flips the sign. **Market-neutral makes
>     PEAD WORSE than the long-only sleeve at ¥2M, not better.**
> - **VERDICT:** idiosyncratic variance AND turnover cost both swamp the +2.47% spread at deployable
>   breadth. This closes the **last** cross-sectional PEAD harvest path (6-slot reorder / standalone
>   sleeve / universe expansion / MN — all rejected). Signal 1 stays validated cross-sectionally but
>   **unharvestable at ¥2M manual**; the breadth wall is structural, not a selection-key or
>   universe-size problem.

_Original (pre-probe) rationale:_

- Better than the rejected long-only sleeve: the sleeve added beta to a 62%-beta book and caught only
  the up-leg. MN-PEAD adds **zero beta** and harvests the **full +2.51% up−down spread**. Borrow 1.1%/yr
  is cheap. **PEAD is size-agnostic** (flat size-gradient) → plausibly works on the wider universe.
- Constraint: breadth + earnings clustering (~4 windows/yr, ~3–6 names/side/window). The +2.51% is a
  mean over *thousands* of events; ~6/side is noisy.
- Realistic Sharpe: **0.4–0.8** good years, high variance, can be flat/negative for a year+. New
  harvestable piece vs the sleeve = the **down-revision short leg** (down-drift), implied by the
  validated spread but untested directionally.
- Min viable scale: market-neutral wants ~20+/side; ¥2M gives ~4–6 → *below* comfortable breadth. The
  pre-reg's binding test: does ~6/side net of cost realize the spread, or does idiosyncratic variance
  swamp it?  **→ ANSWERED: it does not (see PROBE RESULT above).**

## 2. Market-neutral Confluence — long bullish / short bearish signs

**Skip — already diagnosed.**

- `project_confluence_market_neutral.md` already showed the beta-decomposed confluence book is **62%
  beta, 38% alpha, alpha NOT significant** (mn mean_r +0.77%, t=1.39). The bullish signs work *via beta*.
- The short leg is doomed in Japan: confluence signs are momentum-flavored and **momentum is a
  near-failure in Japan** (Asness: Sharpe 0.03). Shorting bearish-momentum names where momentum
  mean-reverts → shorts lose.
- Realistic Sharpe ~0. At most a 1-hour read-only confirm with the existing market-neutral script +
  bearish fires; expected REJECT.

## 3. TSMOM (Moskowitz–Ooi–Pedersen 2012) — index overlay only

**Per-stock: no. Single-index overlay: cheap low-Sharpe diversifier worth a quick test.**

- MOP's headline **Sharpe 1.31 is the diversified 58-instrument FUTURES** version, not equities.
- Per-stock JP equity TSMOM ≈ momentum → **fails in Japan**; also breadth-bound. Skip.
- Single-index TSMOM (long TOPIX ETF when 12-m return > 0, else flat/short): **one position, monthly —
  ideal for manual.** Sharpe modest (~**0.3–0.5**), regime-dependent (rode Abenomics; whipsaws in chop).
  Uncorrelated to stock-picking → worth a quick test as a risk overlay, not a primary edge.

## 4. Quantpedia-informed — what actually fits Japan + ¥2M

- **Value (and value+momentum combined) — the documented Japan winner.** Momentum fails in Japan but
  **value works**; **value+momentum combined ≈ 0.65 Sharpe** via their −0.55 correlation (Asness). We
  have the data (P/B, E/P from `jq_statements`). Value is implementable as a **concentrated long-tilt**
  → suffers the breadth wall *less* than market-neutral. **Worth a pre-reg, co-equal with MN-PEAD.**
- **Low-volatility / BAB / Quality — works in Japan** (Blitz & van Vliet 2007); **naturally long-only,
  concentration-friendly**. Lowest-effort factor surviving small breadth. Probe-worthy.
- **Event-driven catalysts — the category that genuinely fits small capital** (large per-name edge → a
  few names suffice): index reconstitution (Nikkei 225 / TOPIX add/delete), buyback announcements, PEAD.
  Where ¥2M manual is *least* disadvantaged. Probe-worthy.

---

## The map (ranked for ¥2M + manual + short-selling)

| Direction | Fits small breadth? | Realistic Sharpe | Pre-reg? |
|---|---|---|---|
| **Value / value+momentum** (JP) | better (long-tilt) | ~0.5–0.65 documented | **Yes — front-runner** |
| **Event-driven catalysts** (index rebal, buybacks) | **best** | large per-event, lumpy | Probe-worthy |
| **Low-vol / quality long** | good | ~0.3–0.5 | Probe-worthy |
| **Index TSMOM overlay** | trivial (1 pos) | 0.3–0.5 | Cheap test, diversifier |
| **MN-PEAD** (long up / short down) | marginal | net ≤0 at 6/side | **⛔ No — REFUTED 2026-05-28** |
| **MN-Confluence** | — | ~0 | **No (already shown)** |
| **Per-stock TSMOM / pure momentum** | — | ~0 in JP | **No** |

**Recommendation (updated 2026-05-28 after the MN-PEAD probe):** the planned two-axis combo loses its
overlay leg — MN-PEAD's short-leg cost/borrow + low-Sharpe spread don't survive ¥2M breadth. The
highest-EV next move is now the **concentrated value/quality LONG tilt alone** (documented Japan edge,
breadth-*tolerant*, no short-leg drag). First pre-reg if picking one: **value or value+momentum** (NOT
MN-PEAD). The MN-PEAD result also sharpens the program prior: **the breadth wall is structural** — a
validated cross-sectional edge realized as a continuous tradeable book at ~6 names is low-Sharpe and
cost-dominated regardless of how good the per-name signal is. Favor edges that are *concentration-
friendly* (large per-name effect, few names suffice) over diversification plays that need breadth.

## Cross-cutting cautions (carry into any pre-reg)
- **Breadth is the falsifier**, not the factor. Any cross-sectional L/S pre-reg must test whether ~6–12
  names realize the documented spread net of cost — not assume the institutional-breadth Sharpe.
- **Costs at ¥2M:** borrow 1.1%/yr (shorts), spread/commission, opening-auction fills; market-neutral
  doubles turnover vs long-only.
- **Japan momentum failure** is a hard prior — reject anything that is momentum in disguise on the short
  side.
- **Overfit discipline holds:** fundamentals/factor definitions frozen before the null; held-out FYs;
  the paired-null / no-iteration methodology that governed the whole project.

## Sources
- Asness, *Momentum in Japan: The Exception That Proves the Rule* (AQR/JPM) — momentum fails in Japan,
  value works, value+momentum combined ≈ 0.65 Sharpe.
- Quantpedia: Value & Momentum across Asset Classes; Time-Series Momentum Effect (Sharpe 1.31,
  58-instrument futures, monthly); Low Volatility / Betting Against Beta; Equity Long-Short tag.
- Blitz & van Vliet (2007) — low-volatility effect in European & Japanese markets.
