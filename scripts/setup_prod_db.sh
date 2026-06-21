#!/usr/bin/env bash
# One-time setup of the production database (stock_trader_prod).
#
#   - creates the stockproduser role + stock_trader_prod database
#   - seeds it from the most recent stock_trader_dev backup dump
#   - reassigns ownership of the restored objects to stockproduser
#   - runs `alembic upgrade head` to bring the schema to the latest migration
#
# Requires sudo (uses `sudo -u postgres`). Run once:
#   bash scripts/setup_prod_db.sh
#
# The role password is read from prodenv's DATABASE_URL — single source of truth.
# Idempotent-ish: re-running drops nothing; it will refuse if the DB already exists.
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
BACKUP_DIR=/home/ubuntu/db_backups
DB=stock_trader_prod
ROLE=stockproduser
DEV_ROLE=stockdevuser

cd "$REPO"

# --- password from prodenv (postgresql://ROLE:PASS@host:port/db) ----------------
DB_URL=$(grep -E '^DATABASE_URL=' "$REPO/prodenv" | cut -d= -f2-)
PASS=$(printf '%s' "$DB_URL" | sed -E 's#^postgresql://[^:]+:([^@]+)@.*#\1#')
if [ -z "$PASS" ] || [ "$PASS" = "$DB_URL" ]; then
    echo "ERROR: could not parse password from prodenv DATABASE_URL" >&2
    exit 1
fi

# --- guard: refuse if prod DB already exists ------------------------------------
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
    echo "ERROR: database $DB already exists. Drop it first if you really want to re-seed:" >&2
    echo "       sudo -u postgres dropdb $DB" >&2
    exit 1
fi

# --- latest dev dump ------------------------------------------------------------
DUMP=$(ls -1t "$BACKUP_DIR"/stock_trader_dev_*.sql.gz 2>/dev/null | head -1 || true)
if [ -z "$DUMP" ]; then
    echo "ERROR: no stock_trader_dev_*.sql.gz dump found in $BACKUP_DIR" >&2
    echo "       Create one first: scripts/backup_dev_db.sh" >&2
    exit 1
fi
echo ">> seeding from: $DUMP"

# --- role + database ------------------------------------------------------------
echo ">> creating role $ROLE and database $DB ..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$ROLE') THEN
        CREATE ROLE $ROLE LOGIN PASSWORD '$PASS';
    ELSE
        ALTER ROLE $ROLE PASSWORD '$PASS';
    END IF;
END \$\$;
CREATE DATABASE $DB OWNER $ROLE;
SQL

# --- restore dump (as superuser so owner/extension/grant lines apply) -----------
echo ">> restoring dump into $DB (this takes a few minutes for ~345M) ..."
gunzip -c "$DUMP" | sudo -u postgres psql -v ON_ERROR_STOP=0 -d "$DB" >/dev/null

# --- reassign ownership of restored objects to the prod role --------------------
echo ">> reassigning ownership to $ROLE ..."
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB" <<SQL
REASSIGN OWNED BY $DEV_ROLE TO $ROLE;
GRANT ALL ON SCHEMA public TO $ROLE;
GRANT ALL ON ALL TABLES IN SCHEMA public TO $ROLE;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO $ROLE;
SQL

# --- bring schema to head -------------------------------------------------------
echo ">> alembic upgrade head ..."
uv run --env-file prodenv alembic upgrade head

echo ""
echo "DONE. stock_trader_prod is ready."
echo "NOTE: it currently contains a COPY of all dev positions/accounts."
echo "      To start prod with a clean book, see scripts/reset_prod_book.sql."
