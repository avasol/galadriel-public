#!/usr/bin/env bash
# ============================================================
# uninstall.sh — Remove Galadriel systemd service
# Run as: sudo bash cmd/uninstall.sh
# ============================================================

set -euo pipefail

echo "🧝‍♀️ Removing Galadriel systemd service..."

systemctl stop galadriel.service 2>/dev/null || true
systemctl disable galadriel.service 2>/dev/null || true
rm -f /etc/systemd/system/galadriel.service
systemctl daemon-reload

echo "✅ Service removed. The Lady sleeps."
