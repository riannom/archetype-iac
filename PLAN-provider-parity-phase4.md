# Provider Feature Parity Plan - Phase 4

## Overview

This plan addresses the remaining gaps between DockerProvider and LibvirtProvider to achieve full feature parity. The analysis identified four key gaps that need to be closed.

## Gap Analysis Summary

| Gap | Impact | Priority |
|-----|--------|----------|
| Docker lacks VLAN persistence | Agent restart breaks network links | **High** |
| Docker lacks stale network recovery | Redeploy reallocates VLANs, breaking existing containers | **High** |
| Config extraction methods differ | Mixed labs can't extract all configs | Medium |
| SSH console access incomplete | Some VM devices have no console | Low |

---

## Phase 4.1: Docker VLAN Persistence

**Goal:** Persist Docker VLAN allocations to disk, matching Libvirt's approach.

### Current State (Docker)
- VLANs allocated dynamically in `_vlan_allocations` dict (memory-only)
- Lost on agent restart
- Location: `agent/providers/docker.py`

### Target State
- VLANs persisted to `{workspace}/vlans/{lab_id}.json`
- Format matches Libvirt: `{"allocations": {...}, "next_vlan": N}`
- Loaded on demand when needed

### Implementation Tasks

#### Task 4.1.1: Add VLAN persistence methods to DockerProvider
- [x] Add `_save_vlan_allocations(lab_id: str, workspace: str)` method
- [x] Add `_load_vlan_allocations(lab_id: str, workspace: str) -> bool` method
- [x] Add `_remove_vlan_file(lab_id: str, workspace: str)` method
- [x] Mirror the implementation from LibvirtProvider

#### Task 4.1.2: Update VLAN allocation to persist
- [x] Add `_capture_container_vlans()` to capture VLANs from OVS after links created
- [x] Add `_get_interface_vlan()` helper to query OVS for interface VLAN tags
- [x] Call `_capture_container_vlans()` after `_create_links()` in deploy flow

#### Task 4.1.3: Update destroy to clean up VLAN files
- [x] Modify `destroy()` to call `_remove_vlan_file()`
- [x] Update `cleanup_orphan_containers()` to remove VLAN files for orphaned labs

### Files to Modify
- `agent/providers/docker.py`

---

## Phase 4.2: Docker Stale Network Recovery

**Goal:** Recover VLAN allocations for running containers on redeploy, preventing VLAN conflicts.

### Current State (Docker)
- Redeploy always reallocates VLANs
- Running containers from previous deploy get new VLANs
- Network connectivity breaks

### Target State
- On deploy, check for existing VLAN allocations on disk
- Validate containers still exist via Docker API
- Reuse VLANs for existing containers, allocate new for new containers
- Clean up allocations for containers that no longer exist

### Implementation Tasks

#### Task 4.2.1: Add stale network recovery method
- [x] Add `_recover_stale_network(lab_id: str, workspace: str) -> dict`
- [x] Load persisted VLAN allocations
- [x] For each node in allocations, check if container exists
- [x] Return dict of recovered allocations (node_name -> vlan_list)
- [x] Re-persist cleaned allocations (remove non-existent containers)

#### Task 4.2.2: Integrate recovery into deploy flow
- [x] Modify `deploy()` to call `_recover_stale_network()` before creating nodes
- [x] Log recovered allocations count
- [x] VLANs captured after links are created preserve existing state

#### Task 4.2.3: Handle container naming for validation
- [x] Use consistent container naming: `archetype-{lab_id}-{node_name}` (via `_container_name()`)
- [x] Validate container exists via Docker API labels (LABEL_LAB_ID, LABEL_NODE_NAME)
- [x] Handle truncated lab IDs (20-char limit matching Libvirt)

### Files to Modify
- `agent/providers/docker.py`

---

## Phase 4.3: SSH Console Access (Optional)

**Goal:** Add SSH-based console access for devices that require it.

### Current State
- Docker: `docker exec` console only
- Libvirt: `virsh console` only
- Some devices (N9Kv, Catalyst 9800) configured with `console_method="ssh"` but not implemented

### Target State
- Both providers support SSH console as an option
- SSH credentials from vendor config or node metadata
- Fallback to existing methods if SSH unavailable

### Implementation Tasks

#### Task 4.3.1: Research SSH console requirements
- [x] Identify which devices need SSH console (N9Kv, Cat8000v, vManage, vEdge, vBond, vSmart, FTDv, FMC, Cat9800v)
- [x] Determine credential sources (vendor config: console_user, console_password)
- [x] Design SSH console command format (sshpass + ssh with StrictHostKeyChecking=no)

#### Task 4.3.2: Add SSH console support to providers
- [x] Add SSH console command generation to `get_console_command()` in DockerProvider
- [x] Add SSH console command generation to `get_console_command()` in LibvirtProvider
- [x] Handle `console_method="ssh"` in both providers
- [x] Return appropriate SSH command with credentials via sshpass
- [x] Added `_get_vm_management_ip()` to LibvirtProvider for IP discovery

#### Task 4.3.3: Test SSH console with target devices
- [ ] Test with Nexus 9000v
- [ ] Test with other SSH-requiring devices

### Files Modified
- `agent/providers/docker.py` - Added SSH console support
- `agent/providers/libvirt.py` - Added SSH console support and VM IP discovery

### Notes
- Uses `sshpass` for non-interactive password authentication
- Falls back to virsh console if SSH IP not found (LibvirtProvider)

---

## Phase 4.4: Config Extraction Improvements (Optional)

**Goal:** Enable unified config extraction across provider types.

### Current State
- Docker: `config_extract_method="docker"` uses `docker exec`
- Libvirt: `config_extract_method="serial"` uses virsh console + pexpect
- No cross-compatibility for mixed labs

### Potential Solutions

#### Option A: SSH-based extraction (Recommended)
- Add `config_extract_method="ssh"` to both providers
- Use SSH to run extraction commands
- Works for any device with SSH access
- Requires SSH credentials in vendor/node config

#### Option B: Unified console abstraction
- Create abstract console interface
- Implement for docker exec, virsh console, SSH
- Config extraction uses abstraction

### Implementation Tasks (Option A: SSH-based extraction)

#### Task 4.4.1: Add SSH config extraction to DockerProvider
- [x] Detect `config_extract_method="ssh"` in `_extract_all_container_configs()`
- [x] Added `_extract_config_via_docker()` helper for docker exec extraction
- [x] Added `_extract_config_via_ssh()` helper for SSH extraction
- [x] Connect via SSH to container's management IP
- [x] Run extraction commands via SSH session
- [x] Parse and save output

#### Task 4.4.2: Add SSH config extraction to LibvirtProvider
- [x] Detect `config_extract_method="ssh"` in `_extract_config()`
- [x] Added `_extract_config_via_ssh()` helper for SSH extraction
- [x] Use VM's management IP for SSH (via `_get_vm_management_ip()`)
- [x] Falls back gracefully if SSH unavailable

### Files Modified
- `agent/providers/docker.py` - Added SSH config extraction
- `agent/providers/libvirt.py` - Added SSH config extraction

### Notes
- Uses same SSH credentials as console access (config_extract_user/password)
- Both providers now support: docker/serial (native) + ssh (cross-provider)

---

## Implementation Order

### Completed (High Priority)
1. **Phase 4.1**: Docker VLAN Persistence ✅
2. **Phase 4.2**: Docker Stale Network Recovery ✅

### Completed (Lower Priority)
3. **Phase 4.3**: SSH Console Access ✅
4. **Phase 4.4**: Config Extraction Improvements ✅

---

## Testing Strategy

### Current Test Architecture

The existing tests are **provider-specific**:
- Tests in `agent/tests/` mock Docker directly (`docker.from_env`, `mock_container`)
- No parametrized testing across providers
- No shared test fixtures for provider-agnostic behavior

### Recommended: Hybrid Testing Approach

Use a **three-layer** testing strategy that combines provider-specific unit tests with cross-provider contract tests:

```
┌─────────────────────────────────────────────────────┐
│  Layer 3: Contract Tests (provider-agnostic)       │
│  - Verify Provider interface contract              │
│  - Parametrized: runs against both providers       │
│  - Ensures feature parity is maintained            │
└─────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────┐
│  Layer 2: Provider-Specific Unit Tests             │
│  - Test internal methods (_save_vlan_allocations)  │
│  - Mock Docker SDK / libvirt API directly          │
│  - Cover edge cases specific to each provider      │
└─────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────┐
│  Layer 1: Shared Utilities                         │
│  - Test VLAN file format parsing                   │
│  - Test common helper functions                    │
│  - No provider mocks needed                        │
└─────────────────────────────────────────────────────┘
```

### Layer 1: Shared Utility Tests

Test the VLAN file format and common logic independent of providers:

```python
# agent/tests/test_vlan_utils.py
class TestVlanFileFormat:
    """Test VLAN JSON file format - no provider involved."""

    def test_vlan_file_roundtrip(self, tmp_path):
        """VLAN data survives save/load cycle."""
        data = {"allocations": {"r1": [100, 101]}, "next_vlan": 102}
        file_path = tmp_path / "vlans" / "test-lab.json"
        file_path.parent.mkdir(parents=True)
        file_path.write_text(json.dumps(data))

        loaded = json.loads(file_path.read_text())
        assert loaded == data

    def test_vlan_file_schema(self, tmp_path):
        """VLAN file has required keys."""
        data = {"allocations": {}, "next_vlan": 100}
        # Verify schema expectations
```

### Layer 2: Provider-Specific Unit Tests

Test internal methods with provider-specific mocks:

```python
# agent/tests/test_docker_vlan_persistence.py
class TestDockerVlanPersistence:
    """Docker-specific VLAN persistence tests."""

    def test_save_creates_directory(self, tmp_path):
        """Docker provider creates vlans/ directory if missing."""
        provider = DockerProvider()
        # Test _save_vlan_allocations() directly

    def test_load_returns_false_when_missing(self, tmp_path):
        """Load returns False when no VLAN file exists."""
        provider = DockerProvider()
        result = provider._load_vlan_allocations("nonexistent", tmp_path)
        assert result is False

    def test_recovery_checks_container_exists(self, mock_docker):
        """Recovery validates containers via Docker API."""
        # Mock docker.containers.get() to return/raise
```

```python
# agent/tests/test_libvirt_vlan_persistence.py
class TestLibvirtVlanPersistence:
    """Libvirt-specific VLAN persistence tests."""

    def test_recovery_checks_domain_exists(self, mock_libvirt):
        """Recovery validates domains via libvirt API."""
        # Mock conn.lookupByName() to return/raise

    def test_truncated_lab_id_handling(self, tmp_path):
        """Libvirt handles 20-char truncated lab IDs."""
        # Test domain name parsing with truncation
```

### Layer 3: Contract Tests (Parity Verification)

Parametrized tests that verify both providers behave identically:

```python
# agent/tests/test_provider_vlan_contract.py
import pytest

@pytest.fixture(params=["docker", "libvirt"])
def provider_type(request):
    return request.param

@pytest.fixture
def provider(provider_type, mock_docker, mock_libvirt):
    """Create provider with appropriate mocks."""
    if provider_type == "docker":
        return DockerProvider()
    else:
        return LibvirtProvider(libvirt_uri="test:///default")

class TestProviderVlanContract:
    """Contract tests - both providers must pass these."""

    def test_deploy_persists_vlans(self, provider, provider_type, tmp_path):
        """Any provider must persist VLANs on deploy."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Deploy (with mocked infrastructure)
        # ...

        # Verify VLAN file exists
        vlan_file = workspace / "vlans" / "test-lab.json"
        assert vlan_file.exists()

        data = json.loads(vlan_file.read_text())
        assert "allocations" in data
        assert "next_vlan" in data

    def test_destroy_removes_vlan_file(self, provider, provider_type, tmp_path):
        """Any provider must clean up VLAN file on destroy."""
        workspace = tmp_path / "workspace"
        vlan_file = workspace / "vlans" / "test-lab.json"
        vlan_file.parent.mkdir(parents=True)
        vlan_file.write_text('{"allocations": {}, "next_vlan": 100}')

        # Destroy (with mocked infrastructure)
        # ...

        assert not vlan_file.exists()

    def test_recovery_reuses_vlans_for_existing_resources(
        self, provider, provider_type, tmp_path, mock_resource_exists
    ):
        """VLANs are recovered when resources still exist."""
        # Setup persisted VLANs
        # Mock resource existence check to return True
        # Deploy again
        # Verify same VLANs used

    def test_recovery_cleans_stale_allocations(
        self, provider, provider_type, tmp_path, mock_resource_missing
    ):
        """VLANs for deleted resources are removed during recovery."""
        # Setup persisted VLANs for 3 nodes
        # Mock 1 resource as missing
        # Deploy again
        # Verify 2 nodes keep VLANs, 1 removed from file
```

### Test File Structure

```
agent/tests/
├── conftest.py                      # Shared fixtures
├── test_vlan_utils.py               # Layer 1: Shared utilities
├── test_docker_vlan_persistence.py  # Layer 2: Docker-specific
├── test_libvirt_vlan_persistence.py # Layer 2: Libvirt-specific
└── test_provider_vlan_contract.py   # Layer 3: Parity contracts
```

### Benefits of Hybrid Approach

| Aspect | Provider-Specific (Layer 2) | Contract Tests (Layer 3) |
|--------|----------------------------|--------------------------|
| Mock complexity | Simple - knows exact SDK | Abstract - needs adapter |
| Edge cases | Full coverage | Core behaviors only |
| Debug failures | Easy - single provider | Harder - which provider? |
| Parity guarantee | None | Explicit verification |
| Maintenance | Independent | Coupled to both |

### Implementation Order

1. **First:** Add Layer 2 provider-specific tests for Docker (matches existing pattern)
2. **Second:** Implement Docker VLAN persistence (Phase 4.1)
3. **Third:** Add Layer 3 contract tests that pass for both providers
4. **Fourth:** Run contract tests against Libvirt to verify existing implementation

### Phase 4.1 & 4.2 Specific Test Cases

#### Layer 2: Docker-Specific

1. `test_save_vlan_allocations_creates_file`
2. `test_save_vlan_allocations_creates_directory`
3. `test_load_vlan_allocations_returns_data`
4. `test_load_vlan_allocations_missing_file_returns_false`
5. `test_remove_vlan_file_deletes_file`
6. `test_remove_vlan_file_missing_file_no_error`
7. `test_recover_stale_network_validates_containers`
8. `test_recover_stale_network_removes_missing_containers`

#### Layer 3: Contract Tests

1. `test_deploy_creates_vlan_file` - Both providers
2. `test_vlan_file_format_consistent` - Both providers use same schema
3. `test_recovery_preserves_existing_vlans` - Both providers
4. `test_recovery_cleans_stale_entries` - Both providers
5. `test_destroy_removes_vlan_file` - Both providers
6. `test_orphan_cleanup_removes_vlan_files` - Both providers

---

## Estimated Effort

| Phase | Tasks | Complexity | Estimate |
|-------|-------|------------|----------|
| 4.1 | 3 | Low | Small |
| 4.2 | 3 | Medium | Medium |
| 4.3 | 3 | Medium | Medium |
| 4.4 | 2 | High | Large |

**Recommended scope for immediate work:** Phases 4.1 and 4.2 only.

---

## References

- LibvirtProvider VLAN persistence: `agent/providers/libvirt.py` lines ~450-520
- LibvirtProvider stale recovery: `agent/providers/libvirt.py` `_recover_stale_network()`
- Previous plan: `PLAN-provider-feature-parity.md` (Phase 3 implementation)
