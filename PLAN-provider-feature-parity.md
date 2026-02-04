# Provider Feature Parity Plan

This document outlines the implementation plan to achieve feature parity between DockerProvider and LibvirtProvider in Archetype.

## Current State Summary

| Feature | Docker | Libvirt | Priority |
|---------|--------|---------|----------|
| Lab discovery | ✅ | ✅ | High |
| Orphan cleanup | ✅ | ✅ | High |
| Post-boot commands | ✅ | ✅ | Medium |
| Stale network recovery | ✅ | ✅ | Low |
| Config extraction (non-cEOS) | ✅ | N/A | Medium |

**Phase 1 completed** (commit 518bdcb): Lab discovery and orphan cleanup for LibvirtProvider

**Phase 2 completed**: Post-boot commands for Libvirt, generalized config extraction for Docker

**Phase 3 completed**: Stale network recovery for Libvirt (VLAN allocation persistence and recovery)

---

## Phase 1: Operational Hygiene (High Priority)

These features are critical for production reliability - they prevent resource leaks and enable recovery from failures.

### 1.1 Lab Discovery for Libvirt

**Goal**: Enable LibvirtProvider to discover running VMs that belong to Archetype labs.

**Current Docker Implementation** (`agent/providers/docker.py`):
- `discover_labs()` lists all containers with `archetype.lab_id` label
- Groups containers by lab_id and returns lab metadata
- Used by API for reconciliation and orphan detection

**Libvirt Implementation Plan**:

1. **Domain Naming Convention** (already exists):
   - VMs are named `arch-{lab_id}-{node_name}`
   - Domain XML contains metadata with `archetype:device_kind`

2. **Add `discover_labs()` method**:
   ```python
   def discover_labs(self) -> List[Dict[str, Any]]:
       """Discover all Archetype-managed VMs on this host."""
       labs = {}

       # List all domains (running and stopped)
       result = subprocess.run(
           ["virsh", "list", "--all", "--name"],
           capture_output=True, text=True
       )

       for domain_name in result.stdout.strip().split('\n'):
           if not domain_name.startswith('arch-'):
               continue

           # Parse: arch-{lab_id}-{node_name}
           parts = domain_name.split('-', 2)
           if len(parts) < 3:
               continue

           lab_id = parts[1]
           node_name = parts[2]

           # Get domain state
           state = self._get_domain_state(domain_name)

           if lab_id not in labs:
               labs[lab_id] = {
                   'lab_id': lab_id,
                   'nodes': [],
                   'node_count': 0
               }

           labs[lab_id]['nodes'].append({
               'name': node_name,
               'domain': domain_name,
               'state': state
           })
           labs[lab_id]['node_count'] += 1

       return list(labs.values())
   ```

3. **Files to modify**:
   - `agent/providers/libvirt.py`: Add `discover_labs()` method
   - `agent/providers/base.py`: Add `discover_labs()` to base class interface (optional abstract method)

4. **Testing**:
   - Deploy a VM lab, verify discovery returns correct lab_id and nodes
   - Stop some VMs, verify they still appear in discovery
   - Test with multiple labs running simultaneously

---

### 1.2 Orphan Cleanup for Libvirt

**Goal**: Enable cleanup of VMs from failed or abandoned labs.

**Current Docker Implementation**:
- `cleanup_orphan_containers()` removes containers for labs not in the "known labs" list
- Cleans up associated volumes and networks
- Called during reconciliation or manual cleanup

**Libvirt Implementation Plan**:

1. **Add `cleanup_orphan_domains()` method**:
   ```python
   def cleanup_orphan_domains(self, known_lab_ids: List[str]) -> Dict[str, Any]:
       """Remove VMs belonging to labs not in known_lab_ids."""
       cleaned = {'domains': [], 'disks': [], 'vlans': []}

       discovered = self.discover_labs()

       for lab in discovered:
           if lab['lab_id'] in known_lab_ids:
               continue

           logger.info(f"Cleaning orphan lab: {lab['lab_id']}")

           for node in lab['nodes']:
               domain_name = node['domain']

               # Force stop if running
               subprocess.run(
                   ["virsh", "destroy", domain_name],
                   capture_output=True
               )

               # Undefine domain
               subprocess.run(
                   ["virsh", "undefine", domain_name, "--remove-all-storage"],
                   capture_output=True
               )

               cleaned['domains'].append(domain_name)

           # Clean up VLAN allocations for this lab
           self._cleanup_lab_vlans(lab['lab_id'])

       return cleaned
   ```

2. **Add VLAN tracking cleanup**:
   - The `vlan_allocations` dict tracks VLANs per lab
   - Add `_cleanup_lab_vlans(lab_id)` to free allocated VLANs

3. **Add disk cleanup**:
   - Overlay disks at `{workspace}/disks/{lab_id}/`
   - Data volumes at `{workspace}/data/{lab_id}/`
   - Add filesystem cleanup after domain removal

4. **Files to modify**:
   - `agent/providers/libvirt.py`: Add `cleanup_orphan_domains()` method
   - `agent/main.py`: Add endpoint to trigger orphan cleanup (or extend existing)

5. **Testing**:
   - Deploy lab, then call cleanup with empty known_lab_ids
   - Verify domains removed, disks deleted, VLANs freed
   - Test partial failure (some VMs fail to delete)

---

## Phase 2: Operational Features (Medium Priority)

### 2.1 Post-Boot Commands for Libvirt

**Goal**: Execute vendor-specific commands after VM boot (similar to Docker's post-boot exec).

**Current Docker Implementation**:
- `_run_post_boot_commands()` executes commands via `docker exec`
- Used for cEOS iptables fixes, ZTP disable, etc.
- Commands defined in `VendorConfig.post_boot_commands`

**Libvirt Implementation Plan**:

1. **Leverage existing serial console infrastructure**:
   - `SerialConsoleExtractor` in `agent/serial_console.py` already handles:
     - Console connection via pexpect
     - Login automation
     - Command execution with prompt detection

2. **Add `post_boot_commands` to VM vendor configs**:
   ```python
   # In vendors.py, extend VM device configs
   "cisco-iosv": VendorConfig(
       # ... existing fields ...
       post_boot_commands=[
           "terminal length 0",
           "configure terminal",
           "no ip domain-lookup",  # Disable DNS lookups that slow down CLI
           "end"
       ]
   )
   ```

3. **Add `_run_post_boot_commands()` to LibvirtProvider**:
   ```python
   async def _run_post_boot_commands(
       self,
       domain_name: str,
       node_name: str,
       vendor_config: VendorConfig
   ) -> bool:
       """Execute post-boot commands via serial console."""
       if not vendor_config.post_boot_commands:
           return True

       from serial_console import SerialConsoleExtractor

       extractor = SerialConsoleExtractor(
           domain_name=domain_name,
           vendor_config=vendor_config
       )

       try:
           if not await extractor.connect():
               logger.warning(f"Could not connect to {node_name} for post-boot commands")
               return False

           for cmd in vendor_config.post_boot_commands:
               await extractor.send_command(cmd)

           return True
       finally:
           await extractor.disconnect()
   ```

4. **Integrate into deploy flow**:
   - Call `_run_post_boot_commands()` after readiness detection
   - Add timeout handling (commands may hang)
   - Log command output for debugging

5. **Files to modify**:
   - `agent/vendors.py`: Add `post_boot_commands` to VM vendor configs
   - `agent/providers/libvirt.py`: Add `_run_post_boot_commands()` method
   - `agent/serial_console.py`: Add `send_command()` method if not present

6. **Testing**:
   - Deploy IOSv with post-boot commands
   - Verify commands executed via `show running-config`
   - Test timeout handling for hung commands

---

### 2.2 Config Extraction for Non-cEOS Containers

**Goal**: Enable configuration extraction from container-based devices beyond cEOS.

**Current State**:
- Docker only extracts cEOS configs via `FastCli -p 15 -c "show running-config"`
- Other containers (SR Linux, FRR, etc.) have no extraction

**Implementation Options**:

#### Option A: Vendor-Specific Exec Commands (Recommended)

1. **Add extraction config to container VendorConfig**:
   ```python
   "nokia-srlinux": VendorConfig(
       # ... existing fields ...
       config_extraction=ConfigExtractionSettings(
           method="exec",  # Use docker exec
           command="sr_cli -d 'info flat'",  # SR Linux CLI command
           timeout=30
       )
   ),
   "frr": VendorConfig(
       config_extraction=ConfigExtractionSettings(
           method="exec",
           command="vtysh -c 'show running-config'",
           timeout=15
       )
   )
   ```

2. **Generalize `_extract_all_ceos_configs()`**:
   ```python
   def _extract_container_config(
       self,
       container_name: str,
       vendor_config: VendorConfig
   ) -> Optional[str]:
       """Extract config from any container using vendor-specific method."""
       extraction = vendor_config.config_extraction
       if not extraction or extraction.method != "exec":
           return None

       result = subprocess.run(
           ["docker", "exec", container_name] + extraction.command.split(),
           capture_output=True,
           text=True,
           timeout=extraction.timeout
       )

       if result.returncode == 0:
           return result.stdout
       return None
   ```

3. **Update `_extract_all_ceos_configs()` to be generic**:
   - Rename to `_extract_all_container_configs()`
   - Iterate over all containers, check for `config_extraction` setting
   - Use vendor-specific command instead of hardcoded FastCli

#### Option B: gNMI/NETCONF for Modern Devices

Some devices support programmatic config retrieval:
- SR Linux: gNMI (`gnmic get`)
- Juniper cRPD: NETCONF
- Arista cEOS: eAPI (already has FastCli alternative)

This would require:
- Adding gNMI/NETCONF client dependencies
- More complex extraction logic
- Device-specific data transformation

**Recommendation**: Start with Option A (exec commands), add gNMI later for devices that need it.

**Files to modify**:
- `agent/vendors.py`: Add `ConfigExtractionSettings` to container vendors
- `agent/providers/docker.py`: Generalize extraction method

---

## Phase 3: Edge Case Handling (Low Priority) ✅ COMPLETED

### 3.1 Stale Network Recovery for Libvirt ✅

**Goal**: Handle edge case where lab is redeployed but some VMs still have network state.

**Implementation Summary**:

1. **VLAN Allocation Persistence** ✅
   - Added `_vlans_dir()` - directory for VLAN allocation files
   - Added `_save_vlan_allocations()` - persists allocations to JSON
   - Added `_load_vlan_allocations()` - loads allocations from JSON
   - Added `_remove_vlan_file()` - cleanup when lab is destroyed
   - Updated `_allocate_vlans()` to save after allocation

2. **Stale Network Recovery** ✅
   - Added `_ovs_port_exists()` - checks if OVS port exists
   - Added `_recover_stale_network()` - loads and validates previous allocations
   - Validates allocations against existing libvirt domains
   - Discards stale allocations for domains that no longer exist

3. **Deploy Flow Integration** ✅
   - Recovery called at start of `deploy()` before creating nodes
   - `_allocate_vlans()` now checks for existing allocations from recovery
   - Returns recovered VLANs instead of allocating new ones

4. **Cleanup Integration** ✅
   - `destroy()` removes VLAN file when lab is destroyed
   - `cleanup_orphan_domains()` removes VLAN files for orphan labs

**Files modified**:
- `agent/providers/libvirt.py`: All persistence and recovery methods

---

## Phase 4: Future Enhancements

These are not strictly parity issues but would improve overall functionality:

### 4.1 Unified Provider Interface

- Extract common patterns into base class
- Standardize method signatures
- Add capability discovery API

### 4.2 Provider-Agnostic Config Extraction

- Abstract extraction interface
- Support mixed container/VM labs
- Unified extraction endpoint already exists

### 4.3 Live Migration Support (Libvirt)

- VM live migration between hosts
- Not possible with containers (would need checkpoint/restore)

---

## Implementation Order

| Phase | Feature | Estimated Effort | Dependencies |
|-------|---------|------------------|--------------|
| 1.1 | Lab discovery (Libvirt) | Small | None |
| 1.2 | Orphan cleanup (Libvirt) | Small | 1.1 |
| 2.1 | Post-boot commands (Libvirt) | Medium | serial_console.py |
| 2.2 | Config extraction (non-cEOS) | Medium | None |
| 3.1 | Stale network recovery (Libvirt) | Small | None |

---

## Testing Strategy

### Unit Tests
- Mock virsh/docker commands
- Test parsing of domain lists
- Test VLAN allocation/deallocation

### Integration Tests
- Deploy test labs with both providers
- Verify discovery returns correct data
- Test orphan cleanup doesn't affect active labs
- Test post-boot command execution

### Manual Testing
- Deploy mixed container/VM lab
- Kill agent mid-deploy, verify recovery
- Test cleanup of orphaned resources

---

## Files Reference

| File | Changes |
|------|---------|
| `agent/providers/libvirt.py` | discover_labs, cleanup_orphan_domains, post-boot commands, stale recovery |
| `agent/providers/docker.py` | Generalize config extraction |
| `agent/providers/base.py` | Add optional abstract methods |
| `agent/vendors.py` | Add post_boot_commands and config_extraction to more vendors |
| `agent/main.py` | Add cleanup endpoint if needed |
| `agent/serial_console.py` | Add send_command method |
