#!/usr/bin/env bash
set -euo pipefail

# Installs or prints a non-prod cron schedule for observability maintenance.
# Default: print only. Pass --apply to install into current user's crontab.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCHEDULE="${SCHEDULE:-*/30 * * * *}"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/reports/observability/cron.log}"

ENTRY="$SCHEDULE cd $ROOT_DIR && $PYTHON_BIN scripts/observability_canary.py --apply --window 30m >> $LOG_FILE 2>&1"
ENTRY_DB="15 */6 * * * cd $ROOT_DIR && ./scripts/observability_db_report.sh 30 >> $LOG_FILE 2>&1"

echo "# Proposed cron entries:"
echo "$ENTRY"
echo "$ENTRY_DB"

if [[ "${1:-}" != "--apply" ]]; then
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
if ! grep -Fq "$ENTRY_DB" "$TMP_CRON"; then
  echo "$ENTRY_DB" >> "$TMP_CRON"
fi

crontab "$TMP_CRON"
rm -f "$TMP_CRON"
echo "Installed cron entries."
