#!/usr/bin/env bash
set -euo pipefail

DAYS="${1:-30}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.gui.yml}"

# Exclude synthetic support-bundle triage drill jobs from reliability diagnostics.
# Those rows are intentionally injected to validate bundle triage artifacts.

echo "--- Job duration summary (${DAYS}d) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH base AS (
  SELECT
    split_part(j.action,':',1) AS action_root,
    j.status,
    EXTRACT(EPOCH FROM (j.completed_at-j.started_at)) AS dur_s
  FROM jobs j
  LEFT JOIN labs l ON l.id = j.lab_id
  WHERE j.started_at IS NOT NULL
    AND j.completed_at IS NOT NULL
    AND j.started_at > NOW() - INTERVAL '${DAYS} days'
    AND NOT (
      j.action LIKE 'support:bundle-triage-drill%'
      OR COALESCE(j.log_path, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.name, '') LIKE 'ci-triage-lab-%'
      OR COALESCE(l.state_error, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.state_error, '') ILIKE '%synthetic support-bundle triage drill%'
    )
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
    split_part(j.action,':',1) AS action_root,
    COUNT(*) FILTER (WHERE j.status='failed') AS failed_runs,
    COUNT(*) AS total_runs
  FROM jobs j
  LEFT JOIN labs l ON l.id = j.lab_id
  WHERE j.created_at > NOW() - INTERVAL '${DAYS} days'
    AND NOT (
      j.action LIKE 'support:bundle-triage-drill%'
      OR COALESCE(j.log_path, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.name, '') LIKE 'ci-triage-lab-%'
      OR COALESCE(l.state_error, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.state_error, '') ILIKE '%synthetic support-bundle triage drill%'
    )
  GROUP BY split_part(j.action,':',1)
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
    WHEN j.log_path ILIKE '%preflight connectivity check failed%' THEN 'preflight_connectivity_failed'
    WHEN j.log_path ILIKE '%preflight image check failed%' THEN 'preflight_image_check_failed'
    WHEN j.log_path ILIKE '%preflight image validation failed%' THEN 'preflight_image_validation_failed'
    WHEN j.log_path ILIKE '%job timed out after maximum retries%' THEN 'timeout_retries_exhausted'
    WHEN j.log_path LIKE 'Job timed out after 300s%' THEN 'timeout_300s'
    WHEN j.log_path LIKE 'Job timed out after 1200s%' THEN 'timeout_1200s'
    WHEN j.log_path ILIKE '%timed out after%' THEN 'timeout'
    WHEN j.log_path ILIKE '%this session''s transaction has been rolled back%' THEN 'db_session_invalidated'
    WHEN j.log_path ILIKE '%session is in ''inactive'' state%' THEN 'db_session_invalidated'
    WHEN j.log_path ILIKE '%can''t reconnect until invalid transaction is rolled back%' THEN 'db_session_invalidated'
    WHEN j.log_path ILIKE '%idle-in-transaction timeout%' THEN 'db_idle_transaction_timeout'
    WHEN j.log_path ILIKE '%terminating connection due to idle-in-transaction timeout%' THEN 'db_idle_transaction_timeout'
    WHEN j.log_path ILIKE '%server closed the connection unexpectedly%' THEN 'db_connection_closed'
    WHEN j.log_path ILIKE '%connection not open%' THEN 'db_connection_closed'
    WHEN j.log_path ILIKE '%row is otherwise not present%' THEN 'orm_row_stale'
    WHEN j.log_path ILIKE '%objectdeletederror%' THEN 'orm_row_stale'
    WHEN j.log_path ILIKE '%staledataerror%' THEN 'orm_row_stale'
    WHEN j.log_path LIKE 'Parent job completed or missing%' THEN 'orphaned_child'
    WHEN j.log_path ILIKE '%No image found%' THEN 'missing_image'
    WHEN j.log_path ILIKE '%docker image not found%' THEN 'missing_image'
    WHEN j.log_path ILIKE '%required images not available on agent%' THEN 'missing_image'
    WHEN j.log_path ILIKE '%upload/sync required images%' THEN 'missing_image'
    WHEN j.log_path ILIKE '%no healthy agent available%' THEN 'no_healthy_agent'
    WHEN j.log_path ILIKE '%agent became unavailable%' OR j.log_path ILIKE '%agent unavailable%' THEN 'agent_unavailable'
    WHEN j.log_path ILIKE '%connection refused%' THEN 'agent_connection_refused'
    WHEN j.log_path ILIKE '%name or service not known%' THEN 'agent_dns_failure'
    WHEN j.log_path ILIKE '%network is unreachable%' OR j.log_path ILIKE '%host unreachable%' THEN 'agent_unreachable'
    WHEN j.log_path ILIKE '%explicit host assignments failed%' OR j.log_path ILIKE '%assigned host%' THEN 'host_assignment_failed'
    WHEN j.log_path ILIKE '%missing or unhealthy agents for hosts%' THEN 'host_assignment_failed'
    WHEN j.log_path ILIKE '%insufficient resources%' THEN 'insufficient_resources'
    WHEN j.log_path ILIKE '%capacity%' THEN 'capacity_check_failed'
    WHEN j.log_path ILIKE '%link setup failed%' THEN 'link_setup_failed'
    WHEN j.log_path ILIKE '%per-link tunnel creation failed%' THEN 'link_tunnel_creation_failed'
    WHEN j.log_path ILIKE '%could not find ovs port%' THEN 'ovs_port_missing'
    WHEN j.log_path ILIKE '%deployment failed on one or more hosts%' THEN 'deploy_partial_failure'
    WHEN j.log_path ILIKE '%rollback failed%' THEN 'deploy_rollback_failed'
    WHEN j.log_path ILIKE '%stale - cleared after api restart%' THEN 'stale_after_restart'
    WHEN j.log_path ILIKE '%docker api error%' THEN 'docker_api_error'
    WHEN j.log_path ILIKE '%libvirt error%' AND j.log_path ILIKE '%domain not found%' THEN 'libvirt_domain_not_found'
    WHEN j.log_path ILIKE '%libvirt error%' AND j.log_path ILIKE '%unsupported configuration%' THEN 'libvirt_unsupported_configuration'
    WHEN j.log_path ILIKE '%libvirt error%' THEN 'libvirt_error'
    WHEN j.log_path ILIKE '%container creation failed%' THEN 'container_create_failed'
    WHEN j.log_path ILIKE '%create failed%' THEN 'container_create_failed'
    WHEN j.log_path ILIKE '%completed with % error%' THEN 'partial_failure'
    WHEN j.log_path ILIKE '%job execution failed on agent%' THEN 'agent_job_error'
    WHEN j.log_path ILIKE '%unknown action%' THEN 'unknown_action'
    WHEN j.log_path ILIKE '%unexpected error during job execution%' THEN 'unexpected_job_error'
    WHEN j.log_path ILIKE '%failed to create node%' THEN 'create_node_failed'
    WHEN j.log_path ILIKE '%failed to start node%' THEN 'start_node_failed'
    WHEN j.log_path ILIKE '%failed to stop node%' THEN 'stop_node_failed'
    WHEN j.log_path ILIKE '%failed to destroy node%' THEN 'destroy_node_failed'
    WHEN j.log_path IS NULL OR j.log_path='' THEN 'empty'
    ELSE 'other'
  END AS failure_class,
  COUNT(*)
FROM jobs j
LEFT JOIN labs l ON l.id = j.lab_id
WHERE j.status='failed'
  AND j.created_at > NOW() - INTERVAL '${DAYS} days'
  AND NOT (
    j.action LIKE 'support:bundle-triage-drill%'
    OR COALESCE(j.log_path, '') ILIKE '%[support-bundle-triage-drill]%'
    OR COALESCE(l.name, '') LIKE 'ci-triage-lab-%'
    OR COALESCE(l.state_error, '') ILIKE '%[support-bundle-triage-drill]%'
    OR COALESCE(l.state_error, '') ILIKE '%synthetic support-bundle triage drill%'
  )
GROUP BY 1
ORDER BY 2 DESC;
"

echo
echo "--- Failure triage playbook (${DAYS}d, dominant classes) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH classified AS (
  SELECT
    CASE
      WHEN j.log_path ILIKE '%preflight connectivity check failed%' THEN 'preflight_connectivity_failed'
      WHEN j.log_path ILIKE '%preflight image check failed%' THEN 'preflight_image_check_failed'
      WHEN j.log_path ILIKE '%preflight image validation failed%' THEN 'preflight_image_validation_failed'
      WHEN j.log_path ILIKE '%job timed out after maximum retries%' THEN 'timeout_retries_exhausted'
      WHEN j.log_path LIKE 'Job timed out after 300s%' THEN 'timeout_300s'
      WHEN j.log_path LIKE 'Job timed out after 1200s%' THEN 'timeout_1200s'
      WHEN j.log_path ILIKE '%timed out after%' THEN 'timeout'
      WHEN j.log_path ILIKE '%this session''s transaction has been rolled back%' THEN 'db_session_invalidated'
      WHEN j.log_path ILIKE '%session is in ''inactive'' state%' THEN 'db_session_invalidated'
      WHEN j.log_path ILIKE '%can''t reconnect until invalid transaction is rolled back%' THEN 'db_session_invalidated'
      WHEN j.log_path ILIKE '%idle-in-transaction timeout%' THEN 'db_idle_transaction_timeout'
      WHEN j.log_path ILIKE '%terminating connection due to idle-in-transaction timeout%' THEN 'db_idle_transaction_timeout'
      WHEN j.log_path ILIKE '%server closed the connection unexpectedly%' THEN 'db_connection_closed'
      WHEN j.log_path ILIKE '%connection not open%' THEN 'db_connection_closed'
      WHEN j.log_path ILIKE '%row is otherwise not present%' THEN 'orm_row_stale'
      WHEN j.log_path ILIKE '%objectdeletederror%' THEN 'orm_row_stale'
      WHEN j.log_path ILIKE '%staledataerror%' THEN 'orm_row_stale'
      WHEN j.log_path ILIKE '%per-link tunnel creation failed%' THEN 'link_tunnel_creation_failed'
      WHEN j.log_path ILIKE '%completed with % error%' THEN 'partial_failure'
      ELSE 'other'
    END AS failure_class
  FROM jobs j
  LEFT JOIN labs l ON l.id = j.lab_id
  WHERE j.status='failed'
    AND j.created_at > NOW() - INTERVAL '${DAYS} days'
    AND NOT (
      j.action LIKE 'support:bundle-triage-drill%'
      OR COALESCE(j.log_path, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.name, '') LIKE 'ci-triage-lab-%'
      OR COALESCE(l.state_error, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.state_error, '') ILIKE '%synthetic support-bundle triage drill%'
    )
)
SELECT
  failure_class,
  COUNT(*) AS failures,
  CASE
    WHEN failure_class='timeout_300s' THEN 'Check agent preflight reachability and queue pressure; review job timeouts before retry.'
    WHEN failure_class='link_tunnel_creation_failed' THEN 'Inspect OVS/VXLAN drift (duplicate tunnels, stale ports) and reconcile overlays.'
    WHEN failure_class='partial_failure' THEN 'Review per-host sub-errors and reconcile only failed hosts/endpoints.'
    WHEN failure_class='db_session_invalidated' THEN 'Inspect API worker DB transaction handling and check for missing rollback() around exceptions.'
    WHEN failure_class='db_idle_transaction_timeout' THEN 'Investigate long-running idle transactions and query/commit boundaries in API/scheduler jobs.'
    WHEN failure_class='db_connection_closed' THEN 'Check Postgres restarts/network churn and verify SQLAlchemy pool_recycle/pool_pre_ping behavior.'
    WHEN failure_class='orm_row_stale' THEN 'Check concurrent state mutation paths and stale ORM instances before update/delete operations.'
    ELSE 'n/a'
  END AS recommended_action
FROM classified
WHERE failure_class IN (
  'timeout_300s',
  'link_tunnel_creation_failed',
  'partial_failure',
  'db_session_invalidated',
  'db_idle_transaction_timeout',
  'db_connection_closed',
  'orm_row_stale'
)
GROUP BY failure_class
ORDER BY failures DESC, failure_class;
"

echo
echo "--- Recent failure samples for dominant classes (${DAYS}d) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH classified AS (
  SELECT
    j.created_at,
    j.action,
    LEFT(COALESCE(j.log_path,'<empty>'), 220) AS sample,
    CASE
      WHEN j.log_path LIKE 'Job timed out after 300s%' THEN 'timeout_300s'
      WHEN j.log_path ILIKE '%per-link tunnel creation failed%' THEN 'link_tunnel_creation_failed'
      WHEN j.log_path ILIKE '%completed with % error%' THEN 'partial_failure'
      WHEN j.log_path ILIKE '%this session''s transaction has been rolled back%' THEN 'db_session_invalidated'
      WHEN j.log_path ILIKE '%session is in ''inactive'' state%' THEN 'db_session_invalidated'
      WHEN j.log_path ILIKE '%can''t reconnect until invalid transaction is rolled back%' THEN 'db_session_invalidated'
      WHEN j.log_path ILIKE '%idle-in-transaction timeout%' THEN 'db_idle_transaction_timeout'
      WHEN j.log_path ILIKE '%terminating connection due to idle-in-transaction timeout%' THEN 'db_idle_transaction_timeout'
      WHEN j.log_path ILIKE '%server closed the connection unexpectedly%' THEN 'db_connection_closed'
      WHEN j.log_path ILIKE '%connection not open%' THEN 'db_connection_closed'
      WHEN j.log_path ILIKE '%row is otherwise not present%' THEN 'orm_row_stale'
      WHEN j.log_path ILIKE '%objectdeletederror%' THEN 'orm_row_stale'
      WHEN j.log_path ILIKE '%staledataerror%' THEN 'orm_row_stale'
      ELSE 'other'
    END AS failure_class
  FROM jobs j
  LEFT JOIN labs l ON l.id = j.lab_id
  WHERE j.status='failed'
    AND j.created_at > NOW() - INTERVAL '${DAYS} days'
    AND NOT (
      j.action LIKE 'support:bundle-triage-drill%'
      OR COALESCE(j.log_path, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.name, '') LIKE 'ci-triage-lab-%'
      OR COALESCE(l.state_error, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.state_error, '') ILIKE '%synthetic support-bundle triage drill%'
    )
)
SELECT
  to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_utc,
  failure_class,
  split_part(action,':',1) AS action_root,
  sample
FROM classified
WHERE failure_class IN (
  'timeout_300s',
  'link_tunnel_creation_failed',
  'partial_failure',
  'db_session_invalidated',
  'db_idle_transaction_timeout',
  'db_connection_closed',
  'orm_row_stale'
)
ORDER BY created_at DESC
LIMIT 25;
"

echo
echo "--- Top unclassified failure signatures (${DAYS}d) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH classified AS (
  SELECT
    LEFT(COALESCE(j.log_path,'<empty>'), 140) AS msg,
    CASE
      WHEN j.log_path ILIKE '%preflight connectivity check failed%' THEN 'known'
      WHEN j.log_path ILIKE '%preflight image check failed%' THEN 'known'
      WHEN j.log_path ILIKE '%job timed out after%' THEN 'known'
      WHEN j.log_path ILIKE '%this session''s transaction has been rolled back%' THEN 'known'
      WHEN j.log_path ILIKE '%session is in ''inactive'' state%' THEN 'known'
      WHEN j.log_path ILIKE '%can''t reconnect until invalid transaction is rolled back%' THEN 'known'
      WHEN j.log_path ILIKE '%idle-in-transaction timeout%' THEN 'known'
      WHEN j.log_path ILIKE '%terminating connection due to idle-in-transaction timeout%' THEN 'known'
      WHEN j.log_path ILIKE '%server closed the connection unexpectedly%' THEN 'known'
      WHEN j.log_path ILIKE '%connection not open%' THEN 'known'
      WHEN j.log_path ILIKE '%row is otherwise not present%' THEN 'known'
      WHEN j.log_path ILIKE '%objectdeletederror%' THEN 'known'
      WHEN j.log_path ILIKE '%staledataerror%' THEN 'known'
      WHEN j.log_path ILIKE '%parent job completed or missing%' THEN 'known'
      WHEN j.log_path ILIKE '%image not found%' OR j.log_path ILIKE '%no image found%' THEN 'known'
      WHEN j.log_path ILIKE '%no healthy agent available%' THEN 'known'
      WHEN j.log_path ILIKE '%agent unavailable%' OR j.log_path ILIKE '%agent became unavailable%' THEN 'known'
      WHEN j.log_path ILIKE '%explicit host assignments failed%' OR j.log_path ILIKE '%assigned host%' THEN 'known'
      WHEN j.log_path ILIKE '%insufficient resources%' OR j.log_path ILIKE '%capacity%' THEN 'known'
      WHEN j.log_path ILIKE '%link setup failed%' THEN 'known'
      WHEN j.log_path ILIKE '%per-link tunnel creation failed%' OR j.log_path ILIKE '%could not find ovs port%' THEN 'known'
      WHEN j.log_path ILIKE '%deployment failed on one or more hosts%' THEN 'known'
      WHEN j.log_path ILIKE '%rollback failed%' THEN 'known'
      WHEN j.log_path ILIKE '%docker api error%' THEN 'known'
      WHEN j.log_path ILIKE '%libvirt error%' OR j.log_path ILIKE '%domain not found%' OR j.log_path ILIKE '%unsupported configuration%' THEN 'known'
      WHEN j.log_path ILIKE '%completed with % error%' THEN 'known'
      WHEN j.log_path ILIKE '%job execution failed on agent%' THEN 'known'
      WHEN j.log_path ILIKE '%unexpected error during job execution%' THEN 'known'
      WHEN j.log_path ILIKE '%failed to create node%' OR j.log_path ILIKE '%failed to start node%' OR j.log_path ILIKE '%failed to stop node%' OR j.log_path ILIKE '%failed to destroy node%' THEN 'known'
      ELSE 'other'
    END AS class
  FROM jobs j
  LEFT JOIN labs l ON l.id = j.lab_id
  WHERE j.status='failed'
    AND j.created_at > NOW() - INTERVAL '${DAYS} days'
    AND NOT (
      j.action LIKE 'support:bundle-triage-drill%'
      OR COALESCE(j.log_path, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.name, '') LIKE 'ci-triage-lab-%'
      OR COALESCE(l.state_error, '') ILIKE '%[support-bundle-triage-drill]%'
      OR COALESCE(l.state_error, '') ILIKE '%synthetic support-bundle triage drill%'
    )
)
SELECT msg, COUNT(*) AS count
FROM classified
WHERE class='other'
GROUP BY msg
ORDER BY count DESC
LIMIT 12;
"

echo
echo "--- Link endpoint reservation drift ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
WITH expected AS (
  SELECT ls.id AS link_state_id, ls.lab_id, ls.source_node AS node_name,
    CASE
      WHEN ls.source_interface ~* '^(ethernet|eth)[0-9]+$'
        THEN 'eth' || regexp_replace(lower(ls.source_interface), '^(ethernet|eth)', '')
      ELSE lower(ls.source_interface)
    END AS interface_name
  FROM link_states ls
  WHERE ls.desired_state = 'up'
  UNION
  SELECT ls.id AS link_state_id, ls.lab_id, ls.target_node AS node_name,
    CASE
      WHEN ls.target_interface ~* '^(ethernet|eth)[0-9]+$'
        THEN 'eth' || regexp_replace(lower(ls.target_interface), '^(ethernet|eth)', '')
      ELSE lower(ls.target_interface)
    END AS interface_name
  FROM link_states ls
  WHERE ls.desired_state = 'up'
),
missing AS (
  SELECT e.*
  FROM expected e
  LEFT JOIN link_endpoint_reservations r
    ON r.link_state_id = e.link_state_id
   AND r.lab_id = e.lab_id
   AND r.node_name = e.node_name
   AND r.interface_name = e.interface_name
  WHERE r.id IS NULL
),
orphaned AS (
  SELECT r.*
  FROM link_endpoint_reservations r
  LEFT JOIN link_states ls ON ls.id = r.link_state_id
  WHERE ls.id IS NULL OR ls.desired_state != 'up'
)
SELECT 'missing_reservations' AS metric, COUNT(*) AS count FROM missing
UNION ALL
SELECT 'orphaned_reservations', COUNT(*) FROM orphaned
UNION ALL
SELECT 'total_reservations', COUNT(*) FROM link_endpoint_reservations;
"

echo
echo "--- Endpoint reservation conflicts (should be zero) ---"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -c "
SELECT
  lab_id,
  node_name,
  interface_name,
  COUNT(DISTINCT link_state_id) AS active_links
FROM link_endpoint_reservations
GROUP BY lab_id, node_name, interface_name
HAVING COUNT(DISTINCT link_state_id) > 1
ORDER BY active_links DESC, lab_id, node_name, interface_name
LIMIT 20;
"
