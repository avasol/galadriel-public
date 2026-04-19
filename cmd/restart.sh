#!/usr/bin/env bash
# ============================================================
# restart.sh — Graceful restart of Galadriel
# Run as: sudo bash cmd/restart.sh
# ============================================================

set -euo pipefail

echo "🧝‍♀️ Restarting Galadriel..."
sudo systemctl restart galadriel.service

sleep 2
systemctl status galadriel.service --no-pager --lines=5
echo ""
echo "✅ Restarted. Tailing logs (Ctrl+C to stop)..."
journalctl -u galadriel -n 10 -f --no-pager
