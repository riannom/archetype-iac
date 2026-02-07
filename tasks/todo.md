# Per-Link VNI Model for Cross-Host VXLAN Connectivity

## Problem

The current **trunk VTEP model** creates one VXLAN tunnel per remote host in trunk mode (no VLAN tag on the tunnel port). Traffic isolation relies on both sides of a cross-host link using the **same VLAN tag**. But the observed VLANs don't match across hosts (e.g., 3138 on host A vs 3297 on host B), causing all cross-host traffic to be silently dropped.

**Root cause:** The trunk VTEP preserves VLAN tags through the tunnel. When VLANs don't match (due to independent allocation on each agent, agent restarts, or failed overlay attachments), traffic arrives on one side tagged with a VLAN that no local port belongs to.

## Solution

Replace the trunk VTEP model with **per-link access-mode VXLAN ports**. Each cross-host link gets:

1. Its own VXLAN port on each agent (access mode with `tag=<local_vlan>`)
2. A unique VNI per link (shared between both sides, deterministic hash)
3. No VLAN coordination needed — each side uses its container's existing local VLAN

### How it works (OVS access-mode tunnel ports)

```
Host A:  container_a:eth3 [VLAN 3138]  <->  vxlan-abc123 [tag=3138, VNI=40001, remote=B]
                                                    |
                                              VXLAN tunnel
                                                    |
Host B:  container_c:eth1 [VLAN 3297]  <->  vxlan-abc123 [tag=3297, VNI=40001, remote=A]
```

- **Egress:** Frame on VLAN 3138 -> OVS matches access port tag -> strips VLAN -> encapsulates with VNI 40001 -> tunnel
- **Ingress:** Frame from tunnel VNI 40001 -> decapsulates -> tags with VLAN 3297 -> delivers to container

The VNI is the shared link identifier. VLANs are purely local. No coordination.

### Why this is correct

- OVS `tag=` column makes a port "access mode" — strips/adds the specified VLAN
- This applies to tunnel ports (VXLAN, GRE) the same as physical ports
- Works in `fail_mode: standalone` (already required per CLAUDE.md)
- Standard VXLAN behavior: local VLAN <-> VNI <-> remote VLAN

### Safety analysis

- Each container interface has a unique VLAN from `docker_plugin._allocate_vlan()` (range 100-4000)
- Each interface has at most one link (point-to-point), so no VLAN conflicts
- Per-link VXLAN ports: unique VNI per link, unique tag per interface -> no traffic leaking
- Same-host links (hot_connect) are unaffected — they don't use VXLAN

---

## Implementation Plan

### Phase 1: Agent overlay.py — Per-link VXLAN port methods

**File:** `agent/network/overlay.py`

- [x] 1.1 Add `LinkTunnel` dataclass
  - Fields: `link_id, vni, local_ip, remote_ip, local_vlan, interface_name, lab_id, tenant_mtu`

- [x] 1.2 Add `create_link_tunnel()` method
  - Takes: `lab_id, link_id, vni, local_ip, remote_ip, local_vlan, tenant_mtu`
  - Creates access-mode VXLAN port:
    ```
    ovs-vsctl add-port arch-ovs vxlan-{hash} tag={local_vlan} \
      -- set interface vxlan-{hash} type=vxlan \
      options:key={vni} options:remote_ip={remote_ip} options:local_ip={local_ip}
    ```
  - Interface naming: `vxlan-{md5(lab_id:link_id)[:8]}` (max 14 chars)
  - Track in `_link_tunnels: dict[str, LinkTunnel]` (keyed by link_id)
  - Discover path MTU to remote peer for tenant_mtu

- [x] 1.3 Add `delete_link_tunnel(link_id)` method
  - Removes the VXLAN port from OVS: `ovs-vsctl --if-exists del-port bridge <name>`
  - Remove from `_link_tunnels` tracking

- [x] 1.4 Update `cleanup_lab()` to handle per-link tunnels
  - Delete all `_link_tunnels` for the given lab_id

- [x] 1.5 Keep legacy `Vtep` / trunk methods intact but unused (cleanup in separate PR)

### Phase 2: Agent main.py + schemas.py — Endpoint updates

**Files:** `agent/main.py`, `agent/schemas.py`

- [x] 2.1 Update `AttachOverlayInterfaceRequest` schema
  - Remove `vlan_tag` field
  - Add `vni: int` (required)
  - Add `local_ip: str` (required)
  - Make `link_id: str` required (was optional)
  - Make `remote_ip: str` required (was optional)

- [x] 2.2 Modify `POST /overlay/attach-link` endpoint
  - Agent discovers container's current VLAN from docker_plugin state
  - Calls `create_link_tunnel()` instead of just setting VLAN tag
  - No longer needs prior VTEP creation — the endpoint is self-contained

- [x] 2.3 Update `DetachOverlayInterfaceRequest` schema
  - Remove `remote_ip` (agent tracks by link_id)
  - Remove `delete_vtep_if_unused` (no more shared VTEPs)

- [x] 2.4 Modify `POST /overlay/detach-link` endpoint
  - Isolate container port (assign new unique VLAN)
  - Delete per-link VXLAN port via `delete_link_tunnel()`
  - Remove VTEP reference counting logic

- [x] 2.5 Deprecate `POST /overlay/vtep` endpoint (no longer needed)

### Phase 3: API agent_client.py — Client function updates

**File:** `api/app/agent_client.py`

- [x] 3.1 Remove `allocate_link_vlan()` function

- [x] 3.2 Remove `ensure_vtep_on_agent()` function

- [x] 3.3 Update `attach_overlay_interface_on_agent()`
  - Pass `vni` instead of `vlan_tag`
  - Pass `local_ip` (agent's own IP)
  - Make `link_id`, `remote_ip` required

- [x] 3.4 Update `detach_overlay_interface_on_agent()`
  - Remove `remote_ip` parameter
  - Remove `delete_vtep_if_unused` parameter

- [x] 3.5 Rewrite `setup_cross_host_link_v2()`
  - Remove VTEP ensure step (no longer needed)
  - Allocate per-link VNI using `allocate_vni()` from link_manager.py
  - Call updated `attach_overlay_interface_on_agent()` on both agents in parallel
  - Each agent discovers its container's local VLAN internally

### Phase 4: API orchestration — Teardown path updates

**Files:** `api/app/tasks/link_orchestration.py`, `api/app/tasks/live_links.py`, `api/app/services/link_manager.py`

- [x] 4.1 Update `teardown_deployment_links()` in link_orchestration.py
  - Continue calling `cleanup_overlay_on_agent()` for bulk lab teardown
  - Agent-side `cleanup_lab()` handles new per-link tunnels

- [x] 4.2 Update `teardown_link()` in live_links.py
  - Update detach calls to match new schema (no remote_ip, no delete_vtep_if_unused)

- [x] 4.3 Update `LinkManager.teardown_cross_host_link()` in link_manager.py
  - Same schema updates as above

### Phase 5: Agent OVS backend — Delegation updates

**File:** `agent/network/backends/ovs_backend.py`

- [x] 5.1 Add `overlay_create_link_tunnel()` delegation method
- [x] 5.2 Add `overlay_delete_link_tunnel()` delegation method

### Phase 6: Verification

- [x] 6.1 Syntax check all modified Python files
- [x] 6.2 Run API tests (pytest)
- [x] 6.3 Manual review: trace full data flow from `setup_cross_host_link_v2` through agent

### Phase 7: Reconciliation compatibility fixes

- [x] 7.1 Fix `verify_cross_host_link()` in link_validator.py — verify per-link VXLAN tunnel existence instead of comparing VLANs
- [x] 7.2 Fix `get_tunnel_status()` in overlay.py — include `_link_tunnels` data in status output
- [x] 7.3 Fix `attempt_partial_recovery()` in link_reconciliation.py — remove stale VLAN allocation, use VNI only
- [x] 7.4 Update test_link_validator.py — replace VLAN mismatch test with per-link VNI tests

---

## Files Modified (Summary)

| File | Changes |
|------|---------|
| `agent/network/overlay.py` | New `LinkTunnel` dataclass, `create_link_tunnel()`, `delete_link_tunnel()`, updated `cleanup_lab()`, updated `get_tunnel_status()` |
| `agent/main.py` | Modified `/overlay/attach-link` and `/overlay/detach-link` endpoints, deprecate `/overlay/vtep` |
| `agent/schemas.py` | Updated request/response schemas for attach/detach |
| `agent/network/backends/ovs_backend.py` | New delegation methods |
| `api/app/agent_client.py` | Removed `allocate_link_vlan`, `ensure_vtep_on_agent`; updated `setup_cross_host_link_v2`, attach/detach |
| `api/app/tasks/link_orchestration.py` | Updated teardown calls |
| `api/app/tasks/live_links.py` | Updated teardown calls |
| `api/app/services/link_manager.py` | Updated teardown calls |
| `api/app/services/link_validator.py` | Cross-host verification uses per-link tunnel existence instead of VLAN matching |
| `api/app/tasks/link_reconciliation.py` | Partial recovery uses VNI instead of stale VLAN allocation |
| `api/tests/test_link_validator.py` | Updated tests for per-link VNI model |

## Not Changed

- `agent/network/docker_plugin.py` — Container VLAN allocation unchanged (interfaces keep their local VLANs)
- `api/app/models.py` — VxlanTunnel DB model unchanged (already has per-link vni field)
- Frontend — No UI changes needed
- Same-host links — hot_connect mechanism unchanged
