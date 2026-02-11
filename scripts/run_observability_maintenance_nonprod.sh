#!/usr/bin/env bash
set -euo pipefail

# Non-production observability maintenance bundle:
# 1) Controlled canary traffic + coverage checks
# 2) DB-backed diagnostic report persisted under reports/

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports/observability}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$REPORT_DIR"

echo "[maintenance] running canary"
"$ROOT_DIR/scripts/run_observability_canary_nonprod.sh" \
  | tee "$REPORT_DIR/canary_${STAMP}.log"

echo "[maintenance] running db report"
"$ROOT_DIR/scripts/observability_db_report.sh" "${DB_REPORT_DAYS:-30}" \
  | tee "$REPORT_DIR/db_report_${STAMP}.log"

echo "[maintenance] done"
