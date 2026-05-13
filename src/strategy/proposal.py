"""SignalProposal — lightweight proposal type for RegimeSignStrategy."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

"""
  Identity

  ┌─────────────┬──────────┬───────────────────────────────────────────────────────────┐
  │    Field    │   Type   │                          Source                           │
  ├─────────────┼──────────┼───────────────────────────────────────────────────────────┤
  │ sign_type   │ str      │ Detector that fired (str_hold, div_gap, ...)              │
  ├─────────────┼──────────┼───────────────────────────────────────────────────────────┤
  │ stock_code  │ str      │ Ticker                                                    │
  ├─────────────┼──────────┼───────────────────────────────────────────────────────────┤
  │ fired_at    │ datetime │ Bar where condition first satisfied                       │
  ├─────────────┼──────────┼───────────────────────────────────────────────────────────┤
  │ valid_until │ datetime │ Last bar in validity window (typically fire + valid_bars) │
  └─────────────┴──────────┴───────────────────────────────────────────────────────────┘

  Sign-internal

  ┌────────────┬───────┬───────────────────────────────────────────────────────────────┐
  │   Field    │ Type  │                            Source                             │
  ├────────────┼───────┼───────────────────────────────────────────────────────────────┤
  │ sign_score │ float │ Raw score from detector's _score() method — higher = stronger │
  └────────────┴───────┴───────────────────────────────────────────────────────────────┘

  Correlation context (vs ^N225 at fire date)

  ┌───────────┬───────┬──────────────────────────────────────────────────┐
  │   Field   │ Type  │                      Source                      │
  ├───────────┼───────┼──────────────────────────────────────────────────┤
  │ corr_n225 │ float │ 20-bar rolling returns correlation to ^N225      │
  ├───────────┼───────┼──────────────────────────────────────────────────┤
  │ corr_mode │ str   │ "high" (|corr|≥0.6) / "mid" / "low" (|corr|≤0.3) │
  └───────────┴───────┴──────────────────────────────────────────────────┘

  N225 regime context (at fire date)

  ┌────────────┬───────┬─────────────────────────────────────┐
  │   Field    │ Type  │               Source                │
  ├────────────┼───────┼─────────────────────────────────────┤
  │ kumo_state │ int   │ +1 above Kumo / 0 inside / −1 below │
  ├────────────┼───────┼─────────────────────────────────────┤
  │ adx        │ float │ N225 ADX(14)                        │
  ├────────────┼───────┼─────────────────────────────────────┤
  │ adx_pos    │ float │ N225 +DI                            │
  ├────────────┼───────┼─────────────────────────────────────┤
  │ adx_neg    │ float │ N225 −DI                            │
  └────────────┴───────┴─────────────────────────────────────┘

  Historical-benchmark lookup for (sign × kumo_state) cell

  ┌──────────────────┬───────┬─────────────────────────────────────────────────────────┐
  │      Field       │ Type  │                         Source                          │
  ├──────────────────┼───────┼─────────────────────────────────────────────────────────┤
  │ regime_dr        │ float │ Directional rate from multi-year benchmark              │
  ├──────────────────┼───────┼─────────────────────────────────────────────────────────┤
  │ regime_n         │ int   │ Event count behind the estimate                         │
  ├──────────────────┼───────┼─────────────────────────────────────────────────────────┤
  │ regime_bench_flw │ float │ Legacy upside-only: DR × mag_flw                        │
  ├──────────────────┼───────┼─────────────────────────────────────────────────────────┤
  │ regime_ev        │ float │ Primary ranking metric: DR × mag_flw − (1−DR) × mag_rev │
  └──────────────────┴───────┴─────────────────────────────────────────────────────────┘

  Layer 2 — Resolved AFTER fire (not on live proposal)

  From SignBenchmarkEvent (src/analysis/models.py:197) — these are populated only after the validation horizon completes:

  ┌─────────────────┬───────┬──────────────────────────────────────────────────────────────┐
  │      Field      │ Type  │                            Notes                             │
  ├─────────────────┼───────┼──────────────────────────────────────────────────────────────┤
  │ trend_direction │ int   │ +1=HIGH-first, −1=LOW-first (within next zigzag horizon)     │
  ├─────────────────┼───────┼──────────────────────────────────────────────────────────────┤
  │ trend_bars      │ int   │ Bars from entry to first confirmed peak                      │
  ├─────────────────┼───────┼──────────────────────────────────────────────────────────────┤
  │ trend_magnitude │ float │ |peak − entry| / entry (the signed_mag the calibration uses) │
  └─────────────────┴───────┴──────────────────────────────────────────────────────────────┘

  Layer 3 — Analytical labels associated with the sign (not on the proposal struct)

  These live in src/analysis/benchmark.md tables and are looked up offline / for UI display; the proposal carries cell-aggregate stats (Layer 1 regime_*) but the per-sign analytics below are accessible via the
   calibration tables, not the proposal object itself:

  ┌──────────────────────────────────────────────────┬───────────────────────────────────────────┐
  │                     Quantity                     │                  Source                   │
  ├──────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ Spearman ρ_dir (sign_score vs trend_direction)   │ sign_score_calibration § Calibration      │
  ├──────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ Spearman ρ_|mag| (sign_score vs trend_magnitude) │ same                                      │
  ├──────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ Q1–Q4 EV by score quartile                       │ same                                      │
  ├──────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ Per-regime cell: mag_flw, mag_rev, EV, perm_pass │ sign_regime_analysis § Regime Analysis    │
  ├──────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ OOS FY2025 regime-gated DR                       │ sign_benchmark_multiyear --phase backtest │
  └──────────────────────────────────────────────────┴───────────────────────────────────────────┘

  Layer 4 — Computed via lookups around fire-time but NOT in SignalProposal

  These are inside the codebase but not exposed on the proposal struct — they get computed inside the detector or recomputed at ranking time:

  ┌───────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────────┬────────────────────────────────────────────────┐
  │                                   Quantity                                    │            Where computed            │              Why not on proposal               │
  ├───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Score sub-components (e.g. str_hold's rel_gap, consistency, n225_depth_bonus) │ _score() inside each detector        │ Collapsed into sign_score before return        │
  ├───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Recent zigzag legs (zs_history)                                               │ compute_exit_levels at register time │ Attached to EntryCandidate, not SignalProposal │
  ├───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Per-stock historical MAE_03 / r_k3 (cycle-7 cache)                            │ offline probe only                   │ Not piped to live proposal                     │
  └───────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────┴────────────────────────────────────────────────┘

  What's missing that you might expect

  - No per-stock prior (this session's K-bar stationarity probe just refuted it as useful)
  - No corr-to-^GSPC field, despite the daily-tab chart showing it
  - No CorrRegime fraction (universe-wide |corr|>0.70 share — mentioned in CLAUDE.md as a gating concept but not threaded through to SignalProposal)
  - No volume / liquidity context at fire
  - No fired-volatility (ATR or realized σ at fire)

"""

@dataclass(frozen=True)
class SignalProposal:
    """One trade candidate emitted by RegimeSignStrategy.propose().

    Attributes:
        sign_type:        Sign detector that fired (e.g. "str_hold").
        stock_code:       Ticker of the stock.
        sign_score:       Raw score from the detector (higher = stronger signal).
        fired_at:         Datetime the sign condition was first satisfied.
        valid_until:      Last bar within the sign's validity window.
        corr_mode:        "high" (|corr|≥0.6) / "mid" / "low" (|corr|≤0.3) vs ^N225.
        corr_n225:        Actual 20-bar rolling correlation at fired_at.
        kumo_state:       N225 Ichimoku Kumo state at fired_at: +1 above / 0 inside / -1 below.
        adx:              N225 ADX(14) at fired_at.
        adx_pos:          N225 +DI at fired_at.
        adx_neg:          N225 -DI at fired_at.
        regime_bench_flw: Legacy bench_flw for (sign, kumo_state) — DR × mag_flw only (upside).
        regime_ev:        Expected return per trade for (sign, kumo_state):
                          DR × mag_flw − (1−DR) × mag_rev. Primary ranking metric.
        regime_dr:        Direction-rate for (sign, kumo_state) from benchmark history.
        regime_n:         Event count behind the benchmark estimate.
    """

    sign_type:        str
    stock_code:       str
    sign_score:       float
    fired_at:         datetime.datetime
    valid_until:      datetime.datetime
    corr_mode:        str    # "high" | "mid" | "low"
    corr_n225:        float
    kumo_state:       int    # +1 / 0 / -1
    adx:              float
    adx_pos:          float
    adx_neg:          float
    regime_bench_flw: float
    regime_ev:        float
    regime_dr:        float
    regime_n:         int

    def __str__(self) -> str:
        return (
            f"{self.sign_type:<12} {self.stock_code:<10} "
            f"score={self.sign_score:.3f}  corr={self.corr_mode}({self.corr_n225:+.2f})  "
            f"kumo={'▲' if self.kumo_state==1 else ('▼' if self.kumo_state==-1 else '~')}  "
            f"adx={self.adx:.1f}  EV={self.regime_ev:+.4f}  dr={self.regime_dr:.1%}"
        )
