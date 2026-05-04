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

## DB Schema Changes
Always generate an Alembic migration file and get it reviewed before applying.
`alembic revision --autogenerate -m "description"`

## Testing Policy
- All strategy logic must have unit tests (pytest)
- Backtest fixtures go in `tests/fixtures/`
- Tests that touch the DB must roll back transactions in teardown

