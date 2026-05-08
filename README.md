
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

| Section  | Description                                                    |
|----------|----------------------------------------------------------------|
| `test`   | 2 JP stocks + 7 global indices — quick smoke tests            |
| `medium` | ~64 JP stocks + Nikkei 225 + 7 global indices — standard set |

Global indices included in both sets: `^DJI` (Dow Jones), `^GSPC` (S&P 500), `^IXIC` (NASDAQ), `^HSI` (Hang Seng), `^GDAXI` (DAX), `^FTSE` (FTSE 100), `^VIX` (VIX).

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



## Stock Correlation Analysis

Computes sliding-window return correlation between all stock pairs over a given period. Results are stored in `corr_runs` / `stock_corr_pairs` tables and browsable in a Dash UI.

### Run correlation analysis

```bash
uv run --env-file devenv python -m src.analysis.stock_corrs \
    --stock-set medium --start 2022-01-01 --end 2025-12-31
```

Options:

| Flag              | Default | Description                          |
|-------------------|---------|--------------------------------------|
| `--stock-set`     | medium  | Section name from `stock_codes.ini`  |
| `--start`         | —       | Period start date (YYYY-MM-DD)       |
| `--end`           | —       | Period end date (YYYY-MM-DD)         |
| `--window-days`   | 60      | Rolling window size in days          |
| `--step-days`     | 20      | Step between windows in days         |
| `--granularity`   | 1d      | OHLCV granularity                    |
| `--min-windows`   | 3       | Minimum windows required per pair    |

### Correlation UI

```bash
uv run --env-file devenv python -m src.analysis.corr_ui
```

Then open **http://localhost:8051** in a browser. Use a different port:

```bash
uv run --env-file devenv python -m src.analysis.corr_ui 8052
```

**What you can do:**

- **Run** dropdown — select a stored correlation run
- **Stock filter** — filter pairs by stock code substring
- **Pair table** — sortable; columns: stock_a, stock_b, mean_corr, std_corr, n_windows
- **Heatmap** — mean correlation matrix for the top-40 most-involved stocks

See [src/analysis/readme_stock_corrs.md](src/analysis/readme_stock_corrs.md) for methodology and interpretation.

## DB Schema Changes

Always generate an Alembic migration and review before applying:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```
