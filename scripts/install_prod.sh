#!/usr/bin/env bash
# Full production install, one shot. Prompts for your sudo password ONCE, caches
# the sudo credential, then runs all the steps that need root without further
# prompts:
#   1. create + seed stock_trader_prod   (scripts/setup_prod_db.sh)
#   2. (optional) clean the trade book    (scripts/reset_prod_book.sql)
#   3. install + enable the boot UI       (scripts/install_ui_service.sh)
#
# Run it in a REAL terminal (a WSL shell tab), not a non-interactive context,
# so the password prompt can read your input:
#   bash scripts/install_prod.sh
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
cd "$REPO"

# --- prompt for sudo password and cache the credential -------------------------
read -rsp "[sudo] password for $USER: " SUDO_PW; echo
if ! printf '%s\n' "$SUDO_PW" | sudo -S -p '' -v 2>/dev/null; then
    echo "ERROR: sudo authentication failed." >&2
    exit 1
fi
unset SUDO_PW

# Keep the sudo timestamp warm so the multi-minute restore doesn't expire it.
( while true; do sudo -n -v 2>/dev/null || exit; sleep 50; done ) &
KEEPALIVE=$!
trap 'kill "$KEEPALIVE" 2>/dev/null || true' EXIT

# --- 1. create + seed the prod DB ----------------------------------------------
echo ">>> Step 1/3: create + seed stock_trader_prod"
bash scripts/setup_prod_db.sh

# --- 2. optional clean book ----------------------------------------------------
echo ""
read -rp "Start prod with a CLEAN book (wipe the copied dev positions/accounts)? [y/N] " ANS
if [[ "${ANS,,}" == "y" ]]; then
    echo ">>> Step 2/3: resetting prod book"
    sudo -u postgres psql -d stock_trader_prod -f scripts/reset_prod_book.sql
else
    echo ">>> Step 2/3: skipped (prod keeps a copy of dev positions)"
fi

# --- 3. install + enable the boot UI service -----------------------------------
echo ">>> Step 3/3: install + enable trade-advisor-ui.service"
bash scripts/install_ui_service.sh

echo ""
echo "=========================================================="
echo "Production install complete."
echo "  UI:     http://localhost:8050   (pick the account in the selector)"
echo "  status: systemctl status trade-advisor-ui.service"
echo "  logs:   journalctl -u trade-advisor-ui.service -f"
echo "=========================================================="
