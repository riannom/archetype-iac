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

## 2026-02-19: Deploy pre-flight rejects entire batch on ANY host CPU oversubscription

**Bug**: Deploy batch job (`sync:batch`) failed with "Insufficient resources for deployment: agent-01 - CPU: Need 25 cores, projected 156%" even though local-agent had spare capacity.

**Impact**: None of the 14 nodes in the lab could deploy, even those on the non-oversubscribed host.

**Fix**: Rebalanced topology by moving 7 container nodes from agent-01 to local-agent, keeping only 5 VMs on agent-01 (which requires libvirt). Re-ran deploy.

**Rule**: Check node placement vs host capacity before deploying large labs. The resource pre-flight check is all-or-nothing per batch — one oversubscribed host blocks the entire deployment.

## 2026-02-19: _validate_images only checked Docker store, not filesystem

**Bug**: `DockerProvider._validate_images()` called `self.docker.images.get(image)` for all images, but qcow2/img files exist on the filesystem (used by libvirt), not in Docker's image store. Deploy failed with "Missing images" for valid VM images.

**Impact**: Labs with mixed Docker containers and libvirt VMs couldn't deploy through the Docker provider's validation path.

**Fix**: Added file extension/path prefix detection: if image starts with `/` or ends with `.qcow2`/`.img`/`.iol`, check `os.path.exists()` instead of Docker image store.

**Rule**: Image validation must be provider-aware. File-based images (qcow2, img, iol) live on the filesystem, not in Docker's image store. Check both paths.

## 2026-02-19: Single-entity endpoints must match list endpoints for computed fields

**Bug**: `GET /labs/{id}` returned `node_count=0, running_count=0` because the detail endpoint didn't compute these fields (only the list endpoint did).

**Impact**: Frontend lab detail view showed 0 nodes despite having 14 deployed nodes.

**Fix**: Extracted `_populate_lab_counts()` helper, called from both list and detail endpoints.

**Rule**: When computed fields exist on a schema (not stored in DB), ensure ALL endpoints returning that schema populate the fields. If the list endpoint computes something, the detail endpoint must too.
