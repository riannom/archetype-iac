#!/usr/bin/env bash
set -euo pipefail

# Intended for non-production periodic execution (e.g. cron/systemd timer).
# Requires a lab/node pair that is safe for synthetic traffic.

API_URL="${API_URL:-http://localhost:8000}"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
USERNAME="${CANARY_USERNAME:-admin@example.com}"
PASSWORD="${CANARY_PASSWORD:-changeme123}"
LAB_ID="${CANARY_LAB_ID:-}"
SYNC_NODE_ID="${CANARY_SYNC_NODE_ID:-}"
WINDOW="${CANARY_WINDOW:-30m}"

if [[ -z "$LAB_ID" || -z "$SYNC_NODE_ID" ]]; then
  echo "CANARY_LAB_ID and CANARY_SYNC_NODE_ID are required" >&2
  exit 2
fi

python3 scripts/observability_canary.py \
  --apply \
  --api-url "$API_URL" \
  --prometheus-url "$PROM_URL" \
  --username "$USERNAME" \
  --password "$PASSWORD" \
  --lab-id "$LAB_ID" \
  --sync-node-id "$SYNC_NODE_ID" \
  --status-probes 3 \
  --scrape-wait-seconds 20 \
  --window "$WINDOW"
