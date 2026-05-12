
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

| File        | Purpose                          | DB                |
|-------------|----------------------------------|-------------------|
| `devenv`    | Data collection & development    | stock_trader_dev  |
| `btenv`     | Backtest analysis (read-heavy)   | stock_trader_bt   |
| `prodenv`   | Real trading environment         | stock_trader      |

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

## Sign / Strategy Debate Cycle

Multi-step decisions about sign detectors and strategy parameters (revert a
gate? loosen a threshold? redesign a score?) run through a debate cycle
defined in `.claude/agents/` and `.claude/commands/sign-debate.md`.

### Starting a debate

From Claude Code in this repo:

```
/sign-debate <topic>                       # default: up to 3 iterations
/sign-debate <topic> --max-iter 5          # cap iterations explicitly
```

Examples:

```
/sign-debate revert corr_shift state machine
/sign-debate loosen str_lag bull-regime gate
/sign-debate improve str_hold sign_score informativity
```

### What happens

Each iteration runs five agents in order:

| Agent | Role |
|---|---|
| `analyst`   | Reads benchmark.md / calibration / regime tables. No opinion — just numbers. |
| `historian` | Searches memory + git log + sign module headers for prior attempts. |
| `proposer`  | Frames one concrete change (Goal / Change / Expected impact / Evidence / Risks / Rebench scope). |
| `critic`    | Stress-tests the proposal against `docs/evaluation_criteria.md` § 5 failure modes. |
| `judge`     | Verdict: Accept / Reject / Insufficient evidence — with confidence (H/M/L) and a falsifier. |

If the judge returns **Insufficient evidence**, the harness autonomously
executes the judge's "Next action" — running an existing analysis script,
querying the DB, or writing a small one-off script under
`src/analysis/` — and loops with the new evidence. The cycle stops on
Accept, Reject, max iterations, or when the next action falls outside the
autonomous scope (e.g. modifying detector code, running a full rebench).

### Rules and rubric

All five agents share one rubric: **`docs/evaluation_criteria.md`**.
Defines:
- Evidence sources, weighted (§ 1)
- Materiality thresholds (§ 3) — when a ΔDR or Δρ is worth acting on
- Decision matrix for DR × n_events outcomes (§ 4)
- Seven common failure modes the critic checks (§ 5)
- Iteration protocol (§ 8)

Edit the rubric when the team's bar changes; the agents pick up the new
thresholds the next time `/sign-debate` is invoked.

### What the harness will NOT do autonomously

- Modify detector / strategy / portfolio code.
- Mutate the DB (rebench writes, migrations).
- Run a full `scripts/rebenchmark_sign.sh` — always confirmed by the user first.
- Anything that reaches outside the repo (network calls, credentials).

These stop the cycle and are reported back as recommendations.

## DB Schema Changes

Always generate an Alembic migration and review before applying:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```
