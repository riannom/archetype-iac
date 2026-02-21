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

## 2026-02-19: Link reconciliation had no enforcement — only verified existing links

**Bug**: Link reconciliation only checked already-up links for VLAN drift and repaired them. Links with `desired_state=up` but `actual_state=down` were never created. Links with `desired_state=down` but `actual_state=up` were never torn down. This gap meant cross-host links that failed initial creation were never retried.

**Impact**: Two cross-host links stuck in `down` state indefinitely despite `desired_state=up`. Manual intervention required.

**Fix**: Added enforcement branches to `reconcile_link_states()` that call `create_link_if_ready()` for down/pending→up and `teardown_link()` for up→down. Extended `links_needing_reconciliation_filter()` to include these cases.

**Rule**: Reconciliation loops should enforce desired state in BOTH directions (create and destroy), not just verify existing state. If there's a desired_state field, every mismatch should have a corrective path.

## 2026-02-19: Feature flags with safe defaults can silently block safe operations

**Bug**: N9kv POAP skip (sending "yes" to an interactive prompt) was gated behind `n9kv_boot_modifications_enabled` which defaults to `False`. The flag was intended to gate invasive loader recovery, but POAP skip was bundled under it.

**Impact**: N9kv VMs stuck at POAP DHCP discovery indefinitely, never reaching usable state.

**Fix**: Separated POAP skip (safe, ungated) from loader recovery (invasive, gated). Different risk levels deserve different gates.

**Rule**: Don't gate safe operations behind the same flag as invasive ones. Classify operations by risk level and gate accordingly.

## 2026-02-19: Duplicate link_states from inconsistent normalize_interface calls

**Bug**: Two code paths created LinkState records with different interface name formats: `_upsert_link_states()` used `_canonicalize_link_endpoints()` WITH device_type, but `create_deployment_links()` used raw `link.source_interface` from the Link model. Also, `normalize_links_for_lab()` called `normalize_interface()` WITHOUT device_type, so vendors with non-standard patterns (e.g., Juniper `et-0/0/0`) were never normalized.

**Impact**: Duplicate LinkState rows per link (one with `eth1`, one with `Ethernet1` or `et-0/0/0`). Reconciliation confused by conflicting records.

**Fix**: (1) Pass device_type in `normalize_links_for_lab()`. (2) Thread `node_device_map` through `_link_state_endpoint_key()` and all callers. (3) Normalize interfaces in `create_deployment_links()` before creating LinkState records.

**Rule**: Every code path that creates or matches records with interface names must normalize using device_type. If a dedup function calls normalize_interface, it needs device context — pass a device map from the caller rather than querying inside.

## 2026-02-19: Domain XML metadata locks in vendor defaults, blocking future config updates

**Bug**: Domain XML generation stored ALL resolved readiness settings (probe, pattern, timeout) in `<archetype:*>` metadata, including vendor defaults like `readiness_probe="none"`. When vendor configs were later updated, the stale domain XML values took precedence in the probe lookup chain.

**Impact**: N9kv VMs had `is_ready=false` indefinitely because a stale `log_pattern` override in domain XML overrode the vendor's `readiness_probe="none"`.

**Fix**: Compare each readiness value against vendor defaults from `get_vendor_config(kind)` before storing in domain XML. Only true user/image overrides are persisted.

**Rule**: When baking configuration into persistent storage (domain XML, config files), only store overrides that differ from defaults. Storing defaults locks them in and prevents the default source from being updated independently.

## 2026-02-19: Readiness probe aborts POAP before provisioning completes

**Bug**: The readiness probe's `_run_n9kv_poap_skip()` fires on the `poap_abort_prompt` marker — the same prompt that appears during both normal POAP execution AND POAP failure. When POAP preboot provisioning was enabled (DHCP + TFTP + HTTP pipeline configured), the probe sent "yes" to abort POAP before the script could download and apply the startup-config.

**Impact**: N9Kv VMs with POAP preboot enabled never received their startup-config despite the entire POAP infrastructure being correctly configured and functional.

**Fix**: Check `settings.n9kv_poap_preboot_enabled` before firing the POAP skip. When preboot is enabled, only skip on explicit `poap_failure` marker. Let normal POAP proceed.

**Rule**: When the same interactive prompt appears in both "success in progress" and "failure" paths, the automation handler must distinguish the two contexts. Don't fire a "skip/abort" action on a prompt that also appears during the desired workflow.

## 2026-02-19: Test helper kwargs routing must distinguish method params from config dict keys

**Bug**: `_gen_xml()` helper in `test_libvirt_domain_xml.py` checked `if key in node_config` to decide whether to pop overrides into `node_config`. But the initial dict only had 10 keys, so `reserved_nics`, `serial_type`, `nographic`, `cpu_limit`, `cpu_sockets`, `smbios_product`, `readiness_probe` etc. stayed as overrides and were passed as direct kwargs to `_generate_domain_xml()`, which doesn't accept them.

**Impact**: 17 out of 56 domain XML tests failed with `TypeError: got an unexpected keyword argument`.

**Fix**: Changed routing logic to use an explicit `_method_kwargs` set listing `_generate_domain_xml()`'s actual parameters. Any override NOT in this set goes to `node_config`.

**Rule**: When a test helper forwards kwargs to a method that also takes a config dict, use an explicit allowlist of the method's params to route correctly. Don't rely on `if key in defaults_dict` — it misses any config key without a pre-populated default.

## 2026-02-19: docker.from_env() is itself a blocking call

**Bug**: Agent async endpoints wrapped Docker container operations in `asyncio.to_thread()` but called `docker.from_env()` directly on the event loop before wrapping. This socket connection to the Docker daemon is blocking I/O.

**Impact**: Brief event loop stalls on every container start/stop/remove/reconcile call. Compounded when multiple operations run concurrently.

**Fix**: Bundle `docker.from_env()` inside the same `_sync_*` closure as subsequent Docker operations. One thread transition instead of leaving the client creation on the event loop.

**Rule**: When wrapping Docker SDK calls in `asyncio.to_thread()`, include `docker.from_env()` inside the sync closure. Every Docker SDK call is blocking — client creation, container.get(), container.start(), network.create() — they all need thread isolation.

## 2026-02-19: Image metadata can override vendor readiness_probe="none" opt-out

**Bug**: The hw_specs merge chain (vendor → image → device overrides → per-node) allowed ISO-imported image metadata containing `readiness_probe="log_pattern"` to override the vendor config's intentional `readiness_probe="none"` for N9Kv. The "none" probe means "VM runtime state is sufficient — don't probe logs."

**Impact**: N9Kv VMs used `LibvirtLogPatternProbe` watching for serial console patterns that never appear after POAP configures the device. `is_ready` stayed false permanently — VMs ran fine but were never marked ready.

**Fix**: In Layer 1c (image metadata merge in `device_service.py`), skip `readiness_probe` and `readiness_pattern` keys when vendor config explicitly sets `readiness_probe="none"`. Defense-in-depth: `iso.py` also skips writing these fields to the manifest during import.

**Rule**: When a vendor config explicitly opts out of a feature (`readiness_probe="none"`), lower-priority layers (image metadata) must not override that opt-out. Only higher-priority layers (device overrides, per-node config) should be able to re-enable it, as those represent explicit user intent.

## 2026-02-19: Conditional `import` inside function shadows module-level import

**Bug**: `jobs.py:lab_status()` had `import asyncio` inside an `if not agents:` branch (line 627). Python's compiler saw this and marked `asyncio` as a local variable for the entire function. When agents existed (the normal path), the branch was skipped, leaving `asyncio` unbound. Line 653's `asyncio.gather()` raised `UnboundLocalError`.

**Impact**: `/labs/{id}/status` returned HTTP 500 for ALL labs with agents. The frontend polled this every 10 seconds, generating continuous silent 500 errors. Lab node counts displayed incorrectly or stale. API logging was broken (no handlers on root logger), so errors were invisible in `docker logs`.

**Fix**: Removed the redundant `import asyncio` from the conditional branch. The module-level import on line 4 is sufficient.

**Rule**: Never use `import X` inside a function body when `X` is already imported at module level. Python treats any `import` (or assignment) to a name inside a function as a local variable declaration for the ENTIRE function scope — not just the branch where it appears. Use the module-level import.

## 2026-02-19: API middleware sync DB blocks every HTTP request

**Bug**: `CurrentUserMiddleware.dispatch()` used `get_session()` (sync SQLAlchemy) directly in the async `dispatch()` method. This ran a DB query to resolve the current user on every single HTTP request, blocking the event loop each time.

**Impact**: Every API request (REST, WebSocket upgrade, health check) blocked the event loop for the duration of a DB query. Under concurrent load, requests queue behind each other.

**Fix**: Wrapped the user lookup in `asyncio.to_thread()`. The sync session + DB query now runs in a worker thread.

**Rule**: Middleware in async web frameworks runs on EVERY request — it's the highest-impact location for blocking calls. Always audit middleware for sync I/O first when investigating event loop stalls.

## 2026-02-20: Libvirt domain.info() indices — maxMem vs current memory

**Bug**: `get_vm_stats_sync()` used `domain.info()[2]` (current memory, affected by balloon driver) for capacity tracking. This underreports allocated memory when the balloon driver reclaims unused guest RAM.

**Impact**: Bin-packing placement would overcommit hosts — if a VM allocated 8GB but the balloon reduced usage to 4GB, only 4GB would be counted against capacity. Other VMs could be placed assuming 4GB was free when it wasn't.

**Fix**: Changed to `info[1]` (maxMem = allocated ceiling) which reflects the true resource commitment regardless of balloon state.

**Rule**: For capacity/placement calculations, always use `domain.info()[1]` (maxMem_kb) — the allocated ceiling. Use `info[2]` (mem_kb) only for monitoring actual guest memory consumption. Index reference: `[state, maxMem_kb, mem_kb, nrVirtCpu, cpuTime]`.

## 2026-02-20: Agent error responses are truthy dicts — explicit field checks needed

**Bug**: `query_agent_capacity()` could return `{"error": "Failed to gather resource usage"}`. The `not cap` check in the NLM only catches falsy values (None, {}, 0). A dict with an `"error"` key is truthy and passes through, causing `KeyError` when accessing `cap["memory_total_gb"]`.

**Impact**: One failing agent could crash the entire placement pipeline instead of being gracefully excluded.

**Fix**: Added explicit checks: `"error" in cap` and `not cap.get("memory_total_gb")` to filter invalid responses before building agent buckets.

**Rule**: When consuming HTTP responses from internal services, never rely solely on truthiness. Check for error indicator fields explicitly (`"error" in response`, `"status" != "ok"`, etc.) before accessing expected data fields.

## 2026-02-20: Sticky placements need capacity gating to prevent silent overcommit

**Bug**: The NLM honored sticky placements (from NodePlacement records) without checking if the target agent had sufficient capacity. Nodes were assigned to their previous agent even when that agent was already at capacity from other deployments.

**Impact**: Labs that previously fit on an agent could fail to deploy after other labs consumed capacity on the same agent. The bin-packer had no opportunity to redistribute because sticky nodes were pre-assigned.

**Fix**: Added step 5.5 in `_resolve_agents()`: pre-subtract sticky node requirements from agent buckets. If a sticky agent can't fit, remove the sticky assignment and add the node to the bin-packer pool for redistribution.

**Rule**: Affinity/sticky placement must always be capacity-gated. Treat sticky placement as a preference, not a hard constraint. When the preferred agent lacks capacity, fail over to the general placement algorithm rather than forcing an overcommit.

## 2026-02-20: NoopProbe masks broken VMs as "ready" — destructive fallbacks compound the damage

**Bug**: N9Kv had `readiness_probe="none"` (NoopProbe) which returned `is_ready=True` immediately, triggering post-boot commands on a VM still in BIOS/kernel boot. When the piggyback attempt failed (no CLI prompt yet), `run_commands()` fell through to direct console with `kill_orphans=True`, SIGKILLing the user's web console virsh process. The frontend reconnected, and the next readiness cycle repeated the same SIGKILL cascade every ~30 seconds.

**Impact**: Users saw repeated cycles of "Automation timed out → [virsh console exited with code -9] → connection lost → reconnecting" making the console unusable during the entire 5-10 minute boot.

**Fix**: (1) Changed N9Kv to `readiness_probe="log_pattern"` with `r"login:|User Access Verification"` to defer post-boot until NX-OS is actually ready. (2) `run_commands()` returns piggyback failure directly instead of falling through to destructive direct console. (3) `run_commands_capture()` passes `kill_orphans=False` when web session detected.

**Rule**: When a fallback path is both destructive (kills another process) AND futile (same failure will occur), don't attempt it — return the failure and let the caller retry on the next cycle. Also: `readiness_probe="none"` should be a last resort, not a convenience — it masks broken boot sequences and can trigger premature post-boot actions.

## 2026-02-20: Console prompt handlers must check buffer before sending Enter

**Bug**: `_prime_console_for_prompt` sent `\r` (Enter) BEFORE running `expect()` to check what was on screen. When the POAP abort prompt `(yes/no)[no]:` was displayed, Enter selected the default "no", letting POAP proceed. The subsequent `_handle_login` couldn't recover because the prompt was already answered.

**Impact**: N9Kv VMs never had POAP skipped despite the automation being present. VMs entered POAP DHCP discovery and eventually timed out or proceeded to initial admin setup.

**Fix**: Reversed the order — run `expect()` first (with 2s timeout), only send `\r` on timeout. When POAP abort prompt is detected, send "yes" immediately inline (don't delegate to a later handler).

**Rule**: When automating interactive serial consoles with prompts that have default answers (e.g., `[no]:`), always check what's on screen BEFORE sending any keystrokes. Enter/Return selects the default, which may be the wrong answer.

## 2026-02-20: Link state dedup must hard-delete duplicates before renaming

**Bug**: `_upsert_link_states` found duplicate LinkState rows for the same physical link (different naming conventions). It set `desired_state="deleted"` on duplicates, then tried to rename the preferred row to canonical form. The renamed `link_name` collided with the still-existing duplicate row, violating the `uq_link_state_lab_link` unique constraint. This crashed ALL sync jobs for the lab.

**Impact**: Total sync failure — reconciliation, enforcement, and link creation all crashed with `UniqueViolation` on every cycle.

**Fix**: Changed from soft-delete to hard-delete: `database.delete(duplicate)` + `existing_states.remove(duplicate)` + `database.flush()` before renaming the preferred row.

**Rule**: When dedup logic needs to rename a record that would collide with the duplicate, the duplicate must be actually removed from the database (not just marked), and the session must be flushed before the rename. Soft-delete with a status flag doesn't remove unique constraint violations.

## 2026-02-20: Image manifest metadata can silently override vendor config

**Bug**: The N9Kv image's `manifest.json` had `boot_timeout: 480` (stale value from import) and an old `readiness_pattern`. The hw_specs merge chain (Layer 1c: image metadata) applied these over the vendor config's `readiness_timeout: 600` and current `readiness_pattern`. Domain XML generation then stored these stale values.

**Impact**: N9Kv readiness timeout was 480s instead of 600s (correct for 5-10 min boot). Old readiness pattern could cause pattern match failures.

**Fix**: Updated manifest.json with correct values matching vendor config.

**Rule**: When vendor config is updated, also audit the image manifest for the same device — it's Layer 1c in the merge chain and can silently override vendor defaults. The merge order is: vendor config (1a) → image metadata (1c) → device overrides → per-node config_json.

## 2026-02-20: N9Kv boot variable is futile when overlays are recreated

**Bug**: Implemented `_discover_and_set_boot_variable()` to run `show version`, parse the NX-OS image, and set `boot nxos <image>` after first boot. The intent was to prevent the `loader >` prompt on subsequent stop/start cycles.

**Impact**: The boot variable was correctly set in the running VM's overlay disk, but on every stop/start cycle, the qcow2 overlay is recreated from the backing image (fresh disk). The boot variable is lost every time.

**Fix**: Removed the boot variable feature entirely. Instead, enabled POAP preboot provisioning (`n9kv_poap_preboot_enabled=True`) which handles config delivery via DHCP+TFTP+HTTP on each fresh boot. Also discovered that N9Kv requires `serial_port_count=2` (CML reference confirms) — the missing second serial port was the root cause of earlier POAP failures.

**Rule**: When VM disk overlays are recreated on every lifecycle operation, any persistent state written to the overlay (boot variables, saved configs) is lost. Use a provisioning mechanism that operates independently of disk state (e.g., POAP via network, ISO injection, or external config delivery).

## 2026-02-19: async def functions with zero await calls are effectively sync

**Bug**: `get_agent_for_lab()`, `get_healthy_agent()`, `get_agent_by_name()`, `_handle_agent_restart_cleanup()`, `_mark_links_for_recovery()` were all `async def` but contained zero `await` calls — pure synchronous DB operations.

**Impact**: When refactoring handlers that call these functions, they can't be called from sync closures (they return coroutines, not values). But wrapping them in `await` defeats the purpose of the `asyncio.to_thread()` pattern since the async call puts you back on the event loop.

**Fix**: For handler refactoring, inline the pure-DB logic directly in the sync closure. For standalone helper functions, convert from `async def` to plain `def` (renamed with `_sync` suffix).

**Rule**: Before wrapping an `async def` function in `asyncio.to_thread()`, check if it actually uses `await`. If it has zero `await` calls, it's effectively sync — either inline its logic in the sync closure or convert it to a plain `def`.
