# Watchlist Sign Suggestions

Candidate signals for populating the daily watchlist.
Each sign fires on a stock and adds it to the watchlist with a score.
The human reviews the list and decides whether to act manually.
A backtest simulator uses the same signs to evaluate exit-rule parameters.

Naming convention: `<group>_<mechanism>`
- `div_*`  — stock diverges from a benchmark
- `corr_*` — correlation regime change
- `str_*`  — relative strength / resilience
- `brk_*`  — price-level breakout (technical)

---

## Already validated

### `div_bar` — N225 Divergence (open-bar)
**Status:** statistically significant across 164-stock universe (2024-05 – 2025-12)

**Condition (all must hold on the same hourly bar):**
- N225 return on bar < −1.5 %
- Stock return on same bar > +0.3 %
- Rolling 20-bar correlation (stock vs N225) at that bar > +0.30

**Evidence:**
- 159 events, 105 unique stocks
- Next-7-bar positive rate: **64.2 %** vs baseline 52.3 % (binomial p = 0.0017)
- Mean next-7-bar return: +0.15 % vs +0.04 % baseline
- Magnitude noisy (t-test p = 0.53) → treat as directional sign, not magnitude predictor

**Interpretation:**
Market is selling N225 leaders and rotating into this stock.
The buying pressure tends to persist into the next trading session.

**Implementation note:**
Sign fires on the bar close; entry is next-day open.
The event is visible in the Moving Correlation UI as the bar where
the correlation panel flips sharply negative while the price bar is green.

---

## Group 1 — Buildable from existing data

### `corr_flip` — Correlation Regime Flip (negative → positive)
**Condition:**
- Rolling 20-bar corr has been < 0 for 5+ consecutive bars
- Current bar: corr crosses above 0

**Rationale:**
The "independent phase" the stock entered (e.g., after a `div_bar` event) is ending.
The stock may now rejoin market upside momentum.
Complements `div_bar`: `div_bar` catches the entry into divergence, `corr_flip` catches the re-coupling.

**Data needed:** moving_corr table (already populated for 1h and 1d).

**How to find samples in Moving Correlation UI:**
- Granularity: `1h` | Window: `20`
- Look for: corr(^N225) panel staying red (< 0) for several consecutive bars, then crossing back above 0
- Suggested period: `2024-07-12` → `2024-08-31` — many stocks flipped negative after the July sell-off and re-coupled in August

---

### `str_hold` — Multi-day Relative Strength During Decline
**Condition:**
- Over a rolling 5-trading-day window:
  - N225 cumulative return < −2 %
  - Stock cumulative return > −0.5 % (flat or positive)
- At least 3 of the 5 days individually show (stock return ≥ N225 return)

**Rationale:**
Sustained divergence over multiple sessions is stronger evidence of real rotation
than a single large bar. Filters out one-off news spikes.

**Data needed:** hourly OHLCV (existing). Compute rolling 5-day return from close prices.

**How to find samples in Moving Correlation UI:**
- Granularity: `1d` | Window: `20`
- Look for: a multi-day window where the N225 panel (row 3) trends down with confirmed zigzag lows, but the stock price panel stays flat or climbs
- Suggested period: `2024-07-01` → `2024-08-15` (N225 fell ~15 % during this window)

---

### `str_lead` — Post-N225-Bottom Leader
**Condition:**
- ^N225 zigzag confirms a LOW (direction = −2) on bar T
- Look back over the preceding decline (from prior HIGH to this LOW)
- Stock drawdown during that window < 0.5 × N225 drawdown

**Rationale:**
Stocks that held up during a market decline are typically the first to
outperform on recovery. Identifies defensive demand and rotation targets
before the bounce begins.

**Data needed:**
- zigzag peaks on ^N225 (already computed in peak_corr runs)
- OHLCV for drawdown calculation

**How to find samples in Moving Correlation UI:**
- Granularity: `1d` | Window: `20`
- Look for: a confirmed zigzag LOW (▼ marker) in the N225 panel, then compare the stock's drawdown depth in the preceding window vs N225's drop
- Suggested period: `2024-07-01` → `2024-09-30` (N225 confirmed bottom around 2024-08-05)

---

### `div_peer` — Intra-cluster Divergence
**Condition:**
- On the same trading day, a stock's return is > +0.5 %
- At least 60 % of the other members of its classified2023 cluster are down > 0.3 %

**Rationale:**
When one stock rises while its correlated peers fall, a stock-specific catalyst
is likely at work (earnings surprise, contract win, index rebalancing).
The cluster membership from StockClusterMember provides the peer group.

**Data needed:** cluster membership (StockClusterMember table, existing).

**How to find samples in Moving Correlation UI:**
- Not directly observable — requires peer comparison across cluster members
- Closest proxy: Granularity `1d` | Window: `20`; look for a day where the stock has a sharp green candle while the N225 panel shows a down bar and corr drops sharply
- To confirm, cross-check the same date against a few other stocks in the same cluster

---

## Group 2 — Requires one new computed column

### `div_vol` — Volume-Confirmed Divergence
**Condition:** `div_bar` criteria PLUS:
- Volume on the divergence bar > 2 × 20-bar rolling average volume

**Rationale:**
Institutional accumulation leaves a volume signature.
A green bar with above-average volume during a market selloff is more
reliable than a low-volume drift. Strengthens the `div_bar` sign.

**Implementation note:**
Volume is already stored in the OHLCV tables.
Add a rolling average volume column at sign computation time.

**How to find samples in Moving Correlation UI:**
- Granularity: `1h` | Window: `20`
- Look for: same pattern as `div_bar` (corr flips negative while price bar is green), AND the volume bar (row 2) on that bar is visibly taller than its neighbors
- Suggested period: `2024-07-01` → `2024-12-31`

---

### `div_gap` — Opening Gap Divergence
**Condition (checked at first bar of each session):**
- Stock open > previous session close (gap up)
- N225 open < previous N225 close (gap down)
- Gap magnitude: stock gap > +0.5 %, N225 gap < −0.5 %

**Rationale:**
Overnight buyers already acted before the Japanese market opened.
Cleaner than an intraday divergence bar — no ambiguity about whether
the move developed during the session or was pre-positioned.

**How to find samples in Moving Correlation UI:**
- Granularity: `1h` | Window: `20`
- Look for: the first bar of a session (09:00 JST) where the stock candle opens visibly higher than the prior session's close, while the N225 panel opens lower
- Suggested period: `2024-08-01` → `2024-08-10` (volatile open gaps around the August crash)

---

## Group 3 — Longer-horizon signals using daily data

### `corr_shift` — Overseas Correlation Crossover
**Condition (daily granularity):**
- 10-day rolling corr with ^GSPC (or ^GDAXI) is rising: Δcorr > +0.15 over 5 days
- 10-day rolling corr with ^N225 is falling: Δcorr < −0.15 over same 5 days

**Rationale:**
When a JP stock reprices off US/EU factors rather than domestic ones it often
signals earnings exposure (USD revenue, commodity prices) or global sector
rotation. The moving_corr table at 1d granularity with all 5 indicators
makes this computable without new data collection.

**Data needed:** moving_corr table at 1d (existing, 5 indicators).

**How to find samples in Moving Correlation UI:**
- Granularity: `1d` | Window: `10`
- Look for: the ^N225 corr panel trending downward over 5+ bars while the ^GSPC or ^GDAXI panel trends upward over the same bars
- Suggested period: `2024-01-01` → `2024-12-31` (full year gives the most crossover events)

---

### `corr_peak` — Peak Correlation B-Metric Alignment
**Condition:**
- For a given stock, `peak_corr.mean_corr_b` for ^N225 DOWN peaks is negative
  (stock tends to *rise* in the 5-bar window after an N225 confirmed low)
- ^N225 has just confirmed a new zigzag LOW (direction = −2)

**Rationale:**
The peak_corr B-metric captures what a stock typically does in the 5 bars
*after* a major index peak. Stocks with negative B on DOWN peaks are natural
buyers when N225 bottoms. Combines the structural analysis (peak_corr run)
with a live event (new confirmed low).

**Data needed:** peak_corr_results table (existing), live zigzag on ^N225.

**How to find samples in Moving Correlation UI:**
- Granularity: `1d` | Window: `20`
- Look for: a confirmed zigzag LOW (▼ marker) in the N225 panel, then observe whether the stock price rises in the 5 bars immediately after that marker
- Best stocks to try: ones with negative `mean_corr_b` for ^N225 DOWN peaks in the Peak Correlation table
- Suggested period: `2024-07-01` → `2024-09-30` (N225 confirmed bottom around 2024-08-05)

---

## Exit Rules (first version)

For backtesting, use fixed TP/SL with a maximum hold period:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Entry | Next session open after sign | Realistic; avoids using the signal bar itself |
| Take Profit | +4 % from entry | Let winners run; matched to ~2:1 reward/risk |
| Stop Loss | −2 % from entry | Cut losers quickly |
| Max hold | 5 trading days (~35 hourly bars) | Prevents tying up capital in stalled trades |
| Exit logic | First of TP / SL / max-hold to trigger | Whichever occurs first on any hourly bar |

**Expected value at `div_bar` sign quality:**
```
EV = 0.642 × 4% − 0.358 × 2% = +1.85% per trade (theoretical, pre-slippage)
```

**One-position-per-stock rule:**
If a second sign fires on a stock while a position is already open,
skip it. The simulator does not pyramid or re-enter until the prior
position closes.

**Parameters to tune in backtest:**
Vary TP in [3%, 4%, 5%] × SL in [1.5%, 2%, 3%] × hold in [3, 5, 7 days].
Use 2024 dev data to select parameters; validate on 2025 eval data.
