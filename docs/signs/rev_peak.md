# rev_peak — Price Near Recent Same-Side Zigzag Peak (Reversal)
Fires on the bar when the bar's tested price is within ``proximity_pct``
of one of the last ``n_peaks`` confirmed same-type zigzag peaks of the
input cache (typically daily bars).

side='lo'  → test_price = bar.low  near a prior confirmed LOW
sign_type = "rev_lo"  — expect UP bounce (support test)
side='hi'  → test_price = bar.high near a prior confirmed HIGH
sign_type = "rev_hi"  — expect DOWN reversal (resistance test)

Only peaks whose zigzag confirmation has fully passed before the current
bar are used — no look-ahead. Two filters are applied at firing time:

Directional approach (opt-in, default OFF as of 2026-05-16)
When `bearish_body_filter=True`, the bar must be moving toward the
level: close < open for rev_lo; close > open for rev_hi.  Default is
False — empirical A/B (see "Bearish-body filter A/B" below) showed
this filter rejected ~51% of qualifying candidates with no measurable
change in direction-rate or mean forward return.  Production now
fires on both bullish-body and bearish-body candidates that meet the
proximity + long-wick conditions.

Long rejection wick (hammer / shooting-star body)
For rev_lo, the lower wick — the distance from min(open, close) to low —
must be at least ``wick_min`` × (high − low). This captures the
buyer-stepped-in intraday rejection that distinguishes a real reversal
from a straight slide through support. For rev_hi the upper wick —
high − max(open, close) — is required.

Score = 1 − proximity / proximity_pct
1.0 when price is exactly at the prior peak; 0.0 at the boundary.

Valid for up to ``valid_bars`` bars after firing (time-bounded only).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
rev_lo (side='lo'):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_lo --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d
  run_id=28  n=1829  direction_rate=58.6%  p<0.001
  bench_flw=0.049  bench_rev=0.028  mean_bars=13.0  (mag_flw=0.083  mag_rev=0.067)
  → RECOMMEND (FLW) — strong and significant; best direction_rate among high-n signs
rev_lo low-corr only (run_id=43, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_lo --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=356  direction_rate=57.9%  p≈0.003  bench_flw=0.043
  → Note: corr-neutral; support-test thesis holds regardless of index coupling
rev_hi (side='hi'):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_hi --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d
  run_id=29  n=2180  direction_rate=50.5%  p≈0.64
  bench_flw=0.039  bench_rev=0.034  mean_bars=12.4  (mag_flw=0.077  mag_rev=0.069)
  → SKIP — no directional edge at prior-high resistance
rev_hi low-corr only (run_id=44, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign rev_hi --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=520  direction_rate=53.8%  p≈0.083  bench_flw=0.042
  → Note: slight improvement on low-corr stocks but still borderline; remains SKIP
```

## Bearish-body filter A/B (2026-05-16)

Probe: `src/analysis/rev_lo_filter_ab_probe.py`. Tests whether the
`close < open` directional filter on `rev_lo` adds signal, or whether
it just rejects what would otherwise be useful fires. Two arms share
the proximity + long-lower-wick checks; only the body condition differs.

Cohort: 219 stocks (`classified2024`) over FY2024 + FY2025 + 2026 YTD.
Bootstrap: 10,000 iters of (without − with) on direction rate and
mean forward 10-bar return. Pre-registered gate locked before the
data was touched.

### Result (ALL aggregate)

| | n | DR | mean fwd 10-bar |
|---|---:|---:|---:|
| with filter (production) | 1,538 | 59.6% | +1.37% |
| without filter | 3,143 | 59.3% | +1.42% |
| Δ (without − with) | ×2.04 | **−0.25pp [CI −3.26, +2.80]** | **+0.04pp [CI −0.33, +0.42]** |

**Initial verdict (2026-05-16): AMBIGUOUS — keep filter.**
**Revised decision (same day): DROP filter from default.**

The filter rejects ~51% of qualifying candidates but the DR and mean
return are essentially unchanged. The bootstrap CI on ΔDR is wide
(±3pp), so we can't formally prove the filter does nothing. But the
point estimates are within 0.3pp on DR and 0.05pp on mean return at
n≈5000 across both arms — that's not a borderline result, that's
zero. The "AMBIGUOUS, keep filter" framing applied the wrong
asymmetry: it puts the burden of proof on dropping a no-op filter
forever, just because the bootstrap CI is wide.

**The right asymmetry is**: demand tight CIs to *add* a gate
(overfitting risk); demand demonstrated zero-effect to *drop* a
no-op filter (here: point Δ < 0.5pp DR, n > 1000 per arm, rejection
rate > 30% — all met).

**Action taken (2026-05-16)**: `RevPeakDetector` gained a
`bearish_body_filter: bool = False` parameter.  Default OFF means
production rev_lo / rev_hi now fire on both body directions.  Pass
`True` to recover historical strict-hammer behaviour (e.g. to
reproduce pre-change benchmark numbers).

The bench numbers in the "Benchmark notes" section above were
generated under the strict-filter behaviour and are therefore stale
for the new default.  `sign_benchmark_runs.code_hash` will mark
rev_lo as stale automatically; re-bench at next convenient
opportunity to refresh published numbers.

### Operational implication

The bullish-body candidates that the filter rejects (bar tests prior
LOW with a long lower wick but closes above open — e.g. a gap-down
followed by intraday recovery) are NOT empirically inferior to the
filtered fires. Operator may consider taking such candidates manually
when seen in the chart, but the sign won't auto-fire them.

To safely drop the filter from production, one of:

1. Wait for tighter CI (need ~3-4× current sample size; ≈2-3 years more data).
2. Live A/B in two virtual accounts over 3-6 months using the
   accounts feature; if no-filter account doesn't materially trail,
   the change is justified empirically.
3. Make the filter a `bearish_body_filter: bool = True` parameter
   on `RevPeakDetector` (default unchanged) so the live A/B above is
   possible without forking the detector.

## rev_lo × breadth regime interaction (2026-05-16)

Probe: `src/analysis/rev_lo_filter_regime_ab.py`. Same A/B as above
but stratified per fire by the AND-gate (`rev_nhi` top-quintile
breadth ∧ `SMA50` top-quintile breadth) at the fire date — the
"concentrated reversal-risk regime" from
`docs/analysis/breadth_indicators.md`.

The stratification did NOT clarify the filter question (AND-HIGH cell
CI is too wide at ±13pp), but it surfaced an unrelated and stronger
finding:

| cell | n (with-filter) | DR (with-filter) |
|---|---:|---:|
| AND-OFF (normal regime) | 1,463 | **60.8%** |
| AND-HIGH (rev_nhi ∧ SMA50 both top-Q) | 80 | **37.5%** |

`rev_lo`'s direction rate drops from ~60% → ~38% on AND-HIGH days at
the pooled level — a 23pp swing.  Pooled binomial 95% CI on n=80,
p=0.375 is [26.9%, 48.1%], upper bound below 50%.  **This pooled
finding does NOT survive per-cohort verification — see next section.**

The same compression is weakly visible on single-indicator HIGH cells
(rev_nhi HIGH only: DR 48.8%; SMA50 HIGH only: DR 50.3%), but the
strongest effect concentrates on AND-HIGH.

### Per-cohort verification (2026-05-16) — POOLED FINDING REJECTED

Probe: `src/analysis/rev_lo_and_high_cohort_check.py`.  Pre-registered
gate locked before splitting: **ship `BREADTH_VETO` only if 3-of-3
cohorts show AND-HIGH DR < 50% AND AND-HIGH DR < same-cohort AND-OFF
DR**.

| cohort | AND-HIGH n | AND-HIGH DR | 95% Wilson CI | AND-OFF DR | gate |
|---|---:|---:|---|---:|---|
| FY2024 | 3 | 0.0% | [0.0, 56.2] | 52.9% | ✓ (but n=3 unreliable) |
| FY2025 | **33** | **51.5%** | [35.2, 67.5] | 64.4% | ✗ (DR ≥ 50%) |
| 2026 YTD | 17 | 47.1% | [26.2, 69.0] | 52.7% | ✓ marginal |
| ALL (pooled) | 80 | 37.5% | [27.7, 48.5] | 60.8% | — pre-stratification |

**Result: 2/3 cohorts pass → don't ship the gate.**

The pooled finding was driven by:

1. **FY2024 had only n=3 fires** in the AND-HIGH cell (3 losses in a
   row = 0% DR is noise at this n, not signal).
2. **FY2025 — the cohort with the largest n=33 — shows DR = 51.5%**,
   above 50%.  The "rev_lo fades in AND-HIGH" thesis literally does
   not hold in the cohort where it has the most data to test.
3. **~27 of the 80 pooled fires** fall in FY2023 (Apr 2023 → Mar 2024),
   not covered by any of the named individual cohorts — partly
   inflating the pooled finding.

The within-cohort comparison (AND-HIGH DR < AND-OFF DR) still holds
across all three (delta is positive every cohort) — so AND-HIGH is
genuinely *worse* than AND-OFF for `rev_lo` — but the **absolute
level isn't reliably below 50%**, and the gate test specifically
requires that.

### Operational implication (corrected)

The aggregate finding is **suggestive but not robust enough for
production automation**.  Treatment:

- **No code change**: `BREADTH_VETO` entry NOT added to
  `src/analysis/regime_ranking.py`.
- **Operator discretion**: when the Daily-tab regime banner shows
  **BOTH HIGH ▲▲** AND a `rev_lo` candidate appears, the `rev_lo`
  edge is empirically weaker (delta consistently negative across
  cohorts, even though absolute DR isn't reliably < 50%).  Reasonable
  to weigh this when deciding Skip vs Register, but not strong enough
  to skip blindly.
- **Re-examine after more data**: if n in AND-HIGH cell crosses 100
  per cohort, the test may resolve.  Current accrual rate suggests
  ~2-3 more years.

### Methodology note

This is now the **6th finding in 2026-05 where pooled bootstrap
results sign-flipped or weakened under per-cohort stratification**
(see memory `project_timestop40_bootstrap_reject.md`,
`project_breadth_gate_probe_reject.md` and siblings).  The codified
discipline — "pooled CI is necessary-but-not-sufficient; per-cohort
robustness is the binding gate before production ship" — caught this
one as designed.

