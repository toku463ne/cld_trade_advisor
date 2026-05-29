#!/usr/bin/env bash
# One-shot, idempotent installer for the Japan Stock Trade Advisor.
# Safe to re-run. Sets up everything needed to run the UI from a clean machine:
#   1. uv (Python toolchain)            — installed if missing
#   2. PostgreSQL server + client       — installed & started if missing
#   3. DB roles + databases             — created if missing (dev / bt / test)
#   4. Python dependencies              — uv sync
#   5. Alembic migrations               — applied to dev + bt
#
# Usage:
#   scripts/install.sh            # full install (needs sudo for apt + postgres)
#   scripts/install.sh --no-db    # deps + migrations only (Postgres already set up)
#
# After this, start the UI with:  scripts/run_ui.sh
# To seed market data into a fresh DB, see "Recovery" in README.md.
#
# NOTE: the DB credentials below MUST match devenv / btenv. They are non-secret
# local dev credentials (already committed in those env files).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }

DO_DB=1
[ "${1:-}" = "--no-db" ] && DO_DB=0

# ── 1. uv ───────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    log "uv present ($(uv --version))"
fi

# ── 2/3. PostgreSQL + databases ───────────────────────────────────────────────
if [ "$DO_DB" = 1 ]; then
    if ! command -v psql >/dev/null 2>&1; then
        log "Installing PostgreSQL ..."
        sudo apt-get update
        sudo apt-get install -y postgresql postgresql-contrib
    else
        log "PostgreSQL present ($(psql --version))"
    fi

    log "Ensuring PostgreSQL is running ..."
    sudo service postgresql start 2>/dev/null || true
    if ! pg_isready -q 2>/dev/null; then
        warn "pg_isready reports the server is down on the default socket — check 'sudo service postgresql status'."
    fi

    log "Ensuring database roles and databases ..."
    role_exists() { sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$1'" | grep -q 1; }
    db_exists()   { sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$1'" | grep -q 1; }

    role_exists stockdevuser || sudo -u postgres psql -c "CREATE USER stockdevuser WITH PASSWORD 'stockdevpass';"
    role_exists stockbtuser  || sudo -u postgres psql -c "CREATE USER stockbtuser  WITH PASSWORD 'stockbtpass';"

    # "db owner" pairs — dev & test owned by the dev user, bt by the bt user.
    for pair in "stock_trader_dev stockdevuser" "stock_trader_bt stockbtuser" "stock_trader_test stockdevuser"; do
        set -- $pair
        if db_exists "$1"; then
            echo "   $1 exists"
        else
            sudo -u postgres createdb -O "$2" "$1"
            echo "   created $1 (owner $2)"
        fi
    done
fi

# ── 4. Python dependencies ────────────────────────────────────────────────────
log "Installing Python dependencies (uv sync) ..."
uv sync

# ── 5. Migrations ─────────────────────────────────────────────────────────────
if [ "$DO_DB" = 1 ]; then
    log "Applying Alembic migrations (dev + bt) ..."
    uv run --env-file devenv alembic upgrade head
    uv run --env-file btenv  alembic upgrade head
    # stock_trader_test needs no migration — the pytest fixture builds the schema
    # from ORM metadata on each run.
fi

log "Install complete."
echo "   Start the UI:   scripts/run_ui.sh        (http://localhost:8050)"
echo "   Seed market data into a fresh DB (optional):"
echo "       scripts/market_data_dump.sh restore <dumpfile> devenv"
