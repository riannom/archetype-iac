#!/usr/bin/env bash
set -euo pipefail

# Intended for non-production periodic execution (e.g. cron/systemd timer).
# You may pin a specific lab/node via CANARY_* env vars, or let the script
# auto-discover a suitable lab/node for synthetic traffic.

API_URL="${API_URL:-http://localhost:8000}"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
USERNAME="${CANARY_USERNAME:-admin@example.com}"
PASSWORD="${CANARY_PASSWORD:-changeme123}"
LAB_ID="${CANARY_LAB_ID:-}"
SYNC_NODE_ID="${CANARY_SYNC_NODE_ID:-}"
WINDOW="${CANARY_WINDOW:-30m}"

ARGS=(
  --apply
  --api-url "$API_URL"
  --prometheus-url "$PROM_URL"
  --username "$USERNAME"
  --password "$PASSWORD"
  --status-probes 3
  --scrape-wait-seconds 20
  --window "$WINDOW"
)

if [[ -n "$LAB_ID" ]]; then
  ARGS+=(--lab-id "$LAB_ID")
fi
if [[ -n "$SYNC_NODE_ID" ]]; then
  ARGS+=(--sync-node-id "$SYNC_NODE_ID")
fi
if [[ -z "$LAB_ID" || -z "$SYNC_NODE_ID" ]]; then
  echo "[canary] CANARY_LAB_ID/CANARY_SYNC_NODE_ID not fully set; using auto-discovery"
  ARGS+=(--auto-discover)
fi

python3 scripts/observability_canary.py "${ARGS[@]}"
