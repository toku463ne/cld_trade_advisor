# div_peer — Intra-cluster Divergence sign detector
Fires on the first hourly bar of a trading day when:
- Stock daily return > +STOCK_RET_MIN   (+0.5 %)
- ≥ PEER_DOWN_FRAC (60 %) of the cluster peers have daily return < PEER_DOWN_MIN (−0.3 %)

Daily returns are derived from hourly caches (last close of each date).

Score = min(stock_ret / 0.02, 1.0) × peer_down_fraction
A stock rising strongly (+2 %) while all peers fall scores 1.0.
A marginal rise (+0.5 %) while 60 % of peers are down scores 0.3 × 0.6 = 0.18.

Valid for up to ``valid_bars`` *trading days* after firing (default 1 — the
underlying signal is a single-day close-to-close peer return, so a longer
validity window would let stale fires linger past the period actually measured).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
uv run --env-file devenv python -m src.analysis.sign_benchmark \
    --sign div_peer --cluster-set classified2023 \
    --start 2023-04-01 --end 2025-03-31 --gran 1d
run_id=33  n=474  direction_rate=57.4%  p≈0.001
bench_flw=0.048  bench_rev=0.031  mean_bars=12.4  (mag_flw=0.084  mag_rev=0.072)
→ RECOMMEND (FLW) — significant; intra-cluster divergence is a reliable follow-through signal
Low-corr only (run_id=40, --corr-mode low):
  uv run --env-file devenv python -m src.analysis.sign_benchmark \
      --sign div_peer --cluster-set classified2023 \
      --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
  n=97  direction_rate=47.8%  p≈0.665  bench_flw=0.035
  → Note: reverses on low-corr stocks (p not significant, below 50%); use on all corr regimes

## Cluster-size analysis (2026-05-16)

Probe: `src/analysis/div_peer_cluster_size_probe.py`.  Pulls every
`div_peer` `SignBenchmarkEvent` across FY2019–FY2025 and joins each
fire with the firing stock's cluster size in its run's stock_set,
then aggregates per (size_bucket × FY).

Motivation: the `peer_down_frac ≥ 60%` threshold is structurally
trivial at very small cluster sizes — at cluster size 2 it requires
1 of 1 peer to be down (= 100%, trivially clears the gate); at size
3 it requires 2 of 3 (= 66.7%, the only way to clear).  The
threshold only becomes statistically meaningful at size ≥ 4.

### Per-(size_bucket × FY) EV grid

| bucket | FY2019 | FY2020 | FY2021 | FY2022 | FY2023 | FY2024 | FY2025 | pooled EV (n) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| size=2 | −4.22% | +6.61% | −0.54% | +0.75% | +2.34% | −0.55% | +7.22% | **+1.17%** (n=503) |
| size=3 | −4.66% | +0.63% | +2.46% | +5.42% | +6.21% | +1.66% | +0.08% | **+3.48%** (n=161) |
| size=4 | −2.47% | — | — | +2.52% | +7.16% | +8.05% | — | **+4.27%** (n=69) |
| size≥5 | — | — | −2.27% | — | — | — | — | −2.27% (n=7) |

(EV is direction-weighted: `DR × mag_flw − (1−DR) × mag_rev`.)

### Findings

1. **The cluster-size effect IS empirically real**: monotonic EV
   increase from size=2 (+1.2 %) → size=3 (+3.5 %) → size=4 (+4.3 %)
   on the pooled sample.  Size ≥ 5 inverts but n=7 is too small to
   read.
2. **classified2024 live universe is structurally too sparse to
   test it**: only 3 multi-stock clusters covering 7 stocks (sizes
   2, 2, 3).  Only the 3-stock {9101, 9104, 9107} (shipping)
   cluster can produce size≥3 fires, and it only fired 3 times in
   FY2025.
3. **Pre-registered gate to ship a `min_peer_count` parameter**
   (size≥3 EV ≥ +0.015 at n ≥ 30 per FY in EACH of FY2023, FY2024,
   FY2025) **FAILED 2/3**:
   - FY2023: n=49, EV=+6.40 % → PASS
   - FY2024: n=39, EV=+4.45 % → PASS
   - FY2025: n=**3**, EV=+0.08 % → FAIL (n far below 30)
4. **The headline DR=57.4 % from the FY2023–2024 benchmark above
   IS legitimate**, but it pools across cluster sizes.  Operators
   reading the headline should know that the per-trade edge varies
   substantially by cluster size (and not by enough margin yet at
   FY2025 to gate on).

### Operational implication

`RegimeSignStrategy` and `DivPeerDetector` are unchanged.  Cluster
size is surfaced in the Daily-tab UI as a §5.11 decision factor
(panel row "Cluster size") so the operator can apply per-trade
judgment when a div_peer candidate appears — small-cluster fires
(size=2) carry weaker historical edge than larger-cluster fires
(size ≥ 3).

### Salvage paths (untested)

1. **Re-cluster classified2024 at a looser correlation threshold**
   (currently 0.3 — lowering to 0.4–0.5 might produce more
   multi-stock clusters).  Universe-level fix; benefits any future
   sign using cluster membership, not just `div_peer`.
2. **Replace correlation-cluster peers with sector17 peers** —
   every stock would have 10–30 peers naturally, making the
   peer-down threshold statistically meaningful for the entire
   universe.  Changes the semantic of "peer" but solves the
   sparsity problem cleanly.
3. **Wait 12–18 months** — FY2026 + FY2027 data may push per-FY
   size≥3 cells across n ≥ 30; re-run the probe then.

### Full /sign-debate record

See memory `project_div_peer_cluster_size_reject.md` for the full
debate cycle (analyst → historian → proposer → critic → judge),
including the 4 H-severity holes the critic identified in the
initial `min_peer_count=3` proposal and the judge's flip to Option
F (UI disclosure) as the substitute.
```
