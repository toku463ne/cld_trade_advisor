# Japan Stock Trader — Claude Code Instructions

## Project Overview
A system for proposing Japanese stock trade candidates, strategy backtesting, and portfolio/risk management.
**Trading is always manual.** The program is limited to candidate proposals, analysis, and record-keeping.

## Environment & Package Management
- Runtime: Python 3.11+ managed via **uv**
- Never use `pip` directly — always use `uv` commands
- Virtual environment is created and managed automatically by uv

## Environment Files
| File         | Purpose                                      | DB              |
|-------------|----------------------------------------------|-----------------|
| `devenv`    | Development — daily data collection & coding | stock_trader_dev |
| `btenv`     | Backtest analysis — read-heavy, isolated DB  | stock_trader_bt  |

- Never mix credentials between environments
- Load the appropriate file explicitly:
  - Dev tasks    : `uv run --env-file devenv python -m src.data.collect`
  - Backtest tasks: `uv run --env-file btenv python -m src.backtest.cycle`

## Tech Stack
- Python 3.11+
- PostgreSQL (local): port 5432
  - Dev DB: `stock_trader_dev` (daily collection, viz app)
  - Backtest DB: `stock_trader_bt`
  - Test DB: `stock_trader_test`
- Key libraries: yfinance, pandas, numpy, ta, sqlalchemy, psycopg2, loguru
- Visualization: Dash + Plotly (interactive web app)
- Future: Anthropic API (claude-sonnet-4-20250514) for advanced analysis

## Directory Structure
- `src/data/`        : Data collection & DB CRUD (all external API calls go here only)
- `src/indicators/`  : Technical indicators (SMA, EMA, BB, RSI, MACD, Ichimoku, ZigZag, ATR, moving_corr, corr_regime)
- `src/signs/`       : Trade signal detectors — each inherits from `signs/base.py`
- `src/strategy/`    : Strategy definitions — each inherits from `strategy/base.py`
- `src/simulator/`   : Custom bar-based trade simulator (replaces backtrader); `DataCache` + `TradeSimulator`
- `src/backtest/`    : Backtest orchestration — runners, trainers (GA), metrics, cycle
- `src/exit/`        : Exit rule implementations — time stop, ADX trail, ATR trail, ZigZag TP/SL, etc.
- `src/portfolio/`   : Position tracking and risk calculation
- `src/analysis/`    : One-off analysis scripts and research notebooks
- `src/maintenance/` : Background task registry (OHLCV download, sign benchmark)
- `src/viz/`         : Dash web application (daily proposals + backtest viewer + maintenance)

## Interactive Web App
Launch:
```bash
uv run --env-file devenv python -m src.viz.app
# then open http://localhost:8050
```

### Tabs
| Tab         | Purpose |
|-------------|---------|
| **Daily**   | Today's RegimeSign proposals (sign type, stock, regime metrics). Click a row to view the stock chart with N225 panel and ρ(20) correlation panel. Register/close positions from this page. |
| **Backtest**| OHLCV + strategy chart viewer for saved training runs. Strategy and run selected from DB. Supports multi-stock re-backtest live. |
| **Maintenance** | Background workers: OHLCV download, sign benchmark coverage grid. |

### Daily tab — chart layout (when a stock is selected)
Five rows sharing a single x-axis:
1. Stock price (candlestick + SMA + Ichimoku)
2. Stock ADX
3. Stock volume
4. N225 price (candlestick + SMA)
5. ρ(20) rolling correlation (stock vs ^N225 and ^GSPC)

Reference lines on ρ panel: ±0.6 (dotted) and 0.0 (bold solid).

## Signs Catalogue (`src/signs/`)
| Sign         | Description |
|--------------|-------------|
| `str_hold`   | Stock flat while N225 drops — hidden buying absorbs market fall |
| `str_lag`    | Stock lags N225 rally then catches up |
| `str_lead`   | Stock leads N225 trough (early-mover alpha) |
| `brk_sma`    | SMA breakout |
| `brk_bol`    | Bollinger Band breakout |
| `corr_flip`  | Correlation flips from negative to positive |
| `corr_peak`  | Correlation reaches local peak |
| `corr_shift` | Structural shift in correlation regime |
| `div_bar`    | Price/volume bar divergence |
| `div_gap`    | Gap-based divergence |
| `div_peer`   | Peer-relative divergence |
| `div_vol`    | Volatility divergence |
| `rev_nday`   | N-day reversal |
| `rev_nlo`    | N-day low reversal |
| `rev_peak`   | Reversal from peak |

## Exit Rules (`src/exit/`)
Best rule by FY2018–FY2024 benchmark: **`adx_trail_d8.0`** (mean_r +0.93%, Sharpe 1.28, win% 50%, hold 26.7 bars).
Full benchmark results in `src/exit/benchmark.md`.

| Rule | Notes |
|------|-------|
| `time_stop` | Fixed bar hold (10/20/40 bar variants) |
| `adx_trail` | ADX-based trailing stop; d8.0 is the best real-data variant |
| `atr_trail` | ATR trailing stop |
| `zs_tp_sl`  | ZigZag leg-sized TP/SL (used for live position TP/SL preview) |
| `next_peak` | Exit at next confirmed zigzag peak |
| `composite` | Combination of multiple rules |
| `adx_adaptive` | Adaptive ADX trail |

TP/SL levels for new positions are computed at registration time using `ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)` via `src/portfolio/crud.compute_exit_levels`.

## Portfolio Management (`src/portfolio/`)
- **`models.py`** — `Position` ORM model. Fields include: `stock_code`, `sign_type`, `corr_mode`, `kumo_state`, `direction` (long/short), `entry_date`, `entry_price`, `units`, `tp_price`, `sl_price`, `status` (open/closed), `exit_date`, `exit_price`.
- **`crud.py`** — `register_position`, `get_open_positions`, `close_position`, `compute_exit_levels`, `get_latest_price`.
- All position registration goes through `crud.register_position` — never bypass it.

## Operation Cycle
1. **Data Collection** : `uv run --env-file devenv python -m src.data.collect`
2. **Strategy Proposals** : Launch the web app — Daily tab shows today's RegimeSign proposals
3. **Backtest / Training** : `uv run --env-file btenv python -m src.backtest.cycle` (or individual runners)
4. **Analysis** : Scripts in `src/analysis/`; results browsed in Backtest tab
5. **Trading** : **Human executes manually**
6. **Position Entry** : Register via Daily tab → Register Position form in the web app

## Coding Standards
- Type hints required (mypy strict mode)
- Google-style docstrings
- All DB operations via SQLAlchemy ORM only (no raw SQL; use Alembic for migrations)
- All config values via `.env` only (no hardcoding)
- Never suppress errors; log everything with loguru

## Simulator / Backtest Model
- The custom `src/simulator/` replaces backtrader. **Do not use backtrader.**
- **Two-bar fill rule**: signal fires on bar T, position is filled at the open of bar T+1.
- `DataCache` loads and caches OHLCV bars; warmup NaNs are stored as `0.0` — filter with `or None` idiom.
- `TradeSimulator` manages entries, exits, PnL tracking.

## Strictly Prohibited
- Implementing any auto-order / brokerage API execution code
- Committing `.env` files
- Registering positions without going through `src/portfolio/crud.register_position`
- Using `backtrader` — use `src/simulator/` instead

## Trading Philosophy — Correlation-Based Position Selection

The 20-bar daily rolling correlation between a stock and ^N225 determines
which mode to use **at entry time**:

### High-corr mode  (|corr| ≥ ~0.6)
- The stock is effectively a proxy for the index.
- Entry is driven by **N225 signals** (confirmed zigzag LOW, regime gate, etc.).
- Take **one position only** — holding multiple high-corr stocks simultaneously
  is false diversification (they are the same bet).
- Exit rules follow the same N225-linked logic (time stop, ATR stop, zigzag HIGH).

### Low-corr mode  (|corr to N225| ≤ ~0.3)
- The stock moves independently of the index — it carries genuine alpha.
- Entry is driven by **stock-specific signs** (div_bar, str_hold, brk_sma, etc.)
  regardless of current N225 direction.
- **Multiple simultaneous positions are acceptable** because their moves are
  genuinely uncorrelated; each adds real diversification.
- Apply the same exit discipline (time stop, ATR stop, zigzag exit).

### Implications for strategy design and backtest evaluation
- When reviewing multi-stock backtests, count **concurrent high-corr positions
  as one logical bet**, not N independent bets.
- The same rule applies to **any shared foreign indicator**: if multiple
  low-N225-corr stocks all have high |corr| to the same foreign indicator
  (e.g., several stocks all tracking ^GSPC), treat them as one logical bet
  — take only one position among them, same as High-corr mode.
- A strategy that fires on many high-corr stocks during the same N225 event
  is concentrated, not diversified — adjust position sizing accordingly.
- CorrRegime (`src/indicators/corr_regime.py`) measures the fraction of
  universe stocks with corr > 0.70; block *new* entries (especially high-corr
  ones) when this fraction exceeds its historical 80th percentile.
- Evidence base: early_peak_iv analysis showed high-corr stocks in a bear
  N225 environment confirm peaks at only 54%, vs 66% for low-corr stocks —
  confirming that corr regime is a meaningful risk gate.

## Rebenchmarking a Sign

When a sign's detection logic changes (new gate, parameter change, etc.), run the
full rebenchmark pipeline to update both the DB and `src/analysis/benchmark.md`:

```bash
scripts/rebenchmark_sign.sh <sign_type>
# Example:
scripts/rebenchmark_sign.sh str_lead
```

**What the script does (6 steps):**
1. Deletes all `SignBenchmarkRun` rows (and cascaded events) for the sign from the dev DB.
2. Truncates `benchmark.md` at the `## Multi-Year Benchmark` section header.
3. Runs `sign_benchmark_multiyear --phase benchmark validate report --sign <sign>`.
4. Runs `sign_regime_analysis` (rebuilds ADX+Kumo regime snapshots and report).
5. Runs `sign_score_calibration` (Spearman ρ + score-quartile EV table — answers
   "is `sign_score` actually informative?"; processes ALL signs from DB).
6. Runs `sign_benchmark_multiyear --phase backtest --sign <sign>` (FY2025 OOS).

**After the script completes:**
- Review the new tables in `src/analysis/benchmark.md`.
- Update the sign module's header comment with the new DR / perm_pass numbers.
- If the sign's regime behaviour changed, update the sign's note in `## Per-Sign Notes`.

**Note**: The script only rebenchmarks the named sign; other signs in `benchmark.md`
are preserved. Steps 4 (`sign_regime_analysis`), 5 (`sign_score_calibration`), and
6 (backtest) process ALL signs found in the DB, so all regime / calibration tables
are regenerated consistently each time.

## DB Schema Changes
Always generate an Alembic migration file and get it reviewed before applying.
```bash
alembic revision --autogenerate -m "description"
```
**Caution**: autogenerate picks up schema drift from partitioned OHLCV tables
(`ohlcv_1d_yXXXX`). Review the generated file and remove any unrelated index
or table operations before applying.

## Testing Policy
- All strategy logic must have unit tests (pytest)
- Backtest fixtures go in `tests/fixtures/`
- Tests that touch the DB must roll back transactions in teardown
