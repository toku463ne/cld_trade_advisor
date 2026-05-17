# brk_wall + brk_floor — sideways-range breakout signs (2026-05-17)

Two new signs shipped to the catalogue after the operator's hypothesis
that "sideways price ranges form walls that act as tested
support/resistance."  Both pass standalone canonical benchmark gates,
but live strategy impact is essentially zero — they ship as
informational additions to the catalogue, not load-bearing entry
triggers.

## Naming

| Old name | New name | Direction |
|---|---|---|
| `brk_hi_sideway` | **`brk_wall`** | Break above tested resistance |
| `brk_lo_sideway` | **`brk_floor`** | Break below tested support |

Renamed for clarity and to match the operator's vocabulary ("walls").
Rename via `git mv` + `UPDATE sign_benchmark_runs SET sign_type=...`
on the DB (no re-rebench needed since events cascade via run_id FK).

## Fire rule (common)

```
sideways range at bar i (10 trading days):
    (max H − min L) / mean C ≤ θ        on bars [i-K+1, i]

brk_wall  fire[T] = low[T]  > wall[T-1]  AND low[T-1]  ≤ wall[T-1]
brk_floor fire[T] = high[T] < floor[T-1] AND high[T-1] ≥ floor[T-1]

wall[T]  = max(tight_window_high[j] for j in [T-lookback, T-K-1])
floor[T] = min(tight_window_low[j]  for j in [T-lookback, T-K-1])
```

**Parameters (operator-chosen):**
- `K = 10` trading-day sideways window length
- `θ = 0.05` (range / mean tightness)
- `lookback = 120` bars (~6 months for finding walls/floors)

Strict (entire bar above/below the level) and transition-gated (the
previous bar must NOT have been on the same side — fires once per
breakout event).

## Canonical benchmark — both marginal at the headline

### brk_wall (FY2018–FY2024, n=5,006)

| FY | n | DR | perm_p | strong cell |
|---|---:|---:|---:|---|
| FY2019 | 680 | 47.2% | 0.941 | — |
| FY2020 | 642 | 52.8% | 0.076 | bear 59.5% (p=0.004) |
| FY2021 | 644 | 51.1% | 0.302 | — |
| FY2022 | 633 | 48.0% | 0.863 | — |
| FY2023 | 878 | 54.8% | **0.002** | bull 57.6% (p<0.001) |
| FY2024 | 524 | 53.4% | 0.060 | **bear 61.8% (p=0.001)** |
| **FY2025 OOS** | 1,005 | **59.6%** | **<0.001** | **bear 65.7% (p<0.001), bull 55.6% (p=0.006)** |
| **Pooled** | **5,006** | **53.0%** | — | perm_pass 2/7 |

Marginal headline DR with concentrated bear-regime + Kumo-inside cells.

### brk_floor (FY2018–FY2024, n=3,310)

| FY | n | DR | perm_p | strong cell |
|---|---:|---:|---:|---|
| FY2019 | 511 | 36.4% | 1.000 | (low DR but no significance) |
| FY2020 | 317 | 54.3% | 0.052 | bear 59.7% (p=0.018) |
| FY2021 | 666 | 53.0% | 0.062 | — |
| FY2022 | 541 | 59.9% | **<0.001** | **bull 64.9% (p<0.001)** |
| FY2023 | 331 | 50.2% | 0.502 | — |
| FY2024 | 672 | 55.2% | **0.002** | **bear 57.7% (p=0.003)** |
| FY2025 OOS | 272 | 51.5% | 0.339 | — |
| **Pooled** | **3,310** | **51.7%** | — | perm_pass 2/7 |

**Strongest cell**: Kumo below, DR 59.1%, n=1,173 (bear mean-reversion).

**Score calibration notable**: ρ = +0.172 in high-corr cohort
(p<0.001) — **strongest score-to-EV correlation in the entire sign
catalogue**.  Deeper breakdowns in high-corr stocks produce larger
mean-reversion EV (top quartile EV +5.13% vs bottom +1.23%).  This is
the one finding worth preserving even with the informational-only
demote — it points at a real structural effect in high-corr
post-breakdown behaviour.

## Probe vs canonical — the inversion lesson

Both signs were originally validated by a per-fire probe using
`detect_peaks` on the full bar series.  Both probes overstated
canonical numbers, but in importantly different ways:

| Sign | Probe pooled DR | Canonical pooled DR | Gap |
|---|---|---|---|
| brk_wall | 72.6% | 53.0% | **−20pp** (magnitude inflation) |
| brk_floor | 33.2% (breakdown-persistence) | 51.7% (mean-reversion) | **DIRECTION FLIPPED** |

For brk_floor, the operator's original intent was a SHORT entry signal
(price broke below floor → expect continued downside).  The probe
supported it (66.8% short-win rate).  The canonical pipeline
contradicted: short DR is only 48.3%, short EV ≈ −0.44%.

**This is now the documented case study for the probe-vs-canonical
rule** — see [`docs/analysis/probe_vs_canonical_lesson.md`](probe_vs_canonical_lesson.md).

## Strategy-level A/B — brk_wall has zero impact

`src/analysis/regime_sign_brk_wall_ab.py` ran the regime_sign strategy
WITH and WITHOUT brk_wall in the EXCLUDE_SIGNS set.  Result across 5
FYs with trades:

| FY | Trades (both arms) | Sharpe (both arms) | Δ |
|---|---:|---:|---:|
| FY2021 | 31 | −1.31 | +0.00 |
| FY2022 | 30 | +3.03 | +0.00 |
| FY2023 | 36 | +3.71 | +0.00 |
| FY2024 | 36 | −1.92 | +0.00 |
| FY2025 | 38 | +3.15 | +0.00 |
| **All** | **171 = 171** | **+1.33 = +1.33** | **+0.00** |

Not "neutral on average" — **trade-for-trade identical**.

### Why brk_wall is inert

brk_wall's largest cell (Kumo above, n=995 in FY2025) has DR 59.4% but
mean_r −0.2%.  The strategy ranks (sign, kumo) cells by EV-equivalent
metric, and brk_wall's cells never win the daily ranking against other
signs' top cells.  The tiny n=10 "Kumo inside" cell with +20.4% mean_r
doesn't fire often enough to matter.

**Implication**: per-sign canonical-pass ≠ strategy-level impact.
Adding a marginally-positive sign to a regime-ranking strategy can
contribute zero if its cells don't out-rank what's already there.

## Confluence interaction

The bullish confluence framework (see
[Confluence Strategy](confluence_strategy.md)) tested whether including
brk_wall improves the multi-sign ensemble.  Result: adding brk_wall
**dilutes** the v2 confluence uplift by −0.83pp pooled (n[≥3] grew
1,746 → 3,441, but EV/row dropped).  brk_wall is therefore EXCLUDED
from the confluence bullish set.

## Ship decisions

| Decision | brk_wall | brk_floor |
|---|---|---|
| In sign catalogue? | ✅ Yes | ✅ Yes |
| In `_HIDDEN_PROPOSAL_SIGNS`? | ❌ No (appears in Daily) | ✅ Yes (hidden) |
| In ConfluenceSignStrategy bullish set? | ❌ No (dilutes) | ❌ No (bearish framing untested) |
| In regime_sign ranking? | ✅ Yes (but inert) | N/A |
| Recommended as entry trigger? | ❌ No (zero strategy impact) | ❌ No (canonical contradicts operator's SHORT intent) |

The signs are kept primarily for:
1. **Event preservation** — fires persist in `SignBenchmarkEvent` for
   future analysis (score calibration on brk_floor × high-corr is the
   most interesting cell in the catalogue)
2. **Context display** — brk_wall surfaces as a row in proposals so
   operator can see "a sideways-wall broke today" even if it rarely
   wins the daily pick

## Files

| Path | Role |
|---|---|
| `src/signs/brk_wall.py` | Detector |
| `src/signs/brk_floor.py` | Detector |
| `docs/signs/brk_wall.md` | Per-sign reference |
| `docs/signs/brk_floor.md` | Per-sign reference |
| `src/analysis/brk_wall_probe.py` | Original probe (--side hi\|lo) |
| `src/analysis/regime_sign_brk_wall_ab.py` | Strategy A/B |
| `src/analysis/benchmark.md` § Multi-Year Benchmark | Canonical numbers |
| `src/viz/daily.py:_HIDDEN_PROPOSAL_SIGNS` | brk_floor demote |

## Commit trail

- `52bde03` — brk_hi_sideway feature + rebench
- `ac91c94` — probe refactored to `--side hi\|lo`
- `1630d47` — brk_lo_sideway feature + rebench (canonical inverted probe)
- `a47a275` — brk_lo_sideway demoted to informational-only
- `488dfd3` — rename to brk_wall / brk_floor
- `78f4344` — strategy-level A/B (zero impact)
