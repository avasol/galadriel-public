#!/usr/bin/env bash
# ============================================================
# status.sh — Quick health check for Galadriel
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
MEMORY_DIR="${REPO_DIR}/memory"

echo "🧝‍♀️ Galadriel Health Check"
echo "========================="
echo ""

# Service status
echo "— systemd —"
if systemctl is-active --quiet galadriel 2>/dev/null; then
    echo "  Status:  ✅ RUNNING"
else
    echo "  Status:  ❌ NOT RUNNING"
fi
systemctl show galadriel --property=ActiveState,SubState,MainPID,MemoryCurrent,Restart,NRestarts 2>/dev/null | sed 's/^/  /'
echo ""

# Tower UI
echo "— Tower UI —"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
    echo "  Port 8080: ✅ Responding (HTTP $HTTP_CODE)"
else
    echo "  Port 8080: ❌ Not responding (HTTP $HTTP_CODE)"
fi
echo ""

# Memory
echo "— Memory —"
if [[ -d "$MEMORY_DIR" ]]; then
    FILE_COUNT=$(find "$MEMORY_DIR" -name "*.md" | wc -l)
    TODAY=$(date +%Y-%m-%d)
    echo "  Daily logs: $FILE_COUNT"
    if [[ -f "${MEMORY_DIR}/${TODAY}.md" ]]; then
        ENTRIES=$(grep -c '^\- \*\*' "${MEMORY_DIR}/${TODAY}.md" 2>/dev/null || echo "0")
        echo "  Today ($TODAY): $ENTRIES entries"
    else
        echo "  Today ($TODAY): No log yet"
    fi
fi
echo ""

# Disk
echo "— Disk —"
df -h / | tail -1 | awk '{printf "  Root: %s used of %s (%s)\n", $3, $2, $5}'
echo ""

# Uptime & memory
echo "— System —"
echo "  $(uptime)"
free -h | awk '/Mem:/{printf "  Memory: %s used of %s (%s available)\n", $3, $2, $7}'
