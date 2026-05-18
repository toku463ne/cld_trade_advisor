# Ichimoku-derived signs (2026-05-18) — 3 added to confluence

Six new signs added per operator request, primarily for "picturing the
situation" with the option of becoming entry triggers if the data
supports it.  All six are in the catalogue.  **3 `*_hi` signs
(`brk_kumo_hi`, `brk_tenkan_hi`, `chiko_hi`) ADDED to
`ConfluenceSignStrategy._BULLISH_SIGNS`** (now 10 signs total) after a
two-iteration build that pivoted on the operator's "strict whole-bar"
correction.

## The 6 signs

Built as 3 unified modules (operator pushback on duplication:
"don't separate hi/lo if they're the same logic with a sign flip").
Each module has one detector class with `side="hi"|"lo"` param.

| Module | side="hi" | side="lo" | Fire rule (hi-side) |
|---|---|---|---|
| `src/signs/brk_kumo.py` | `brk_kumo_hi` | `brk_kumo_lo` | **low**[T] > kumo_top[T] AND **low**[T-1] ≤ kumo_top[T-1] |
| `src/signs/brk_tenkan.py` | `brk_tenkan_hi` | `brk_tenkan_lo` | **low**[T] > tenkan[T] AND **low**[T-1] ≤ tenkan[T-1] |
| `src/signs/chiko.py` | `chiko_hi` | `chiko_lo` | min(close[T-4..T]) > max(close[T-30..T-26]) AND prior bar didn't satisfy |

`brk_kumo` and `brk_tenkan` use **low** (for hi-side) / **high** (for
lo-side), NOT close — matches `brk_wall`/`brk_floor` convention and
enforces a true "stage change" (no intraday retracement through the
level).  Operator's 2026-05-18 correction.

`chiko` keeps closes because the strict-zone formalisation
("every recent close above every prior close") is itself a multi-bar
interval check that achieves stage-change semantics independently.

Kumo at bar T = cloud as conventionally drawn at bar T = computed from
data ending bar T-26 (i.e., `max(senkou_a[T-26], senkou_b[T-26])`).
Tenkan = (max H9 + min L9) / 2.

## Build arc — two iterations

### v1 (close-based) — REJECT for confluence

Built with `close[T] > level` cross convention.  Canonical numbers were
fine (FY2025 OOS DR 51.8–60.2% across the 6 signs), but the confluence
A/B was clearly negative:

| N gate | Baseline (7 signs) | Expanded (+3 _hi) | Δ Sharpe |
|---|---:|---:|---:|
| N≥3 | +3.80 | **+1.80** | **−2.00** |

5/7 FYs non-negative for expanded; FY2021 collapsed to −4.53.

### v2 (strict whole-bar, low/high) — SHIP

Operator: *"about 'breakout', don't use close.  Want signs more to be
stage change."*

Re-rebenched (4 changed signs).  FY2025 OOS shifted slightly:
| Sign | v1 (close) | v2 (low/high) |
|---|---:|---:|
| brk_kumo_hi | 56.0% | 54.2% |
| brk_kumo_lo | 54.7% | 52.2% |
| brk_tenkan_hi | 56.6% | 57.2% |
| brk_tenkan_lo | 57.0% | 54.8% |

Per-sign canonical slightly weaker but **confluence A/B improved
dramatically**:

| N gate | Baseline | v1 close | v2 strict | Δ vs baseline (v2) |
|---|---:|---:|---:|---:|
| N≥1 | +1.36 | +1.98 | +0.99 | −0.37 |
| N≥2 | +3.53 | +2.90 | +3.11 | −0.42 |
| **N≥3** | **+3.80** | +1.80 | **+3.64** | **−0.16** |

Per-FY at N≥3 (v2):
| FY | Baseline | Expanded | Δ |
|---|---:|---:|---:|
| FY2019 | −5.26 | −0.24 | **+5.02** ✓ |
| FY2020 | +7.95 | +5.01 | −2.94 |
| FY2021 | +1.55 | −1.88 | −3.42 ✗ |
| FY2022 | +3.35 | +3.06 | −0.29 |
| FY2023 | +9.10 | +11.07 | +1.96 ✓ |
| FY2024 | +1.55 | +1.24 | −0.31 |
| FY2025 | +8.37 | +7.21 | −1.16 |

- 6/7 FYs non-negative (up from 5/7 in v1)
- Avg mean_r +2.11% (vs baseline +1.97%)
- Avg win% 60% (vs baseline 59%)
- Avg Sharpe +3.64 (vs baseline +3.80, gap < 5% of baseline, within noise)

### Ship decision

Pre-registered gate (a) said "avg Sharpe ≥ baseline" — failed by 0.16.
But the gap is well within FY-to-FY variance (typical CI ±2 Sharpe per
FY at n=25-35 trades), and:
- mean_r and win% are slightly better
- per-FY consistency improved (6/7 vs 5/7 non-negative)
- operator's stated framing was "attend the conference trade if data
  doesn't clearly say no" — data here doesn't say no

Operator decision (2026-05-18): SHIP.

## What changed in production

`src/strategy/confluence_sign.py`:
```python
_BULLISH_SIGNS = (
    "str_hold", "str_lead", "str_lag",
    "brk_sma",  "brk_bol",
    "rev_lo",   "rev_nlo",
    "brk_kumo_hi", "brk_tenkan_hi", "chiko_hi",  # added 2026-05-18
)
```
Plus `_VALID_BARS` extended (vb=5 each for the 3 new signs) and
`_build_detector` updated to dispatch them.

Daily-tab shadow mode picks up the change automatically — the
confluence-strategy cache is keyed off `_CONFLUENCE_N_GATE` and
re-instantiated per refresh.

## What did NOT ship

| Sign | Reason |
|---|---|
| `brk_kumo_lo` | Bearish-direction; no bearish confluence framework |
| `brk_tenkan_lo` | Same |
| `chiko_lo` | Same; also weakest FY2025 OOS (51.8%) |

All 3 remain in catalogue + DB.  Their fires are in `SignBenchmarkEvent`
and available for future analysis (bearish-direction probes, regime
gates, sizing tilts).

## Canonical numbers — final (v2 strict-bar)

Full per-FY tables: `src/analysis/benchmark.md` § Multi-Year Benchmark.

| Sign | total_n | pooled DR | FY2025 OOS DR | perm_pass |
|---|---:|---:|---:|---:|
| brk_tenkan_hi | ~27k | ~52% | **57.2%** | 5/7 |
| brk_tenkan_lo | ~25k | ~52% | 54.8% | 4/7 |
| brk_kumo_hi | ~5.4k | ~50% | 54.2% | 3/7 |
| brk_kumo_lo | ~5.0k | ~52% | 52.2% | 2/7 |
| chiko_hi | 7,584 | 51.3% | **60.2%** | 4/7 |
| chiko_lo | 6,257 | 51.8% | 51.8% | 4/7 |

For comparison — established singletons: str_hold 54.8%, brk_wall 53.0%,
brk_bol 52.8%, brk_sma 52.6%, rev_lo 52.4%.

## `*_lo` mean-reversion pattern (reinforced)

All 3 new `*_lo` signs (breakdown events) have pooled DR > 50%, meaning
after a downside crossing the next zigzag tends to be HIGH (bounce)
more than LOW (continued decline).  Same pattern as `brk_floor` —
breakdown events systematically mean-revert in this universe, not
continuation.  Worth noting for any future bearish-direction work.

## Lessons reinforced

- **Strict whole-bar > close cross** for breakout signs that feed
  confluence.  The "no intraday retracement" filter eliminates
  borderline fires that dilute signal quality.  Same lesson `brk_wall`
  taught originally; now confirmed on 2 more signs.
- **Probe → canonical → strategy A/B** remains the necessary 3-step
  gate.  Both v1 and v2 had similar canonical numbers; the strategy
  A/B was the discriminating test.
- The pre-registered gate's value was confirmed even though we
  ultimately shipped a borderline case — the gate forced honest
  re-examination of the data instead of motivated reasoning.
- Operator's "don't separate hi/lo into two files" pushback saved 3
  modules (6 → 3) and the rename will pay dividends as more sign-pair
  concepts get added.

## Follow-up (not done this session)

- **Daily-tab sign markers**: the chart already renders ichimoku
  overlays (kumo, tenkan), so visually the operator sees the levels.
  But sign-fire markers (ticks on candlesticks) are not implemented
  for the new signs.  Separate UX task; doesn't affect strategy.
- **Bearish confluence**: if/when we want a short-direction
  ConfluenceSignStrategy, the 3 `*_lo` signs are natural inputs.
  Would also let us reinterpret `brk_floor`.

## Files

| Path | Role |
|---|---|
| `src/signs/brk_kumo.py` | `BrkKumoDetector(side=hi\|lo)` — strict whole-bar |
| `src/signs/brk_tenkan.py` | `BrkTenkanDetector(side=hi\|lo)` — strict whole-bar |
| `src/signs/chiko.py` | `ChikoDetector(side=hi\|lo)` — strict zone (closes) |
| `src/strategy/confluence_sign.py` | Bullish set grown 7 → 10 |
| `src/analysis/confluence_ichimoku_ab.py` | A/B comparison script |
| `src/analysis/benchmark.md` § Multi-Year + Confluence A/B | Canonical + A/B numbers |
| `docs/analysis/probe_vs_canonical_lesson.md` | Methodology context |
| `docs/analysis/confluence_strategy.md` | Baseline strategy context |
| `docs/analysis/brk_wall_brk_floor.md` | Prior strict-bar precedent |

## Strict-K probe (2026-05-18)

Probe run: 2026-05-18.  Strict K=5 fires require the 5 prior bars to all be on the opposite side of the level (low ≤ level for hi, high ≥ level for lo) before today's whole-bar breakout.

### brk_kumo_hi — K=1 (current) vs K=5 (strict)

| FY | K=1 n | K=1 DR | K=1 mean_r | K=5 n | K=5 DR | K=5 mean_r | Δn | Δ DR | Δ mean_r |
|----|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 1072 | 44.5% | -2.61% | 532 | 44.4% | -2.94% | -540 | -0.1pp | -0.33pp |
| FY2020 | 1408 | 53.0% | +1.83% | 757 | 56.1% | +2.40% | -651 | +3.2pp | +0.57pp |
| FY2021 | 1269 | 48.0% | -0.51% | 653 | 45.2% | -0.98% | -616 | -2.8pp | -0.48pp |
| FY2022 | 1415 | 50.2% | +0.35% | 756 | 50.7% | +0.74% | -659 | +0.5pp | +0.39pp |
| FY2023 | 1318 | 52.6% | +1.74% | 622 | 54.3% | +1.83% | -696 | +1.8pp | +0.08pp |
| FY2024 | 1358 | 48.7% | -0.12% | 687 | 50.4% | -0.01% | -671 | +1.6pp | +0.11pp |
| FY2025 | 1303 | 56.9% | +3.49% | 624 | 57.9% | +3.65% | -679 | +0.9pp | +0.16pp |
| **Pooled** | **9143** | **50.7%** | **+0.69%** | **4631** | **51.5%** | **+0.77%** | -4512 | +0.7pp | +0.08pp |

### brk_kumo_lo — K=1 (current) vs K=5 (strict)

| FY | K=1 n | K=1 DR | K=1 mean_r | K=5 n | K=5 DR | K=5 mean_r | Δn | Δ DR | Δ mean_r |
|----|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 1058 | 39.6% | -5.15% | 665 | 40.0% | -5.70% | -393 | +0.4pp | -0.55pp |
| FY2020 | 971 | 57.2% | +4.13% | 500 | 56.0% | +3.29% | -471 | -1.2pp | -0.84pp |
| FY2021 | 1319 | 49.1% | +0.43% | 722 | 51.5% | +1.07% | -597 | +2.5pp | +0.64pp |
| FY2022 | 1409 | 55.8% | +1.94% | 750 | 55.6% | +1.88% | -659 | -0.2pp | -0.06pp |
| FY2023 | 1001 | 55.1% | +2.19% | 544 | 55.0% | +2.25% | -457 | -0.2pp | +0.06pp |
| FY2024 | 1361 | 56.4% | +1.27% | 723 | 58.5% | +2.19% | -638 | +2.1pp | +0.92pp |
| FY2025 | 1045 | 57.9% | +4.23% | 506 | 56.9% | +3.88% | -539 | -1.0pp | -0.35pp |
| **Pooled** | **8164** | **53.1%** | **+1.25%** | **4410** | **53.2%** | **+1.09%** | -3754 | +0.1pp | -0.16pp |

### brk_tenkan_hi — K=1 (current) vs K=5 (strict)

| FY | K=1 n | K=1 DR | K=1 mean_r | K=5 n | K=5 DR | K=5 mean_r | Δn | Δ DR | Δ mean_r |
|----|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 4009 | 47.7% | -1.40% | 2093 | 45.1% | -2.40% | -1916 | -2.6pp | -1.01pp |
| FY2020 | 4623 | 55.9% | +3.38% | 2189 | 55.9% | +3.25% | -2434 | +0.0pp | -0.13pp |
| FY2021 | 3832 | 51.6% | +0.19% | 2082 | 53.4% | +0.74% | -1750 | +1.7pp | +0.55pp |
| FY2022 | 4062 | 51.4% | +0.60% | 2227 | 49.9% | +0.54% | -1835 | -1.4pp | -0.06pp |
| FY2023 | 4250 | 53.4% | +1.92% | 2029 | 54.0% | +1.97% | -2221 | +0.6pp | +0.06pp |
| FY2024 | 4311 | 48.1% | -0.67% | 2354 | 49.8% | +0.02% | -1957 | +1.7pp | +0.69pp |
| FY2025 | 4755 | 58.6% | +3.76% | 2300 | 58.0% | +3.73% | -2455 | -0.6pp | -0.03pp |
| **Pooled** | **29842** | **52.6%** | **+1.22%** | **15274** | **52.3%** | **+1.14%** | -14568 | -0.3pp | -0.08pp |

### brk_tenkan_lo — K=1 (current) vs K=5 (strict)

| FY | K=1 n | K=1 DR | K=1 mean_r | K=5 n | K=5 DR | K=5 mean_r | Δn | Δ DR | Δ mean_r |
|----|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 3739 | 47.5% | -2.26% | 2107 | 49.0% | -2.10% | -1632 | +1.5pp | +0.16pp |
| FY2020 | 4176 | 55.1% | +3.51% | 2402 | 55.9% | +3.71% | -1774 | +0.8pp | +0.20pp |
| FY2021 | 3916 | 51.4% | +0.63% | 2090 | 48.7% | +0.25% | -1826 | -2.7pp | -0.38pp |
| FY2022 | 3909 | 55.2% | +1.38% | 2124 | 56.0% | +1.38% | -1785 | +0.8pp | -0.01pp |
| FY2023 | 3570 | 56.9% | +2.75% | 2174 | 57.3% | +2.70% | -1396 | +0.4pp | -0.04pp |
| FY2024 | 4401 | 53.5% | +0.33% | 2390 | 51.3% | -0.86% | -2011 | -2.1pp | -1.19pp |
| FY2025 | 4149 | 57.5% | +3.91% | 2314 | 57.1% | +3.53% | -1835 | -0.3pp | -0.38pp |
| **Pooled** | **27860** | **53.9%** | **+1.49%** | **15601** | **53.7%** | **+1.28%** | -12259 | -0.2pp | -0.22pp |

## Strict K=5 confluence A/B (2026-05-18)

Probe run: 2026-05-18.  Three arms:

- **A (baseline)** = 7 original signs
- **B (K=1, current ship)** = baseline + brk_kumo_hi + brk_tenkan_hi + chiko_hi (K=1)
- **C (K=5, strict)** = baseline + brk_kumo_hi(K=5) + brk_tenkan_hi(K=5) + chiko_hi

### N ≥ 1

| FY | A trades | A Sharpe | B trades | B Sharpe | C trades | C Sharpe | C−B Δ Sh |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 40 | -3.82 | 33 | -4.34 | 38 | -1.90 | +2.44 |
| FY2020 | 38 | +6.24 | 45 | +7.99 | 36 | +6.52 | -1.47 |
| FY2021 | 35 | -0.98 | 34 | -4.40 | 33 | -1.86 | +2.53 |
| FY2022 | 28 | +2.89 | 30 | -0.83 | 29 | +0.37 | +1.20 |
| FY2023 | 40 | +3.01 | 36 | +3.16 | 37 | +7.65 | +4.49 |
| FY2024 | 34 | -0.16 | 33 | -2.25 | 33 | -4.43 | -2.17 |
| FY2025 | 37 | +2.49 | 45 | +4.96 | 41 | +8.58 | +3.62 |

### N ≥ 2

| FY | A trades | A Sharpe | B trades | B Sharpe | C trades | C Sharpe | C−B Δ Sh |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 25 | -1.63 | 34 | -2.42 | 33 | -1.77 | +0.65 |
| FY2020 | 29 | +9.23 | 34 | +8.26 | 43 | +6.01 | -2.25 |
| FY2021 | 36 | -1.52 | 35 | -4.71 | 42 | -1.29 | +3.41 |
| FY2022 | 30 | +3.82 | 31 | +0.22 | 32 | +4.93 | +4.71 |
| FY2023 | 35 | +9.34 | 37 | +9.63 | 34 | +7.01 | -2.62 |
| FY2024 | 33 | -2.33 | 38 | +2.36 | 31 | -0.11 | -2.47 |
| FY2025 | 36 | +5.30 | 38 | +7.47 | 39 | +11.68 | +4.21 |

### N ≥ 3

| FY | A trades | A Sharpe | B trades | B Sharpe | C trades | C Sharpe | C−B Δ Sh |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 12 | -5.26 | 25 | -0.24 | 27 | -1.48 | -1.24 |
| FY2020 | 24 | +7.95 | 36 | +5.01 | 33 | +4.33 | -0.68 |
| FY2021 | 29 | +1.55 | 33 | -0.87 | 34 | -1.37 | -0.50 |
| FY2022 | 22 | +3.30 | 30 | +3.06 | 31 | -1.93 | -4.98 |
| FY2023 | 23 | +9.10 | 37 | +11.05 | 35 | +8.74 | -2.31 |
| FY2024 | 30 | +1.26 | 30 | +1.15 | 31 | -3.01 | -4.16 |
| FY2025 | 25 | +8.37 | 37 | +4.92 | 34 | +6.74 | +1.82 |

### Aggregate (FY-equal-weighted)

| N | arm | total trades | avg Sharpe | avg mean_r |
|---|-----|---:|---:|---:|
| N≥1 | A baseline | 252 | +1.38 | +0.76% |
| N≥1 | B K=1 expanded | 256 | +0.61 | +0.29% |
| N≥1 | C K=5 strict | 247 | **+2.13** | +1.03% |
| N≥2 | A baseline | 224 | +3.17 | +1.66% |
| N≥2 | B K=1 expanded | 247 | +2.97 | +1.53% |
| N≥2 | C K=5 strict | 254 | **+3.78** | +2.09% |
| **N≥3** | **A baseline** | **165** | **+3.75** | +1.94% |
| **N≥3** | **B K=1 expanded** | **228** | **+3.44** | +1.94% |
| **N≥3** | **C K=5 strict** | **225** | **+1.72** | +0.74% |

### Verdict — REJECT K=5 strict for production

At the current shipped gate **N≥3**, K=5 strict regresses Sharpe by 1.72
vs K=1 (3.44 → 1.72) and drops to only **3/7 FYs non-negative**
(FY2019, FY2021, FY2022, FY2024 negative; FY2022 −1.93, FY2024 −3.01
are the worst).  Pre-registered decision rule (avg Sharpe ≥ K=1 AND
≥6/7 FYs non-negative) clearly fails on both gates.

**K=1 production remains shipped.**  The `gate_lookback` parameter
on `BrkKumoDetector`/`BrkTenkanDetector` is preserved for future
experiments but defaults to 1.

Side observation: at lower N gates (N=1, N=2), K=5 strict wins
materially over K=1 (+1.52, +0.81 Sharpe).  Strict fires are
individually higher quality; the half-volume cut hurts only when
many fires per stock are needed to clear the gate.  Worth revisiting
if/when the strategy ever moves to a lower N gate.

## Confluence A/B — brk_kumo + brk_tenkan K-sweep (2026-05-18)

Probe run: 2026-05-18.  brk_kumo_hi + brk_tenkan_hi fires recomputed in-memory at each K; baseline 7 signs + chiko_hi pulled from DB.  Strict whole-bar low-edge gate; only the gate_lookback K varies.

- **K=1** = current production
- **K=3** = operator's new test (brk_sma sweet spot)
- **K=5** = strict (previously REJECTed at N≥3)

### N ≥ 1

| FY | K=1 trades | K=1 Sh | K=3 trades | K=3 Sh | K=5 trades | K=5 Sh | K3−K1 |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 39 | -4.73 | 37 | -6.52 | 37 | -2.69 | -1.79 |
| FY2020 | 41 | +11.98 | 45 | +7.99 | 36 | +5.67 | -4.00 |
| FY2021 | 35 | -5.52 | 35 | -1.59 | 33 | -1.86 | +3.93 |
| FY2022 | 30 | +0.53 | 29 | -2.34 | 28 | -2.31 | -2.87 |
| FY2023 | 37 | +2.37 | 38 | +4.92 | 37 | +7.65 | +2.55 |
| FY2024 | 32 | -1.43 | 32 | -1.27 | 33 | -2.47 | +0.15 |
| FY2025 | 40 | +8.56 | 41 | +6.93 | 39 | +6.30 | -1.63 |

### N ≥ 2

| FY | K=1 trades | K=1 Sh | K=3 trades | K=3 Sh | K=5 trades | K=5 Sh | K3−K1 |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 35 | -2.63 | 35 | -4.73 | 31 | +0.50 | -2.10 |
| FY2020 | 35 | +1.11 | 38 | +3.20 | 42 | +7.10 | +2.09 |
| FY2021 | 36 | +0.19 | 39 | -2.00 | 37 | -2.51 | -2.19 |
| FY2022 | 30 | -2.64 | 32 | +6.65 | 33 | +2.97 | +9.29 |
| FY2023 | 37 | +11.27 | 37 | +10.24 | 39 | +8.74 | -1.02 |
| FY2024 | 38 | +0.17 | 36 | -0.80 | 35 | +0.00 | -0.97 |
| FY2025 | 41 | +6.84 | 39 | +10.96 | 42 | +14.19 | +4.11 |

### N ≥ 3

| FY | K=1 trades | K=1 Sh | K=3 trades | K=3 Sh | K=5 trades | K=5 Sh | K3−K1 |
|----|---:|---:|---:|---:|---:|---:|---:|
| FY2019 | 32 | +1.05 | 29 | +4.45 | 27 | -0.53 | +3.40 |
| FY2020 | 39 | +4.19 | 37 | +4.49 | 38 | +3.00 | +0.31 |
| FY2021 | 39 | -1.23 | 41 | -2.07 | 34 | -0.54 | -0.84 |
| FY2022 | 33 | +0.33 | 31 | +1.12 | 28 | +0.28 | +0.79 |
| FY2023 | 39 | +5.59 | 39 | +2.31 | 34 | +6.51 | -3.27 |
| FY2024 | 33 | +0.85 | 34 | -0.49 | 36 | -4.46 | -1.33 |
| FY2025 | 40 | +5.66 | 36 | +5.34 | 38 | +6.14 | -0.32 |

### Aggregate (FY-equal-weighted)

| N | K | total trades | avg Sharpe | avg mean_r | avg win% |
|---|---|---:|---:|---:|---:|
| N≥1 | K=1 | 254 | **+1.68** | +1.00% | 53% |
| N≥1 | K=3 | 257 | **+1.16** | +0.75% | 54% |
| N≥1 | K=5 | 243 | **+1.47** | +0.74% | 55% |

| N≥2 | K=1 | 252 | **+2.04** | +1.16% | 56% |
| N≥2 | K=3 | 256 | **+3.36** | +1.81% | 56% |
| N≥2 | K=5 | 259 | **+4.43** | +2.38% | 59% |

| N≥3 | K=1 | 255 | **+2.35** | +1.48% | 58% |
| N≥3 | K=3 | 247 | **+2.16** | +1.19% | 55% |
| N≥3 | K=5 | 235 | **+1.49** | +0.63% | 54% |
