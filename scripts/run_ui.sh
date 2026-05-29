#!/usr/bin/env bash
# Start the Trade Advisor web UI (Dash). Ensures PostgreSQL is up first, then
# launches the app against the dev database.
#
# Usage:
#   scripts/run_ui.sh           # http://localhost:8050
#   scripts/run_ui.sh 8080      # custom port
#
# Ctrl-C to stop. Run scripts/install.sh once before the first launch.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PORT="${1:-8050}"

# WSL doesn't autostart Postgres on boot — bring it up if it's down.
if ! pg_isready -q 2>/dev/null; then
    echo "Starting PostgreSQL ..."
    sudo service postgresql start
fi

echo "Open http://localhost:$PORT   (Ctrl-C to stop)"
exec uv run --env-file devenv python -m src.viz.app "$PORT"
