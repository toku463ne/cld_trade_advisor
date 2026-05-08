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
  - Backtest tasks: `uv run --env-file btenv python -m src.backtest.run --strategy <name>`


## Tech Stack
- Python 3.11+
- PostgreSQL (local): port 5432, database name `stock_trader`
- For backtesting use database name `stock_trader_bt`
- For pytesting use database name `stock_trader_test`
- Key libraries: yfinance, pandas, numpy, ta, sqlalchemy, psycopg2, backtrader, loguru
- Future: Anthropic API (claude-sonnet-4-20250514) for advanced analysis

## Directory Structure
- `src/data/`      : Data collection & DB CRUD (all external API calls go here only)
- `src/strategy/`  : Strategy definitions (each strategy inherits from Strategy base class)
- `src/backtest/`  : Backtest execution using backtrader
- `src/report/`    : Markdown/HTML report generation (output to reports/)
- `src/portfolio/` : Position tracking, capital management, risk calculation

## Coding Standards
- Type hints required (mypy strict mode)
- Google-style docstrings
- All DB operations via SQLAlchemy ORM only (no raw SQL; use Alembic for migrations)
- All config values via `.env` only (no hardcoding)
- Never suppress errors; log everything with loguru

## Strictly Prohibited
- Implementing any auto-order / brokerage API execution code
- Committing `.env` files
- Registering positions without going through the portfolio management module

## Operation Cycle
1. Data Collection  : `uv run python -m src.data.collect`
2. Strategy Review  : Edit files under `src/strategy/`
3. Backtest         : `uv run python -m src.backtest.run --strategy <name>`
4. Report           : `uv run python -m src.report.generate`
5. Trading          : **Human executes manually**
6. Position Entry   : `uv run python -m src.portfolio.register`

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
- CorrRegime (src/indicators/corr_regime.py) measures the fraction of
  universe stocks with corr > 0.70; block *new* entries (especially high-corr
  ones) when this fraction exceeds its historical 80th percentile.
- Evidence base: early_peak_iv analysis showed high-corr stocks in a bear
  N225 environment confirm peaks at only 54 %, vs 66 % for low-corr stocks —
  confirming that corr regime is a meaningful risk gate.

## DB Schema Changes
Always generate an Alembic migration file and get it reviewed before applying.
`alembic revision --autogenerate -m "description"`

## Testing Policy
- All strategy logic must have unit tests (pytest)
- Backtest fixtures go in `tests/fixtures/`
- Tests that touch the DB must roll back transactions in teardown

