# PEAD — Management-Forecast-Revision Surprise (Pre-Registration)

**Status:** pre-registered 2026-05-24, *before* the 10-year J-Quants Standard backfill
exists. This fixes the surprise definition and the accept/reject gates up front so the
study cannot devolve into "compute several surprise measures and keep the winner" — the
exact trap the 2026-05-24 /sign-debate flagged (`project_pead_price_drift_reject.md`).

## Why this definition
J-Quants does **not** provide analyst consensus, so textbook SUE (actual − consensus) is
impossible. Japanese firms issue detailed **management guidance**, and the *revision* of
that guidance is a well-documented drift driver. We commit to **one** surprise measure:
the change in full-year management EPS guidance, scaled by price.

## Surprise definition (single, pre-registered)
For a disclosure `t` carrying a full-year forecast EPS (`forecast_earnings_per_share`,
v2 `FEPS`) targeting fiscal-year-end `F`:

1. **Pair** `t` with the most recent prior disclosure `t-1` from the same `local_code`
   whose forecast targets the **same** `F` (same `current_fiscal_year_end_date`) and also
   has a non-null forecast EPS.
2. **Surprise** = `(FEPS_t − FEPS_{t-1}) / P`, where `P` = the stock's adjusted close on
   the last trading day strictly **before** the tradable event day (defined below).
   This is a clean, cross-sectionally comparable ΔE/P; positive = guidance **raised**.

### Exclusions (pre-registered, not tunable later)
- No same-`F` prior forecast (e.g. initial FY guidance issued at FY results) → **excluded**
  from the revision sample.
- `FEPS_t` or `FEPS_{t-1}` null, or `P` missing/zero → excluded.
- `type_of_document` accounting basis (JP / IFRS / consolidated vs NC) differs between
  the paired rows → excluded (no apples-to-oranges revisions).
- Surprise winsorized at the pooled 0.5% / 99.5% tails.

## Event timing (two-bar fill, no look-ahead)
- After-close rule: if `disclosed_time ≥ 15:00` (TSE close) the information is actionable
  only next session, so **effective day = next trading day** after `disclosed_date`;
  otherwise effective day = `disclosed_date`.
- **Entry = open of the first trading day on/after the effective day** (`jq_trading_calendar`
  supplies the trading-day set). Drift is measured from that entry.

## Drift / outcome metric
- **Primary:** β-stripped cumulative abnormal return (CAR) over **H = 60** trading bars
  from entry. Abnormal return per bar = `r_stock − β · r_TOPIX`; `β` from the trailing
  60 daily returns ending the bar before entry (look-ahead-safe), `jq_topix` as market.
- **Secondary (robustness only):** H = 20 bars.
- Report **raw and β-stripped** CAR. The β-strip is mandatory: a long-only ~3-month hold
  is partly market beta, and the confluence work already showed this book is ~89% beta.

## Accept gates (ALL must hold; else REJECT)
Bucket events into surprise **quintiles** (Q1 = most-negative revision … Q5 = most-positive).

1. **Monotone:** Spearman(quintile, mean β-stripped 60-bar CAR) > 0 **and** Q5 mean > Q1 mean.
2. **Long-short:** (Q5 − Q1) β-stripped 60-bar CAR mean > 0 with t-stat > **2.0**
   (report Newey-West; naive per-event t flagged as upper bound).
3. **Sample:** ≥ **1000** paired events pooled; each quintile ≥ 100.
4. **OOS:** most-recent full fiscal year held out — (Q5 − Q1) keeps the **same sign** and > 0.
5. **β-strip survives:** the (Q5 − Q1) result must remain > 0 *after* β-stripping (not an
   artifact of beta).
6. **Horizon robustness:** sign of (Q5 − Q1) agrees at H = 20 and H = 60.

## Falsifier (single line)
If the β-stripped (Q5 − Q1) 60-bar CAR ≤ 0, **or** it flips sign OOS, **or** quintiles are
non-monotone → the management-forecast-revision PEAD signal is rejected and not wired to
any sign/strategy.

## Data dependency
Requires the J-Quants Standard 10-yr backfill (`jq_statements` with consecutive same-FY
forecasts + `jq_daily_quotes` adjusted closes + `jq_topix` + `jq_trading_calendar`). The
Free-plan 12-week window is too short to form revision pairs with a 60-bar forward window.

## Implementation
`src/analysis/pead_forecast_revision.py` — surprise/pairing/event-timing/CAR logic is in
pure, unit-tested functions (`tests/test_pead_forecast_revision.py`); a thin DB driver
(`run()`) assembles the per-event table and prints the quintile drift once data exists.
This document is the spec; the code must not deviate from it without a new pre-registration.
