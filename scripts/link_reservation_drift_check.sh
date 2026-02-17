#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.gui.yml}"
PSQL=(docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U archetype -d archetype -At)

echo "--- Link reservation drift self-check ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ---"

COUNTS="$("${PSQL[@]}" -c "
WITH expected AS (
  SELECT ls.id AS link_state_id, ls.lab_id, ls.source_node AS node_name,
    CASE
      WHEN ls.source_interface ~* '^(ethernet|eth)[0-9]+$'
        THEN 'eth' || regexp_replace(lower(ls.source_interface), '^(ethernet|eth)', '')
      ELSE lower(ls.source_interface)
    END AS interface_name
  FROM link_states ls
  WHERE ls.desired_state = 'up'
  UNION ALL
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
  SELECT COUNT(*) AS count
  FROM expected e
  LEFT JOIN link_endpoint_reservations r
    ON r.link_state_id = e.link_state_id
   AND r.lab_id = e.lab_id
   AND r.node_name = e.node_name
   AND r.interface_name = e.interface_name
  WHERE r.id IS NULL
),
orphaned AS (
  SELECT COUNT(*) AS count
  FROM link_endpoint_reservations r
  LEFT JOIN link_states ls ON ls.id = r.link_state_id
  WHERE ls.id IS NULL OR ls.desired_state != 'up'
),
conflicts AS (
  SELECT COUNT(*) AS count
  FROM (
    SELECT lab_id, node_name, interface_name
    FROM link_endpoint_reservations
    GROUP BY lab_id, node_name, interface_name
    HAVING COUNT(DISTINCT link_state_id) > 1
  ) c
)
SELECT
  (SELECT count FROM missing),
  (SELECT count FROM orphaned),
  (SELECT count FROM conflicts),
  (SELECT COUNT(*) FROM link_endpoint_reservations);
")"

IFS='|' read -r MISSING ORPHANED CONFLICTS TOTAL <<< "$COUNTS"

echo "missing_reservations=$MISSING"
echo "orphaned_reservations=$ORPHANED"
echo "conflicting_endpoints=$CONFLICTS"
echo "total_reservations=$TOTAL"

if [[ "${CONFLICTS:-0}" != "0" ]]; then
  echo
  echo "--- Conflicting endpoints (top 20) ---"
  "${PSQL[@]}" -c "
SELECT
  r.lab_id,
  r.node_name,
  r.interface_name,
  string_agg(ls.link_name, ', ' ORDER BY ls.link_name) AS links
FROM link_endpoint_reservations r
JOIN link_states ls ON ls.id = r.link_state_id
GROUP BY r.lab_id, r.node_name, r.interface_name
HAVING COUNT(DISTINCT r.link_state_id) > 1
ORDER BY r.lab_id, r.node_name, r.interface_name
LIMIT 20;
"
fi

if [[ "${MISSING:-0}" != "0" || "${ORPHANED:-0}" != "0" || "${CONFLICTS:-0}" != "0" ]]; then
  echo "RESULT=FAIL"
  exit 2
fi

echo "RESULT=PASS"
