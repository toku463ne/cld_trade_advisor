
# Japan Stock Trade Advisor

## Setup
Setup postgreSQL databases:
```bash
sudo apt update && sudo apt install -y postgresql postgresql-contrib
sudo service postgresql start
sudo -u postgres psql <<EOF
CREATE USER stockdevuser WITH PASSWORD 'stockdevpass';
CREATE USER stockbtuser WITH PASSWORD 'stockbtpass';
CREATE DATABASE stock_trader_dev OWNER stockdevuser;
CREATE DATABASE stock_trader_bt  OWNER stockbtuser;
EOF
```

Apply DB migrations:
```bash
uv run --env-file devenv alembic upgrade head
```

## Environment files

| File     | Purpose                          | DB                |
|----------|----------------------------------|-------------------|
| `devenv` | Data collection & development    | stock_trader_dev  |
| `btenv`  | Backtest analysis (read-heavy)   | stock_trader_bt   |

## Stock code sets (`configs/stock_codes.ini`)

| Section  | Description                       |
|----------|-----------------------------------|
| `test`   | 2 stocks — quick smoke tests      |
| `medium` | ~64 stocks — standard train/collect set |

## Config files (`configs/dev_*.yaml`)

Each task has its own config file. The file name is stored in the DB (`train_runs.config`) so every result is traceable back to the config used.

| File                        | Stock set | Strategy          |
|-----------------------------|-----------|-------------------|
| `dev_test_sma_bo.yaml`      | test      | sma_breakout      |
| `dev_medium_sma_bo.yaml`    | medium    | sma_breakout      |

## Training

The trainer automatically downloads any missing OHLCV data before running, so there is no separate collect step.

### SMA Breakout — test set (quick smoke test)

```bash
uv run --env-file devenv python -m src.backtest.trainer \
    --config configs/dev_test_sma_bo.yaml
```

### SMA Breakout — medium set

```bash
uv run --env-file devenv python -m src.backtest.trainer \
    --config configs/dev_medium_sma_bo.yaml
```

### Bollinger Breakout — test set

```bash
uv run --env-file devenv python -m src.backtest.trainer \
    --config configs/dev_test_sma_bo.yaml --strategy bollinger_breakout
```

### Bollinger Breakout — medium set

```bash
uv run --env-file devenv python -m src.backtest.trainer \
    --config configs/dev_medium_sma_bo.yaml --strategy bollinger_breakout
```

### Override individual parameters from CLI

CLI flags always override YAML values:

```bash
# More generations
uv run --env-file devenv python -m src.backtest.trainer \
    --config configs/dev_test_sma_bo.yaml --ga-gen 80

# Grid search instead of GA
uv run --env-file devenv python -m src.backtest.trainer \
    --config configs/dev_test_sma_bo.yaml --trainer grid
```

Reports are written to `reports/` and the top results are saved to the DB.

## Manual data collection (optional)

Download OHLCV data without training:

```bash
uv run --env-file devenv python -m src.data.collect ohlcv \
    --stock-set medium --start 2020-01-01 --granularity 1d
```

Update the stock master list from JPX:

```bash
uv run --env-file devenv python -m src.data.collect stocks --update
```

## Visualization UI

An interactive chart viewer built with Dash + Plotly. Requires training results in the DB (run at least one training first).

```bash
uv run --env-file devenv python -m src.viz.app
```

Then open **http://localhost:8050** in a browser.

To use a different port:

```bash
uv run --env-file devenv python -m src.viz.app 8080
```

**What you can do in the UI:**

- **Strategy** dropdown — switch between trained strategies
- **Training Run** dropdown — select a specific run (shows date, stocks, and number of combinations)
- **Parameter Set** table — click any row to switch the chart to that parameter combination
- **Sidebar metrics** — total return, CAGR, Sharpe, drawdown, win rate, profit factor, score for the selected parameter set
- **Main chart** — candlestick + SMA (+ Bollinger band if applicable), buy/sell markers with P&L labels, shaded holding periods
- **Equity curve** panel below the candles
- **Volume** panel at the bottom
- Scroll to zoom, drag to pan; use the camera button to export a PNG

## DB Schema Changes

Always generate an Alembic migration and review before applying:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```
