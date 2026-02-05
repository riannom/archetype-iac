# Agent Network Module

This module provides container networking for the Archetype agent using Open vSwitch (OVS).
The networking stack is wrapped by a backend abstraction in `agent/network/backends/` to
allow future backends without changing external APIs.

## Architecture

All container networking uses OVS with a single bridge (`arch-ovs`) per host. VLAN tags provide isolation between different links/networks.

```
                     HOST NAMESPACE
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              ┌─────────────────────────────────┐            │
│              │      arch-ovs (OVS Bridge)      │            │
│              │      fail_mode: standalone      │            │
│              │                                 │            │
│              │  vhA-e1 (tag=100)    ←───────── Local link   │
│              │  vhB-e1 (tag=100)    ←───────── (same VLAN)  │
│              │                                 │            │
│              │  vhC-e1 (tag=3884)   ←───────── Overlay      │
│              │  vxlan13446884 (tag=3884) ←──── (VXLAN+VLAN) │
│              └─────────────────────────────────┘            │
│                          │                                  │
│    ┌─────────────────────┼─────────────────────┐            │
│    │                     │                     │            │
│  ┌─┴───┐              ┌──┴──┐              ┌───┴──┐         │
│  │vhA  │              │vhB  │              │vhC   │         │
│  └──┬──┘              └──┬──┘              └──┬───┘         │
└─────┼────────────────────┼─────────────────────┼────────────┘
      │ Container A        │ Container B         │ Container C
   ┌──┴──┐              ┌──┴──┐              ┌───┴──┐
   │eth1 │              │eth1 │              │eth1  │
   └─────┘              └─────┘              └──────┘
```

## Modules

### `ovs.py` - OVSNetworkManager

Manages local container networking with OVS:
- Creates veth pairs and attaches to OVS bridge
- Uses VLAN tags for per-link isolation
- Supports hot-connect/disconnect (change VLAN tag to connect/disconnect)
- Pre-provisions interfaces before container boot (for cEOS compatibility)

### `overlay.py` - OverlayManager

Manages cross-host VXLAN tunnels:
- Creates VXLAN ports on OVS (not Linux bridge)
- Uses VNI for VXLAN tunnel identification
- Uses VLAN tags (3000-4000 range) for OVS isolation
- Both VXLAN port and veth share same VLAN tag for L2 connectivity

### `docker_plugin.py` - OVS Docker Plugin

Docker network plugin for pre-boot interface provisioning:
- Creates real interfaces (not dummy) before container init
- Required for cEOS which enumerates interfaces at boot
- Attaches to shared OVS bridge (`arch-ovs`)

### `local.py` - LocalNetworkManager

Legacy local networking (veth pairs without OVS):
- Used for simple point-to-point links on single host
- Being replaced by OVS-based networking

### `cleanup.py` - Network Cleanup

Periodic cleanup of orphaned network resources:
- Removes veth pairs without valid peers
- Cleans up stale OVS ports
- Preserves veths attached to bridges (not orphaned)

## Important Notes

### OVS Fail Mode

The OVS bridge MUST use `fail_mode: standalone` for normal L2 switching:

```bash
ovs-vsctl set-fail-mode arch-ovs standalone
```

With `fail_mode: secure` (the default when using OpenFlow), all traffic is dropped unless explicit flow rules are installed.

### Linux Bridge Issues

Do NOT use Linux bridge for VXLAN overlay. Linux bridge has issues forwarding unicast packets to VXLAN ports:
- Broadcast (ARP) works correctly
- Unicast (ICMP, TCP, etc.) gets dropped

OVS handles both correctly.

### VLAN Tag Ranges

| Range | Usage |
|-------|-------|
| 100-999 | Local OVS plugin links |
| 3000-4000 | Cross-host overlay links |

## Debugging

```bash
# Show OVS bridge configuration
ovs-vsctl show

# List ports on arch-ovs
ovs-vsctl list-ports arch-ovs

# Check port VLAN tag
ovs-vsctl get port <port_name> tag

# Check fail mode
ovs-vsctl get bridge arch-ovs fail_mode

# Dump flows (should be empty in standalone mode)
ovs-ofctl dump-flows arch-ovs

# Check overlay status via API
curl http://localhost:8001/overlay/status
```
