#!/usr/bin/env bash
# Portable dump / restore of the *expensive-to-download* market data only:
#   stocks, ohlcv_* (yfinance, partitioned), jq_* (J-Quants).
# Lets a NEW environment be seeded without re-downloading 10 yr of J-Quants +
# the OHLCV history. The dump is a single custom-format file under
# /home/ubuntu/db_backups/ (outside git — never commit the data).
#
# Reads the connection string from an env file (default: devenv); no hardcoded
# password. Run the DUMP only after a backfill has FINISHED (a mid-write dump of
# jq_daily_quotes / jq_statements would be partial).
#
# Usage:
#   scripts/market_data_dump.sh dump    [envfile]            # -> timestamped .dump
#   scripts/market_data_dump.sh restore <dumpfile> [envfile] # replace those tables
#
# Restore is safe whether the target tables exist or not (--clean --if-exists):
# it drops & recreates only stocks/ohlcv_*/jq_* (incl. OHLCV partition DDL + data)
# and leaves every other table + alembic_version untouched. Recommended new-env
# flow:  create DB -> `alembic upgrade head` -> this restore.
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
BACKUP_DIR=/home/ubuntu/db_backups
KEEP=5
TABLES=(-t stocks -t 'ohlcv_*' -t 'jq_*')

cmd=${1:-}
mkdir -p "$BACKUP_DIR"

db_url() {
    local envfile=$1
    local url
    url=$(grep -E '^DATABASE_URL=' "$envfile" | cut -d= -f2-)
    [ -n "$url" ] || { echo "ERROR: DATABASE_URL not in $envfile" >&2; exit 1; }
    echo "$url"
}

case "$cmd" in
  dump)
    ENVFILE=${2:-$REPO/devenv}
    DB_URL=$(db_url "$ENVFILE")
    OUT="$BACKUP_DIR/market_data_$(date +%Y%m%d_%H%M).dump"
    echo "$(date '+%F %T') dumping stocks/ohlcv_*/jq_* from $ENVFILE -> $OUT"
    pg_dump "$DB_URL" --no-owner --no-privileges -Fc "${TABLES[@]}" -f "$OUT"
    ls -1t "$BACKUP_DIR"/market_data_*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
    echo "$(date '+%F %T') OK -> $OUT ($(du -h "$OUT" | cut -f1)); kept $(ls -1 "$BACKUP_DIR"/market_data_*.dump | wc -l)"
    ;;
  restore)
    DUMP=${2:?usage: restore <dumpfile> [envfile]}
    ENVFILE=${3:-$REPO/devenv}
    [ -f "$DUMP" ] || { echo "ERROR: dump not found: $DUMP" >&2; exit 1; }
    DB_URL=$(db_url "$ENVFILE")
    echo "$(date '+%F %T') restoring $DUMP into $ENVFILE (replaces stocks/ohlcv_*/jq_* only)"
    pg_restore --no-owner --no-privileges --clean --if-exists --dbname "$DB_URL" "$DUMP"
    echo "$(date '+%F %T') restore OK"
    ;;
  *)
    echo "usage: $0 dump [envfile] | restore <dumpfile> [envfile]" >&2
    exit 2
    ;;
esac
