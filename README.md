# Japan Stock Trade Advisor

Proposes Japanese-stock **trade candidates**, backtests sign/strategy ideas, and tracks a
manually-traded portfolio. **Trading is always manual** — this app only proposes, analyses, and
records; it never places orders.

The day-to-day surface is a **Dash web UI** (`Daily` proposals · `Backtest` viewer · `Maintenance`).
Architecture, the signs catalogue, and the trading philosophy live in **[CLAUDE.md](CLAUDE.md)**.

---

## Quick start

```bash
scripts/install.sh        # uv + PostgreSQL + databases + deps + migrations (idempotent)
scripts/run_ui.sh         # start the UI, then open http://localhost:8050
```

`install.sh` is safe to re-run and installs PostgreSQL for you if it isn't present (Debian/Ubuntu/WSL,
uses `sudo apt`). On a machine where Postgres is already set up, use `scripts/install.sh --no-db` to do
deps + migrations only.

A fresh database is **empty** — see [Recovery](#recovery) to seed it, or [Daily operation](#daily-operation)
to collect data.

> Requires a Debian/Ubuntu-family Linux (or WSL) with `sudo` for the database step. Python is managed
> by [uv](https://docs.astral.sh/uv/); never call `pip` directly.

---

## The UI

```bash
scripts/run_ui.sh         # http://localhost:8050
scripts/run_ui.sh 8080    # custom port
```

| Tab | What it does |
|-----|--------------|
| **Daily** | Today's confluence / RegimeSign proposals. Click a row → stock chart with N225 + ρ(20) panels. Register / close positions; the Register panel recommends lot-aware Units against the active account's budget. |
| **Backtest** | OHLCV + strategy chart viewer for saved runs; supports multi-stock re-backtest. |
| **Maintenance** | **Accounts** (create / list, set budget), OHLCV download, cluster analysis, and the sign-benchmark coverage grid — all run as background workers. |

---

## Environment files

Each environment is one gitignored env file at the repo root; load the right one explicitly with
`uv run --env-file <file>`. **Never mix credentials between environments.**

| File      | Purpose                              | Database            | Provisioned by |
|-----------|--------------------------------------|---------------------|----------------|
| `devenv`  | Daily collection, the UI, dev work   | `stock_trader_dev`  | `scripts/install.sh` |
| `btenv`   | Backtest analysis (read-heavy)       | `stock_trader_bt`   | `scripts/install.sh` |
| `prodenv` | **Live real-money book & boot UI**   | `stock_trader_prod` | `scripts/install_prod.sh` |

`stock_trader_test` is used only by the pytest suite (created by `install.sh`, no env file).

The env files are **gitignored** — they never get committed. The local Postgres credentials below are
non-secret dev passwords; the J-Quants API key is the one real secret, so don't paste it anywhere tracked.

### Setup — `devenv` & `btenv`

1. Create the two files at the repo root with these contents:

   ```bash
   # devenv
   DATABASE_URL=postgresql://stockdevuser:stockdevpass@localhost:5432/stock_trader_dev
   DATA_SOURCE=yfinance
   JQUANTS_API_KEY="<your-jquants-key>"

   # btenv
   DATABASE_URL=postgresql://stockbtuser:stockbtpass@localhost:5432/stock_trader_bt
   DATA_SOURCE=yfinance
   ```

2. Provision the roles, databases, and schema (idempotent — installs Postgres if absent):

   ```bash
   scripts/install.sh            # creates stockdevuser/stockbtuser, all 3 DBs, runs migrations
   ```

   The credentials in `install.sh` must match the `DATABASE_URL`s above. A fresh DB is empty — seed it
   from a backup ([Recovery](#recovery)) or collect data ([Daily operation](#daily-operation)).

### Setup — `prodenv` (live trading)

Prod is a **separate database** so the real-money book can never be touched by anything pointed at the
dev DB (`stock_trader_dev` was wiped twice — see [Testing](#testing)). The `prodenv` file ships with a
generated DB password already filled in. Provision everything in one step:

```bash
bash scripts/install_prod.sh    # prompts for sudo password ONCE, then:
                                #   1. creates stockproduser + stock_trader_prod, seeds from the
                                #      latest dev dump, runs migrations
                                #   2. (optional) clears the book → fresh "Production" account
                                #   3. installs the systemd service that auto-starts the UI on boot
```

Run it from a **real terminal** (the password prompt needs a TTY). The individual steps live in
`scripts/setup_prod_db.sh`, `scripts/reset_prod_book.sql`, and `scripts/install_ui_service.sh`; full
details in **[docs/deploy.md](docs/deploy.md)**.

After install, the UI auto-starts on every WSL boot at `http://localhost:8050` — pick the **Production**
account in the selector. Keep prod data fresh by collecting against it too, and back it up daily:

```bash
uv run --env-file prodenv python -m src.data.collect ohlcv --stock-set medium   # alongside the dev collect
( crontab -l 2>/dev/null; echo "35 18 * * * $PWD/scripts/backup_prod_db.sh >> $HOME/db_backups/backup.log 2>&1" ) | crontab -
```

---

## Daily operation

1. **Refresh data** — in the UI, *Maintenance* → **Download OHLCV** (fetches ^N225, ^GSPC and all
   Nikkei 225 constituents, then rebuilds the regime snapshots) — this is the canonical full refresh.
   The CLI is for ad-hoc subsets and takes a subcommand, e.g.
   `uv run --env-file devenv python -m src.data.collect ohlcv --stock-set medium`.
2. **Read proposals** — open the UI (`scripts/run_ui.sh`) and review the *Daily* tab.
3. **Trade manually**, then register fills via *Daily* → **Register Position**.

Backtests and analysis run from `btenv`, e.g. `uv run --env-file btenv python -m src.backtest.cycle`,
or the one-off scripts in `src/analysis/`.

---

## Recovery

Everything is recoverable from a backup + the install script. Two backup scripts exist (both write to
`~/db_backups/`, outside git):

```bash
scripts/backup_dev_db.sh                 # full gzip dump of stock_trader_dev (keeps newest 14)
scripts/market_data_dump.sh dump         # just the expensive market data (stocks / ohlcv_* / jq_*)
```

Schedule the daily backup once (runs while the machine is up):

```bash
( crontab -l 2>/dev/null; echo "30 18 * * * $PWD/scripts/backup_dev_db.sh >> $HOME/db_backups/backup.log 2>&1" ) | crontab -
```

**Rebuild a machine from scratch:**

```bash
scripts/install.sh                                           # toolchain + empty databases + schema
# then either restore a full dump …
gunzip -c ~/db_backups/stock_trader_dev_YYYYMMDD_HHMM.sql.gz | \
    psql "$(grep '^DATABASE_URL=' devenv | cut -d= -f2-)"
# … or just re-seed the market-data tables (schema already migrated):
scripts/market_data_dump.sh restore ~/db_backups/market_data_YYYYMMDD_HHMM.dump devenv
```

---

## Testing

```bash
uv run pytest tests/ -q
```

> ⚠️ **Never run pytest with `--env-file devenv`/`btenv`.** The `db_engine` fixture calls
> `Base.metadata.drop_all` on whatever `DATABASE_URL` points at — with a real env-file that **drops every
> table in your dev/bt DB** (this wiped `stock_trader_dev` twice on 2026-05-23). Run pytest with **no
> env-file** so it defaults to `stock_trader_test`. A guard in `tests/conftest.py` hard-refuses any target
> DB whose name doesn't contain `test`.

Most tests are pure-unit. DB-backed tests use `stock_trader_test` (created by `install.sh`); the fixture
builds the schema from ORM metadata and rolls back after each test, so no migration is needed there.

---

## DB schema changes

Always generate an Alembic migration and review it before applying (autogenerate can pick up drift from
the partitioned `ohlcv_1d_yXXXX` tables — strip unrelated ops):

```bash
uv run --env-file devenv alembic revision --autogenerate -m "description"
uv run --env-file devenv alembic upgrade head
```

---

## More

- **[CLAUDE.md](CLAUDE.md)** — architecture, signs catalogue, exit rules, trading philosophy, the
  fill-order-null methodology.
- **`docs/analysis/`** — research write-ups (confluence & RegimeSign backlogs, benchmarks).
- **Sign/strategy debates** — run `/sign-debate <topic>` from Claude Code; rubric in
  `docs/evaluation_criteria.md`.
