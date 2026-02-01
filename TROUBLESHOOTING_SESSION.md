# Troubleshooting Session: cEOS Ethernet1 Interface Issue

## Original Problem
User reported that EOS-1 in lab project_1 couldn't get Ethernet1 to work:
```
EOS-1(config)#interface ethernet 1
! Interface does not exist. The configuration will not take effect until the module is inserted.
```

## Root Cause Analysis

### Issue 1: Container created with `network_mode: "none"`
**Location:** `agent/providers/docker.py:329`

The Docker provider was creating containers with `network_mode: "none"`, which completely isolates containers from Docker networking. When the code later tried to attach the container to OVS plugin networks, Docker rejected it because containers in "none" mode cannot join additional networks.

**Fix Applied:**
- Removed hardcoded `network_mode: "none"` from `_create_container_config()`
- Modified `_create_containers()` to:
  - When OVS plugin enabled: Create container attached to first interface network (`{lab_id}-eth1`), then attach to remaining networks
  - When OVS plugin disabled: Use `network_mode: "none"` for legacy post-start interface provisioning

### Issue 2: Uninitialized `graph` variable in jobs.py
**Location:** `api/app/tasks/jobs.py:1352`

The `run_node_sync()` function used `if graph:` before `graph` was defined, causing:
```
UnboundLocalError: cannot access local variable 'graph' where it is not associated with a value
```

**Fix Applied:**
- Added `graph = None` initialization at line 1232 (beginning of the function)

### Issue 3: Network naming mismatch (vendor prefix vs eth)
**Location:** `agent/providers/docker.py`

Code was changed to use vendor-specific port naming (e.g., "Ethernet" for cEOS) for Docker network names, but:
- Existing networks used "eth" prefix (e.g., `lab-eth1`)
- New code tried to use vendor prefix (e.g., `lab-Ethernet1`)

**Fix Applied:**
- Reverted to always use "eth" prefix for Docker network names
- The OVS plugin handles renaming interfaces inside containers

### Issue 4: OVS plugin state not synchronized with Docker networks
When agent restarts, the OVS plugin discovers existing OVS bridges but not Docker networks created through it. This causes "Network not found" errors when Docker tries to create endpoints.

**Workaround:**
- Clean up stale Docker networks before redeploying
- Restart agent to reset plugin state

### Issue 5: Docker IP address pool exhaustion
Creating 64 networks per lab (for cEOS max_ports) exhausted Docker's predefined address pools:
```
400 Client Error: "all predefined address pools have been fully subnetted"
```

**Workaround:**
- Run `docker network prune -f` to remove unused networks
- Consider reducing max_ports or using a different network driver configuration

## Files Modified

1. **agent/providers/docker.py**
   - Removed `network_mode: "none"` from container config
   - Updated `_create_containers()` to attach to networks before starting
   - Simplified `_create_lab_networks()` to always use "eth" prefix

2. **api/app/tasks/jobs.py**
   - Added `graph = None` initialization in `run_node_sync()`

## Commands Used for Cleanup

```bash
# Remove stale container
docker rm -f archetype-d35ec857-8976-4c3a-9-eos_1

# Remove stale networks for a lab
docker network ls --filter "name=d35ec857" -q | xargs -r docker network rm

# Prune unused networks (frees IP address pools)
docker network prune -f

# Restart agent to reset OVS plugin state
docker restart archetype-iac-agent-1

# Rebuild containers after code changes
docker compose -f docker-compose.gui.yml up -d --build agent api worker
```

## Key Insights

1. **cEOS Interface Requirements:**
   - cEOS uses `INTFTYPE=eth` environment variable to know Linux interface naming
   - Interfaces must exist BEFORE container init runs (can't hot-add)
   - Linux `eth1`, `eth2`, etc. map to EOS `Ethernet1`, `Ethernet2`, etc.

2. **OVS Plugin Architecture:**
   - Creates per-lab OVS bridge (`ovs-{lab_id}`)
   - Each Docker network = one interface on the bridge
   - VLAN tags isolate interfaces until hot_connect links them
   - Plugin state is in-memory; lost on restart unless recovered from OVS

3. **Docker Network Limitations:**
   - Containers with `network_mode: "none"` cannot join other networks
   - Docker has limited IP address pools for bridge networks
   - Network driver plugins must track their own state

## Current Status
- Code fixes applied and containers rebuilt
- Agent restarted with fresh plugin state
- Docker networks pruned to free IP address pools
- Ready to retry EOS-1 deployment

## Next Steps
1. Click "Start" on EOS-1 in the GUI
2. Monitor with: `docker logs -f archetype-d35ec857-8976-4c3a-9-eos_1`
3. cEOS typically takes 2-5 minutes to fully boot
4. Check for `%SYS-5-CONFIG_I` or `System ready` in logs to confirm boot complete
