# Lessons Learned

## 2026-02-14: sed `a` command does NOT interpret `\t` as tab in single quotes

**Bug**: `sed -i '/pattern/a\\ttext' file` produces literal `\t` instead of a tab character.

**Impact**: LVM `global_filter` was written as `\tglobal_filter = ...` (invalid config key). LVM silently ignored it, leaving the system vulnerable to the NBD/LVM crash cascade. This caused repeated system hangs on agent-01 requiring power cycles.

**Fix**: Use `$'...'` ANSI-C quoting: `sed -i $'/pattern/a\\\\\ttext' file` to get a real tab.

**Rule**: Always verify config changes take effect. For LVM, run `lvm dumpconfig <setting>` after writing to `lvm.conf`. Never trust `grep` alone to validate config correctness.

## 2026-02-19: Libvirt Python bindings are completely synchronous and not thread-safe

**Bug**: Agent `/healthz` permanently unhealthy (20+ failing streak). The asyncio event loop was frozen by ~25 synchronous `self.conn.*` libvirt API calls made directly from async FastAPI endpoints.

**Impact**: All agent endpoints (healthz, status, deploy) became unresponsive whenever libvirt operations ran. API timed out waiting for agent, cascading failures through the system.

**Fix**: Dedicated single-thread `ThreadPoolExecutor(max_workers=1)` for all libvirt calls. Single thread = thread-safe by design (no locks needed). Methods split into `_method_sync()` helpers called via `await self._run_libvirt(self._method_sync, ...)`.

**Rule**: Never call libvirt Python bindings from an async event loop. Always route through a dedicated executor. The same `self.conn` object must only be accessed from one thread.

## 2026-02-19: Per-subprocess overhead dominates OVS startup with many ports

**Bug**: OVS state discovery took 60 seconds on restart. The loop spawned 2 subprocesses per port (`ovs-vsctl get tag` + `cat ifindex`). With ~264 ports, that's 528 subprocess spawns at ~200ms each.

**Impact**: Agent startup took 65+ seconds. During this time healthz couldn't respond.

**Fix**: (1) Batch all port tags in one call: `ovs-vsctl --format=json -- --columns=name,tag list Port`. (2) Read ifindex from sysfs directly via `Path.read_text()` — no subprocess needed for virtual filesystem reads.

**Rule**: When querying OVS for multiple ports, always use `--format=json` with `list` to batch. For sysfs/procfs reads, use Python file I/O directly — spawning `cat` as a subprocess is pure overhead.

## 2026-02-14: QEMU writeback cache mode corrupts page cache of backing images

**Bug**: QEMU with default `writeback` cache mode (`cache.direct=false`) modifies the host page cache of read-only qcow2 backing images during COW operations. The file on disk is fine, but any process reading the file (including QEMU itself) gets corrupted data from the page cache.

**Symptoms**: Unstable MD5/SHA256 hashes of the backing image (changes between reads), I/O errors in QEMU logs, VM boot failures due to corrupted data (e.g., RPM digest failures in XRv9000). Hashes stabilize only after killing QEMU + `echo 3 > /proc/sys/vm/drop_caches`.

**Fix**: Use `cache='none'` (O_DIRECT) on all libvirt disk driver elements to bypass the page cache entirely. Also add `io='native'` for optimal AIO performance with O_DIRECT.

**Rule**: Always use `cache='none'` for QEMU disks backed by qcow2 overlays. The default `writeback` mode is unsafe when multiple VMs share a backing image or when the backing image is on network storage. If you see unstable file hashes on a file that should be read-only, suspect page cache corruption before suspecting disk corruption.

## 2026-02-19: Docker eth0 is management — 0-indexed vendors map first data port to eth0 (wrong)

**Bug**: `normalize_interface("et-0/0/0", "juniper_cjunos")` returned `eth0`, but Docker reserves `eth0` for the management bridge. The OVS plugin creates data interfaces starting at `eth1`. Result: "Could not find OVS port for juniper_cjunos_14:et-0/0/0" in an infinite retry loop.

**Impact**: All 0-indexed vendors (Juniper, SONiC, Cisco IOSv/XR/ASAv) had broken link resolution for cross-host links. 1-indexed vendors (cEOS, N9Kv, SRL) worked by coincidence since their index already started at 1.

**Fix**: Introduced `DOCKER_DATA_PORT_START = 1` constant. Normalize formula changed from `eth{vendor_index}` to `eth{vendor_index - port_start_index + 1}`. Denormalize formula updated with inverse. Also added `et-` to Juniper fallback regex pattern.

**Rule**: When translating vendor port indices to Docker interface numbers, always account for Docker's eth0 management reservation. The formula must map the first data port to eth1, not eth0, regardless of whether the vendor starts numbering at 0 or 1.
