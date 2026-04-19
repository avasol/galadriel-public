#!/usr/bin/env bash
# ============================================================
# install.sh — Install Galadriel as a systemd service
#
# Run as: sudo bash cmd/install.sh
#
# Before running:
#   1. Edit cmd/galadriel.service — update the three path lines
#      (WorkingDirectory, ExecStart, EnvironmentFile) to match
#      where you cloned the repo and where your venv lives.
#   2. Make sure .env exists at the path you set in EnvironmentFile.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="${SCRIPT_DIR}/galadriel.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "🧝‍♀️ Installing Galadriel systemd service..."

# Verify the service file exists
if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "❌ Service file not found: $SERVICE_FILE"
    exit 1
fi

# Copy service file (don't symlink — systemd prefers real files)
cp "$SERVICE_FILE" "${SYSTEMD_DIR}/galadriel.service"
echo "✅ Service file copied to ${SYSTEMD_DIR}/galadriel.service"

# Reload systemd daemon
systemctl daemon-reload
echo "✅ systemd daemon reloaded"

# Enable on boot
systemctl enable galadriel.service
echo "✅ Service enabled (will start on boot)"

# Start the service
systemctl start galadriel.service
echo "✅ Service started"

# Show status
echo ""
echo "— Status —"
systemctl status galadriel.service --no-pager || true

echo ""
echo "🧝‍♀️ Galadriel is alive. Use these commands to manage her:"
echo "  sudo systemctl status galadriel    # Check status"
echo "  sudo systemctl restart galadriel   # Restart"
echo "  sudo systemctl stop galadriel      # Stop"
echo "  journalctl -u galadriel -f         # Tail logs"
echo "  bash cmd/logs.sh                   # Quick log viewer"
