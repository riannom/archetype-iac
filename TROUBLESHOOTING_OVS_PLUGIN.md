# OVS Plugin Troubleshooting Session - 2026-02-01

## Problem Summary
cEOS containers couldn't get Ethernet1 to work, and the agent kept becoming unresponsive during deploy.

## Root Causes Identified and Fixed

### 1. Docker SDK Blocking Event Loop (FIXED - Committed)
**Problem:** Synchronous Docker SDK calls (`networks.get()`, `containers.get()`, etc.) were blocking the async event loop while Docker was processing plugin callbacks, causing a deadlock.

**Solution:** Wrapped all Docker SDK calls in `asyncio.to_thread()`.

**Files:** `agent/providers/docker.py`

### 2. Docker Processes network.connect() Asynchronously (FIXED - Committed)
**Problem:** `network.connect()` returns immediately, but Docker creates endpoints asynchronously. When `container.start()` was called, Docker was still processing network attachments, causing deadlock.

**Solution:** Added 0.5s delay after network attachments to let Docker finish processing.

**Files:** `agent/providers/docker.py`

### 3. Missing EndpointOperInfo Handler (FIXED - Committed)
**Problem:** Docker called `/NetworkDriver.EndpointOperInfo` but plugin returned 404.

**Solution:** Added `handle_endpoint_oper_info()` handler.

**Files:** `agent/network/docker_plugin.py`

### 4. Synchronous File I/O in Plugin (FIXED - Committed)
**Problem:** State save used synchronous file I/O, blocking event loop.

**Solution:** Wrapped file I/O in `asyncio.to_thread()`.

**Files:** `agent/network/docker_plugin.py`

### 5. Thread Pool Exhaustion (FIXED - Committed)
**Problem:** Each network attachment spawned a separate thread, exhausting the pool.

**Solution:** Batched all network attachments into a single thread per container.

**Files:** `agent/providers/docker.py`

### 6. Missing /lib/modules Mount (FIXED - Not Committed)
**Problem:** cEOS containers tried `modprobe` but couldn't access host kernel modules.

**Solution:** Added `/lib/modules:/lib/modules:ro` to cEOS binds in vendors.py.

**Files:** `agent/vendors.py`

### 7. Orphan Veth Cleanup Deleting Active Veths (FIXED - Not Committed)
**Problem:** The cleanup manager was deleting veth pairs that were actively in use by containers. When container-side veth is in the container's network namespace, `ip link show` on the host can't see it, so cleanup thought the peer didn't exist.

**Solution:** Added check for OVS plugin's tracked veths before deleting. Created `get_active_host_veths()` method in plugin.

**Files:**
- `agent/network/docker_plugin.py` - Added `get_active_host_veths()` method
- `agent/network/cleanup.py` - Check OVS plugin state before deleting

### 8. Additional Blocking Docker SDK Calls (FIXED - Not Committed)
**Problem:** Many Docker SDK calls in status, start_node, stop_node, destroy, etc. were not wrapped in `asyncio.to_thread()`, causing the agent to become unresponsive.

**Solution:** Wrapped all remaining Docker SDK calls:
- `container.reload()` and `container.logs()` in `_wait_for_readiness()`
- `container.remove()` in exception handlers
- `containers.list()`, `container.start()`, `container.stop()`, `container.get()` in various methods
- `volumes.list()`, `volume.remove()`, `volumes.prune()` in cleanup
- `container.exec_run()` in config extraction

**Files:** `agent/providers/docker.py`

### 9. cEOS Platform Detection Race Condition (FIXED - Not Committed)
**Problem:** EOS-2/EOS-3 fail to boot because `Ark.getPlatform()` returns `None` instead of `ceoslab` during boot. This causes the EosInitStage script to attempt `modprobe rbfd` which fails.

**Root Cause:** Race condition in systemd boot ordering:
1. "Check hypervisor...create platform detection file" starts but hasn't finished
2. VEosLabInit runs and sees `platform=None`
3. Platform detection finishes, but EosStage2 has already cached `platform=None`
4. EosStage2 runs EosInitStage which calls `modprobe rbfd` (not skipped because platform is None)

**Solution (from containerlab):** Use an `if-wait.sh` script that runs BEFORE `/sbin/init` to wait for network interfaces to be available. This ensures the network stack is fully initialized before platform detection runs.

**Implementation:**
1. Added `IF_WAIT_SCRIPT` constant with the wait script content
2. `_ensure_directories()` creates `/mnt/flash/if-wait.sh` for each cEOS node
3. `_count_node_interfaces()` helper counts interfaces per node from topology
4. `_create_container_config()` sets:
   - `CLAB_INTFS` environment variable with interface count
   - Entrypoint: `["/bin/bash", "-c"]`
   - Command: `["/mnt/flash/if-wait.sh ; exec /sbin/init"]`

**Verification:**
```
# Boot logs now show:
if-wait: Waiting for 1 interfaces (timeout: 300s)
if-wait: Found 5 interfaces (required: 1)
if-wait: Starting init
...
VEosLabInit[360]: Skipping VEosLabInit due to platform=ceoslab  <-- Correct!
```

**Files:** `agent/providers/docker.py`

## Commits Made
```
33b84ed fix(ovs): Prevent deadlock during container network attachment
```
Contains fixes 1-5 above.

## Uncommitted Changes
- `agent/vendors.py` - Added /lib/modules mount for cEOS + alias lookup fixes
- `agent/providers/docker.py` - Additional asyncio.to_thread() wrapping + if-wait.sh for cEOS platform detection fix
- `agent/network/docker_plugin.py` - Added get_active_host_veths() method
- `agent/network/cleanup.py` - Check OVS plugin state before deleting veths
- `CLAUDE.md` - Minor updates

## Current State
- EOS-1: Running, CLI works
- EOS-2: Running, CLI works (platform detection fixed with if-wait.sh)
- Agent: Deploy completes without blocking (asyncio.to_thread wrapping working)

## Key Findings

### Agent Crash During Deploy
The agent would crash/become unresponsive when starting EOS-2 after EOS-1. The `await asyncio.to_thread(container.start)` call was blocking. This happens because Docker SDK's container.start() triggers plugin callbacks. Even with the Docker SDK call in a thread, something was causing a deadlock.

Manual `docker start` from CLI works fine. The issue is specific to the Docker SDK in the agent process.

### cEOS Boot Order Issue
The platform detection race is an upstream cEOS issue. The boot log shows:
```
Starting [Check hypervisor...create platform detection file]...
[VEosLabInit] Skipping VEosLabInit due to platform=None  <-- Runs before detection finishes!
[OK] Finished [Check hypervisor...create platform detection file]
[FAILED] Failed to start [Perform cEOS specific initialization...]
[FAILED] Failed to start [Insert dmamem module if needed]
Starting [Eos system stage2...]
[EosInitStage] platform = None  <-- Still None!
[EosInitStage] modprobe rbfd... FATAL: Module not found
[FAILED] Failed to start [Eos system stage2]
```

## Key Code Locations

### Network Attachment Flow
```
agent/providers/docker.py:
  - _create_lab_networks() - Creates Docker networks
  - _attach_container_to_networks() - Batched attachment function
  - _create_containers() - Creates containers with first network, then attaches rest
  - _start_containers() - Starts containers with 5s cEOS stagger
```

### OVS Plugin
```
agent/network/docker_plugin.py:
  - handle_create_endpoint() - Creates veth pair, attaches to OVS
  - handle_join() - Provides interface config to Docker
  - handle_endpoint_oper_info() - Returns endpoint operational info
  - _save_state() - Async state persistence
  - get_active_host_veths() - Returns set of tracked veths (for cleanup)
```

### Cleanup
```
agent/network/cleanup.py:
  - cleanup_orphaned_veths() - Now checks OVS plugin state before deleting
  - _get_ovs_plugin_active_veths() - Helper to get active veths from plugin
```

### Vendor Config
```
agent/vendors.py:
  - ceos/eos config around line 380
  - binds list includes /lib/modules mount
```

## Next Steps
1. Commit all fixes once fully tested
2. Test with 3+ cEOS nodes to verify fix is robust
3. Investigate link hot_connect - OVS ports not being found for VLAN bridging

## Testing Commands
```bash
# Check container status
docker ps -a --filter "label=archetype.lab_id"

# Test CLI
docker exec <container> Cli -c "show version"

# Check platform detection
docker exec <container> python3 -c "import Ark; print(Ark.getPlatform())"

# Check boot logs for platform issues
docker logs <container> 2>&1 | grep -E "platform|ceoslab|modprobe"

# Check agent health
curl -s http://localhost:8001/health

# Restart agent if stuck
docker restart archetype-iac-agent-1
```
