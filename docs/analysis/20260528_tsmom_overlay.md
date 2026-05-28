# Single-Index TSMOM Overlay — Result

**Date:** 2026-05-28 · **Status:** discovery probe complete, no commitment · **Type:** read-only analysis
**Scripts:** `src/analysis/tsmom_index_probe.py` (standalone overlay),
`src/analysis/confluence_tsmom_gate_probe.py` (confluence entry-filter variant)

## Verdict

Single-index **Time-Series Momentum (long/flat, 12-month lookback, monthly)** is a **genuine defensive
overlay** — over 41 years of ^N225 it roughly **halves max drawdown** (−46% vs −81%) and lifts Sharpe
(0.45 vs 0.30), and the result is **robust across lookbacks**. It is the **first deployable lever found
in the 2026-05 direction reassessment**. But it is **tail-insurance, not alpha**: its entire edge comes
from sitting out sustained bear markets, and **in the recent decade (the regime we would deploy into) it
underperformed buy-and-hold on every metric.** The **short leg is dead** (use long/flat only). Using
TSMOM as a **confluence entry filter is a clear REJECT** — it fights the strategy's regime-inverse alpha.

The deploy question is therefore *"do we want drawdown insurance on the ¥2M (accepting a bull-market
drag)?"* — not *"is this alpha?"*

---

## 1. Hypothesis & method

TSMOM (Moskowitz–Ooi–Pedersen 2012): hold the index when its trailing return is positive, else step
aside. We test the **single-index overlay** form — breadth-immune (one position), trivial to run
manually, uncorrelated to single-name stock-picking — as a candidate risk overlay for the ¥2M book.

- **Signal:** at each month-end, sign of the trailing **L-month** (L×21-bar) index return. Positive →
  **long** (weight 1) for next month; non-positive → **flat** (0) [long/flat] or **short** (−1)
  [long/short]. Position held one month, then re-evaluated. No look-ahead (signal at end of month *t*
  sets the position held through month *t+1*).
- **Canonical lookback:** L=12 (MOP standard), pre-registered; L∈{3,6,9} shown for robustness.
- **Cost:** 30 bps per position switch (one-way notional traded); 1.1%/yr borrow while short.
- **Benchmark:** buy-and-hold the index over the same (lookback-trimmed) window.
- **Metrics:** CAGR, annualized vol, Sharpe, **max drawdown**, switch count, crisis-window behavior.

## 2. Data

| series | source | span | note |
|---|---|---|---|
| TOPIX | `jq_topix` (in-DB) | 2016-05 → 2026-05 (10 yr) | the recent decade we'd actually trade; **too short** for a 12-mo rule (~10 independent signals) → indicative only |
| Nikkei 225 (^N225) | yfinance (monthly) | 1985-01 → 2026-05 (41 yr) | **binding evidence** — spans real bears (GFC, 2011, 2018, 2020) |

A price index excludes dividends → buy-hold's edge is slightly understated, but TSMOM sits in cash
part-time so the omission cuts both books (≈ wash). MOP's headline Sharpe 1.31 is a **58-instrument
diversified futures** program; a single equity index is far more modest (~0.3–0.5) and regime-dependent.

## 3. Result — standalone overlay

### Long history ^N225 (1985–2026, 41 yr) — binding

| book | CAGR | vol | Sharpe | maxDD | switches |
|---|---|---|---|---|---|
| buy & hold | 4.1% | 20.4% | 0.30 | **−80.6%** | — |
| **TSMOM long/flat, L=12** | 5.5% | 14.0% | **0.45** | **−45.6%** | 49 (~1.2/yr) |
| TSMOM long/short, L=12 | 4.1% | 20.4% | 0.30 | −60.9% | 49 |

**Lookback robustness (long/flat):** L=3 → Sharpe 0.36 / maxDD −62%; L=6 → 0.36 / −45%; L=9 → 0.34 /
−50%; L=12 → 0.45 / −46%. **All beat buy-hold on both Sharpe and drawdown** → not a lookback cherry-pick.

**Short leg is dead:** long/short = buy-hold Sharpe (0.30) with a *worse* drawdown (−61%) — shorting a
positively-drifting index gets run over in rallies. **Use long/flat only.**

### Recent decade TOPIX (2016–2026) — the deployment regime

| book | CAGR | vol | Sharpe | maxDD |
|---|---|---|---|---|
| buy & hold | 10.6% | 14.2% | **0.79** | **−23.6%** |
| TSMOM long/flat, L=12 | 5.2% | 11.5% | 0.50 | −27.0% |

**TSMOM underperformed buy-hold on every metric** in the recent decade (lower CAGR, lower Sharpe, and a
*larger* drawdown). No sustained bear to dodge — just a strong bull plus sharp V-recoveries (2020, 2025)
and chop (2022), exactly where timing costs.

### Crisis behavior (the crisis-alpha case) — TSMOM long/flat vs buy-hold, cumulative in window

| window (^N225 long series) | buy-hold | TSMOM | protection |
|---|---|---|---|
| GFC 2008 | −43.4% | 0.0% (fully out) | **+43.4pp** |
| EU / quake 2011 | −13.5% | −12.2% | +1.4pp |
| 2015–16 China | −22.1% | −15.1% | +7.0pp |
| 2018 Q4 | −17.0% | −9.4% | +7.6pp |
| COVID 2020 | −20.0% | −10.9% | +9.1pp |
| 2022 chop | −9.4% | −19.4% | **−10.0pp** |
| 2025 drawdown | +26.2% | +15.1% | **−11.1pp** |

TSMOM protects strongly in **sustained** bears (GFC, COVID, 2015–16, 2018 Q4) and **whipsaws** in chop /
V-recoveries (2022, 2025). Net over 41 years it wins (drawdown halved), but much of the long-run edge is
"it would have saved you from the 1990s lost decades" (buy-hold maxDD −81%) — which only repeats if a
prolonged Japan bear recurs.

## 4. Result — TSMOM as a Confluence entry filter (REJECT)

Gating `ConfluenceSignStrategy` entries by TOPIX 12-mo-on (open new positions only when on; existing
positions run to their normal exits), capital-aware ¥2M 6-slot book:

| | Sharpe | total | maxDD |
|---|---|---|---|
| baseline | **+0.88** | **+155%** | **−21.8%** |
| TSMOM-gated | +0.68 | +81% | −27.0% |
| Δ | **−0.20** | **−74pp** | **−5.2pp (worse)** |

Worse on **every** axis — including drawdown, which the gate was supposed to cut. Helped only 2/8 FYs.
**FY2020 (COVID) ΔSharpe −1.33** (the gate skipped the post-crash recovery entries; +38% → +10%, drawdown
doubled); **FY2019 −0.93**; **FY2024 (the documented good-in-bearish year) Δ 0.00** (TSMOM was 98%-on, so
the gate never bit). Mechanism: ConfluenceSignStrategy's alpha is **regime-inverse** (best in/after
bearish regimes — see `project_confluence_market_neutral`, `project_confluence_fy_attribution`), so a
12-mo-downtrend entry gate skips its best entries. Standalone TSMOM cuts the *index's* drawdown because
the index is a passive beta exposure; gating *confluence* fights the strategy's own regime edge. This is
the previously-refuted **"go cash when the index is weak"** rule, confirmed directly. Do **not** re-propose
index-trend entry gates for confluence.

## 5. Deployment considerations (if pursued)

- **Form:** long/flat, L=12, monthly, on a TOPIX ETF (e.g. 1306.T) — 1 position, ~1 switch/yr, trivial
  manual execution via the opening auction.
- **It is insurance, not return.** Expect it to **lag in bull/chop** (the recent-decade result) and pay
  only in a **sustained** drawdown. It competes for the ¥2M capital with the stock-picking book.
- **Sizing as an overlay**, not a standalone sleeve: e.g. a fixed defensive allocation toggled by the
  signal, sized so the bull-market drag is tolerable for the drawdown protection bought.
- **Binding pre-reg test would be drawdown reduction, not Sharpe** (Sharpe ≈ buy-hold is expected; the
  value is in the tail), with a frozen rule + held-out split and the bull-market drag accepted up front.

## 6. Caveats

- The in-DB decade (~10 signals) is too short to conclude; the ^N225 long history is the binding
  evidence, but it is dominated by the post-bubble lost decades — a structurally different regime from
  post-2013 corporate-reform Japan.
- Price index (no dividends); cost model is per-switch + borrow, not modeling tracking error / ETF
  spread; monthly close-to-close.
- Long/short leg rejected (short leg negative-EV on a drifting index).

## 7. Reproduce

```bash
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.tsmom_index_probe
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_tsmom_gate_probe
```
The ^N225 long-history arm fetches monthly data via yfinance (graceful fallback to the in-DB TOPIX decade
if offline). See also the territory map `docs/analysis/20260528_new_directions.md` (§3).
