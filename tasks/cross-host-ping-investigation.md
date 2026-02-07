# Cross-Host Ping Failure Investigation

## Problem
Cross-host pings fail between cEOS containers on different compute hosts:
- ceos_1 (10.14.23.93) → ceos_2 (10.14.23.181): **100% packet loss**
- ceos_1 (10.14.23.93) → ceos_4 (10.14.23.181): **100% packet loss**
- Same-host pings (e.g., ceos_1 → ceos_3) work fine

**User reports this is a regression** — it used to work in past commits.

## Three Issues Identified

### Issue 1: MAC Address Bleeding
ceos_1's interface MAC (72:42:1f:f2:85:ef) appears on the VTEP (port 111) across **30 VLANs it shouldn't be on** — Docker plugin VLANs 100-131 and overlay VLANs 3034, 3096, 3285, 3644. Caused by cEOS L2 bridging through ceos_5 (a pure L2 switch) and the trunk VTEP carrying all VLANs. The VLAN crossover happens inside containers that act as bridges — invisible to OVS.

### Issue 2: ARP Reply Not Received
ceos_1 sends ARP requests on VLAN 3138 through the VXLAN tunnel. The requests reach the remote OVS (confirmed by datapath flows and VTEP TX stats). But **ceos_2's ARP reply never comes back** — zero incoming VLAN 3138 traffic from the tunnel. This is the blocking issue preventing all cross-host connectivity.

### Issue 3: MTU Asymmetry
Local VTEP tenant_mtu=**1450** (path MTU 1500), remote VTEP tenant_mtu=**8950** (path MTU 9000). The path MTU discovery is buggy — it tests from sender side only, and IP fragmentation on the reply path masks the asymmetry. Won't affect ARP (42 bytes) but will break data transfer for packets >1450 bytes even after ARP is fixed.

## Environment

| Component | Value |
|-----------|-------|
| Lab ID | e844e435-fde4-4d95-98c3-4fa8966362f9 |
| Local agent | 10.14.23.93:8001 (b39a05df), HEAD (40be3a5) |
| Remote agent | 10.14.23.181:8001 (614fc24c), commit 2acf0c4 (20 behind) |
| OVS bridge | arch-ovs, fail_mode=standalone, single flow: priority=0 actions=NORMAL |
| VTEP (local) | vtep-10-14-23-1, ofport=111, VNI=194382 |
| VTEP (remote) | vtep-10-14-23-9, VNI=194382 |
| OVS version | 3.5.0 (local) |

### Container Placement
- **Local (10.14.23.93)**: ceos_1, ceos_3, ceos_5 + 8 libvirt VMs (vnet9-16, VLANs 2000-2007)
- **Remote (10.14.23.181)**: ceos_2, ceos_4

### Cross-Host Link VLAN Assignments (confirmed matching on both sides)
| Link | VLAN | Local Side | Remote Side |
|------|------|------------|-------------|
| ceos_1:eth3 ↔ ceos_2:eth1 | 3138 | ceos_1:eth3 | ceos_2:eth1 |
| ceos_1:eth2 ↔ ceos_4:eth1 | 3644 | ceos_1:eth2 | ceos_4:eth1 |
| ceos_3:eth3 ↔ ceos_2:eth3 | 3034 | ceos_3:eth3 | ceos_2:eth3 |
| ceos_3:eth2 ↔ ceos_4:eth3 | 3096 | ceos_3:eth2 | ceos_4:eth3 |
| ceos_5:eth2 ↔ ceos_2:eth4 | 3285 | ceos_5:eth2 | ceos_2:eth4 |

### Same-Host Link VLANs
| Link | VLAN |
|------|------|
| ceos_1:eth1 ↔ ceos_3:eth1 | 3917 |
| ceos_1:eth4 ↔ ceos_5:eth1 | 3920 |
| ceos_1:eth5 ↔ ceos_5:eth? | 3919 |

### MTU Mismatch
- Local VTEP tenant_mtu: **1450** (path_mtu=1500, local interface enp3s0 MTU=1500)
- Remote VTEP tenant_mtu: **8950** (path_mtu=9000, remote may have jumbo frames)
- MTU discovery tests 9000→4000→1500 and takes first success
- Bug: ping-based MTU discovery doesn't properly test the reverse path (IP fragmentation makes the reply succeed even when outgoing MTU is lower)
- However, ARP packets are ~42 bytes, so MTU shouldn't affect ARP

## Confirmed Working
- VLAN tags match perfectly on both sides (all 5 cross-host links)
- VTEP VNI matches (194382 on both)
- VTEP tunnel is bidirectional at L3 level (rx=152K pkts, tx=155K pkts)
- Local VTEP properly in trunk mode (tag=[], trunks=[], vlan_mode=[])
- OVS flow table correct on both sides (NORMAL action)
- Same-host ping works (ceos_1→ceos_3 via VLAN 3917, 100% success)
- ofproto/trace shows correct packet processing for both outgoing and incoming
- Remote containers running and ready (ceos_2 and ceos_4 both report is_ready=true)
- STP disabled on OVS bridge (both sides)

## Key Findings

### Finding 1: ARP Request Goes Out, Reply Never Comes Back
After flushing FDB and sending a single ping from ceos_1 to 10.1.2.2:
- **Outgoing datapath flow confirmed**: ARP broadcast (src=72:42:1f:f2:85:ef, dst=broadcast) on VLAN 3138 sent through VTEP tunnel
- **Zero incoming flows on VLAN 3138**: No ARP reply from ceos_2 arrives through the tunnel
- **FDB shows only local MAC**: port 60/VLAN 3138 = 72:42:1f:f2:85:ef (ceos_1's MAC). No ceos_2 MAC.

### Finding 2: Massive MAC Leakage Through VTEP
ceos_1's interface MAC (72:42:1f:f2:85:ef) appears on the VTEP (port 111) across **30 different VLANs**:
- Docker plugin VLANs: 100-131 (remote agent's container VLANs)
- Overlay VLANs: 3034, 3096, 3285, 3644 (all overlay VLANs EXCEPT 3138)

### Finding 3: All Local Container Traffic Floods Through VTEP
886 outgoing tunnel datapath flows from 43 unique local ports — **every container port** (including non-cross-host) sends multicast (IPv6 mDNS) through the trunk VTEP. This is expected behavior for a trunk port but creates unnecessary traffic load.

### Finding 4: cEOS Container Configurations
**ceos_1** (checked locally):
- Ethernet1,2,3,5: `no switchport` + IP address (L3 routed)
- Ethernet4: switchport (default, L2) — link to ceos_5
- Ethernet6-16: switchport (default, unused)
- `no ip routing`, `spanning-tree mode mstp`

**ceos_3** (checked locally):
- Ethernet1,2,3: `no switchport` + IP address (L3 routed)
- Ethernet4-16: switchport (default, unused)
- `no ip routing`

**ceos_5** (checked locally):
- **ALL interfaces (Ethernet1-16): switchport** (no `no switchport`, no IPs)
- Pure L2 switch — bridges between ALL interfaces
- `no ip routing`

**ceos_2, ceos_4**: Cannot check configuration (remote agent, no SSH access, config extraction endpoint not available on older agent version)

### Finding 5: L2 Bridge Chain Causes Cross-VLAN MAC Leakage
ceos_5 is a pure L2 switch that bridges between all interfaces. This creates a cross-VLAN path:
1. Traffic enters ceos_5:eth1 (OVS VLAN 3920, same-host from ceos_1:eth4)
2. ceos_5 bridges internally to eth2 (OVS VLAN 3285, cross-host to ceos_2:eth4)
3. On remote side, if ceos_2:eth4 is also switchport, it bridges to eth6-16 (Docker plugin VLANs 100-131)
4. These frames flood through remote VTEP back to local OVS

The VLAN crossover occurs INSIDE the container (invisible to OVS). Each OVS port is on its own access VLAN, but the container bridge connects them at L2.

### Finding 6: Code Diff Between Agent Versions
Only one commit changed `agent/network/overlay.py` between versions: MTU preservation settings (no VTEP logic changes). `docker_plugin.py` unchanged. `main.py` had significant changes to same-host link creation (provider-agnostic refactor for libvirt) but cross-host overlay code unchanged.

### Finding 7: Agent Version Mismatch
Remote agent is 20 commits behind HEAD. Only 4 commits affected agent/ directory. Most relevant: 40be3a5 (libvirt VM interface discovery fix). The cross-host overlay code path is functionally identical between versions.

## Unsolved Questions

### Q1: Why doesn't ceos_2's ARP reply come back through the tunnel?
This is THE critical question. The ARP request reaches the remote OVS on VLAN 3138. It should be delivered to ceos_2:eth1. ceos_2 should reply. The reply should go back through VTEP. But no reply arrives.

Possible causes:
1. **ceos_2 has no IP on Ethernet1** (or wrong IP) — can't verify without remote config access
2. **Remote OVS FDB has wrong entry for ceos_1's MAC** — MAC 72:42:1f:f2:85:ef might be learned on a local (remote-side) port instead of the VTEP, causing the reply to go to the wrong port
3. **ceos_2:eth1 is in switchport mode** (not `no switchport`) — unlikely given the pattern from other devices, but can't verify
4. **Remote VTEP configuration issue** — can't inspect directly, but overlay/status shows it exists with correct VNI
5. **cEOS system MAC collision** — all cEOS interfaces use the same system MAC. If ceos_2 bridges this MAC between interfaces, the remote FDB may associate it with a local port instead of the VTEP

### Q2: Why do all overlay VLANs (except 3138) show reflected MACs?
The ceos_1 interface MAC appears on the VTEP on VLANs 3034, 3096, 3285, 3644 but NOT on 3138. This pattern suggests the remote cEOS containers (acting as L2 bridges or switches) are propagating the frame across their interfaces, which exit on different overlay VLANs.

### Q3: Is the broadcast loop through ceos_5 contributing?
ceos_5 (pure L2 switch) bridges between eth1 (VLAN 3920) and eth2 (VLAN 3285). If the remote side also bridges through switchport interfaces, broadcasts could loop (but each hop changes VLAN, so OVS sees it as different traffic). The loop would be: ceos_1:eth4 → ceos_5 → ceos_2:eth4 → ceos_2 bridge → other VLANs → tunnel → local.

### Q4: What role do libvirt VMs play?
8 VMs on local host with VLANs 2000-2007. They don't participate in the cEOS overlay topology, but they add traffic through the trunk VTEP (their multicast/broadcast floods through) and add ports to the OVS bridge (58 total ports).

### Q5: MTU asymmetry impact?
Local tenant_mtu=1450, remote=8950. Shouldn't affect ARP (42 bytes) but could cause issues with larger packets once connectivity is established.

## Next Steps to Try

1. **Get ceos_2's running config**: Try updating the remote agent to HEAD, or use the API to extract configs, or try direct docker exec via remote agent console endpoint
2. **Check remote FDB**: Need to see what the remote OVS FDB says for VLAN 3138. Try adding a diagnostic endpoint or use the console
3. **Test with ceos_5 disconnected**: Remove the L2 switch from the topology to eliminate the bridge chain. If ping works without ceos_5, the L2 bridging is the cause.
4. **Try restricting VTEP trunks**: Set VTEP trunks to only overlay VLANs (3034,3096,3138,3285,3644) on BOTH sides. This was tried on local only (didn't fix the issue — reflections still occur on overlay VLANs).
5. **Check iptables on remote**: cEOS adds iptables DROP rules for data plane traffic. The remote agent might have these active.
6. **Update remote agent to HEAD**: The 20-commit gap might include relevant fixes. At minimum, newer endpoints would allow better diagnostics.
7. **Try static ARP**: Configure static ARP entries on ceos_1 and ceos_2 to bypass ARP resolution and test if IP/ICMP flows work directly.
8. **Check cEOS iptables**: Run `docker exec <ceos_2> iptables -L -n` on the remote to check for the EOS_FORWARD DROP rule
