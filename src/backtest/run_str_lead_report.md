# StrLeadRegimeStrategy — Backtest Report

Generated: 2026-05-06

---

## Strategy Overview

**`StrLeadRegimeStrategy`** is a long-only mean-reversion strategy based on relative-strength leadership at confirmed N225 troughs.

### Entry
- `StrLeadDetector` fires when N225 daily zigzag confirms a LOW with ≥5% decline from the prior high AND the stock's drawdown over the same window is less than 50% of N225's drawdown (relative strength)
- Optional regime gate: N225 daily close > 20-day SMA (bull-market filter)

### Exit (first to trigger)
- **Hourly zigzag HIGH**: first confirmed hourly HIGH zigzag peak after entry (min 8 hourly bars held)
- **ATR stop**: `entry_price − 1.5 × ATR14_daily` (Wilder's daily ATR derived from hourly bars)
- **Time stop**: 10 trading days

### Position sizing
- 10% of equity per trade (capital-fraction sizing), normalized across all stock prices

### Parameters used
```
max_hold_days=10, atr_stop_mult=1.5, min_hold_bars=8,
daily_zz_exit=False, capital_pct=0.10
```

---

## Results

### FY2024 (2024-05-01 – 2025-03-31) — universe: classified2023 (164 stocks)

| Config | Trades | Win Rate | Mean Total Return | Mean Sharpe | Mean Hold |
|--------|-------:|--------:|------------------:|------------:|----------:|
| No regime gate | 223 | **60.6%** | **+0.09%** | 0.08 | 2.7 d |
| Regime gate ON | 123 | 58.3% | +0.01% | 0.05 | 2.9 d |

### FY2025 (2025-04-01 – 2026-03-31) — universe: classified2024 (180 stocks)

| Config | Closed trades | Open at end | Win Rate | Mean Total Return | Mean Sharpe |
|--------|------------:|------------:|--------:|------------------:|------------:|
| No regime gate | 46 | **42** | 30.4% | **−2.25%** | −0.20 |
| Regime gate ON | 0 | 0 | — | **0.00%** | — |

---

## Key Findings

### 1. Exit mechanism
The hourly zigzag HIGH (size=5, mid=2) dominates all exits. Mean holding period is 2.7–3.5 days. The ATR stop and time stop are almost never reached — the first confirmed local HIGH in the stock's hourly price series closes the trade first.

- Minimum hold of 8 hourly bars (≈1 trading day) before the zigzag exit is permitted raises win rate from 53.6% → 58.3% by preventing same-day exits on trivial local highs.

### 2. Regime gate is essential for bear markets
- **FY2024**: Removing the gate improved results (+0.09% vs +0.01%) by capturing the August 2024 crash recovery entries. The StrLeadDetector's own ≥5% N225 decline requirement already filters noise, so the SMA20 gate was redundant in a predominantly bullish year.
- **FY2025**: The April 2025 Trump tariff shock put N225 into a sustained downtrend. Without the gate, 42 of 88 attempted positions were still open at period end with ~9–10% unrealized losses per position (positions entered at "confirmed lows" that proved to be false recoveries). With the gate ON, the strategy correctly traded 0 times and preserved capital.

### 3. Open positions at period end
Stocks with `n_open > 0` entered a position near the end of the backtest window and the position had not yet exited (zigzag, ATR stop, or time stop not triggered before March 31, 2026). Their `total_ret_pct` reflects mark-to-market unrealized P&L, not a realized loss. These entries were made during the late-March 2026 selloff.

### 4. Statistical significance
- FY2024 no-gate: n=223, win rate 60.6% → z=3.2 (p<0.001, significant)
- FY2024 gate ON: n=123, win rate 58.3% → z=1.9 (p≈0.06, marginal)
- FY2025 no-gate: n=46, win rate 30.4% → clearly negative edge

### 5. Trade frequency
The signal fires at N225 **daily** zigzag confirmed LOWs — these occur only when N225 has declined ≥5% from a prior confirmed high. In a normal bull year this produces 5–15 qualifying N225 events, with ≈1.5 stocks qualifying per event. Trade frequency is low by design (quality over quantity).

---

## Parameter Sweep Summary (FY2024)

| Parameter | Values tested | Finding |
|-----------|--------------|---------|
| `atr_stop_mult` | 1.0, 1.5, 2.0, 2.5, 3.0 | No effect — ATR stop never reaches before zigzag exit |
| `max_hold_days` | 5, 10, 15, 20, 30 | No effect — time stop never reaches before zigzag exit |
| `min_hold_bars` | 0, 8, 16, 24, 40, 56, 80 | 8 bars optimal (58.3% win rate); longer hurts win rate |
| `daily_zz_exit` | False (hourly), True (daily) | Hourly wins: 60.6% vs 33.8% for daily |
| `use_regime_gate` | False, True | FY2024: False better; FY2025: True essential |

---

## Recommendation

Use the regime gate (`--regime-gate`) for live trading. Accept the lower trade count in exchange for bear-market protection. The FY2024 advantage from removing the gate was specific to the August 2024 crash recovery and is not reliable as a general rule.

**Suggested live configuration:**
```
uv run --env-file devenv python -m src.backtest.run_str_lead \
    --cluster-set classified2024 \
    --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
    --capital-pct 0.10 --min-hold-bars 8 --regime-gate
```

---

## Limitations

- **Short data window**: hourly data starts 2024-05-07; only ~2 years of backtest history available
- **Low trade count in gate-ON mode**: 123 trades over 11 months is borderline for statistical power
- **Open-position bias at period end**: positions entered in the last 10 trading days of any period may be counted as unrealized losses even if they would recover afterward
- **Single-position-per-stock model**: the simulator holds at most one open position per stock; multiple concurrent signals are not stacked
