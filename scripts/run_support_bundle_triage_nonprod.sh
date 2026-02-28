#!/usr/bin/env bash
set -euo pipefail

# Intended for non-production periodic execution (e.g. cron/systemd timer).
# Generates and validates a support bundle with seeded deterministic failure signals.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
USERNAME="${SUPPORT_BUNDLE_USERNAME:-${CANARY_USERNAME:-admin@example.com}}"
PASSWORD="${SUPPORT_BUNDLE_PASSWORD:-${CANARY_PASSWORD:-changeme123}}"
TIME_WINDOW_HOURS="${SUPPORT_BUNDLE_TIME_WINDOW_HOURS:-24}"
OUTPUT_ZIP="${SUPPORT_BUNDLE_OUTPUT_ZIP:-}"
ALLOW_COMPLETENESS_WARNINGS="${SUPPORT_BUNDLE_ALLOW_COMPLETENESS_WARNINGS:-0}"

ARGS=(
  --api-url "$API_URL"
  --username "$USERNAME"
  --password "$PASSWORD"
  --time-window-hours "$TIME_WINDOW_HOURS"
)

if [[ -n "$OUTPUT_ZIP" ]]; then
  ARGS+=(--output-zip "$OUTPUT_ZIP")
fi
if [[ "$ALLOW_COMPLETENESS_WARNINGS" == "1" ]]; then
  ARGS+=(--allow-completeness-warnings)
fi

python3 "$ROOT_DIR/scripts/support_bundle_triage_drill.py" "${ARGS[@]}"
