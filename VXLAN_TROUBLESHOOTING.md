# VXLAN Cross-Host Connectivity Troubleshooting

## Status: RESOLVED

The issue was caused by Linux bridge not forwarding unicast packets to the VXLAN port.
The fix was to replace Linux bridge with Open vSwitch (OVS) for the overlay networking.

## Problem
Cannot ping from eos_1 (on local-agent, 10.14.23.36) to eos_2 (on debian, 10.14.23.11) over VXLAN overlay.

## Lab Details
- Lab ID: `d35ec857-8976-4c3a-9dbe-4b59cbf13f24`
- Link: `eos_1:Ethernet1-eos_2:Ethernet1`
- VNI: 13446884
- IPs: eos_1 = 10.1.2.1/24, eos_2 = 10.1.2.2/24

## Bugs Fixed

### 1. Link endpoint ordering bug (api/app/services/topology.py)
- **Issue**: When creating links, source/target node IDs weren't swapped to match alphabetically-sorted link names
- **Effect**: Cross-host links connected wrong containers to wrong agents
- **Fix**: Added endpoint swap logic when `generate_link_name` reorders endpoints

### 2. Container name resolution (agent/main.py)
- **Issue**: Overlay `/attach` endpoint received short names like "eos_1" but Docker needs full names like "archetype-d35ec857-eos_1"
- **Fix**: Added `provider.get_container_name()` call to convert names

### 3. Interface name conversion for cEOS (agent/main.py)
- **Issue**: EOS containers expect `eth1` for Linux interface but API sent `Ethernet1`
- **Effect**: Overlay created `Ethernet1` interface, but EOS control plane bound to different `eth1`
- **Fix**: Added conversion for containers with `INTFTYPE=eth` env var: `Ethernet1` -> `eth1`

### 4. Network cleanup deleting overlay veths (agent/network/cleanup.py)
- **Issue**: Cleanup task marked overlay veths as "orphaned" because peer is in container namespace (not visible from host)
- **Effect**: Periodic cleanup deleted working overlay connections
- **Fix**: Check `master` field - if veth is attached to a bridge, it's not orphaned

### 5. EOS iptables DROP rule
- **Issue**: cEOS has `DROP all -- eth1 *` rule in `EOS_FORWARD` chain
- **Effect**: Blocks all forwarding through eth1 data plane interfaces
- **Workaround**: `iptables -D EOS_FORWARD -i eth1 -j DROP` (needs to be done on both eos_1 and eos_2)
- **Note**: This rule gets recreated on container restart

## Bugs Fixed (continued)

### 6. EOS IP routing disabled
- **Issue**: cEOS has `no ip routing` by default in its configuration
- **Effect**: EOS could not generate or forward IP packets (Layer 3)
- **Fix**: Enable IP routing on both containers:
  ```bash
  docker exec archetype-d35ec857-8976-4c3a-9-eos_1 Cli -p15 -c "configure terminal
  ip routing
  end
  write memory"
  ```

## Current State (Updated)

### Working
- Underlay connectivity: Can ping between hosts (10.14.23.36 <-> 10.14.23.11)
- VXLAN tunnel exists on both sides with same VNI 13446884
- Bridge `abr-13446884` exists on both sides with veth and VXLAN attached
- Container eth1 interfaces exist and have correct IPs
- EOS has IP routing enabled (verified `show ip route` has no "not enabled" warning)
- Layer 2 (ARP) works: `arping` from eos_1 gets replies from eos_2
- ARP traffic goes through VXLAN (VXLAN TX/RX counters increase)
- EOS Ethernet1 is correctly bound to Linux eth1 (MACs match)
- ICMP packets are transmitted from eos_1's eth1 (TX counter increases)
- Veth host-side receives packets from container (RX counter increases)
- FDB entries correct: dest MAC learned on VXLAN port, source MAC on veth port

### NOT Working
- ICMP ping fails (100% packet loss)
- VXLAN TX counter does NOT increase when pinging (only for ARP)
- Bridge receives packets from veth but does NOT forward them to VXLAN port

## Key Observation (Updated)
**Bridge is not forwarding unicast IP packets to VXLAN port!**

The problem is NOT with VXLAN encapsulation or underlay connectivity. The problem is with the Linux bridge:
- Broadcast frames (ARP): Bridge floods to all ports including VXLAN ✓
- Unicast frames (ICMP): Bridge receives but does NOT forward to VXLAN ✗

Path confirmed working:
1. Container eth1 sends packet (TX increases) ✓
2. Veth host-side receives (RX increases) ✓
3. Veth does not drop (no new drops after FDB is populated) ✓
4. **Bridge should forward to VXLAN port** ✗ (VXLAN TX stays same)

## Investigation Summary

### What we verified:
- FDB has correct entries:
  - `d2:e9:83:9f:05:ef dev v68847564h` (source MAC on veth)
  - `6e:82:4f:a5:87:50 dev vxlan13446884` (dest MAC on VXLAN)
- Both bridge ports in forwarding state (state=3)
- STP disabled on bridge (stp_state=0)
- unicast_flood=1, broadcast_flood=1, learning=1 on both ports
- isolated=0 on both ports
- neigh_suppress=0 on both ports
- nf_call_iptables=0 on bridge (iptables not called for bridged traffic)
- VLAN filtering disabled (vlan_filtering=0)
- No XDP/eBPF programs attached
- Destination MAC not present on any local interface

### Packet characteristics (verified with tcpdump):
- Source MAC: d2:e9:83:9f:05:ef
- Dest MAC: 6e:82:4f:a5:87:50
- Ethertype: 0x0800 (IPv4)
- No VLAN tags

### What remains unknown:
- Why bridge forwards broadcast (ARP ethertype 0x0806) but not unicast (IP ethertype 0x0800)
- Need kernel-level tracing (requires root) to investigate further

## Potential Root Causes

1. **Kernel bridge bug** - Possible bug in bridge FDB lookup for unicast forwarding
2. **Hidden eBPF/tc filter** - Some filter we couldn't detect without root
3. **Race condition** - FDB entry expiring or being modified during forwarding
4. **MTU mismatch effect** - Container eth1 at 1500, veth at 1450 (though small packets also fail)

## Next Steps
1. Get root access to run kernel tracing (`bpftrace`, `perf`, etc.)
2. Try recreating the bridge and VXLAN from scratch
3. Test with a different Linux bridge (OVS instead of native bridge)
4. Check if issue is reproducible with a simple test setup (no EOS, just namespaces)

## Remote Agent Access
```bash
# SSH to debian (keyboard-interactive auth)
ssh adrian@10.14.23.11
# Password: WWTwwt1!
# sudo password: WWTwwt1!

# Agent runs as systemd service
sudo systemctl restart archetype-agent
sudo journalctl -u archetype-agent -f
```

## Useful Commands
```bash
# Check VXLAN status
ip -d link show type vxlan
ip -s link show vxlan13446884

# Check bridge attachments
ip link show master abr-13446884

# Check container interfaces
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 ip link show
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 ip -s link show eth1

# Check EOS iptables
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 iptables -L EOS_FORWARD -n -v

# Test ARP (works even when ICMP doesn't)
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 arping -I eth1 -c 2 10.1.2.2

# Capture traffic
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 tcpdump -i eth1 -n

# API overlay status
curl http://10.14.23.11:8001/overlay/status?lab_id=d35ec857-8976-4c3a-9dbe-4b59cbf13f24

# Check FDB entries
/usr/sbin/bridge fdb show br abr-13446884

# Check bridge port states
cat /sys/class/net/vxlan13446884/brport/state
cat /sys/class/net/v68847564h/brport/state

# Monitor VXLAN TX during test
watch -n1 'ip -s link show vxlan13446884 | grep TX -A1'

# SSH to remote with expect (for keyboard-interactive)
expect -c '
spawn ssh adrian@10.14.23.11
expect "assword:"
send "WWTwwt1!\r"
expect "$ "
send "<command>\r"
expect "$ "
send "exit\r"
expect eof
'
```

## Quick Test Commands
```bash
# Test ARP (should work)
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 arping -I eth1 -c 2 10.1.2.2

# Test ICMP (now works with OVS fix)
docker exec archetype-d35ec857-8976-4c3a-9-eos_1 ping -c 2 10.1.2.2

# Check OVS overlay status
curl -s http://localhost:8001/overlay/status | jq .
ovs-vsctl show | grep -A15 "Bridge arch-ovs"
```

## Resolution

### Root Cause
Linux bridge was not forwarding unicast packets to the VXLAN port, even though:
- FDB entries were correct (dest MAC learned on VXLAN port)
- Both ports in forwarding state
- No iptables interference (nf_call_iptables=0)
- Broadcast (ARP) worked fine

This appears to be a Linux bridge bug or limitation with VXLAN ports.

### Fix
Replaced Linux bridge with Open vSwitch (OVS) for overlay networking.

**Changes made to `agent/network/overlay.py`:**
1. Use OVS bridge (`arch-ovs`) instead of creating per-link Linux bridges
2. Create VXLAN ports on OVS with `ovs-vsctl add-port ... type=vxlan`
3. Use VLAN tags (3000-4000 range) for per-link isolation
4. Set OVS fail-mode to `standalone` for normal L2 switching
5. Attach container veths to OVS with matching VLAN tags

**Key configuration:**
- OVS bridge: `arch-ovs`
- Fail mode: `standalone` (required - `secure` mode drops all traffic without flows)
- VLAN tags: VNI mapped to 3000-4000 range for isolation
- VXLAN port: `vxlan{vni}` with `options:key={vni}`

### Verification
```bash
# Ping now works
$ docker exec archetype-d35ec857-8976-4c3a-9-eos_1 ping -c 3 10.1.2.2
64 bytes from 10.1.2.2: icmp_seq=1 ttl=64 time=1.94 ms
64 bytes from 10.1.2.2: icmp_seq=2 ttl=64 time=0.676 ms
64 bytes from 10.1.2.2: icmp_seq=3 ttl=64 time=0.384 ms

# OVS shows correct setup
$ ovs-vsctl show | grep -A12 "Bridge arch-ovs"
    Bridge arch-ovs
        fail_mode: standalone
        Port arch-ovs
            Interface arch-ovs
                type: internal
        Port vxlan13446884
            tag: 3884
            Interface vxlan13446884
                type: vxlan
                options: {key="13446884", local_ip="10.14.23.36", remote_ip="10.14.23.11"}
        Port v68848fa7h
            tag: 3884
            Interface v68848fa7h
```

### Remaining Issue
cEOS still has `iptables -D EOS_FORWARD -i eth1 -j DROP` rule that blocks eth1 forwarding.
This is inside the container and needs to be removed after each container restart.
A future fix could add this to the cEOS startup handling.
