# gspc_corr_accel probe — Round-3 terminal falsifier

Generated: 2026-05-15  
Window: 20d return-corr, Δ over 10 bars; pooled from 2020-06-01 onward.  
Universe: 223 active stocks with ≥120 bars.

## Critic's variant gate (probe spec)
- Δ10_corr_gspc ≥ +0.20
- |Δ10_corr_n225| ≤ 0.10
- trailing-5-bar GSPC return ≥ +0.5%
- confirmed zigzag LOW (dir=-2, size=5)
- CorrRegime gate SKIPPED (moving_corr table empty in dev DB)

## Falsifier #1 — joint bar-day count (Critic H#1, Judge falsifier)
Pre-registered floor: **≥ 10,000 bar-days**.
- Observed (sans zigzag): **13,026** bar-days (4.048% of 321,810 eligible bar-days)

**PASS — falsifier #1 cleared.**

## Falsifier #2 — Δmean_r vs matched-null at H=10 (Judge falsifier)
Pre-registered floor: **Δmean_r ≥ +0.35%**.

- Fire events (cell ∩ zigzag-LOW): n=412
- Null pool   (zigzag-LOW ∖ cell): n=17113
- Matched-null subsample (per-stock balanced): n=419

### mean forward return by horizon

| arm | H=5 | H=10 | H=20 |
|-----|------|------|------|
| fire | +3.46% | +3.92% | +4.27% |
| matched-null | +3.84% | +5.32% | +6.01% |
| **Δmean_r** | **-0.38%** | **-1.40%** | **-1.73%** |

**FAIL — falsifier #2 triggered at H=10.** Δmean_r = -1.40% < +0.35%.

## Mechanism monotonicity (Critic H#1, accept gate)
- ΔEV monotone / U-shaped over H ∈ [5, 10, 20]?
- Δmean_r: H=5 -0.38%, H=10 -1.40%, H=20 -1.73%
- shape: **monotone-down (suspect — edge concentrated at short H)**

## ZigZag confirmation lag (Critic H#3)
- ZigZag dir=-2 requires `size=5` bars after the LOW for confirmation → built-in detection lag is **exactly 5 bars**.
- With two-bar fill rule, T_fill = T_actual_low + 6 bars.
- Entry is NOT at-trough; it is at +6 bars post-trough.
- Mechanism implication: the sign trades CONTINUATION after a confirmed trough, not the trough itself.

## Notes for round-3 Judge
- CorrRegime universe gate omitted; would require pre-populating `moving_corr` table. If the cell passes both falsifiers, populating that table is a prerequisite to any production rollout.
- Matched-null is per-stock balanced ZigZag-LOWs OUTSIDE the cell. This is conservative (compares to other zigzag-LOWs, not all bars).
- Composite walk against ZsTpSl is NOT in this probe; that requires `regime_sign_backtest` with a virtual sign — out of autonomous scope.
