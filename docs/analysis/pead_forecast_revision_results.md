# PEAD — Management-Forecast-Revision Surprise (Results)

**Verdict: ACCEPT — all 7 pre-registered gates pass.** Run 2026-05-25 on the J-Quants
Standard 10-yr backfill (`stock_trader_dev`). Spec: `pead_forecast_revision_preregistration.md`
(incl. the two 2026-05-25 amendments). Reproduce:

```bash
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_forecast_revision
```

Driver: `src/analysis/pead_forecast_revision.py` (`run()`). It streams the 10.08M-row
adjusted-close matrix (5,356 codes × 2,442 TOPIX trading days), forms same-FY forecast pairs,
computes the β-stripped (vs TOPIX) cumulative abnormal return, and bins by the **sign of the
EPS-guidance revision** (signed terciles — see amendment 1).

## Sample
- **84,863** same-FY forecast pairs → **80,267** usable events (price join + β + 60-bar CAR).
- Surprise winsorized at 0.5 / 99.5% → range `[−0.3242, +0.1810]`.
- ~58% of pairs are **reaffirmations** (ΔFEPS = 0): 46,320 reaffirm / 19,188 up / 14,759 down.

## Pooled discovery — signed terciles (gates 1–6, full universe; power only)
| group | n | mean β-stripped CAR60 | mean CAR20 |
|---|---:|---:|---:|
| down (ΔEPS < 0) | 14,759 | −0.04% | −0.03% |
| reaffirm (= 0) | 46,320 | +0.76% | +0.67% |
| up (ΔEPS > 0) | 19,188 | **+1.48%** | +0.65% |

- **(up − down) β-stripped 60-bar CAR = +1.52%**, naive Welch **t = +7.0**.
- Spearman(group, mean CAR60) = **+1.00** (monotone).

## Per-fiscal-year robustness (behind gate 4)
(up − down) β-stripped 60-bar CAR, by event fiscal-year-end:

| FY | n | (up − down) | note |
|---|---:|---:|---|
| FY2016 | 624 | −2.19% | thin/early |
| FY2017 | 8,288 | +2.04% | |
| FY2018 | 8,310 | +0.83% | |
| FY2019 | 8,118 | +1.67% | |
| FY2020 | 8,090 | +2.84% | |
| FY2021 | 8,006 | +2.62% | |
| FY2022 | 8,245 | +1.92% | |
| FY2023 | 8,371 | +2.02% | |
| FY2024 | 8,458 | +0.77% | |
| **FY2025** | 8,374 | **+1.07%** | **← OOS holdout (gate 4)** |
| FY2026 | 5,381 | −0.24% | **partial — excluded from OOS** (watch-item) |

**Positive in all 9 complete fiscal years FY2017–FY2025.** FY2026 is truncated (last ~3 months
carry no events for lack of a 60-bar forward window; ~65% coverage, early-FY-biased) — a
watch-item, not evidence of decay.

## Size-gradient diagnostic ((up − down) β-stripped 60-bar CAR by TOPIX scale)
| scale | n | (up − down) |
|---|---:|---:|
| TOPIX Core30 | 539 | +0.48% |
| TOPIX Large70 | 1,391 | +2.62% |
| TOPIX Mid400 | 8,660 | +1.53% |
| TOPIX Small 1 | 11,204 | +0.40% |
| TOPIX Small 2 | 14,841 | +1.82% |
| (unclassified) | 36,143 | +1.92% |

**Flat / non-monotone — NOT the textbook small-cap concentration.** The effect does not decay
into the large caps, so it is not a small-cap artifact being borrowed down to N225.

## N225 deployment cohort (gate 7, BINDING) — n = 4,679 of 80,267
- Cohort = the 225 `ohlcv_1d` codes (`to_yf_code` mapped). down n=1,092 / up n=1,792.
- **(up − down) β-stripped 60-bar CAR = +2.51%**, same sign as pooled.
- The effect is present and **stronger** on the names we actually trade → the deployability
  worry that motivated gate 7 is **refuted**.

## Gate verdicts
| gate | result | detail |
|---|---|---|
| 1 monotone | PASS | ρ +1.0, up > down (up−down +1.52%) |
| 2 long-short t > 2 | PASS | t = +7.0 |
| 3 sample n ≥ 1000, ≥ 100/group | PASS | n = 80,267, min group = 14,759 |
| 4 OOS same sign | PASS | FY2025 (up−down) = +1.07% (FY2026 partial, excluded) |
| 5 β-strip survives | PASS | raw (up−down) = +1.71% |
| 6 H20 ≈ H60 sign | PASS | H20 (up−down) = +0.68% |
| **7 N225 cohort (BINDING)** | **PASS** | (up−down) = +2.51%, n = 4,679 |

**VERDICT: ACCEPT (gates 1–7).**

## Implementation notes
- **Binning amendment (1):** signed terciles {down / reaffirm / up}, long-short = (up − down).
  Value-percentile quintiles are degenerate on the ~58% ΔEPS=0 mass point.
- **Gate-4 amendment (2):** OOS holds out the most-recent *complete* FY, where "complete" =
  `fy_end + ~135 days ≤ data end` (annual-report lag + 60-bar window); the truncated trailing
  FY is excluded and reported as a watch-item.
- **Basis pairing:** forecast-revision disclosures (`EarnForecast*`, no basis suffix) inherit
  the code's modal accounting basis so they pair into the same-FY quarterly chain — implements
  the pre-reg's "no JP/IFRS/Consolidated-vs-NC apples-to-oranges" exclusion (`doc_basis()`).
- **Trading calendar** = TOPIX dates (`jq_trading_calendar.holiday_division` is unpopulated);
  TOPIX is also the β-strip market series, so stock/market are date-aligned by construction.

## Next step (build, user-authorized)
Wire the **up-revision** event as a confluence sign (~60-bar validity from the entry day) and
A/B it inside `ConfluenceSignStrategy`. This is a fresh, pre-registered, cohort-validated edge
on a *different axis* than the existing price/momentum signs — the lever that's been missing
(every selection rule on the current signs failed the fill-order null at ~36 trades/yr).
