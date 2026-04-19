#!/usr/bin/env bash
# ============================================================
# logs.sh — View Galadriel logs
# Usage:
#   bash logs.sh          # Last 50 lines + follow
#   bash logs.sh 100      # Last 100 lines + follow
#   bash logs.sh --no-follow  # Last 50 lines, no follow
# ============================================================

LINES="${1:-50}"
FOLLOW=true

if [[ "${1:-}" == "--no-follow" ]] || [[ "${2:-}" == "--no-follow" ]]; then
    FOLLOW=false
fi

if $FOLLOW; then
    journalctl -u galadriel -n "$LINES" -f --no-pager
else
    journalctl -u galadriel -n "$LINES" --no-pager
fi
