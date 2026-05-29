# Pre-registration — Confluence + uncorrelated TSMOM overlay blend (backlog item 6)

**Date:** 2026-05-29 · **Type:** structural diversification lever · **Status:** frozen before run
**Script:** `src/analysis/confluence_overlay_blend_null.py`
**Binding null:** paired fill-order null on the capital-aware 6-slot book (CLAUDE.md discipline).

## Hypothesis

`project_confluence_buyhold_win`: the 6-slot confluence book only **ties** the equal-weight universe;
its edge over the index is a drawdown cut, not alpha (~62% beta). The standing thesis (backlog item 6)
is that the biggest risk-adjusted gain available is **pairing the book with an uncorrelated stream**, not
optimizing the book itself. The candidate uncorrelated stream is the **single-index TSMOM long/flat,
L=12, monthly defensive overlay** (`docs/analysis/20260528_tsmom_overlay.md`): breadth-immune, ~1
switch/yr, and — over the 41-yr ^N225 history — roughly halves the index's max drawdown by sitting out
sustained bears.

**This is NOT the rejected TSMOM entry-gate.** The gate (`confluence_tsmom_gate_probe`, REJECT) *skipped
confluence entries* when the index trend was down, fighting the strategy's regime-inverse alpha. This
probe instead runs the overlay as a **parallel capital sleeve** blended at the portfolio level — it never
changes which names confluence buys, only splits the ¥2M between the stock book and a timed index sleeve.

## Method

Reuse the `confluence_voltarget_null` machinery: reconstruct the production 6-slot confluence book
(`_MAX_LOW_CORR=5`), FY2018–2025, capital-aware equal-weight daily-return series `r_c`. For each of
K=200 fill-order shuffles the confluence fills (and thus `r_c[k]`) vary; the **overlay is deterministic**.

**Overlay (`r_o`, computed once):** ^N225 daily via yfinance (clean multi-decade 12-mo lookback;
graceful fallback to in-DB ^N225 daily). Monthly long/flat L=12 signal exactly as `tsmom_index_probe`
(`_tsmom_book`): at each month-end, sign of trailing-12-month return → long (1) / flat (0) for the next
month; 30 bps per switch. `r_o[d]` = pos(month of d) × index daily return − switch cost. Flat = cash
(0 return, no interest credited — conservative).

**Blend (no leverage, fixed strategic split):**
`r_blend[d] = (1−f)·r_c[d] + f·r_o[d]`. A fixed fraction `f` of the ¥2M is permanently allocated to the
TSMOM index sleeve, `(1−f)` to the stock book; total gross ≤ 1 (overlay holds cash when flat). `f=0` is
the pure-confluence baseline.

- **PRIMARY allocation:** `f = 0.30`. Carries the verdict.
- **Dose-response (report only):** `f ∈ {0.20, 0.30, 0.50}`.

Paired Δ per shuffle = blend(`f`) − confluence-only, same fills each draw. Stitched FY2018–2025 Sharpe,
maxDD, return; FY2025 OOS reported separately.

## Frozen gate

This is judged as a **diversification / drawdown lever** (the overlay is tail-insurance, not alpha —
expect a bull/chop drag), mirroring the framing under which backlog item 2 was accepted.

1. **PRIMARY (Sharpe, standing project bar):** `f=0.30` blend vs confluence-only —
   **P(Δ Sharpe > 0) ≥ 0.95 AND 95% CI-lo > 0** → ACCEPT (risk-adjusted return improved).
2. **SECONDARY drawdown-lever escape** (if PRIMARY Sharpe gate fails): ACCEPT-as-drawdown-lever iff
   **ALL** of — mean Δ maxDD ≥ **+2.0pp** shallower AND **P(Δ maxDD shallower) ≥ 0.95** AND Δ Sharpe
   **CI-lo ≥ −0.10** (bull-drag tolerated, no Sharpe collapse) AND OOS FY2025 Δ Sharpe ≥ −0.30.
3. **Else REJECT.**

## Crux diagnostic (reported regardless of verdict)

- **Pooled ρ(r_c, r_o)** over FY2018–2025 — the whole thesis is "uncorrelated stream." If ρ is materially
  positive, the overlay is *not* a diversifier in this window (it is long-equity beta when on), and any
  blend just dilutes toward a lower-Sharpe asset.
- **Overlay behaviour during confluence's worst-drawdown days** — does the overlay sit flat (protect) or
  ride down with the book (no protection)? Per-FY Δ maxDD breakdown.
- Overlay standalone Sharpe/maxDD/CAGR + %long, FY2018–2025.

## Prior / expected failure mode (stated up front, not used to pre-judge)

The 41-yr TSMOM edge is dominated by sustained bears (1990s lost decades, GFC) that lie **outside** the
FY2018–2025 test window. Inside the window the index bears are V-recoveries (COVID 2020) and chop (2022,
2025) — exactly TSMOM's documented **whipsaw** regimes (overlay doc §3 crisis table: −10pp / −11pp). In
both COVID and Q4-2018 (likely confluence drawdown peaks) TSMOM was *long going in* (trailing-12mo still
positive) → no protection on the sharp leg. So the realistic prior is the overlay **dilutes Sharpe and
does not cut confluence's actual drawdowns** in this window. The gate decides; this is the falsifiable bet.
