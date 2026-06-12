# J-Quants margin/short data — plan check (proposal #3) — BLOCKED

**Date:** 2026-06-13 · **Probe:** `src/analysis/jquants_plan_probe.py` · **Verdict:**
the weekly margin-interest axis (and every other margin/short endpoint) is **not on
the current J-Quants subscription**. Proposal #3 (margin-buy-overhang sizing tilt on
confluence fills) is blocked at the data wall — a subscription decision, not a
research reject. Do not re-propose without a plan upgrade
(plan/dataset matrix: https://jpx-jquants.com/#dataset).

## v2 endpoint map (v1 names do not exist in v2)

| v2 path | Data | Probe result |
|---|---|---|
| `/markets/margin-interest` | 信用取引週末残高 (weekly margin interest) — the #3 axis | 403 not on subscription |
| `/markets/margin-alert` | 日々公表信用取引残高 (daily-published balances) | 403 not on subscription |
| `/markets/short-ratio` | 業種別空売り比率 (sector short ratio) | 403 not on subscription |
| `/markets/short-sale-report` | 空売り残高報告 (per-stock large shorts) | 403 not on subscription |
| `/markets/breakdown` | 売買内訳 (long/margin volume decomposition) | 403 not on subscription |

Error-message semantics (useful for future probes): "The requested endpoint does not
exist" = bad path; "The api key is required" / "invalid or expired" = path valid, key
problem; "This API is not available on your subscription" = path + key valid,
plan-gated.

## ⚠ Side finding — the refreshed key is a lower tier than the old one

`/markets/calendar` for June 2026 returns
`400 "Your subscription covers the following dates: 2024-03-21 ~ 2026-03-21"` —
a ~2-year window lagging today by ~12 weeks (Free-tier shape). The previous key had
2016–2026 access (it performed the 10-year statements backfill on 2026-05-28).
Until resolved, the statements collector cannot fetch the most recent ~12 weeks nor
re-backfill pre-2024. Existing `jq_statements` rows in the DB are unaffected.

## Status of the 2026-06-13 proposal slate

1. **N225 reconstitution events** — Stage-0 REJECT (deletion-reversal backwards in
   Japan; addition run-up is overnight-gap). `src/analysis/n225_reconstitution_event_study.py`.
2. **Earnings-announcement window** — Stage-0 REJECT, verified on unique positions.
   `src/analysis/confluence_earnings_window_stage0.py` + `_verify.py`.
3. **Weekly margin-interest axis** — BLOCKED on subscription (this doc).
4. **TSMOM long/flat overlay** — validated defensive option; deploy decision with the
   operator (no further analysis pending).
