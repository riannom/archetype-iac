#!/usr/bin/env bash
set -euo pipefail

# Installs or prints a non-prod cron schedule for observability maintenance.
# Default: print only. Pass --apply to install into current user's crontab.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCHEDULE="${SCHEDULE:-*/30 * * * *}"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/reports/observability/cron.log}"
API_URL="${API_URL:-http://localhost:8000}"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
CANARY_USERNAME="${CANARY_USERNAME:-admin@example.com}"
CANARY_PASSWORD="${CANARY_PASSWORD:-changeme123}"
CANARY_LAB_ID="${CANARY_LAB_ID:-}"
CANARY_SYNC_NODE_ID="${CANARY_SYNC_NODE_ID:-}"
CANARY_WINDOW="${CANARY_WINDOW:-30m}"
CANARY_SCRAPE_WAIT_SECONDS="${CANARY_SCRAPE_WAIT_SECONDS:-65}"
SUPPORT_BUNDLE_USERNAME="${SUPPORT_BUNDLE_USERNAME:-$CANARY_USERNAME}"
SUPPORT_BUNDLE_PASSWORD="${SUPPORT_BUNDLE_PASSWORD:-$CANARY_PASSWORD}"
SUPPORT_BUNDLE_TIME_WINDOW_HOURS="${SUPPORT_BUNDLE_TIME_WINDOW_HOURS:-24}"
SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS="${SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS:-0}"

ENTRY="$SCHEDULE cd $ROOT_DIR && API_URL='$API_URL' PROMETHEUS_URL='$PROM_URL' CANARY_USERNAME='$CANARY_USERNAME' CANARY_PASSWORD='$CANARY_PASSWORD' CANARY_LAB_ID='$CANARY_LAB_ID' CANARY_SYNC_NODE_ID='$CANARY_SYNC_NODE_ID' CANARY_WINDOW='$CANARY_WINDOW' CANARY_SCRAPE_WAIT_SECONDS='$CANARY_SCRAPE_WAIT_SECONDS' ./scripts/run_observability_canary_nonprod.sh >> $LOG_FILE 2>&1"
ENTRY_TRIAGE="$SCHEDULE cd $ROOT_DIR && API_URL='$API_URL' SUPPORT_BUNDLE_USERNAME='$SUPPORT_BUNDLE_USERNAME' SUPPORT_BUNDLE_PASSWORD='$SUPPORT_BUNDLE_PASSWORD' SUPPORT_BUNDLE_TIME_WINDOW_HOURS='$SUPPORT_BUNDLE_TIME_WINDOW_HOURS' SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS='$SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS' ./scripts/run_support_bundle_triage_nonprod.sh >> $LOG_FILE 2>&1"
ENTRY_DB="15 */6 * * * cd $ROOT_DIR && ./scripts/observability_db_report.sh 30 >> $LOG_FILE 2>&1"
ENTRY_DRIFT="30 2 * * * cd $ROOT_DIR && ./scripts/link_reservation_drift_check.sh >> $LOG_FILE 2>&1"

echo "# Proposed cron entries:"
echo "$ENTRY"
echo "$ENTRY_TRIAGE"
echo "$ENTRY_DB"
echo "$ENTRY_DRIFT"

if [[ "${1:-}" != "--apply" ]]; then
  if [[ -z "$CANARY_LAB_ID" || -z "$CANARY_SYNC_NODE_ID" ]]; then
    echo "Info: CANARY_LAB_ID/CANARY_SYNC_NODE_ID are empty; canary will auto-discover a lab/node at runtime."
  fi
  echo
  echo "Dry-run only. Re-run with --apply to install."
  exit 0
fi

mkdir -p "$(dirname "$LOG_FILE")"
TMP_CRON="$(mktemp)"
crontab -l 2>/dev/null > "$TMP_CRON" || true

if ! grep -Fq "$ENTRY" "$TMP_CRON"; then
  echo "$ENTRY" >> "$TMP_CRON"
fi
if ! grep -Fq "$ENTRY_TRIAGE" "$TMP_CRON"; then
  echo "$ENTRY_TRIAGE" >> "$TMP_CRON"
fi
if ! grep -Fq "$ENTRY_DB" "$TMP_CRON"; then
  echo "$ENTRY_DB" >> "$TMP_CRON"
fi
if ! grep -Fq "$ENTRY_DRIFT" "$TMP_CRON"; then
  echo "$ENTRY_DRIFT" >> "$TMP_CRON"
fi

crontab "$TMP_CRON"
rm -f "$TMP_CRON"
echo "Installed cron entries."
