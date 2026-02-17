# Network Architecture Reference

## Overview

Archetype uses OVS (Open vSwitch) for all container and VM networking. Every host runs a single shared OVS bridge (`arch-ovs`) in **standalone fail-mode** with a single flow rule: `priority=1 actions=NORMAL` (standard MAC learning). VLAN tags provide L2 isolation between links.

**Design principle**: The database is the source of truth for VLAN tags. Background convergence pushes DB state to reality (OVS ports). Never the reverse.

## VLAN Range Architecture

| Range | Tags | Purpose | Who Assigns | Managed By |
|-------|------|---------|-------------|------------|
| **100-2049** | 1950 | Unlinked container ports, ephemeral | Plugin at container creation | Plugin (in-memory + state file) |
| **2050-4000** | 1951 | Linked ports, DB-stored | Link creation (`hot_connect`, `attach-link`) | DB + convergence |

At a glance: VLAN < 2050 = isolated/unlinked port. VLAN >= 2050 = linked port managed by convergence.

**Files defining ranges**:

| File | Constants | Purpose |
|------|-----------|---------|
| `agent/network/docker_plugin.py:76-83` | `VLAN_RANGE_START=100`, `VLAN_RANGE_END=2049`, `LINKED_VLAN_START=2050`, `LINKED_VLAN_END=4000` | Plugin allocation |
| `agent/providers/docker.py:75-76` | `VLAN_RANGE_START=100`, `VLAN_RANGE_END=2049` | Docker provider |
| `agent/providers/libvirt.py:155-156` | `VLAN_RANGE_START=100`, `VLAN_RANGE_END=2049` | VM provider |
| `agent/network/overlay.py:49-50` | `OVERLAY_VLAN_BASE=2050`, `OVERLAY_VLAN_MAX=4000` | Overlay VNI-to-VLAN mapping |
| `agent/network/ovs.py:63-64` | `VLAN_START=100`, `VLAN_END=4000` | Full validation range |

## Key Components

### Docker OVS Plugin (`agent/network/docker_plugin.py`)

Docker network plugin that provisions container interfaces **before boot**.

**Architecture**:
- Single shared `arch-ovs` bridge for all labs (enables same-bridge VXLAN + same-host links)
- Each container interface = one Docker network attachment = one veth pair
- VLAN tags isolate interfaces until `hot_connect()` links them

**Endpoint lifecycle**:
1. **CreateEndpoint** (Docker hook): Creates veth pair (`vh<hash>` host-side, `vc<hash>` container-side), attaches host-side to OVS with unique VLAN (100-2049)
2. **Join** (Docker hook): Returns interface name, Docker moves container-side veth into container netns
3. **Leave/DeleteEndpoint**: Cleans up OVS port and veth pair, releases VLAN

**VLAN allocation**:
- `_allocate_vlan()`: Isolated range (100-2049), collision-avoidance via `_get_used_vlan_tags_on_bridge()`
- `_allocate_linked_vlan()`: Linked range (2050-4000), same collision-avoidance pattern
- Both are global across all labs on the bridge

**State persistence**:
- Saves to `docker_ovs_plugin_state.json` via atomic temp-file + rename
- On startup: loads persisted state, then `_discover_existing_state()` reconciles with actual Docker/OVS state
- `_ensure_lab_network_attachments()`: post-start reconciliation reconnects containers to plugin networks

### OVS Network Manager (`agent/network/ovs.py`)

- Manages the shared `arch-ovs` bridge lifecycle
- `VlanAllocator`: Persistent VLAN allocation with OVS recovery via `recover_from_ovs()`
- Bridge init: `ovs-vsctl add-br arch-ovs` + `set-fail-mode standalone` (CRITICAL: secure mode drops all traffic)
- Default flow: `ovs-ofctl add-flow arch-ovs priority=1,actions=normal`

### Overlay Manager (`agent/network/overlay.py`)

Creates Linux VXLAN devices and attaches them to `arch-ovs` for cross-host links.

**VXLAN device creation** (`_create_vxlan_device()`):
```bash
ip link add <name> type vxlan id <vni> local <local_ip> remote <remote_ip> dstport 4789 df unset
ip link set <name> mtu <overlay_mtu>
ip link set <name> up
ovs-vsctl add-port arch-ovs <name> tag=<local_vlan>   # Access mode
```

**OVS access mode behavior**:
- Egress: OVS strips local VLAN tag, VXLAN encapsulates with VNI
- Ingress: VXLAN decapsulates VNI, OVS adds local VLAN tag
- Each side uses independent local VLANs — no cross-host coordination needed

**Key methods**:
- `create_link_tunnel()`: Creates per-link VXLAN port with unique VNI
- `declare_state()`: Convergence handler — creates/updates/deletes VXLAN ports to match declared desired state
- `recover_link_tunnels()`: Rebuilds in-memory `_link_tunnels` from existing VXLAN devices on agent startup
- `cleanup_ovs_vxlan_orphans()`: Runs every 5 min, deletes VXLAN ports not in `_link_tunnels` (recovery prevents false deletion)

**MTU discovery** (`_discover_path_mtu()`):
- Tests with `ping -M do -s <payload>` (Don't Fragment)
- Candidates: 9000, 4000, 1500
- Caches results in `_mtu_cache`

### Carrier Monitor (`agent/network/carrier.py`)

- Background task monitoring OVS port carrier states
- Tracks ports from both DockerOVSPlugin and OVSNetworkManager
- Propagates carrier state changes between linked peers
- Reports changes to API via callback

### Network Backend Abstraction (`agent/network/backends/`)

- Backend registry with OVS adapter
- Provides unified interface for overlay operations across different network backends
- Default backend: `ovs`

## Link Creation Flow

### Same-Host Links

```
API                                              Agent
 |                                                 |
 |  create_same_host_link()                        |
 |  link_orchestration.py:553                      |
 |                                                 |
 |  1. Set actual_state = "creating"               |
 |  2. Normalize interfaces (Ethernet1 -> eth1)    |
 |  3. POST /labs/{id}/links ------------------>   |
 |                                                 |  hot_connect()
 |                                                 |  - Find endpoints by container+iface
 |                                                 |  - _allocate_linked_vlan() -> tag (2050-4000)
 |                                                 |  - Set BOTH ports to new tag
 |                                                 |  - Release old isolated tags
 |  <-- {success, vlan_tag} ---------------------- |
 |                                                 |
 |  4. Store vlan_tag in LinkState                 |
 |     (vlan_tag, source_vlan_tag, target_vlan_tag)|
 |  5. verify_link_connected() -> re-read OVS      |
 |  6. Update InterfaceMapping                     |
 |  7. Set actual_state = "up"                     |
```

**Data flow**:
```
Container A:ethN  -->  vh_A (tag=X)  -->  arch-ovs  <--  vh_B (tag=X)  <--  Container B:ethM
                                            |
                                       Same VLAN tag X
                                       = shared L2 domain
```

### Cross-Host Links

```
API                                Agent A                    Agent B
 |                                   |                          |
 |  create_cross_host_link()         |                          |
 |  link_orchestration.py:654        |                          |
 |                                   |                          |
 |  1. Set actual_state = "creating" |                          |
 |  2. Allocate VNI (MD5 hash)       |                          |
 |  3. Resolve data plane IPs        |                          |
 |                                   |                          |
 |  POST /overlay/attach-link -----> |                          |
 |                                   | _allocate_linked_vlan()  |
 |                                   | Set container port tag   |
 |                                   | Create VXLAN device      |
 |                                   | Add to OVS (tag=X)       |
 |  <-- {local_vlan: X} ----------- |                          |
 |                                   |                          |
 |  POST /overlay/attach-link --------------------------->     |
 |                                   |     _allocate_linked_vlan()
 |                                   |     Set container port tag
 |                                   |     Create VXLAN device
 |                                   |     Add to OVS (tag=Y)
 |  <-- {local_vlan: Y} --------------------------------      |
 |                                   |                          |
 |  4. Store source_vlan_tag=X,      |                          |
 |     target_vlan_tag=Y in LinkState|                          |
 |  5. Create VxlanTunnel record     |                          |
 |  6. Verify cross-host link        |                          |
 |  7. Set actual_state = "up"       |                          |
```

**Data flow**:
```
Host A:                                                    Host B:
Container:ethN  -->  vh_A (tag=X)  -->  arch-ovs           arch-ovs  <--  vh_B (tag=Y)  <--  Container:ethM
                                          |                   |
                                    vxlan-XXXXX (tag=X)  vxlan-XXXXX (tag=Y)
                                          |                   |
                                          +--- VXLAN tunnel --+
                                              VNI: <unique>
```

Each side has its **own local VLAN tag** (X on source, Y on target). The VXLAN access-mode port shares the same tag as its container port on each host. OVS bridges traffic between ports with matching tags.

### Tag Chain Example

For link `ceos_1:eth3 <-> ceos_2:eth1` with `source_vlan=2056, target_vlan=2056, VNI=15074138`:

1. ceos_1 sends packet on eth3 (inside container)
2. Host A OVS receives on `vh_ceos1_eth3` (tag=2056)
3. Host A OVS forwards to `vxlan-0792fb72` (also tag=2056, same VLAN = same L2)
4. VXLAN encapsulates with VNI 15074138, sends UDP to Host B
5. Host B OVS receives on `vxlan-0792fb72` (tag=2056 on Host B)
6. Host B OVS forwards to `vh_ceos2_eth1` (also tag=2056, same VLAN)
7. ceos_2 receives on eth1

### VNI Allocation

`allocate_vni()` in `api/app/services/link_manager.py`:
- Deterministic: MD5 hash of `{lab_id}:{link_name}`
- Range: 100000-16777215
- Same input always produces same VNI (idempotent)

### VXLAN Port Naming

`compute_vxlan_port_name()` in `api/app/agent_client.py`:
- Format: `vxlan-<hash>` where hash = first 8 chars of MD5(`{lab_id}:{link_name}`)
- Deterministic: same link always gets same port name
- Max length: 14 chars (OVS limit for interface names is 15)

## Background Convergence & Reconciliation

### Link Reconciliation Monitor (`link_reconciliation.py`)

Runs as a long-lived async task. Every 60 seconds:

**Every cycle**:
1. Detect and remove duplicate VxlanTunnel records
2. `reconcile_link_states()` — verify "up" links, attempt recovery for "error" links
3. Clean up orphaned LinkState records (link definition deleted)
4. Clean up orphaned VxlanTunnel records

**Every 5th cycle** (~5 min, convergence block):
1. **Overlay convergence** (`run_overlay_convergence()`) — pushes DB tunnel config to VXLAN ports on agents
2. **InterfaceMapping refresh** (`refresh_interface_mappings()`) — reads current OVS state from agents for ALL active links (same-host + cross-host)
3. **Cross-host port convergence** (`run_cross_host_port_convergence()`) — pushes DB VLAN tags to container ports on cross-host links
4. **Same-host port convergence** (`run_same_host_convergence()`) — pushes DB VLAN tags to container ports on same-host links

### Overlay Convergence (`run_overlay_convergence()`)

**Declarative model**: Builds desired tunnel state from `VxlanTunnel` + `LinkState` tables, groups by agent, sends `declare_state()` to each. Agent converges to match: creates missing, updates drifted, removes orphans.

Reads `overlay_mtu` from `infra_settings` (DB source of truth).

Protects in-progress links (creating/connecting) by adding placeholder entries so `declare_state()` won't orphan-clean them.

### Cross-Host Port Convergence (`run_cross_host_port_convergence()`)

**The key fix for container restart recovery**:
1. Queries all cross-host LinkStates with `desired_state=up`, `actual_state=up`
2. For each endpoint, looks up InterfaceMapping to find current OVS port name + VLAN tag
3. Compares current tag vs DB truth (`source_vlan_tag`/`target_vlan_tag`)
4. If mismatch, calls `set_port_vlan_on_agent()` to push DB tag to container port
5. Corrections applied in parallel across agents via `asyncio.gather()`

### Link Repair Strategies (`link_repair.py`)

**Partial recovery** (`attempt_partial_recovery()`):
- Scenario: One side of cross-host link lost VXLAN attachment (agent restart)
- Logic: Re-attaches only the missing side using existing VNI
- Uses `source_vxlan_attached` / `target_vxlan_attached` flags

**VLAN repair** (`attempt_vlan_repair()`):
- Same-host: Re-calls `hot_connect()` to re-match tags
- Cross-host: Pushes DB tag to BOTH container port AND VXLAN tunnel port (DB is truth)

**Full link repair** (`attempt_link_repair()`):
- Last resort: Tears down and recreates entire link
- Calls `create_same_host_link()` or `create_cross_host_link()` with `verify=True`

### Link Cleanup (`link_cleanup.py`)

- `_cleanup_deleted_links()`: Links with `desired_state=deleted` — tears down VXLAN, deletes records
- `cleanup_orphaned_link_states()`: `link_definition_id IS NULL` — only deletes non-"up" orphans
- `cleanup_orphaned_tunnels()`: `link_state_id IS NULL` or `status=cleanup` for >5 min
- `detect_duplicate_tunnels()`: Groups by canonical key `(min(a,b), max(a,b), vni)`, keeps newest

## Libvirt VM Networking

### How VMs Get OVS Interfaces

VMs use TAP devices instead of veth pairs:
1. Libvirt creates TAP device during domain definition
2. TAP attached to `arch-ovs` with VLAN tag (same bridge as Docker containers)
3. Uses `virtio` or `e1000` NIC driver depending on vendor config
4. VLAN assigned during domain creation from isolated range (100-2049)

### Cross-Provider Links

Docker-to-Libvirt links work seamlessly:
- Both providers attach ports to the same `arch-ovs` bridge
- VLAN tag matching provides L2 connectivity
- Same-host: `hot_connect()` works across providers
- Cross-host: Same VXLAN mechanism (access-mode ports with local VLANs)

### NIC Driver Substitutions (`libvirt.py:174`)

```python
NIC_DRIVER_SUBSTITUTIONS = {
    "vmxnet3": "virtio",  # VMware-specific, unsupported by QEMU
    "vmxnet2": "e1000",
    "vmxnet": "e1000",
}
```

## State Sources of Truth

| Data | Source of Truth | Location |
|------|----------------|----------|
| Link topology | `links` table | PostgreSQL |
| Link runtime state | `link_states` table | PostgreSQL |
| Expected VLAN tags | `link_states.source_vlan_tag/target_vlan_tag` | PostgreSQL |
| VXLAN tunnel config | `vxlan_tunnels` table | PostgreSQL |
| Overlay MTU | `infra_settings.overlay_mtu` | PostgreSQL |
| Actual container port tags | OVS bridge port config | Per-agent (ephemeral) |
| Actual VXLAN port tags | OVS bridge port config | Per-agent (ephemeral) |
| Plugin endpoint tracking | Docker OVS Plugin state file + memory | Per-agent |
| VXLAN tunnel tracking | Overlay Manager `_link_tunnels` | Per-agent (in-memory) |

## Key Data Models

### LinkState (`api/app/models.py`)

```
link_name              "ceos_1:eth3-ceos_2:eth1"
source_node            "ceos_1"
source_interface       "eth3"
target_node            "ceos_2"
target_interface       "eth1"
desired_state          "up" | "down" | "deleted"
actual_state           "pending" | "creating" | "connecting" | "up" | "down" | "error" | "unknown"
is_cross_host          true/false
vni                    VXLAN Network Identifier (cross-host only)
vlan_tag               Legacy shared tag (same-host)
source_vlan_tag        Local VLAN on source agent
target_vlan_tag        Local VLAN on target agent
source_host_id         Agent hosting source node
target_host_id         Agent hosting target node
source_vxlan_attached  Partial recovery tracking
target_vxlan_attached  Partial recovery tracking
source_carrier_state   "on" | "off" (link up/down simulation)
target_carrier_state   "on" | "off"
source_oper_state      Derived: "up" | "down" | "admin_down" | "peer_down" | "no_carrier"
target_oper_state      Derived operational state
oper_epoch             Increment on change (optimistic locking)
```

### VxlanTunnel (`api/app/models.py`)

```
lab_id                 Lab identifier
link_state_id          FK to LinkState (unique)
vni                    VXLAN Network Identifier
agent_a_id / agent_a_ip   Source endpoint
agent_b_id / agent_b_ip   Target endpoint
port_name              Deterministic OVS port name "vxlan-<hash>"
status                 "pending" | "active" | "failed" | "cleanup"
```

### InterfaceMapping (`api/app/models.py`)

```
lab_id                 Lab identifier
node_id                FK to Node
ovs_port               "vh614ed63ed40" (host-side veth)
ovs_bridge             "arch-ovs"
vlan_tag               Current VLAN tag (from last refresh)
linux_interface        "eth1"
vendor_interface       "Ethernet1", "ge-0/0/0"
device_type            "ceos", "srl"
last_verified_at       Last refresh timestamp
```

## Recovery Scenarios

### Container Restart (Same-Host Link)

1. Docker recreates container → plugin assigns new veth with isolated VLAN (100-2049)
2. Every 5th reconciliation cycle (~5 min):
   - InterfaceMapping refresh reads new VLAN from agent
   - Same-host convergence detects mismatch vs DB tag
   - Pushes DB tag to both container ports
3. L2 connectivity restored

### Container Restart (Cross-Host Link)

1. Docker recreates container → plugin assigns new veth with isolated VLAN (100-2049)
2. VXLAN tunnel port still has DB-stored VLAN → mismatch → L2 blackhole
3. Every 5th reconciliation cycle (~5 min):
   - InterfaceMapping refresh reads new VLAN from agent
   - Cross-host port convergence detects mismatch vs DB tag
   - Pushes DB tag to container port via `set_port_vlan_on_agent()`
   - Overlay convergence pushes DB tag to VXLAN tunnel port
4. Both ports now have matching DB tag → L2 connectivity restored

### Agent Restart

1. Plugin loads state from `docker_ovs_plugin_state.json`
2. `_discover_existing_state()` reconciles with actual Docker/OVS state
3. Overlay Manager: `recover_link_tunnels()` scans for existing VXLAN devices, rebuilds `_link_tunnels`
4. Reconciliation loop re-verifies all "up" links
5. Partial recovery re-attaches any missing VXLAN sides

## Agent Endpoints Reference

### Network/Overlay

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/overlay/bridge-ports` | GET | List all OVS bridge ports with VLAN tags, VXLAN details |
| `/overlay/attach-link` | POST | Create VXLAN tunnel + allocate linked VLAN for container port |
| `/overlay/detach-link` | POST | Remove VXLAN tunnel + isolate container port |
| `/overlay/declare-state` | POST | Declarative convergence: desired tunnel set → create/update/delete |
| `/overlay/ports/{port}/vlan` | PUT | Set VLAN tag on any OVS port (used by convergence) |
| `/labs/{lab_id}/port-state` | GET | All container port names + VLANs (for InterfaceMapping refresh) |
| `/labs/{lab_id}/links` | POST | Same-host hot-connect |
| `/labs/{lab_id}/links` | DELETE | Same-host hot-disconnect |
| `/labs/{lab_id}/interfaces/{node}/{iface}/vlan` | GET | Single container port VLAN tag |
| `/labs/{lab_id}/interfaces/{node}/{iface}/carrier` | POST | Set carrier state (on/off) |
| `/ports/declare-state` | POST | Same-host port convergence (set VLAN tags on port pairs) |

### Plugin Management

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/ovs-plugin/status` | GET | Plugin health, endpoint/network counts |
| `/ovs-plugin/state` | GET | Full plugin state dump |
| `/ovs-plugin/labs/{lab_id}/ports` | GET | Plugin-tracked ports for a lab (with traffic stats) |
| `/ovs-plugin/labs/{lab_id}/flows` | GET | OVS flow table |
| `/labs/{lab_id}/repair-endpoints` | POST | Repair plugin endpoint tracking |
| `/labs/{lab_id}/nodes/{node}/fix-interfaces` | POST | Fix container interface naming |

### Debug

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/overlay/bridge-ports` | GET | Full bridge port dump with FDB entries |
| `/debug/exec` | POST | Execute command on agent (debug only) |
| `/overlay/bridge-ports/{port}` | DELETE | Remove specific bridge port |

## Known Issues & Vendor Quirks

### cEOS

- **iptables DROP rules**: Adds `iptables -A EOS_FORWARD -i ethN -j DROP` at boot. Post-boot commands (vendors.py) remove these.
- **errdisable cascade**: `ip link set carrier off/on` triggers link-flap detection -> errdisable -> IFF_UP cleared -> host veth carrier drops -> cascades to peer. Fix: `no errdisable detect cause link-flap` in post-boot commands.
- **IP routing disabled**: `no ip routing` is default. Must enable via startup-config or CLI.
- **arp_ignore=1**: Data interfaces only respond to ARP if target IP is on incoming interface.
- **MTU override**: cEOS overrides Linux `ip link set` MTU. Must use EOS CLI for MTU changes.

### Linux VXLAN

- `df unset` does NOT enable transparent fragmentation for locally-originated tunnel packets. Jumbo frames over 1500 MTU underlay don't work reliably.
- The `overlay_mtu` setting is applied to VXLAN interfaces via `ip link set mtu`.

### Docker Network Naming

Two formats can coexist:
- Full: `e844e435-fde4-4d95-98c3-4fa8966362f9-eth1`
- Truncated (20 chars): `e844e435-fde4-4d95-9-eth1`

Fix applied: Network ID check (not just name) in `_ensure_lab_network_attachments()`.

## Container Name Formats

| Provider | Format | Example |
|----------|--------|---------|
| Docker | `archetype-{lab_id_truncated}-{node}` | `archetype-e844e435-fde4-4d95-9-ceos_1` |
| Libvirt | `arch-{lab_id_short}-{node}` | `arch-e844e435-ceos_1` |

Docker truncates lab_id to 20 characters for the 63-char Docker network name limit.

## Infrastructure Settings

| Setting | Default | Purpose |
|---------|---------|---------|
| `overlay_mtu` | 0 (agent default: 1450) | MTU for VXLAN tunnel interfaces |
| `ARCHETYPE_AGENT_OVS_BRIDGE_NAME` | `arch-ovs` | Shared OVS bridge name |
| `ARCHETYPE_AGENT_ENABLE_VXLAN` | true | Enable VXLAN overlay |
| `ARCHETYPE_AGENT_ENABLE_OVS` | true | Enable OVS networking |
| `ARCHETYPE_AGENT_NETWORK_BACKEND` | `ovs` | Network backend selection |

## Debugging Checklist

### Cross-Host Ping Failure

1. **Check VLAN tag alignment**: Container port tag must match VXLAN tunnel tag on each host
   - `GET /overlay/bridge-ports` -> compare VXLAN port tags vs container port tags
   - `GET /labs/{lab_id}/interfaces/{node}/{iface}/vlan` -> check specific interface
2. **Check link_states DB**: `SELECT source_vlan_tag, target_vlan_tag, actual_state FROM link_states WHERE is_cross_host = true`
3. **Wait for convergence**: Cross-host port convergence runs every ~5 min. Check scheduler logs for `"Cross-host port convergence: updated=N"`
4. **Check ARP**: `docker exec <container> arping -I ethN -c 2 <target_ip>` — if ARP works but ICMP doesn't, check iptables
5. **Check iptables**: `docker exec <container> iptables -L EOS_FORWARD -n -v` — look for DROP rules on ethN
6. **Check VXLAN tunnel stats**: `ip -d link show type vxlan` and `ip -s link show <vxlan_name>` — check TX/RX counters
7. **Check OVS FDB**: `GET /overlay/bridge-ports` -> `fdb_lines` shows MAC learning

### Same-Host Ping Failure

1. **Check VLAN tags match**: Both container ports must have identical VLAN tags
2. **Check OVS bridge mode**: Must be `fail_mode: standalone` (not secure)
3. **Check hot_connect fired**: Look for linked-range VLAN (2050+) on both ports

### Container Missing Interfaces

1. `GET /labs/{lab_id}/nodes/{node}/linux-interfaces` — verify interface names
2. `POST /labs/{lab_id}/nodes/{node}/fix-interfaces` — attempt auto-fix
3. Check for `_old_*` duplicate interfaces in container namespace
