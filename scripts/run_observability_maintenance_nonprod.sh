#!/usr/bin/env bash
set -euo pipefail

# Non-production observability maintenance bundle:
# 1) Controlled canary traffic + coverage checks
# 2) Support-bundle triage drill (artifact completeness + warnings gate)
# 3) DB-backed diagnostic report persisted under reports/

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports/observability}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
API_URL="${API_URL:-http://localhost:8000}"
SUPPORT_BUNDLE_USERNAME="${SUPPORT_BUNDLE_USERNAME:-${CANARY_USERNAME:-admin@example.com}}"
SUPPORT_BUNDLE_PASSWORD="${SUPPORT_BUNDLE_PASSWORD:-${CANARY_PASSWORD:-changeme123}}"
SUPPORT_BUNDLE_TIME_WINDOW_HOURS="${SUPPORT_BUNDLE_TIME_WINDOW_HOURS:-24}"
SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS="${SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS:-0}"

mkdir -p "$REPORT_DIR"

echo "[maintenance] running canary"
"$ROOT_DIR/scripts/run_observability_canary_nonprod.sh" \
  | tee "$REPORT_DIR/canary_${STAMP}.log"

echo "[maintenance] running support-bundle triage drill"
API_URL="$API_URL" \
SUPPORT_BUNDLE_USERNAME="$SUPPORT_BUNDLE_USERNAME" \
SUPPORT_BUNDLE_PASSWORD="$SUPPORT_BUNDLE_PASSWORD" \
SUPPORT_BUNDLE_TIME_WINDOW_HOURS="$SUPPORT_BUNDLE_TIME_WINDOW_HOURS" \
SUPPORT_BUNDLE_OUTPUT_ZIP="$REPORT_DIR/support_bundle_triage_${STAMP}.zip" \
SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS="$SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS" \
"$ROOT_DIR/scripts/run_support_bundle_triage_nonprod.sh" \
  | tee "$REPORT_DIR/support_bundle_triage_${STAMP}.log"

echo "[maintenance] running db report"
"$ROOT_DIR/scripts/observability_db_report.sh" "${DB_REPORT_DAYS:-30}" \
  | tee "$REPORT_DIR/db_report_${STAMP}.log"

echo "[maintenance] running link reservation drift self-check"
if ! "$ROOT_DIR/scripts/link_reservation_drift_check.sh" \
  | tee "$REPORT_DIR/link_reservation_drift_${STAMP}.log"; then
  echo "[maintenance] drift self-check reported failures (see log)"
fi

echo "[maintenance] done"
