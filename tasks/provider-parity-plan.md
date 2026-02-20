# Provider Parity Plan: Libvirt VMs vs Docker Containers

## Overview

Comprehensive audit identified ~30 feature parity gaps between Docker containers and libvirt VMs across agent providers, API/tasks, frontend, and image/vendor management. Core lifecycle (create/start/stop/destroy) has full parity, but significant gaps exist in deployment workflow, vendor configuration, and resource visibility.

---

## P0 — Fix Before Next Mixed-Mode Deployment

### 1. ~~Migration Provider Mismatch (CRITICAL)~~ DONE
- **File**: `api/app/tasks/node_lifecycle.py:1063`
- **Bug**: `_handle_migration()` cleanup passes `self.provider` (lab-wide provider) instead of the node's actual provider
- **Impact**: In mixed-mode labs, VM cleanup uses Docker provider (or vice versa), silently failing
- **Fix**: Resolve node's actual provider from image type before calling `destroy_node_on_agent()`
- **Also**: Line 1143 migration cleanup uses `container_action()` WITHOUT `lab_id` → falls back to Docker-only legacy endpoint
- **Resolution**: Per-node provider resolved via `resolve_node_image()` + `get_image_provider()` with fallback to `self.provider`. Line 1143 already passes `lab_id`. Log message updated from "container(s)" to "node(s)".

### 2. ~~Legacy Container-Only Agent Endpoints Still Called from API~~ DONE (already fixed)
- **File**: `agent/main.py:2227-2384`
- **Endpoints**: `POST /containers/{name}/start`, `POST /containers/{name}/stop`, `DELETE /containers/{name}`, `DELETE /containers/{lab_id}/{name}`
- **Bug**: Hardcoded to Docker SDK, no provider parameter
- **Impact**: API code path `container_action()` in `agent_client.py:1806-1885` without `lab_id` hits these Docker-only endpoints
- **Fix**: Ensure all API callers use provider-aware endpoints (`/labs/{lab_id}/nodes/{node_name}/start|stop|destroy`) instead. Audit all `container_action()` call sites in `agent_client.py` and `node_lifecycle.py`
- **Resolution**: Audit confirmed all 3 call sites (lines 1155, 1486, 1743) already pass `lab_id=self.lab.id`, using the provider-aware reconcile endpoint. Legacy Docker-only fallback is dead code for all current callers.

### 3. ~~`discover_labs()` Only Returns Docker~~ DONE
- **File**: `agent/main.py:2389-2418`
- **Bug**: `GET /discover-labs` defaults to Docker provider, misses all libvirt VMs
- **Fix**: Query both Docker and libvirt providers, merge results by lab_id
- **Resolution**: Both `discover_labs()` and `cleanup_orphans()` now iterate over all available providers via `list_providers()` and merge results. Each provider is queried independently with error isolation.

---

## P1 — Important for Production Mixed-Mode Labs

### 4. ResourcesPopup Only Shows Container Counts
- **File**: `web/src/studio/components/ResourcesPopup.tsx:68-126`
- **Gap**: "By Agent" section (line 80) shows `{agent.containers} containers` — no VM count. "By Lab" section (line 105-113) header says "(Container Distribution)" and only shows container counts
- **Fix**: Add VM counts to both sections. Update interfaces to include `vm_count`. Show combined or separate counts

### 5. `destroy_node()` Cleanup Asymmetry
- **File**: `agent/providers/libvirt.py` vs `agent/providers/docker.py`
- **Gap**: DockerProvider's `destroy_node()` checks if destroyed node was last in lab and cleans up networks/VLANs. LibvirtProvider does NOT — potentially leaving orphaned OVS resources
- **Fix**: Add lab-level resource cleanup to LibvirtProvider's `destroy_node()` when it's the last node

### 6. Post-Boot Commands Not Executed for VMs
- **File**: `agent/providers/libvirt.py` (missing), `agent/vendors.py:345-348`
- **Gap**: `post_boot_commands` defined in VendorConfig (e.g., cEOS iptables workaround, N9Kv recovery) but LibvirtProvider never executes them. Docker runs them via `docker exec`
- **Note**: For VMs, post-boot would need to execute via SSH or virsh console after readiness. This is a design decision — some VMs may not have SSH ready when post-boot should run
- **Fix**: Implement post-boot command execution in LibvirtProvider via SSH (after readiness probe passes)

### 7. No Libvirt Orphan Cleanup / Prune Endpoints
- **File**: `agent/main.py`
- **Gap**: `POST /prune-docker` (line 2652) and `POST /cleanup-orphans` (line 2421) are Docker-only
- **Fix**: Add libvirt equivalents or make existing endpoints provider-aware. `cleanup_orphan_domains()` exists in LibvirtProvider but isn't exposed via HTTP

---

## P2 — Completeness & Robustness

### 8. Config Injection Missing for 8+ VM Types
- **File**: `agent/vendors.py`
- **Gap**: Only CSR1000v and IOS-XRv support config injection (`iso` method). These have `config_inject_method="none"`:
  - IOSv, IOSvL2 (serial method exists but incomplete)
  - N9Kv (bootflash attempted but incomplete)
  - ASAv, vSRX3, vManage, Palo Alto
- **Impact**: Users must manually configure these VMs after boot
- **Fix**: Incremental — implement injection methods per device as feasible. Not all devices support config injection

### 9. Docker Image Validation Weaker Than QCOW2
- **File**: `api/app/routers/images.py:61-79` vs `api/app/utils/image_integrity.py:33-79`
- **Gap**: QCOW2 gets magic bytes + `qemu-img info` + SHA256 pre-deploy check. Docker TAR only checks for `manifest.json` in first 20 tar entries
- **Fix**: Add SHA256 computation on Docker image upload, store in manifest

### 10. VM Readiness Probes Missing for SSH-Based Devices
- **File**: `agent/vendors.py`
- **Gap**: vManage (line 1271), and other SSH-console devices have `readiness_probe="none"` — report "ready" immediately
- **Fix**: Implement `cli_probe` readiness (execute SSH command to verify boot complete)

### 11. No Hardware Resource Validation on Node Creation
- **File**: `api/app/services/device_constraints.py:38-54`
- **Gap**: `validate_minimum_hardware()` only checks cat9k during image upload. Not called during topology creation or deployment. VMs with strict requirements (XRv9k: 16GB, N9Kv: 8GB) aren't validated
- **Fix**: Call validation during topology update and pre-deployment

### 12. Config Extraction for VMs is SSH-Only
- **File**: `agent/providers/libvirt.py`
- **Gap**: DockerProvider extracts via `docker exec`, SSH, or NVRAM. LibvirtProvider only via SSH
- **Impact**: If SSH isn't ready, config extraction fails silently
- **Fix**: Add virsh console-based extraction as fallback (complex, lower priority)

### 13. No VM Boot Log Endpoint
- **File**: `agent/main.py`
- **Gap**: `GET /labs/{lab_id}/boot-logs` queries Docker logs only
- **Fix**: Add libvirt serial log retrieval (from virsh or log files)

### 14. Docker Image Deletion Incomplete
- **File**: `api/app/routers/images.py:2408-2445`
- **Gap**: `DELETE /library/{image_id}` removes QCOW2 files from disk but only removes Docker images from manifest (not from Docker daemon)
- **Fix**: Add optional `remove_from_docker=true` parameter

---

## P3 — Minor / Cosmetic

### 15. Log Messages Say "container(s)" During VM Operations
- **File**: `node_lifecycle.py:1052-1055`
- **Fix**: Change to "node(s)" or "resource(s)"

### 16. Properties Panel Doesn't Distinguish VM vs Container Semantics
- **File**: `web/src/studio/components/PropertiesPanel.tsx:464-546`
- **Gap**: Hardware tab shows CPU/RAM sliders without explaining Docker values are soft limits vs libvirt hard allocations
- **Fix**: Add help tooltip or label per provider type

### 17. `start_node` Passes Docker-Specific Params for All Providers
- **File**: `node_lifecycle.py:1889`
- **Gap**: `repair_endpoints=True, fix_interfaces=True` are Docker-specific but passed for VMs too
- **Fix**: Make params provider-aware or have agent ignore for libvirt

### 18. IOL Uploads Don't Auto-Trigger vrnetlab Build
- **File**: `api/app/routers/images.py:1898-1960`
- **Gap**: QCOW2 uploads auto-trigger build; IOL requires manual `/images/{id}/build-docker` call
- **Fix**: Add `auto_build` parameter to IOL upload

### 19. Lab Provider Auto-Detection Forces Mono-Provider Mode
- **File**: `api/app/utils/lab.py` — `update_lab_provider_from_nodes()`
- **Gap**: Sets entire lab to "libvirt" if ANY node uses qcow2. Per-node provider detection exists downstream but lab-level field creates confusion
- **Fix**: Document behavior or add per-node provider field to topology schema

---

## Areas With Full Parity (No Work Needed)

- State machine transitions (provider-agnostic)
- WebSocket broadcasts / real-time state updates
- Link reconciliation and VXLAN overlay networking
- Console WebSocket access (docker exec + virsh both work)
- Dashboard lab/node counts (shows containers + VMs)
- Device catalog sidebar (handles both image kinds)
- Per-node lifecycle endpoints (`/labs/{id}/nodes/{name}/create|start|stop`)
- Cross-host VXLAN links (tap devices + veth pairs)
- VLAN assignment via OVS
- Node state tracking and enforcement
- Broadcaster / state_ws.py
- Agent registration and capability reporting
