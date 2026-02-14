# NBD + LVM Auto-Activation Crash Prevention

## Status: IMPLEMENTED (install scripts updated, manual apply needed on existing hosts)

## Incident Summary

On Feb 13, 2026 at ~18:06, agent-01 (10.14.23.181) became unresponsive and required a hard poweroff. SSH connections hung indefinitely.

## Root Cause

A cascade triggered by qemu-nbd connecting a Cisco N9Kv qcow2 disk image to `/dev/nbd0`:

1. **16:44:03** - Something connected a 64GB N9Kv qcow2 to `/dev/nbd0` (5 partitions with NX-OS internal LVM)
2. **16:44:03** - udev/LVM auto-activation immediately activated 10 logical volumes (`panini_vol_grp` 9 LVs from nbd0p3, `app_vol_grp` 1 LV from nbd0p5), creating dm-2 through dm-12
3. **16:44:05** - NBD disconnected 2 seconds later, but 10 LVM volumes left dangling pointing at dead nbd0
4. **17:57-18:05** - Every dm device access generated cascading I/O errors, flooding kernel log
5. **18:05:37** - A second NBD connection (nbd1) with same disk caused duplicate PVID conflicts
6. **~18:06** - System unresponsive. sshd child processes entered D-state (uninterruptible sleep) on block device I/O

## Key Finding: Agent Code is NOT the Source

Searched entire codebase - **no references to qemu-nbd, nbd, or block device mounting exist**. The agent only uses:
- `qemu-img create -b <base>` for overlay disks
- `qemu-img create -f qcow2` for data volumes
- Standard `unlink()` for cleanup

The NBD connection was triggered by something external to our code (likely libvirt, udisksd, or a system service inspecting qcow2 files).

## Prevention Layers

### Layer 1: LVM Global Filter (Host-Level)
Prevent LVM from ever scanning NBD devices, even if something connects one.

```
# /etc/lvm/lvm.conf
global_filter = [ "r|/dev/nbd.*|", "a|.*|" ]
```

- **Scope**: Each agent host
- **Risk**: None - we don't use NBD-backed LVM intentionally
- **Effectiveness**: Prevents the entire cascade (no LVM activation = no dangling dm devices)

### Layer 2: Blacklist NBD Kernel Module (Host-Level)
Prevent the nbd kernel module from loading at all.

```
# /etc/modprobe.d/blacklist-nbd.conf
blacklist nbd
```

- **Scope**: Each agent host
- **Risk**: Low - we don't use NBD. Some VM images might load it via udev rules
- **Effectiveness**: Prevents qemu-nbd from functioning entirely

### Layer 3: Identify and Stop the NBD Trigger
Investigate what actually connected qemu-nbd:
- [ ] Check if libvirt auto-inspects qcow2 backing files
- [ ] Check if udisksd or other disk management daemons scan image files
- [ ] Check udev rules for qcow2/disk image triggers
- [ ] Review systemd services that might probe block devices
- [ ] Check if N9Kv's disk driver config (`sata`) triggers different libvirt behavior

### Layer 4: Deactivate Stale LVM on Agent Startup (Defense in Depth)
If LVM volumes from VM images do get activated somehow, clean them up:

```bash
# Deactivate any LVM VGs that came from nbd devices
vgchange -an panini_vol_grp 2>/dev/null
vgchange -an app_vol_grp 2>/dev/null
```

## Recommended Approach

Apply Layers 1 + 2 on all agent hosts. Layer 1 is the critical fix. Layer 2 is belt-and-suspenders. Layer 3 is investigative (nice to know but not blocking). Layer 4 is optional recovery logic.

## Hosts to Apply

- [x] agent-01: 10.14.23.181 (applied 2026-02-13)
- [ ] local dev host (needs sudo, apply manually)

## Implementation Notes

- Changes are host-level, not in our codebase
- LVM filter change takes effect immediately (no restart needed)
- NBD blacklist takes effect after reboot (or `rmmod nbd` if not in use)
- **DONE**: Added to `agent/install.sh` (after suspend disable, before Docker install)
- **DONE**: Added to `install.sh` (as `prevent_nbd_lvm_crash()` function, called during setup)
- Both scripts are idempotent (check before applying)

### Manual Apply Commands

For existing hosts that won't be reinstalled:

```bash
# Layer 1: LVM global filter
# NOTE: sed's 'a' command does NOT interpret \t as tab in single quotes.
# Use $'...' quoting to get a real tab character.
sudo sed -i '/^\\t.*global_filter.*nbd/d' /etc/lvm/lvm.conf  # clean broken lines
sudo sed -i $'/^\\s*# global_filter = /a\\\\\tglobal_filter = [ "r|/dev/nbd.*|", "a|.*|" ]' /etc/lvm/lvm.conf
# Verify: should show global_filter with nbd
sudo lvm dumpconfig devices/global_filter

# Layer 2: Blacklist NBD module
echo "blacklist nbd" | sudo tee /etc/modprobe.d/blacklist-nbd.conf

# Optional: unload if currently loaded
sudo rmmod nbd 2>/dev/null
```
