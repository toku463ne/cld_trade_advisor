# rev_nhi synthetic-flip probe

Probe run: 2026-05-19.  Re-runs regime_sign baseline (no exclusions), filters to rev_nhi-originated trades only, and compares original-long return distribution against synthetic-flipped (`return_pct → −return_pct`).

## Setup

- Sign: `rev_nhi`
- Total rev_nhi trades across FY2019-FY2025: **9**
- Strategy: regime_sign with default min_dr=0.52, ZsTpSl(2.0,2.0,0.3)
- **Caveat**: Synthetic flip only inverts the return sign at close.  A real short would hit SL when price RISES (not falls), so the time-to-exit and which trades hit TP-vs-SL would differ.  If this probe shows a clean positive after flip, the proper next step is a mirrored short simulator.

## Aggregate

| arm | n | mean_r | Sharpe | win% | avg_win | avg_loss | EV check |
|---|---:|---:|---:|---:|---:|---:|---:|
| rev_nhi long (original) | 9 | -5.18% | **-11.96** | 33.3% | +2.56% | -9.05% | -5.18% |
| rev_nhi flipped (synthetic short) | 9 | +5.18% | **+11.96** | 66.7% | +9.05% | -2.56% | +5.18% |

## Per corr_mode

| corr_mode | arm | n | mean_r | Sharpe | win% | avg_win | avg_loss | EV check |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **low** | long original |  9 | -5.18% | **-11.96** | 33.3% | +2.56% | -9.05% | -5.18% |
| low | flipped       |  9 | +5.18% | **+11.96** | 66.7% | +9.05% | -2.56% | +5.18% |

## Verdict — PARK (n=9 too thin to act on)

The Sharpe inversion +11.96 ↔ −11.96 is **mechanical**: a return-sign flip
makes mean negate while stdev is invariant to sign, so Sharpe inverts
exactly.  The probe doesn't *prove* rev_nhi is an inverse signal — it
shows what existing trades' returns would look like as shorts.

What the **distribution shape** does tell us:

- 6 of 9 rev_nhi LONG trades lost money; avg loss −9.05% vs avg win +2.56%.
- **Losses are 3.5× larger than wins** — the signature of entering long
  into a bearish-direction signal (price keeps going down → SL hits hard;
  the few wins exit on TP at much smaller magnitudes).
- All 9 trades are **LOW-corr** (stock-specific).  rev_nhi never produces
  a high-corr trade that survives the regime_sign filter.

So direction is probably wrong — but **n=9 is too thin to act on**:
- ~1-2 trades per FY.
- Any bootstrap CI would be enormous (likely [−20, +40] or wider).
- A mirrored short simulator would still produce n=9 results — same trap.

**Decision: park.**  Existing UI-only salvage (`_HIDDEN_PROPOSAL_SIGNS`
includes rev_nhi) already captures discretionary value.  Universe
expansion is the unblocker — when stock count and effective n rise,
this and ~5 other parked salvage paths become testable.

- Original long: n=9, Sharpe −11.96, win 33%, mean_r −5.18%
- Flipped:       n=9, Sharpe +11.96, win 67%, mean_r +5.18%
- Δ Sharpe = +23.92  ← mechanical inversion, not new evidence
