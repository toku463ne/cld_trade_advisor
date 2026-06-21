#!/usr/bin/env bash
# Install + enable the systemd service that auto-starts the prod UI on boot.
# Requires sudo. Run once:
#   bash scripts/install_ui_service.sh
#
# systemd is enabled in this WSL (/etc/wsl.conf has [boot] systemd=true), so the
# service starts whenever WSL boots. postgresql.service is already enabled, and
# the unit orders itself After= it.
set -euo pipefail

REPO=/home/ubuntu/cld_trade_advisor
UNIT=trade-advisor-ui.service

sudo cp "$REPO/deploy/$UNIT" "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT"

echo ""
echo "Installed + started $UNIT."
echo "  status:  systemctl status $UNIT"
echo "  logs:    journalctl -u $UNIT -f"
echo "  UI:      http://localhost:8050"
