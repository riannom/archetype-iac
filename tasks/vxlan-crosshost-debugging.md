# Cross-Host VXLAN Debugging Progress

## Goal
Fix cross-host pings between containers on different agent hosts after switching from OVS-managed VXLAN to Linux VXLAN devices (commit `1f0ceef`).

## Agents
- **local-agent** (`b39a05df`): 10.14.23.93 — Docker container `archetype-iac-agent-1`
- **agent-01** (`614fc24c`): 10.14.23.181 — Systemd service at `/opt/archetype-agent/repo`, restart via `sudo systemctl restart archetype-agent`
- **Lab ID**: `e844e435-fde4-4d95-98c3-4fa8966362f9`

## Code Changes Made (committed + pushed)

### Commit `aa3fa23` — VXLAN code fixes
1. **`agent/network/overlay.py`**: Added `"df", "unset"` to `_create_vxlan_device`
2. **`agent/network/overlay.py`**: Fixed veth MTU in overlay attach paths (use `tenant_mtu` not `settings.local_mtu`)
3. **`agent/network/ovs.py`**: Added `"df", "unset"` to VXLAN device creation
4. **`agent/main.py`**: Updated bridge-ports diagnostic to detect system-type VXLAN ports by name pattern
5. **`agent/main.py`**: Updated reconcile-ports to clean up system-type VXLAN ports + `ip link delete`

### Commit `4e92281` — nopmtudisc removal + interface normalization
1. **`agent/network/overlay.py`, `ovs.py`, `docker_plugin.py`**: Removed `nopmtudisc` flag — NOT SUPPORTED by iproute2 on these hosts. `df unset` alone is sufficient.
2. **`api/app/topology.py`**: Fixed GigabitEthernet normalization: `GigabitEthernet0` → `eth0` (was `eth-1` due to incorrect 1-indexed assumption)
3. **`api/app/routers/labs.py:1888-1889`**: Normalize vendor interface names (`Ethernet1` → `eth1`) when creating LinkState records from frontend graph
4. **`api/app/tasks/state_enforcement.py`**: Made `_is_enforceable` async (was sync but used `await _is_on_cooldown`)

## Issues Found & Fixed
1. **`nopmtudisc` not supported**: `ip link` error "unknown command nopmtudisc" — removed, `df unset` is sufficient
2. **Interface name mismatch**: Frontend sends `Ethernet1` (Arista) / `GigabitEthernet0` (Cisco), but OVS ports use `eth1`/`eth0`. Added normalization at link state creation point.
3. **GigabitEthernet0 → eth-1 bug**: Off-by-one in normalization regex (assumed 1-indexed, but Cisco uses 0-indexed)
4. **State enforcement syntax error**: `await` in sync function `_is_enforceable` — made it async
5. **Stale Ethernet-named link states**: Duplicate link states with vendor names alongside eth-named ones — cleaned up in DB

## Current Blocking Issue
**Cross-host VXLAN tunnels are NOT being created**, but link states show `actual=up`.

### Symptoms
- All 4 cross-host links show `actual=up` in LinkState table
- **Zero VXLAN devices** on both local and remote agents (`ip -br link show type vxlan` returns empty)
- **Zero VXLAN OVS ports** on either agent
- Same-host links work fine (VLAN matching via OVS)
- Cross-host pings fail (100% packet loss)
- No VXLAN/tunnel/overlay log entries in API logs
- Nodes in mixed states: some `stopped`, some `running` (enforcement is slow or not running)

### Root Cause Hypothesis
The **link reconciliation** code is marking cross-host links as `up` without actually creating VXLAN tunnels. This could be because:

1. The reconciliation checks if both nodes are running but since nodes are in mixed states (some stopped), it may be reading stale state
2. The auto-connect code in `reconciliation.py` (`_auto_connect_links`, lines ~1259-1336) may have a bug where it marks links up without calling the VXLAN creation code
3. The NLM's `_connect_same_host_links` is called after deployment but the **cross-host link creation** might not be triggered by enforcement-driven start operations (only by initial deploy)

### Investigation Needed
1. Check `api/app/tasks/reconciliation.py` auto-connect code — does it call `create_cross_host_link` or just mark links as `up`?
2. Check `api/app/tasks/link_reconciliation.py` — does it verify VXLAN tunnels exist for cross-host links?
3. Check NLM flow for node start (not deploy) — does it create cross-host links?
4. The state enforcement might not be running (3 nodes still stuck in `stopped` state for >60s despite `desired_state=running`)

### DB State Cleanup Done
- Deleted all `Ethernet`-named LinkState records (7 stale entries)
- Fixed `eth-1` → `eth0` in Link table for CSR1000v link
- Reset cross-host links to `pending` and `error` multiple times — they keep being set back to `up` without VXLAN creation

## Environment Notes
- Remote agent is **systemd-deployed** (not Docker): update via `sudo -S bash -c "cd /opt/archetype-agent/repo && git pull && systemctl restart archetype-agent"`
- SSH: `sshpass -p '<REDACTED>' ssh -o StrictHostKeyChecking=no azayaka@10.14.23.181`
- Auth token: `TOKEN=$(cat /tmp/archetype_token)` (or re-generate via `source .env && curl -s -X POST http://localhost:8000/auth/login -d "username=${ADMIN_EMAIL}&password=${ADMIN_PASSWORD}"`)
- API: `docker compose -f docker-compose.gui.yml up -d --build api`
- Local agent: `docker compose -f docker-compose.gui.yml up -d --build agent`
