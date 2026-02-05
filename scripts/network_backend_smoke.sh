#!/usr/bin/env bash
set -euo pipefail

AGENT_URL=${AGENT_URL:-"http://localhost:8001"}
LAB_ID=${LAB_ID:-"smoke-lab"}
STATEFUL=${STATEFUL:-"0"}

usage() {
  cat <<USAGE
Network backend smoke checks.

Non-destructive checks run by default. Set STATEFUL=1 to run
optional stateful checks (requires additional env vars).

Environment variables:
  AGENT_URL   Agent base URL (default: http://localhost:8001)
  LAB_ID      Lab identifier to target (default: smoke-lab)
  STATEFUL    Run stateful checks (default: 0)

Stateful link test vars:
  LINK_SOURCE_NODE
  LINK_SOURCE_IFACE
  LINK_TARGET_NODE
  LINK_TARGET_IFACE

Stateful overlay test vars:
  OVERLAY_LOCAL_IP
  OVERLAY_REMOTE_IP
  OVERLAY_LINK_ID
  OVERLAY_CONTAINER
  OVERLAY_INTERFACE
  OVERLAY_VLAN
  OVERLAY_TENANT_MTU   (optional, default: 0)
  OVERLAY_REMOTE_HOST_ID (optional)
  OVERLAY_DELETE_VTEP  (optional, default: true)

Optional VM/OVS port check (local host only):
  OVS_PORT_NAME         (requires ovs-vsctl in PATH)

Examples:
  AGENT_URL=http://agent-1:8001 ./scripts/network_backend_smoke.sh
  STATEFUL=1 LAB_ID=lab1 LINK_SOURCE_NODE=r1 LINK_SOURCE_IFACE=eth1 \
    LINK_TARGET_NODE=r2 LINK_TARGET_IFACE=eth1 ./scripts/network_backend_smoke.sh
USAGE
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

need_env() {
  local var=$1
  local label=$2
  if [[ -z "${!var:-}" ]]; then
    echo "  - missing $label ($var)"
    return 1
  fi
  return 0
}

echo "== Network backend smoke checks =="

echo "[1/6] Backend selection"
if command -v jq >/dev/null 2>&1; then
  curl -sS "$AGENT_URL/health" | jq -r '.status' >/dev/null
else
  curl -sS "$AGENT_URL/health" >/dev/null
fi

echo "[2/6] OVS status"
if command -v jq >/dev/null 2>&1; then
  curl -sS "$AGENT_URL/ovs/status" | jq -r '.initialized' >/dev/null
else
  curl -sS "$AGENT_URL/ovs/status" >/dev/null
fi

echo "[3/6] OVS plugin health"
if command -v jq >/dev/null 2>&1; then
  curl -sS "$AGENT_URL/ovs-plugin/health" | jq -r '.healthy' >/dev/null
else
  curl -sS "$AGENT_URL/ovs-plugin/health" >/dev/null
fi

echo "[4/6] Overlay status"
if command -v jq >/dev/null 2>&1; then
  curl -sS "$AGENT_URL/overlay/status" | jq -r '.tunnels | length' >/dev/null
else
  curl -sS "$AGENT_URL/overlay/status" >/dev/null
fi

echo "[5/6] External connections list"
if command -v jq >/dev/null 2>&1; then
  curl -sS "$AGENT_URL/labs/$LAB_ID/external" | jq -r '.connections | length' >/dev/null
else
  curl -sS "$AGENT_URL/labs/$LAB_ID/external" >/dev/null
fi

echo "[6/6] Links list"
if command -v jq >/dev/null 2>&1; then
  curl -sS "$AGENT_URL/labs/$LAB_ID/links" | jq -r '.links | length' >/dev/null
else
  curl -sS "$AGENT_URL/labs/$LAB_ID/links" >/dev/null
fi

if [[ "$STATEFUL" == "1" ]]; then
  echo "== Stateful checks =="

  echo "[S1] Hot-connect / hot-disconnect"
  missing=false
  need_env LINK_SOURCE_NODE "link source node" || missing=true
  need_env LINK_SOURCE_IFACE "link source interface" || missing=true
  need_env LINK_TARGET_NODE "link target node" || missing=true
  need_env LINK_TARGET_IFACE "link target interface" || missing=true

  if [[ "$missing" == "true" ]]; then
    echo "  Skipping hot-connect; provide LINK_* env vars."
  else
    link_payload=$(cat <<JSON
{
  "source_node": "$LINK_SOURCE_NODE",
  "source_interface": "$LINK_SOURCE_IFACE",
  "target_node": "$LINK_TARGET_NODE",
  "target_interface": "$LINK_TARGET_IFACE"
}
JSON
)

    curl -sS -X POST "$AGENT_URL/labs/$LAB_ID/links" \
      -H 'Content-Type: application/json' \
      -d "$link_payload" >/dev/null

    link_id="$LINK_SOURCE_NODE:$LINK_SOURCE_IFACE-$LINK_TARGET_NODE:$LINK_TARGET_IFACE"

    curl -sS -X DELETE "$AGENT_URL/labs/$LAB_ID/links/$link_id" >/dev/null
  fi

  echo "[S2] Overlay attach/detach (optional)"
  missing=false
  need_env OVERLAY_LOCAL_IP "overlay local IP" || missing=true
  need_env OVERLAY_REMOTE_IP "overlay remote IP" || missing=true
  need_env OVERLAY_LINK_ID "overlay link id" || missing=true
  need_env OVERLAY_CONTAINER "overlay container" || missing=true
  need_env OVERLAY_INTERFACE "overlay interface" || missing=true
  need_env OVERLAY_VLAN "overlay vlan" || missing=true

  if [[ "$missing" == "true" ]]; then
    echo "  Skipping overlay attach/detach; provide OVERLAY_* env vars."
  else
    overlay_mtu=${OVERLAY_TENANT_MTU:-0}
    overlay_delete_vtep=${OVERLAY_DELETE_VTEP:-true}

    vtep_payload=$(cat <<JSON
{
  "local_ip": "$OVERLAY_LOCAL_IP",
  "remote_ip": "$OVERLAY_REMOTE_IP",
  "remote_host_id": "${OVERLAY_REMOTE_HOST_ID:-}"
}
JSON
)

    curl -sS -X POST "$AGENT_URL/overlay/vtep" \
      -H 'Content-Type: application/json' \
      -d "$vtep_payload" >/dev/null

    attach_payload=$(cat <<JSON
{
  "lab_id": "$LAB_ID",
  "container_name": "$OVERLAY_CONTAINER",
  "interface_name": "$OVERLAY_INTERFACE",
  "vlan_tag": $OVERLAY_VLAN,
  "tenant_mtu": $overlay_mtu,
  "link_id": "$OVERLAY_LINK_ID",
  "remote_ip": "$OVERLAY_REMOTE_IP"
}
JSON
)

    curl -sS -X POST "$AGENT_URL/overlay/attach-link" \
      -H 'Content-Type: application/json' \
      -d "$attach_payload" >/dev/null

    detach_payload=$(cat <<JSON
{
  "lab_id": "$LAB_ID",
  "container_name": "$OVERLAY_CONTAINER",
  "interface_name": "$OVERLAY_INTERFACE",
  "link_id": "$OVERLAY_LINK_ID",
  "remote_ip": "$OVERLAY_REMOTE_IP",
  "delete_vtep_if_unused": $overlay_delete_vtep
}
JSON
)

    curl -sS -X POST "$AGENT_URL/overlay/detach-link" \
      -H 'Content-Type: application/json' \
      -d "$detach_payload" >/dev/null
  fi

  echo "[S3] VM/OVS port presence (optional, local host)"
  if [[ -n "${OVS_PORT_NAME:-}" ]]; then
    if command -v ovs-vsctl >/dev/null 2>&1; then
      ovs-vsctl port-to-br "$OVS_PORT_NAME" >/dev/null 2>&1 \
        && echo "  OVS port $OVS_PORT_NAME exists" \
        || echo "  OVS port $OVS_PORT_NAME not found"
    else
      echo "  Skipping OVS port check; ovs-vsctl not found."
    fi
  else
    echo "  Skipping OVS port check; set OVS_PORT_NAME."
  fi
fi

echo "All smoke checks completed."
