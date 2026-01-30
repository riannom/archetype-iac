#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_FILE="$ROOT_DIR/docker-compose.gui.yml"

DB_DUMP=${1:-}
WORKSPACE_TAR=${2:-}

if [[ -z "$DB_DUMP" || -z "$WORKSPACE_TAR" ]]; then
  echo "Usage: scripts/restore.sh <db_dump.sql> <workspaces.tar.gz>"
  exit 1
fi

echo "Restoring Postgres..."
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype < "$DB_DUMP"

echo "Restoring workspaces..."
docker run --rm \
  -v archetype_workspaces:/data \
  -v "$ROOT_DIR":/backup \
  alpine:3.20 \
  tar -xzf "/backup/$WORKSPACE_TAR" -C /data

echo "Restore complete."
