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

## 2026-02-21: Pexpect readiness probe only reads current console state

**Bug**: N9Kv readiness pattern `login:|User Access Verification` never matched even though the device had successfully booted and was at `switch#` prompt. The `login:` prompt appeared during boot but scrolled out of the buffer by the time the readiness probe connected.

**Impact**: N9Kv VMs stayed `is_ready=false` indefinitely despite being fully operational at the CLI prompt.

**Fix**: Expanded readiness pattern to `login:|User Access Verification|switch[^\s]*[#>]` to also match the NX-OS CLI prompt that appears after login.

**Rule**: Pexpect-based readiness probes connect fresh each cycle, send Enter, and read ~3 seconds of output. They do NOT see historical console output. Readiness patterns must match what's currently on screen — include both the expected boot-time prompt AND the post-login CLI prompt.

## 2026-02-21: N9Kv POAP has two prompt variants with different text and defaults

**Bug**: The `poap_abort_prompt` diagnostic pattern only matched `Abort Power On Auto Provisioning` but NX-OS also uses `System is not fully online. Skip POAP? (yes/no)[n]:` — a completely different prompt with a different default indicator (`[n]` vs `[no]`). The POAP skip handler never fired for the second variant.

**Impact**: N9Kv VMs stuck at the POAP prompt indefinitely. The readiness probe showed the prompt text in the serial log tail but no `poap_abort_prompt` marker appeared.

**Fix**: Extended the diagnostic pattern to `Abort Power On Auto Provisioning|Skip POAP\?`. Updated the prompt_pattern in `_run_n9kv_poap_skip` to match both `[no]` and `[n]`: `\(yes/no\)\[n(?:o)?\]:\s*$`.

**Rule**: Vendor CLIs often have multiple prompt variants for the same interactive question. When automating console interactions, test against ALL firmware versions and boot scenarios — the prompt text and default indicators may differ. Use regex alternation to match all known variants.

## 2026-02-21: host-passthrough CPU mode causes guest kernel panics in NX-OS

**Bug**: N9Kv VMs consistently hit `ksm_scan_thread` kernel panic during first boot. The NX-OS guest (Wind River Linux) lacks SMAP support, but `host-passthrough` CPU mode exposes SMEP/SMAP/PKU/UMIP features from the host. The kernel enables SMAP in CR4 without corresponding STAC/CLAC instructions, causing general protection faults when KSM scans user-space VMAs from kernel context.

**Impact**: N9Kv VMs entered an infinite kernel panic loop — loader recovery boots NX-OS → panic → drops to loader → recovery boots again → panic. The VM never reached a usable state.

**Fix**: Added `cpu_features_disable` field to VendorConfig/VmConfig/LibvirtRuntimeConfig. N9Kv config disables `["smep", "smap", "pku", "umip"]`. Domain XML generates `<feature policy='disable' name='smep'/>` etc. inside the `<cpu mode='host-passthrough'>` element.

**Rule**: Don't assume `host-passthrough` is safe for all guest OSes. Older kernels (especially embedded/vendor Linux) may lack support for modern CPU security features. Check what CPU model other lab platforms use for each device — if they all use `qemu64`, there's a reason. Keep `host-passthrough` for performance but selectively disable incompatible features.

## 2026-02-21: Serial console \r characters break diagnostic regex patterns

**Bug**: `_collect_diagnostic_hits()` ran regex patterns directly against raw serial console output. Serial output contains embedded `\r` (carriage return) characters that split words — `Kernel panic` appears as `Ker\rnel pan\ric`. The `kernel_panic` diagnostic pattern never matched despite the panic being visible on screen.

**Impact**: Kernel panic detection failed silently. The panic recovery handler never fired, leaving VMs stuck with no automated recovery.

**Fix**: Added output sanitization in `_collect_diagnostic_hits()` — calls `_sanitize_console_output()` to strip control characters before regex matching.

**Rule**: Always sanitize serial console output before regex matching. Terminal output contains `\r`, ANSI escape sequences, and other control characters that are invisible on screen but break pattern matching. Clean the output first, then match.

## 2026-02-19: async def functions with zero await calls are effectively sync

**Bug**: `get_agent_for_lab()`, `get_healthy_agent()`, `get_agent_by_name()`, `_handle_agent_restart_cleanup()`, `_mark_links_for_recovery()` were all `async def` but contained zero `await` calls — pure synchronous DB operations.

**Impact**: When refactoring handlers that call these functions, they can't be called from sync closures (they return coroutines, not values). But wrapping them in `await` defeats the purpose of the `asyncio.to_thread()` pattern since the async call puts you back on the event loop.

**Fix**: For handler refactoring, inline the pure-DB logic directly in the sync closure. For standalone helper functions, convert from `async def` to plain `def` (renamed with `_sync` suffix).

**Rule**: Before wrapping an `async def` function in `asyncio.to_thread()`, check if it actually uses `await`. If it has zero `await` calls, it's effectively sync — either inline its logic in the sync closure or convert it to a plain `def`.

## 2026-02-21: Mutable class-level attributes shared across all Python instances

**Bug**: `LibvirtProvider` had 7 mutable dicts/sets (`_n9kv_loader_recovery_attempts`, `_n9kv_poap_skip_attempted`, etc.) defined at class level. In Python, class-level mutable objects are shared across ALL instances — modifications from one instance are visible to every other instance.

**Impact**: If multiple `LibvirtProvider` instances were created (e.g., testing, future multi-host), N9Kv recovery state from one VM could leak into another instance's state tracking, causing incorrect retry counts, skipped recovery attempts, or guard sets that never reset.

**Fix**: Moved all 7 mutable attributes into `__init__()` as instance attributes (`self._n9kv_*`). Kept 4 immutable constants (`_N9KV_LOADER_RECOVERY_MAX_ATTEMPTS`, etc.) at class level since they're never modified.

**Rule**: Mutable defaults (dicts, sets, lists) must NEVER be defined at class level unless intentionally shared. Always initialize in `__init__()`. Only immutable values (ints, strings, frozensets, tuples) are safe as class attributes.

## 2026-02-21: Computed threshold value silently discarded — no variable assignment

**Bug**: `reconciliation.py` computed `now - timedelta(seconds=settings.stale_starting_threshold)` but never assigned it to a variable or used it in the subsequent query filter. The threshold expression was evaluated and immediately discarded. All transitional labs (starting/stopping/unknown) were reconciled regardless of age.

**Impact**: Every reconciliation cycle swept ALL transitional labs instead of only those older than the configured threshold. This caused unnecessary agent queries and potential false positives for labs that were legitimately in mid-transition.

**Fix**: Assigned to `transitional_threshold` variable and added `.filter(models.Lab.updated_at < transitional_threshold)` to the query.

**Rule**: When computing a threshold or filter value, verify it's actually used in the subsequent query. A bare expression on its own line in Python is a no-op — it computes a value and throws it away. Linters catch `unused variable` but not `unused expression`.

## 2026-02-21: @functools.total_ordering is incompatible with str enums

**Bug**: Attempted to simplify `GlobalRole(str, Enum)` and `LabRole(str, Enum)` comparison methods using `@functools.total_ordering` (which derives `__gt__`, `__ge__`, `__le__` from `__eq__` + `__lt__`). The decorator was a no-op because `str` already defines all comparison methods.

**Impact**: `total_ordering` checks `getattr(cls, op) is not getattr(object, op)` for each comparison method. Since `str.__gt__` etc. exist and differ from `object.__gt__`, the decorator considers them "already user-defined" and doesn't generate replacements. The result: `__gt__` calls `str.__gt__` (lexicographic: `"admin" > "viewer"` → False) instead of our rank-based comparison.

**Fix**: Reverted to explicit 4-method implementation (`__ge__`, `__gt__`, `__le__`, `__lt__`). The original code was correct.

**Rule**: `@functools.total_ordering` does not work with `str` (or any type that already defines rich comparison methods). It only fills in MISSING methods. For `str` enums with custom ordering, explicitly implement all 4 comparison methods.

## 2026-02-23: Linux VXLAN devices persist independently of OVS ports

**Bug**: Deleting an OVS VXLAN port (via `ovs-vsctl del-port`) does NOT delete the underlying Linux VXLAN device created by `ip link add ... type vxlan`. The "ghost" device survives agent restarts and all OVS cleanup routines. When tunnel recreation is attempted, `ip link add` fails with "already exists" — permanently blocking that tunnel.

**Impact**: Cross-host links stuck in `down` state permanently. Link reconciliation retried every 30s but could never succeed. Required manual `ip link delete` on the agent host to resolve.

**Fix**: Added retry logic in `_create_vxlan_device()` in `agent/network/overlay.py`. On "already exists" error, automatically deletes the stale Linux device and retries creation.

**Rule**: When cleaning up VXLAN infrastructure, always clean both the OVS port AND the Linux netdev. OVS port deletion is not sufficient. When creating VXLAN devices, handle "already exists" as a recoverable error (delete and retry) rather than a permanent failure.

## 2026-02-23: Database queries should never assume error state implies specific status values

**Bug**: Infrastructure notifications query filtered `VxlanTunnel.status.in_(["cleanup", "failed"])` to find problematic tunnels. But tunnels can have `status='active'` with a non-null `error_message` — this happens when cleanup is deferred (database record re-activated while the physical device remains orphaned).

**Impact**: Notifications panel showed empty despite a known tunnel issue with a cleanup deferral error message.

**Fix**: Added `or_()` clause to also match tunnels with non-null `error_message` regardless of status.

**Rule**: When querying for records with problems/errors, check BOTH explicit status fields AND error message fields. Status values can be misleading — a record may be "active" while having an unresolved error. Use `or_(status_check, error_message_check)` pattern.

## 2026-02-23: Nonexistent model attributes crash silently in periodic tasks

**Bug**: `reconciliation.py` line 438 referenced `models.Lab.updated_at` — an attribute that doesn't exist on the Lab model (correct name: `Lab.state_updated_at`). This raised `AttributeError` every 30 seconds, crashing the entire `refresh_states_from_agents()` function.

**Impact**: State reconciliation completely disabled. Link states for new links were never created. Transitional labs never reconciled. The scheduler caught the exception and continued, so no visible crash — failure was silent.

**Fix**: Changed `Lab.updated_at` to `Lab.state_updated_at`.

**Rule**: SQLAlchemy model attribute references in queries are only validated at query execution time, not import time. A typo in a model attribute name compiles fine but crashes at runtime. When referencing model columns in filter expressions, verify the column name exists on the model class. Periodic tasks are especially dangerous — failures are silent and may go undetected for days.

## 2026-02-23: backdrop-filter: blur() required for translucent dark-on-dark distinction

**Bug**: SystemStatusStrip metric bar text was unreadable in dark mode despite using `text-white` classes. The strip background used `color-mix(in srgb, var(--color-bg-surface) 88%, transparent)` — translucent dark surface on dark wallpaper with no visual distinction. CSS `dark:` prefix classes had specificity issues against component-level styles.

**Impact**: Metric bar text appeared gray/washed-out regardless of color class changes. Multiple iterations of opacity tuning (50%, 75%, 95%) failed to create readable contrast without blur.

**Fix**: (1) Switched from CSS `dark:` prefix to JS `effectiveMode` check for all color classes. (2) Added `backdrop-filter: blur(12px)` alongside 55% opacity for frosted glass effect. The blur creates visual distinction between strip and background that opacity alone cannot achieve.

**Rule**: When using translucent backgrounds in dark mode, always pair with `backdrop-filter: blur()`. Without blur, dark-on-dark translucency is invisible at any opacity. For components that already have `effectiveMode` state, use JS conditionals for color classes instead of CSS `dark:` prefix to avoid specificity battles.

## 2026-02-24: Docker containerd-snapshotter produces wrong diff IDs on large image loads

**Bug**: `docker load` on agent-01 failed with `wrong diff id calculated on extraction` for the 2.1GB cjunosevolved image. The computed diff ID changed with every attempt, indicating non-deterministic layer extraction. The containerd snapshotter (io.containerd.snapshotter.v1, default in Docker 29.x) was computing different hashes each time it extracted the same layer.

**Impact**: Image sync to agent-01 completed successfully (100%), `docker load` reported "Loaded image" but only stored a 1.62kB manifest stub. Container creation failed with "content digest not found". Node deployment impossible.

**Fix**: Created `/etc/docker/daemon.json` with `{"features": {"containerd-snapshotter": false}}` on agent-01 to fall back to the legacy overlay2 driver. Restarted Docker, reloaded all images, restarted agent.

**Rule**: If `docker load` reports success but the image size is wrong (< 1MB for a multi-GB image) or container creation fails with "content digest not found" or "wrong diff id", suspect the containerd snapshotter. Disable it with `containerd-snapshotter: false` in daemon.json and restart Docker. Note: changing the image store requires reloading all images.

## 2026-02-24: Scheduler container needs Docker socket for image sync

**Bug**: The scheduler container ran `docker save` to stream images to remote agents but had no Docker socket mount. All image sync attempts failed with "failed to connect to the docker API at unix:///var/run/docker.sock".

**Impact**: No images could be synced to remote agents. cJunos node stuck in error state with max enforcement attempts reached.

**Fix**: Added `/var/run/docker.sock:/var/run/docker.sock` volume mount to the scheduler service in `docker-compose.gui.yml`.

**Rule**: Any container that runs `docker save`, `docker load`, or other Docker CLI/SDK commands needs the Docker socket mounted. When adding new background tasks that interact with Docker, verify the container they run in has the socket.

## 2026-02-24: Function-scoped `import asyncio` shadows module-level import (again)

**Bug**: `agent/routers/images.py` had `import asyncio` inside `backfill_image_checksums()` (line 107) and `receive_image()` (line 239). Python treats any import inside a function as a local variable for the entire function scope. When the Docker load code path (line 323) was reached without executing the inner import first, `asyncio` was unbound.

**Impact**: All Docker image transfers to agent-01 failed with `UnboundLocalError: cannot access local variable 'asyncio'`. This is the exact same bug pattern documented in lessons from 2026-02-19 (`jobs.py:lab_status()`).

**Fix**: Removed both function-scoped `import asyncio` statements. The module-level import on line 4 is sufficient.

**Rule**: This is the THIRD occurrence of this bug. Grep for `^\s+import asyncio` in ALL Python files after any code move or refactoring. The pattern is always the same: conditional `import` inside a function body shadows the module-level import for the entire function.

## 2026-02-24: Reconcile endpoint must clean up Docker networks like destroy does

**Bug**: `_reconcile_single_node()` in `agent/routers/labs.py` stopped and removed containers but never cleaned up associated Docker networks. The NLM's `_stop_nodes` phase uses `reconcile_nodes_on_agent()` (not `destroy_node_on_agent()`), so the network cleanup in `destroy_node()` was never reached during normal node migration.

**Impact**: Orphaned `{lab_id}-ethN` Docker networks persisted after container migration. When redeploying on a different agent, `_create_lab_networks()` failed with Docker 409 "network already exists" errors. The user reported no containers had been on agent-01 for a day, yet stale networks remained — pointing directly to missing cleanup.

**Fix**: (1) Added network cleanup to `_reconcile_single_node()` after container removal, triggered only when the last container for a lab is removed (matching `destroy_node()` behavior). (2) Added 409 resilience in `_create_lab_networks()` — on conflict, removes stale network and retries creation.

**Rule**: When multiple code paths can remove resources (reconcile, destroy, cleanup), ALL paths must clean up associated dependent resources (networks, volumes, etc.). If `destroy_node()` cleans networks, `reconcile_single_node()` must too. Audit all resource removal paths for consistent cleanup behavior.

## 2026-02-22: Mock patch targets must follow code extraction

**Bug**: After extracting endpoints from `agent/main.py` into `agent/routers/*.py`, 14 test files had `patch("agent.main.X")` that silently created new mocks instead of patching the real function. Python's `unittest.mock.patch` patches the name in the specified module — if the function moved to `agent.routers.nodes`, patching `agent.main.create_node` creates a new mock on `agent.main` that nothing calls.

**Impact**: Tests passed vacuously — mocks were never invoked because the real code path used the function from its new module. Some tests failed outright when the mock was the return value of an endpoint call.

**Fix**: Updated all 14 test files to use `patch("agent.routers.<module>.X")` matching where each function is now defined and looked up. Also caught single-quoted strings (`patch('agent.main.X')`) missed by the initial double-quoted bulk replace.

**Rule**: When extracting code to new modules, update ALL mock patch targets in tests. `patch("old.module.func")` silently succeeds even if `func` no longer lives there — it just creates an unused mock. Search for BOTH `"old.module.` AND `'old.module.` patterns. Verify by checking that mocked functions are actually called (assert_called, assert_awaited).

## 2026-02-27: CSS transform stacking contexts break SVG z-index for port labels

**Bug**: Canvas port labels (interface names like "eth0", "eth1") were rendered as SVG `<text>` elements within the same SVG layer as links. The zoom container's CSS `transform` creates a new stacking context, causing HTML node divs (with `z-index: 10`) to always render above earlier SVG layers regardless of CSS z-index values. When a new node was added near existing nodes, the 48x48 node div plus name label occluded port labels on nearby links.

**Impact**: Port assignment labels invisible on newly connected nodes, especially when nodes were placed close together.

**Fix**: Moved port label rendering to a completely separate SVG overlay element positioned AFTER all node divs in the DOM, with explicit `zIndex: 20`. This ensures labels always render above node elements.

**Rule**: When mixing SVG elements and HTML divs inside a CSS-transformed container, z-index between the two element types is unreliable. To guarantee SVG elements appear above HTML divs, place them in a separate positioned layer after the HTML elements in DOM order.

## 2026-02-27: Complex placement algorithms produce worse results than simple math

**Bug**: A 300-line `linkLabelPlacement.ts` algorithm computed canvas port label positions using candidate generation, perpendicular offsets, overlap scoring, node avoidance, and stagger logic. Despite 3 rewrites (scoring rebalance, full rewrite with `placeLabel()`, simplified on-line placement), labels consistently appeared displaced from their link lines — especially for angled links between distant nodes.

**Impact**: Port assignment labels (interface names like "eth0", "GigabitEthernet1/0/1") floated far from their links, making it impossible to identify which interface was assigned to which connection. Multiple algorithm iterations and container rebuilds failed to fix the visual issue.

**Fix**: Deleted all algorithm usage. Replaced with 3 lines of inline math in Canvas.tsx: `source.x + 0.2 * (target.x - source.x)` for each coordinate. Labels placed at exactly 20% along the link line from each endpoint. Confirmed with RED debug text, then restored proper theme colors.

**Rule**: For UI element positioning on geometric shapes (lines, curves), start with the simplest possible math (linear interpolation) before reaching for algorithms. A complex placement algorithm with overlap avoidance and scoring is only justified after proving the simple approach fails. In this case, the simple approach was both correct and sufficient — the algorithm was pure over-engineering that introduced bugs.

## 2026-03-03: Runtime manifest writes must never use destructive catalog pruning

**Bug/Issue**: `save_manifest()` synced catalog rows using prune semantics even when the payload was partial (for example, read via fallback path after catalog read failures). Missing rows in that partial payload were treated as deletions.

**Impact**: Existing image catalog entries could be removed unintentionally, collapsing available/assignable images in the UI from expected historical totals to a small subset.

**Fix**: Added `prune_missing` control to `sync_catalog_from_manifest()`, switched runtime `save_manifest()` sync to `prune_missing=False`, and added regression coverage plus dedicated gates (`make test-api-catalog-regression`, confidence-gate rules, CI job) for merge-vs-prune behavior.

**Rule**: Only use prune semantics for explicit full-snapshot reconciliation jobs. Any runtime read/modify/write path must default to merge/upsert behavior and carry regression tests for fallback-read then save flows.

## 2026-03-07: Async DB sessions must be released before awaited agent I/O

**Bug/Issue**: Reconciliation and provisioning helpers read ORM state, awaited agent/network calls, and then wrote back using the same SQLAlchemy session/transaction.

**Impact**: This produced `idle in transaction` sessions, row-lock chains on `link_states` and `interface_mappings`, statement timeouts, and stale post-op state after rollback paths.

**Fix**: Centralized transaction-release/reset helpers in `api/app/tasks/jobs.py`, threaded them through node lifecycle, link reconciliation, and interface mapping flows, and added regression tests plus DB-contention observability.

**Rule**: Never hold a SQLAlchemy transaction open across awaited agent or network calls. Read state, release the transaction, await external I/O, then reopen a short write transaction.

## 2026-03-07: Runtime identity and interface readiness must be first-pass provisioning gates

**Bug/Issue**: Initial provisioning could report success before authoritative runtime status appeared or before required live interfaces/OVS ports were actually resolvable.

**Impact**: Healthy-looking first deploys still needed reconciliation, backfill, or link repair to become usable, especially for libvirt nodes and cross-host links.

**Fix**: Added hard first-init gates for host/image/runtime conflicts/capacity, made runtime visibility mandatory for start success, required `is_ready` plus live interface readiness before link creation, and demoted name-based recovery logic out of the hot path.

**Rule**: Provisioning success must mean the runtime is visible through the authoritative status path and required interfaces are live enough to attach links. Reconciliation should handle drift, not normal initialization gaps.
