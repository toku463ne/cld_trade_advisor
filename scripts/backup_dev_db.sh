#!/usr/bin/env bash
# Daily backup of stock_trader_dev → /home/ubuntu/db_backups/, keeping the newest
# $KEEP dumps.  Reads the connection string from devenv (no hardcoded password).
# The 2026-05-23 incident (pytest --env-file devenv wiping the DB) is why this
# exists — a recent dump means recovery is `gunzip -c <dump> | psql`.
#
# Schedule (user crontab, runs while WSL is up):
#   crontab -l 2>/dev/null; (echo "30 18 * * * /home/ubuntu/cld_trade_advisor/scripts/backup_dev_db.sh >> /home/ubuntu/db_backups/backup.log 2>&1") | crontab -
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
BACKUP_DIR=/home/ubuntu/db_backups
KEEP=14

mkdir -p "$BACKUP_DIR"
DB_URL=$(grep -E '^DATABASE_URL=' "$REPO/devenv" | cut -d= -f2-)
if [ -z "$DB_URL" ]; then
    echo "$(date '+%F %T') ERROR: DATABASE_URL not found in $REPO/devenv" >&2
    exit 1
fi

DUMP="$BACKUP_DIR/stock_trader_dev_$(date +%Y%m%d_%H%M).sql.gz"
pg_dump "$DB_URL" | gzip > "$DUMP"

# prune: keep only the newest $KEEP dumps
ls -1t "$BACKUP_DIR"/stock_trader_dev_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "$(date '+%F %T') backup OK -> $DUMP ($(du -h "$DUMP" | cut -f1)); kept $(ls -1 "$BACKUP_DIR"/stock_trader_dev_*.sql.gz | wc -l) dumps"
