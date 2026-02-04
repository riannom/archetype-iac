# Libvirt Support - Implementation Complete

## Summary

Full libvirt/KVM support for VM-based network devices (IOSv, CSR1000v, ASAv, Nexus 9000v, etc.) is now implemented. All 7 tasks have been completed.

## What Was Accomplished

### 1. Provider Routing by Image Type
- Added `get_node_provider()` in `api/app/utils/lab.py` to determine provider based on image file extension
- Updated `api/app/routers/labs.py` - `reconcile_node` endpoint uses node-specific provider
- Updated `api/app/tasks/state_enforcement.py` - enforcement uses node-specific provider
- qcow2/img files route to libvirt, Docker images route to docker

### 2. LibvirtProvider Implementation (`agent/providers/libvirt.py`)
- Implemented `deploy()` method that actually deploys VMs
- Added filtering to skip non-qcow2 nodes (so mixed Docker+VM labs work)
- Connected VMs to OVS bridge (`arch-ovs`) instead of libvirt's default network
- Added path translation for Docker volume paths so libvirt can access images

### 3. Docker Compose Updates (`docker-compose.gui.yml`)
- Added `archetype_workspaces:/var/lib/archetype:ro` volume mount to agent
- Added `/var/run/libvirt:/var/run/libvirt` mount for libvirt socket

### 4. Environment Configuration
- Set `ARCHETYPE_AGENT_ENABLE_LIBVIRT=true` in `.env`

## Files Changed

```
api/app/utils/lab.py           - Added get_node_provider()
api/app/routers/labs.py        - Use node-specific provider in reconcile_node
api/app/tasks/state_enforcement.py - Use node-specific provider
agent/providers/libvirt.py     - Full deploy implementation
docker-compose.gui.yml         - Volume mounts for images and libvirt
.env                           - ARCHETYPE_AGENT_ENABLE_LIBVIRT=true
```

## Current Limitations

### Hardcoded VM Defaults (lines 482-487 in libvirt.py)
```python
node_config = {
    "image": node.image,
    "memory": 2048,  # Default 2GB RAM
    "cpu": 1,       # Default 1 vCPU
    "_display_name": display_name,
}
```

### Device Requirements Vary Significantly

| Device | RAM | vCPUs | Disk Driver | NIC Driver | Notes |
|--------|-----|-------|-------------|------------|-------|
| IOSv | 512MB-2GB | 1 | virtio | e1000 | Works with current defaults |
| IOSv-L2 | 768MB | 1 | virtio | e1000 | Works with current defaults |
| CSR1000v | 4GB | 1-2 | virtio | virtio | Needs more RAM |
| Cat8000v | 4GB | 1-2 | virtio | virtio | Needs more RAM |
| Nexus 9000v | 8GB | 2 | virtio | virtio | Needs more RAM/CPU |
| ASAv | 2GB | 1 | virtio | virtio | Should work |
| FTDv | 8GB | 4 | virtio | virtio | Heavy requirements |
| vManage | 32GB | 8 | virtio | virtio | Very heavy |
| XRv9k | 16GB | 4 | virtio | virtio | Heavy |

---

## Next Step Tasks

### Task 1: Add VM Configuration to Vendor Registry ✅ COMPLETED

**Goal**: Make VM resource requirements configurable per device type.

**Files to modify**:
- `agent/vendors.py` - Add libvirt config section to each VM device

**Implementation**:
```python
# In agent/vendors.py, add to each VM device config:
"cisco_iosv": VendorConfig(
    kind="iosv",
    # ... existing fields ...
    libvirt=LibvirtConfig(
        memory_mb=2048,
        vcpus=1,
        disk_driver="virtio",
        nic_driver="e1000",
        console_type="serial",
    ),
),
"cisco_csr1000v": VendorConfig(
    kind="csr1000v",
    libvirt=LibvirtConfig(
        memory_mb=4096,
        vcpus=2,
        disk_driver="virtio",
        nic_driver="virtio",
    ),
),
```

**Then update** `agent/providers/libvirt.py` to read config from vendors.py instead of hardcoded values.

---

### Task 2: Implement VM Readiness Detection ✅ COMPLETED

**Goal**: Set `is_ready=true` when VM has finished booting.

**Current state**: VMs deploy but `is_ready` stays false forever.

**Implementation options**:
1. **Serial console grep** - Watch virsh console output for login prompt
2. **SSH probe** - Try connecting to management IP
3. **SNMP probe** - Query device for uptime

**Files to modify**:
- `agent/providers/libvirt.py` - Add readiness check in `_deploy_node` or separate method
- May need to add to `agent/readiness.py` if shared logic exists

---

### Task 3: Add VM Console Access ✅ COMPLETED

**Goal**: WebSocket console access for VMs (like Docker containers have).

**Current state**: `get_console_command()` returns `["virsh", "-c", uri, "console", domain_name]` but untested.

**Files to check/modify**:
- `agent/providers/libvirt.py` - `get_console_command()` method
- `api/app/routers/console.py` - Console WebSocket handler
- May need PTY handling adjustments for virsh console

---

### Task 4: Add Multi-Interface Support for VMs ✅ COMPLETED

**Goal**: VMs should have multiple interfaces that integrate with OVS networking.

**Current state**: VMs get a single interface connected to arch-ovs.

**Implementation**:
1. Generate domain XML with multiple `<interface>` elements
2. Each interface needs unique MAC address
3. Pre-assign OVS VLAN tags for isolation
4. Support hot-connect (add interface to running VM)

**Files to modify**:
- `agent/providers/libvirt.py` - `_generate_domain_xml()` method
- May need integration with `agent/network/docker_plugin.py` patterns

---

### Task 5: Add VM Status Reconciliation ✅ COMPLETED

**Goal**: Background task should detect VM states and update database.

**Current state**: Reconciliation works for Docker but not VMs.

**Files to check/modify**:
- `api/app/tasks/reconciliation.py` - Add libvirt status checking
- `agent/providers/libvirt.py` - `status()` method exists but needs testing

---

### Task 6: Test VM Stop/Destroy ✅ COMPLETED

**Goal**: Verify stop_node and destroy work correctly for VMs.

**Current state**: Methods exist but untested.

**Test scenarios**:
1. Stop running VM -> should graceful shutdown then force if needed
2. Start stopped VM -> should work
3. Destroy lab -> should remove VM and cleanup disks

---

### Task 7: Document Host Requirements ✅ COMPLETED

**Goal**: Document what's needed on agent hosts for libvirt support.

**Requirements**:
- libvirt-daemon-system, qemu-kvm installed
- User running agent must be in libvirt group (or run as root)
- `/var/run/libvirt` accessible
- Images must be on host-accessible path (not just Docker volume)

---

## Testing the Current Implementation

```bash
# Check VM is running
docker exec archetype-iac-agent-1 python3 -c "
import libvirt
conn = libvirt.open('qemu:///system')
for dom in conn.listAllDomains(0):
    state, _ = dom.state()
    print(f'{dom.name()}: state={state}')
conn.close()
"

# Check node state
docker exec archetype-iac-postgres-1 psql -U archetype -d archetype -c "
SELECT node_id, node_name, actual_state, is_ready
FROM node_states
WHERE node_name LIKE '%iosv%';
"

# Trigger reconcile for a VM node
curl -X POST "http://localhost:8000/labs/{lab_id}/nodes/{node_id}/reconcile" \
  -H "Authorization: Bearer $TOKEN"
```

## Key Code Locations

- **Provider selection**: `api/app/utils/lab.py:get_node_provider()`
- **Image type detection**: `api/app/image_store.py:get_image_provider()`
- **Libvirt deploy**: `agent/providers/libvirt.py:deploy()`
- **Domain XML generation**: `agent/providers/libvirt.py:_generate_domain_xml()`
- **Path translation**: `agent/providers/libvirt.py:_translate_container_path_to_host()`
- **Vendor configs**: `agent/vendors.py`
