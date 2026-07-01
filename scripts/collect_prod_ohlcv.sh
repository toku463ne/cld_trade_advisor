#!/usr/bin/env bash
# Refresh the PROD OHLCV database (the live Daily UI reads stock_trader_prod):
# incremental N225 universe + ^N225/^GSPC, then rebuild N225 regime snapshots.
# Runs headless via src.maintenance.collect_ohlcv (no Dash). 0 new rows is a
# normal no-op when the vendor hasn't posted the day's bar yet.
#
# Schedule (user crontab, JST server) — 18:00 weekdays, after the 15:00 JP close
# with vendor-lag margin and before the 18:30 DB backups:
#   0 18 * * 1-5 /home/ubuntu/cld_trade_advisor/scripts/collect_prod_ohlcv.sh >> /home/ubuntu/db_backups/collect_prod.log 2>&1
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
UV=/home/ubuntu/.local/bin/uv

cd "$REPO"
echo "$(date '+%F %T') [collect_prod_ohlcv] start"
"$UV" run --env-file prodenv python -m src.maintenance.collect_ohlcv
echo "$(date '+%F %T') [collect_prod_ohlcv] done (exit $?)"
