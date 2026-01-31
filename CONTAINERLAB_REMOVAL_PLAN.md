# Containerlab Removal Plan

## Overview

Migrate fully from containerlab CLI to native DockerProvider. This document outlines the steps to remove containerlab references and simplify the architecture.

## Current State

- **DockerProvider** (`agent/providers/docker.py`) - NEW, manages containers directly via Docker SDK
- **LocalNetworkManager** (`agent/network/local.py`) - NEW, handles veth pairs for local links
- **ContainerlabProvider** (`agent/providers/containerlab.py`) - TO BE REMOVED
- **OverlayManager** (`agent/network/overlay.py`) - KEEP, handles cross-host VXLAN
- **LibvirtProvider** (`agent/providers/libvirt.py`) - KEEP, handles qcow2 VMs

## Migration Steps

### Phase 1: Make DockerProvider the Default

**Files to modify:**

1. `agent/config.py`
   - Change `enable_docker: bool = False` → `enable_docker: bool = True`
   - Change `enable_containerlab: bool = True` → `enable_containerlab: bool = False`

2. `agent/schemas.py`
   - Change `provider: Provider = Provider.CONTAINERLAB` → `provider: Provider = Provider.DOCKER` in DeployRequest
   - Change default in DestroyRequest similarly

3. `api/app/models.py`
   - Change `provider: Mapped[str] = mapped_column(String(50), default="containerlab")` → `default="docker"`

### Phase 2: Update Agent Hardcoded References

**File: `agent/main.py`** - Many places hardcode "containerlab":

1. Line ~907: `get_provider_for_request("containerlab")` in destroy endpoint
2. Line ~943: `get_provider_for_request("containerlab")` in async destroy
3. Line ~986: `get_provider_for_request("containerlab")` in node action
4. Line ~1033: `get_provider_for_request("containerlab")` in status
5. Line ~1070: `get_provider_for_request("containerlab")` in extract configs
6. Line ~1168: `get_provider_for_request("containerlab")` in discover labs
7. Line ~1204: `get_provider_for_request("containerlab")` in cleanup orphans
8. Line ~1498: `get_provider("containerlab")` in readiness check
9. Line ~2127: `get_provider("containerlab")` in console WebSocket

**Solution:**
- For endpoints that receive a request with provider field, use that
- For endpoints without provider field, add it or use a default
- Console WebSocket should get provider from container labels

### Phase 3: Update Console Handling

**Files:**
- `agent/main.py` - Console WebSocket handler
- `api/app/routers/console.py` - Console routing

**Changes:**
- DockerProvider containers have label `archetype.node_kind` instead of `clab-node-kind`
- Console handler should read provider from container labels to determine shell
- Update `get_console_command()` to work with DockerProvider naming

### Phase 4: Update API/Controller

**File: `api/app/tasks/jobs.py`**
- Update `run_deploy_job()` to use docker provider by default
- Update `run_multihost_deploy()` to use docker provider by default
- Remove containerlab-specific error parsing
- Update `_get_container_name()` - can simplify once containerlab is gone

**File: `api/app/agent_client.py`**
- Review for containerlab-specific logic
- Update any container name references

**File: `api/app/topology.py`**
- Keep `graph_to_containerlab_yaml()` for export compatibility (users may want to export and use with vanilla containerlab)
- Remove any containerlab-specific parsing that's not needed

### Phase 5: Remove ContainerlabProvider

**Files to delete:**
- `agent/providers/containerlab.py`

**Files to update:**
- `agent/providers/__init__.py` - Remove ContainerlabProvider import/export
- `agent/providers/registry.py` - Remove containerlab registration block
- `agent/config.py` - Remove `enable_containerlab` setting

### Phase 6: Simplify Vendor Configuration

**File: `agent/vendors.py`**

The `kind` field was containerlab's node type identifier. Options:
- Keep it for compatibility with containerlab YAML export
- OR rename to something more generic like `device_id`

Decision: Keep `kind` for now - it's used in YAML export and doesn't hurt.

Remove containerlab-specific comments and update docstrings.

### Phase 7: Update Labels and Naming

**Current DockerProvider labels:**
```python
LABEL_LAB_ID = "archetype.lab_id"
LABEL_NODE_NAME = "archetype.node_name"
LABEL_NODE_KIND = "archetype.node_kind"
LABEL_PROVIDER = "archetype.provider"
```

**ContainerlabProvider labels (to remove support for):**
```
clab-node-kind
clab-node-name
clab-topo-file
```

Update any code that reads `clab-*` labels to use `archetype.*` labels.

### Phase 8: Update Documentation

**Files:**
- `CLAUDE.md` - Update architecture description
- `README.md` (if exists) - Update setup instructions
- Remove references to `clab` binary requirement

### Phase 9: Clean Up Schemas ✅ COMPLETED

**File: `agent/schemas.py`**
- ✅ `Provider.CONTAINERLAB` marked as deprecated with comment (kept for API compatibility)

**File: `api/app/schemas.py`**
- ✅ Default provider changed to "docker"
- ✅ Comments referencing containerlab updated

**File: `api/app/config.py`**
- ✅ Default provider changed from "clab" to "docker"

**File: `api/app/providers.py`**
- ✅ Removed containerlab CLI commands, updated for docker provider

**Tests:**
- ✅ Updated all test files to use provider="docker" instead of provider="containerlab"
- ✅ Updated container naming in tests from clab- to archetype-
- ✅ Updated provider capabilities in test fixtures

### Phase 10: Testing Checklist

1. [ ] Deploy single-node lab with DockerProvider
2. [ ] Deploy multi-node lab with local links
3. [ ] Deploy multi-host lab with VXLAN overlay
4. [ ] Console access to cEOS node
5. [ ] Console access to Linux node
6. [ ] Console access to SR Linux node
7. [ ] Node start/stop operations
8. [ ] Lab destroy and cleanup
9. [ ] Readiness detection for cEOS (slow boot)
10. [ ] Mixed container + VM lab (DockerProvider + LibvirtProvider)
11. [ ] Export topology to containerlab YAML format

---

## File-by-File Checklist

### Agent Files

| File | Action | Notes |
|------|--------|-------|
| `agent/providers/containerlab.py` | DELETE | Remove entirely |
| `agent/providers/docker.py` | KEEP | Primary container provider |
| `agent/providers/libvirt.py` | KEEP | VM provider, unchanged |
| `agent/providers/base.py` | KEEP | Interface, unchanged |
| `agent/providers/registry.py` | MODIFY | Remove containerlab registration |
| `agent/providers/__init__.py` | MODIFY | Remove ContainerlabProvider |
| `agent/config.py` | MODIFY | Remove enable_containerlab, default enable_docker=True |
| `agent/schemas.py` | MODIFY | Update default provider |
| `agent/main.py` | MODIFY | Replace hardcoded "containerlab" references |
| `agent/vendors.py` | MODIFY | Update comments, keep structure |
| `agent/network/local.py` | KEEP | New, for local links |
| `agent/network/overlay.py` | KEEP | For cross-host VXLAN |
| `agent/network/vlan.py` | KEEP | For external networks |

### API Files

| File | Action | Notes |
|------|--------|-------|
| `api/app/models.py` | MODIFY | Change default provider |
| `api/app/tasks/jobs.py` | MODIFY | Update default provider, simplify container naming |
| `api/app/agent_client.py` | REVIEW | Check for containerlab-specific logic |
| `api/app/topology.py` | KEEP | Keep containerlab YAML export for compatibility |
| `api/app/routers/console.py` | REVIEW | Ensure works with new labels |
| `api/app/main.py` | REVIEW | Check for containerlab references |

---

## Execution Order

1. Phase 1: Make DockerProvider default (config changes)
2. Phase 2: Update agent main.py hardcoded references
3. Phase 3: Update console handling for new labels
4. Phase 4: Update API/controller code
5. Phase 5: Delete ContainerlabProvider
6. Phase 6-7: Clean up vendors and labels
7. Phase 8: Update documentation
8. Phase 9: Clean up schemas
9. Phase 10: Test everything

---

## Rollback Plan

If issues are found:
1. Restore `agent/providers/containerlab.py` from git
2. Set `enable_containerlab=True`, `enable_docker=False`
3. Revert schema default to `Provider.CONTAINERLAB`

Keep containerlab YAML export in topology.py so users can export and run with vanilla containerlab if needed.

---

## Notes

- The overlay networking (VXLAN) is provider-agnostic and continues to work
- LibvirtProvider is completely unaffected
- Container naming changes from `clab-{lab}-{node}` to `archetype-{lab}-{node}`
- Console shell detection uses vendor config, not container labels
