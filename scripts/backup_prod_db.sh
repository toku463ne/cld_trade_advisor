#!/usr/bin/env bash
# Daily backup of stock_trader_prod → /home/ubuntu/db_backups/, keeping the newest
# $KEEP dumps. Real-money position records live here, so back them up too.
# Reads the connection string from prodenv (no hardcoded password).
#
# Schedule (user crontab, runs while WSL is up) — 5 min after the dev backup:
#   crontab -l 2>/dev/null; (echo "35 18 * * * /home/ubuntu/cld_trade_advisor/scripts/backup_prod_db.sh >> /home/ubuntu/db_backups/backup.log 2>&1") | crontab -
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
BACKUP_DIR=/home/ubuntu/db_backups
KEEP=14

mkdir -p "$BACKUP_DIR"
DB_URL=$(grep -E '^DATABASE_URL=' "$REPO/prodenv" | cut -d= -f2-)
if [ -z "$DB_URL" ]; then
    echo "$(date '+%F %T') ERROR: DATABASE_URL not found in $REPO/prodenv" >&2
    exit 1
fi

DUMP="$BACKUP_DIR/stock_trader_prod_$(date +%Y%m%d_%H%M).sql.gz"
pg_dump "$DB_URL" | gzip > "$DUMP"

# prune: keep only the newest $KEEP dumps
ls -1t "$BACKUP_DIR"/stock_trader_prod_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "$(date '+%F %T') backup OK -> $DUMP ($(du -h "$DUMP" | cut -f1)); kept $(ls -1 "$BACKUP_DIR"/stock_trader_prod_*.sql.gz | wc -l) dumps"
