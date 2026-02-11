#!/usr/bin/env bash
set -euo pipefail

DAYS="${1:-30}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.gui.yml}"

echo "--- Job duration summary (${DAYS}d) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH base AS (
  SELECT
    split_part(action,':',1) AS action_root,
    status,
    EXTRACT(EPOCH FROM (completed_at-started_at)) AS dur_s
  FROM jobs
  WHERE started_at IS NOT NULL
    AND completed_at IS NOT NULL
    AND started_at > NOW() - INTERVAL '${DAYS} days'
)
SELECT
  action_root,
  status,
  COUNT(*) AS runs,
  ROUND(AVG(dur_s)::numeric,2) AS avg_s,
  ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY dur_s)::numeric,2) AS p95_s,
  ROUND(MAX(dur_s)::numeric,2) AS max_s
FROM base
GROUP BY action_root,status
ORDER BY p95_s DESC NULLS LAST;
"

echo
echo "--- Failure rate by action_root (${DAYS}d) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH c AS (
  SELECT
    split_part(action,':',1) AS action_root,
    COUNT(*) FILTER (WHERE status='failed') AS failed_runs,
    COUNT(*) AS total_runs
  FROM jobs
  WHERE created_at > NOW() - INTERVAL '${DAYS} days'
  GROUP BY split_part(action,':',1)
)
SELECT
  action_root,
  failed_runs,
  total_runs,
  ROUND((failed_runs::numeric/NULLIF(total_runs,0))*100,2) AS fail_pct
FROM c
ORDER BY fail_pct DESC NULLS LAST, total_runs DESC;
"

echo
echo "--- Failure classes (${DAYS}d) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
SELECT
  CASE
    WHEN log_path LIKE 'Job timed out after 300s%' THEN 'timeout_300s'
    WHEN log_path LIKE 'Job timed out after 1200s%' THEN 'timeout_1200s'
    WHEN log_path LIKE 'Parent job completed or missing%' THEN 'orphaned_child'
    WHEN log_path ILIKE '%No image found%' THEN 'missing_image'
    WHEN log_path ILIKE '%no healthy agent available%' THEN 'no_healthy_agent'
    WHEN log_path ILIKE '%explicit host assignments failed%' OR log_path ILIKE '%assigned host%' THEN 'host_assignment_failed'
    WHEN log_path ILIKE '%preflight connectivity check failed%' THEN 'preflight_connectivity_failed'
    WHEN log_path ILIKE '%preflight image check failed%' THEN 'preflight_image_check_failed'
    WHEN log_path IS NULL OR log_path='' THEN 'empty'
    ELSE 'other'
  END AS failure_class,
  COUNT(*)
FROM jobs
WHERE status='failed'
  AND created_at > NOW() - INTERVAL '${DAYS} days'
GROUP BY 1
ORDER BY 2 DESC;
"
