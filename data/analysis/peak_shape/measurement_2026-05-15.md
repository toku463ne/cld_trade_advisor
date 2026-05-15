# peak_shape — round-1 measurement step

Generated: 2026-05-15  
Universe: 223 stocks, FY2019-FY2024 confirmed ZigZag(size=5) peaks  
Total (P2, next-same-dir) pairs: 35,605

## Verdict: **ROUND-2 ACCEPT-PATH NARROWED**

## (1) Bars from P2 → next same-direction pivot
Threshold: median ≤ 25 bars (within adx_trail_d8 hold window).

- n = 35,605
- p25 = 12.0  median = **16.0**  p75 = 23.0  p90 = 31.0
- mean = 18.2
- **PASS** — median 16.0 ≤ 25

## (2) Unconditional P(continuation), bull / bear
Threshold: each ∈ [0.40, 0.55] (genuine information room).

- Bull (P2=HIGH, P3.price > P2.price): n=17,890  P = **0.5279**
- Bear (P2=LOW,  P3.price < P2.price): n=17,715  P = **0.4418**
- **PASS** — both in [0.40,0.55]

## (3) (entry_open − P2.price)/ATR14 at fire bar P2.bar_index + 6
Threshold: median < 1.0×ATR (rev_peak-style structural-lateness check).

- n = 35,604
- p25 = 1.171  median = **1.884**  p75 = 2.800  p90 = 3.894
- mean = 2.150
- **FAIL** — median 1.884 ≥ 1.0

## Notes
- This is a measurement step, not a sign or a probe.
- 'Next same-direction pivot' = 2 positions ahead in the confirmed-peaks list (alternation rule).
- ATR window = 14 bars. Displacement is |entry_open − P2.price| / ATR14[P2.bar_index].
- These measurements inform round-2 framing: whether the original HSF primary cell survives, whether the Critic's P1-anchor counter-proposal is structurally better, or whether the whole concept is dead.
