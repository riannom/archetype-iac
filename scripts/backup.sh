#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TIMESTAMP=$(date +%Y%m%d%H%M%S)
BACKUP_DIR="$ROOT_DIR/backups"
COMPOSE_FILE="$ROOT_DIR/docker-compose.gui.yml"

mkdir -p "$BACKUP_DIR"

echo "Backing up Postgres..."
docker compose -f "$COMPOSE_FILE" exec -T postgres pg_dump -U archetype archetype > "$BACKUP_DIR/db-$TIMESTAMP.sql"

echo "Backing up lab workspaces..."
docker run --rm \
  -v archetype_workspaces:/data \
  -v "$BACKUP_DIR":/backup \
  alpine:3.20 \
  tar -czf "/backup/workspaces-$TIMESTAMP.tar.gz" -C /data .

echo "Backup complete: $BACKUP_DIR"
