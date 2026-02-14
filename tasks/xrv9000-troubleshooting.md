# IOS-XRv 9000 Boot Troubleshooting

## The Problem

XRv9000 VM boots its Host OS (Wind River Linux) successfully, but **XR control plane never fully installs**. Two issues were identified:

### Issue 1: Spirit grub.cfg failure (FIXED)
Libvirt auto-created a second pflash device (NVRAM) that confused Spirit's device enumeration during early boot. vrnetlab uses a single read-only pflash (OVMF CODE only). Fix: use `<qemu:commandline>` to bypass libvirt firmware auto-selection.

### Issue 2: XR RPM payload corruption (ROOT CAUSE FOUND)
After fixing pflash, Spirit successfully installs sysadmin packages and boots Calvados. The XR packages exist as an ISO inside the install repo (`/install_repo/gl/xr/xrv9k-xr-25.1.1`, 552MB ISO). During first boot, the install agent extracts 25 RPMs from this ISO and installs them. **One RPM is corrupted in the base qcow2 image:**

```
xrv9k-iosxr-infra-1.0.0.0-r2511.x86_64.rpm: DIGESTS signatures NOT OK
Payload SHA256 digest: BAD
Expected: e19d706e9e1031313728fdeda93de616c08b0c93f4992c57ccbd5924a0bf8690
Actual:   4ef2896f8b901850f019cb57a15e3e4e02488b499b8e87666f9e66a47b139726
```

All other 24 RPMs verify correctly. The install agent retries once, fails again, then aborts:
```
Failed to install iso rpms
failed to unpack RPM pkgs to /install/tmp/partprep for card 1
ERROR! instagt_prep_sdr_pkg_and_part_ready failed to prep partition for XR VM
```

This means XR packages are never installed, so `show vm` only shows sysadmin.

### Fix for Issue 2
**Re-download the qcow2 image.** The current image has data corruption in one compressed cluster affecting the `xrv9k-iosxr-infra` RPM payload. `qemu-img check` passes (structure is fine), but the data within the 83.24% compressed clusters has a bad sector.

Current image:
- Path: `/var/lib/archetype/images/xrv9k-fullk9-x-25.1.1.qcow2`
- Size: 2,002,911,232 bytes (1.87GB actual, 64GB virtual)
- MD5: `74e7e943bc4590753e9a2fcfd6bfd4fc`
- Compression: 83.24% compressed clusters, 86.65% fragmented

After replacing the image, destroy the existing VM and overlay disk, then redeploy.

## Current State (Feb 14, 2026)

### What Works (after pflash fix)
- GRUB loads grub.cfg from `(hd0,gpt4)/EFI/BOOT/grub.cfg` (size 717)
- Host OS (Wind River Linux) boots fully
- Spirit completes sysadmin installation (no more grub.cfg failure)
- Calvados sysadmin VM boots with all services operational
- `show platform` shows `0/RP0 OPERATIONAL` for both HW and SW state
- Calvados login works (cisco/cisco on pts/2 and pts/3)
- TCP serial (serial0) connects
- Single pflash (OVMF CODE only, read-only) — no NVRAM, matching vrnetlab
- `<smm state='off'/>` in features
- i440fx machine type with virtio-blk-pci disk

### What Doesn't Work (corrupted image)
- XR control plane never installs due to corrupted RPM in base qcow2
- `show vm` from sysadmin only shows sysadmin, no XR VM
- `show install active` shows only `xrv9k-sysadmin-25.1.1`
- Install logs at `/var/log/install/inst_agent.log` show `Payload SHA256 digest: BAD`
- Serial0 has no XR CLI output (XR never starts)

### Previous Root Cause (RESOLVED)
Spirit failed with "Update grub.cfg failed" because libvirt auto-created a second pflash device (NVRAM). With `<os firmware='efi'>` or `<loader type='pflash'>`, libvirt 11.3+ unconditionally creates NVRAM as pflash1, adding an unexpected flash device that confused Spirit's device enumeration.

**Fix**: Use `<qemu:commandline>` to inject OVMF CODE as a raw `-drive if=pflash` argument, bypassing libvirt firmware auto-selection entirely. Also set `efi_vars="stateless"` in vendor config and add `<smm state='off'/>` to features.

### Secondary Issue: calvados_launch.sh grub.cfg update (non-fatal)
The dmesg error `System recovery: Update grub.cfg failed` comes from `/opt/cisco/hostos/bin/calvados_launch.sh` (line 73), which hardcodes `mount -o ro /dev/sda4 ${TMPMNT}` to mount the EFI partition. With virtio-blk-pci, the disk is `/dev/vda*` not `/dev/sda*`, so this mount fails. The `mv` then fails because the mount point is empty.

**This is non-fatal** — the script continues to `calvados_start_pd` and Calvados boots regardless. The grub.cfg recovery entry update is a nice-to-have, not a boot requirement. vrnetlab has the same `/dev/sda4` hardcoding issue but it doesn't prevent boot.

Also: `pi_update_efi_grub()` in `pd-functions:1129` hardcodes `/dev/sda4` for signed grub check — same non-fatal issue.

## Dead Ends (investigated, not the fix)

### Nested KVM / CPU migratable (Feb 14)
**Hypothesis**: XR VM needs nested KVM; `migratable='on'` strips VMX from guest CPU.
**What we did**: Changed CPU XML to `migratable='off'`, checked `/dev/kvm` inside guest, verified host nested=1.
**Result**: Host is AMD Ryzen 7 5700G (SVM, not VMX). SVM IS visible inside guest (count=4). But guest kernel (Wind River Linux) has `CONFIG_KVM is not set` — KVM completely disabled. XRv9000 uses LXC containers for Calvados and XR, not nested VMs. The migratable change is harmless but irrelevant.
**Commit**: `ec178ea`

### TCP Serial Console (Feb 14)
**Hypothesis**: XR CLI lives on a TCP telnet serial port; switching from PTY serial would surface it.
**What we did**: Added `serial_type="tcp"` to vendor config, changed serial0 from PTY to TCP telnet, set `readiness_probe="none"` (TCP is single-connection). Also fixed XML generation to keep all 4 serial ports when using TCP (was only creating serial0).
**Result**: TCP serial works — telnet bridge connects and shows the same WRL boot output. But no XR CLI appears because **the problem isn't the serial port type — it's that XR never starts**. Spirit fails before XR is even installed.
**Commits**: `0a204ea`, `553c0b8`
**Status**: TCP serial is harmless and correct for when XR eventually works, so leaving it in place.

## Commits Made
- `a2ba969` - fix(agent): use QEMU commandline passthrough for stateless EFI pflash
- `2255a9d` - fix(agent): align XRv9000 EFI config with vrnetlab (stateless pflash, SMM off)
- `ec178ea` - fix(agent): set CPU migratable=off for host-passthrough (harmless, not the fix)
- `553c0b8` - fix(agent): add remaining PTY serial ports for TCP serial VMs
- `0a204ea` - fix(agent): use TCP telnet serial for IOS-XRv 9000 console
- `ff3732b` - fix(agent): undefine stale shut-off libvirt domains instead of reusing them
- `32c5f11` - fix(agent): add dummy NICs and SMP topology for IOS-XRv 9000 boot
- `4ded870` - fix(agent): use i440fx machine type for IOS-XRv 9000
- `e45a6ef` - fix(agent): add SMBIOS product identification for IOS-XRv 9000

## Lab Details
- Lab ID: `e844e435-fde4-4d95-98c3-4fa8966362f9`
- Domain: `arch-e844e435-fde4-4d95-9-cisco_iosxr_10`
- Agent-01: `614fc24c` at `10.14.23.181:8001`
- Image: `/var/lib/archetype/images/xrv9k-fullk9-x-25.1.1.qcow2` (64GB virtual, 1.87GB actual)
- Auth: `admin@example.com` / `changeme123`

## Disk Layout (GPT)
| Partition | Size | Type | Purpose |
|-----------|------|------|---------|
| vda1 | 900.3M | Linux filesystem | Host OS boot? |
| vda2 | 12.2G | Linux filesystem | Install repo (ISO images) |
| vda3 | 44.1G | Linux filesystem | LVM (panini_vol_grp) |
| vda4 | 19.1M | EFI System | GRUB + grub.cfg (FAT32, label "EFS") |
| vda5 | 3.8G | Linux filesystem | Unknown |

## LVM Layout (panini_vol_grp on vda3)
- host_lv0 (1G) - Host OS root (/)
- host_data_scratch_lv0 (2.8G) - scratch
- host_data_log_lv0 (948M) - logs
- host_data_config_lv0 (236M) - config
- calvados_lv0 (3G) - Calvados root
- calvados_data_lv0 (2.8G) - Calvados data
- ssd_disk1_calvados_1 (5.3G) - /misc/disk1
- ssd_disk1_xr_1 - XR disk (not yet provisioned)
- app_vol_grp-app_lv0 - App volume

## grub.cfg Content (on EFI partition)
```
set default=0
serial --unit=0 --speed=115200
terminal_input console
terminal_output serial
set timeout=2
menuentry "System Host OS" {
    echo "Booting from Disk.."
    search.fs_label HostOs root
    linux /boot/bzImage root=/dev/panini_vol_grp/host_lv0 ... platform=xrv9k ...
    initrd /boot/initrd.img
}
```
Note: Only Host OS entry. No XR entry (Spirit never added it).

## Key Spirit Scripts (on Host OS root at /tmp/hostos/)
- `/etc/systemd/system/spirit_sysinit.service` - Early boot setup
- `/etc/systemd/system/spirit-start.service` - Main Spirit service
- `/etc/init.d/spirit` - Main Spirit init script
- `/etc/init.d/spirit-functions` - Spirit helper functions
- `/etc/init.d/pi_grub_and_menu_lst_update.sh` - GRUB update logic
- `/etc/init.d/warmboot_upgrade_kernel.sh` - Warmboot kernel upgrade (uses `/dev/ieusb4`)
- `/usr/bin/spirit_sysinit.sh` - Sysinit script

## vrnetlab Comparison

| Setting | Our Setup | vrnetlab (containerlab) | Status |
|---------|-----------|------------------------|--------|
| Machine type | pc-i440fx-10.0 | pc | OK (both i440fx) |
| CPU | host-passthrough,migratable=off | host,+ssse3,+sse4.1,+sse4.2,+x2apic | OK |
| SMP | sockets=1,cores=4 | sockets=1,cores=4 | FIXED (was sockets=4) |
| RAM | 20GB | 24GB (clab) / 16GB (orig) | OK |
| Disk | virtio-blk-pci | IDE (old) / virtio-blk-pci (25.x) | OK (both virtio for 25.x) |
| OVMF | Single pflash via qemu:commandline | Single OVMF.fd via -drive if=pflash | FIXED (was 2 pflash) |
| SMM | smm=off | smm=off | FIXED (was not set) |
| Dummy NICs | ctrl-dummy + dev-dummy | ctrl-dummy + dev-dummy (tap) | FIXED |
| Boot | strict=on | order=c | OK |
| Serial | serial0=TCP, serial1-3=PTY | 4x TCP telnet | OK |
| /dev/sda4 hardcode | same as vrnetlab (non-fatal) | same (non-fatal) | Known, non-blocking |

## Accessing Host OS from Calvados
- Calvados = LXC container on Host OS, shares kernel
- `dmesg` from calvados shows Host OS kernel messages
- Host OS root mounted at `/dev/mapper/panini_vol_grp-host_lv0` → mount at `/tmp/hostos/`
- EFI partition at `/dev/vda4` → mount at `/tmp/testefi/` (writable!)
- Can't SSH to Host OS (no password known, sshpass needed)
- Serial0 has no getty (no login prompt after boot)

## Kernel Command Line (from grub.cfg)
```
BOOT_IMAGE=/boot/bzImage root=/dev/panini_vol_grp/host_lv0
__hw_profile=vpe intel_iommu=on pcie_aspm=off platform=xrv9k
isolcpus=2-3 default_hugepagesz=1G hugepagesz=1G hugepages=6
elevator=noop boardtype=RP vmtype=hostos ima_tcb ima_appraise=log
evm=off console=ttyS0,115200 prod=1 crashkernel=400M@0
bigphysarea=10M quiet pci=assign-busses aer=off
pci=hpmemsize=0M,hpiosize=0M enable_efi_vars=0
pcie_port_pm=off net.ifnames=0
```

Key observations:
- `hugepages=6` (6x 1GB hugepages) - requires 6GB contiguous RAM
- `isolcpus=2-3` - CPUs 2-3 reserved for XR
- `console=ttyS0,115200` - serial console on port 0
- `prod=1` - production mode
- `ima_tcb ima_appraise=log` - IMA in log mode (not enforcing)
- `enable_efi_vars=0` - EFI variables disabled

## XR Installation Flow
1. Host OS boots (Wind River Linux), `spirit_sysinit.service` runs early boot setup
2. `calvados_launch.sh` starts — tries to update grub.cfg (fails non-fatally), then starts Calvados LXC
3. Inside Calvados, `inst_agent` service starts and reads ISOs from `/install_repo/gl/`
4. For XR: mounts `/install_repo/gl/xr/xrv9k-xr-25.1.1` (ISO, 552MB) at tmpfs
5. Extracts 25 RPMs from ISO `rpm/xr/` directory, copies to `temp_loc_for_rpm/`
6. Runs `chroot /install/tmp/partprep rpm -iv` to install all RPMs at once
7. **Failure point**: RPM checks payload SHA256 digests, finds `xrv9k-iosxr-infra` corrupted
8. Retries once, fails again, aborts entire XR partition preparation
9. Without XR installed, `show vm` never shows an XR VM

## Install Logs
- `/var/log/install/inst_agent.log` — RPM extraction and installation (contains the BAD digest error)
- `/var/log/install/inst_mgr.log` — install manager coordination
- `/var/log/install/install_functions_py.log` — Python install helpers
- All logs accessible from Calvados bash shell (login cisco/cisco on pts/2, then `run`)
