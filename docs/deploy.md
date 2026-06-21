# Production deployment — live book + boot-time UI

This sets up a **separate production database** (`stock_trader_prod`) for the
real-money book, and a **systemd service** that auto-starts the prod UI on WSL
boot. Dev (`stock_trader_dev`) is untouched and stays your sandbox.

Why a separate DB: `stock_trader_dev` was wiped twice by an accidental
`pytest --env-file devenv`. Real-money position records must not share that DB.
The protection is the **database name** — anything pointed at `stock_trader_dev`
cannot touch `stock_trader_prod`.

## Files
| Path | Role |
|------|------|
| `prodenv` | Prod env file (gitignored). `DATABASE_URL` → `stock_trader_prod`, prod DB role. |
| `scripts/setup_prod_db.sh` | One-time: create role+DB, seed from latest dev dump, reassign owner, `alembic upgrade head`. |
| `scripts/reset_prod_book.sql` | Optional: clear copied dev positions/accounts, create a fresh `Production` account. |
| `deploy/trade-advisor-ui.service` | systemd unit: prod UI on :8050, `After=postgresql.service`. |
| `scripts/install_ui_service.sh` | One-time: install + `enable --now` the service. |
| `scripts/backup_prod_db.sh` | Daily prod dump → `/home/ubuntu/db_backups/` (mirror of dev backup). |

## One-time setup (run in order — these need sudo)

```bash
cd /home/ubuntu/cld_trade_advisor

# 1. Create + seed the prod DB (role/password read from prodenv). ~few min for 345M.
bash scripts/setup_prod_db.sh

# 2. (Optional) Start prod with a clean book instead of a copy of dev positions.
sudo -u postgres psql -d stock_trader_prod -f scripts/reset_prod_book.sql

# 3. Install + enable the boot-time UI service.
bash scripts/install_ui_service.sh
```

Verify:
```bash
systemctl status trade-advisor-ui.service
journalctl -u trade-advisor-ui.service -f      # watch startup logs
curl -sI http://localhost:8050                 # UI responding
```
In the UI, pick the **Production** account in the account selector.

## Daily operation
Collection is manual for this project. To keep prod proposals fresh, run the
collector against **both** environments each trading day:
```bash
uv run --env-file devenv  python -m src.data.collect   # dev sandbox
uv run --env-file prodenv python -m src.data.collect   # prod book
```

Add the prod backup to cron (5 min after the dev backup at 18:30):
```bash
crontab -l 2>/dev/null; (echo "35 18 * * * /home/ubuntu/cld_trade_advisor/scripts/backup_prod_db.sh >> /home/ubuntu/db_backups/backup.log 2>&1") | crontab -
```

## Service management
```bash
sudo systemctl restart trade-advisor-ui.service   # after pulling code changes
sudo systemctl stop    trade-advisor-ui.service
sudo systemctl disable trade-advisor-ui.service   # stop auto-starting on boot
```

The unit runs `uv run --env-file prodenv python -m src.viz.app 8050` as user
`ubuntu`, waits for Postgres to accept connections (`pg_isready` loop), and
restarts on failure. `scripts/run_ui.sh` (manual dev launch on devenv) is unchanged.
```
