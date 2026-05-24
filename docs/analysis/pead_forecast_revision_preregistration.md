# PEAD — Management-Forecast-Revision Surprise (Pre-Registration)

**Status:** pre-registered 2026-05-24, *before* the 10-year J-Quants Standard backfill
exists. This fixes the surprise definition and the accept/reject gates up front so the
study cannot devolve into "compute several surprise measures and keep the winner" — the
exact trap the 2026-05-24 /sign-debate flagged (`project_pead_price_drift_reject.md`).
Amended 2026-05-24 (still before any data is fit) to split discovery universe from the
N225 deployment cohort — see **Cross-sectional scope** and gate 7.

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

## Cross-sectional scope — discovery universe vs deployment cohort
PEAD is strongly **size-dependent**: it concentrates in small, illiquid, lightly-covered
names and decays toward ~zero in large, liquid, heavily-covered ones — i.e. the N225
constituents we actually trade. A pooled ~4,000-stock result can therefore be a small-cap
artifact that does **not** exist in our book. To avoid borrowing a small-cap effect down to
large caps (the recurring "pooled significance ≠ tradable cohort" trap), scope is split:

- **Discovery universe** = the full J-Quants universe (all `jq_statements` codes with a
  price join). Used **only** for statistical power (gates 1–6 below) and to *measure the
  size gradient* — never as the deployment decision on its own.
- **Deployment cohort** = the **N225 names we hold**, defined as the codes present in
  `ohlcv_1d` (the 225-name confluence universe), mapped via `to_yf_code`. The narrower,
  most-honest variant — **N225 ∩ confluence-eligible at the event's entry day** — is
  reported alongside it, since a PEAD sign only helps by improving selection among
  confluence candidates.

### Size-gradient diagnostic (pre-registered, reported always)
Stratify events into size buckets — market cap (`book_value_per_share` × `shares_outstanding_fy`),
or J-Quants `ScaleCat`, or 60-bar median turnover `Va` as the liquidity proxy — and report
the (Q5 − Q1) β-stripped 60-bar CAR per bucket. The literature predicts monotone decay with
size and ~0 in the top bucket; if observed, that is itself evidence the signal is **not**
deployable on N225, regardless of the pooled result.

## Accept gates (ALL must hold; else REJECT)
Gates 1–6 establish that the effect **exists** (run on the discovery universe for power).
Gate 7 is the **binding deployment gate**: existence on the full universe is necessary but
**not sufficient** — the decision to wire a PEAD sign into the confluence strategy hinges on
the N225 deployment cohort.

Bucket events into surprise **quintiles** (Q1 = most-negative revision … Q5 = most-positive).

1. **Monotone:** Spearman(quintile, mean β-stripped 60-bar CAR) > 0 **and** Q5 mean > Q1 mean.
2. **Long-short:** (Q5 − Q1) β-stripped 60-bar CAR mean > 0 with t-stat > **2.0**
   (report Newey-West; naive per-event t flagged as upper bound).
3. **Sample:** ≥ **1000** paired events pooled; each quintile ≥ 100.
4. **OOS:** most-recent full fiscal year held out — (Q5 − Q1) keeps the **same sign** and > 0.
5. **β-strip survives:** the (Q5 − Q1) result must remain > 0 *after* β-stripping (not an
   artifact of beta).
6. **Horizon robustness:** sign of (Q5 − Q1) agrees at H = 20 and H = 60.
7. **Deployment (N225 cohort, BINDING):** restricted to the deployment cohort, the
   β-stripped (Q5 − Q1) 60-bar CAR is > 0 with the **same sign** as the pooled result.
   Sample floor relaxed to the cohort's reach: ≥ **200** paired events, ≥ 40 per extreme
   group (a tercile Top−Bottom split is permitted instead of quintiles if quintiles fall
   below 40/group). If the cohort cannot reach even this floor over 10 years, the result is
   **n-thin / untestable for our book** — report as such; do **not** substitute the pooled
   number. The only legitimate harvest of a small-cap-only effect is expanding the tradable
   universe (a separate strategy-scope decision), never falling the pooled result down to N225.

## Falsifier (single line)
If the β-stripped (Q5 − Q1) 60-bar CAR ≤ 0, **or** it flips sign OOS, **or** quintiles are
non-monotone, **or** it is ≤ 0 / sign-flipped on the N225 deployment cohort → the
management-forecast-revision PEAD signal is rejected and not wired to any sign/strategy.

## Data dependency
Requires the J-Quants Standard 10-yr backfill (`jq_statements` with consecutive same-FY
forecasts + `jq_daily_quotes` adjusted closes + `jq_topix` + `jq_trading_calendar`). The
Free-plan 12-week window is too short to form revision pairs with a 60-bar forward window.

## Implementation
`src/analysis/pead_forecast_revision.py` — surprise/pairing/event-timing/CAR logic is in
pure, unit-tested functions (`tests/test_pead_forecast_revision.py`); a thin DB driver
(`run()`) assembles the per-event table and prints the quintile drift once data exists.
`run()` must report three views from the same event table: the pooled discovery quintiles
(gates 1–6), the size-gradient buckets, and the N225 deployment-cohort split (gate 7).
This document is the spec; the code must not deviate from it without a new pre-registration.
