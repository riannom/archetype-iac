# N9Kv Startup-Config Troubleshooting Log

## Scope
- Node: `cisco_n9kv_4` (Nexus 9000v)
- Behavior: after `stop` + `start` (full destroy/recreate), node boots into POAP instead of applying saved startup config.
- Constraint from operator: use existing saved config; do not re-extract.

## Environment
- Agent host: `x300` (`10.14.23.181`)
- Lab ID: `52c138e7-de39-4b4a-8daa-d75014ffe2e0`
- N9Kv image: `/var/lib/archetype/images/nexus9300v64.10.5.3.F.qcow2`

## Initial Symptom
- Startup config existed under workspace, but N9Kv booted to POAP prompt:
  - `Abort Power On Auto Provisioning ... (yes/no)[no]:`

## What We Tried

### 1) Startup-config source selection and config sanitization
- Commit: `b506659`
- Changes:
  - API startup-config selection for N9Kv prioritizes saved workspace config.
  - Added fallback for `config_json["startup-config"]`.
  - N9Kv config sanitizer strips extraction header noise (`!Command`, `!Running configuration`, `!Time`).
- Tests:
  - `agent/tests/test_plugins_providers_batch1.py` passed.
  - API unit tests for startup-config selection passed (container test run).

### 2) Bootflash partition detection fix (first major root cause)
- Commit: `f402523`
- Evidence before fix:
  - Agent logs showed:
    - `No suitable bootflash partition found ...`
    - `Config injection failed for cisco_n9kv_4; VM will boot without config`
- Changes:
  - Accept `SEC_TYPE` fallback (e.g., ext3 with `SEC_TYPE=ext2`).
  - If multiple candidates match, probe markers and pick better partition.
- Tests:
  - `agent/tests/test_bootflash_inject.py` passed.

### 3) Agent hang on restart/stop (separate issue discovered during troubleshooting)
- Commit: `622fba5`
- Evidence:
  - On stop, systemd hit `stop-sigterm` timeout and had to SIGKILL agent + `virsh` child processes.
- Changes:
  - Hardened orphan virsh cleanup (TERM with KILL escalation + verification).
  - Reaped killed console subprocesses correctly.
  - Added shutdown-time cleanup of active console sessions.
  - Tracked/cancelled background interface-fix task on shutdown.
- Result:
  - Restart now exits cleanly (no `stop-sigterm` timeout in validated restart).

### 4) Additional bootflash staging hardening (current latest)
- Commit: `cd98a19`
- Why:
  - POAP still seen despite successful injection logs in some cycles.
  - Need stronger compatibility for N9Kv bootflash path/partition behavior.
- Changes:
  - If no marker match, choose largest matching ext partition (instead of first).
  - Mirror config write to both:
    - `/startup-config`
    - `/bootflash/startup-config`
- Tests:
  - `agent/tests/test_bootflash_inject.py` passed (`15 passed`).

## Key Log Evidence Collected

### Failure evidence (historical)
- POAP prompt detected in console extraction flow.
- Injection failure (earlier state):
  - `No suitable bootflash partition found`
  - `Config injection failed ... boot without config`

### Success evidence (later state)
- Injection action confirmed in logs:
  - `Wrote 2621 bytes to /startup-config on /dev/nbd0p4`
  - `Injected startup config for cisco_n9kv_4 (2621 bytes)`
- VM recreate/start confirmed:
  - overlay recreated
  - domain defined
  - start operation success

## Current Status
- Agent host is running latest troubleshooting changes (`cd98a19`) and healthy.
- N9Kv startup-config staging path is now more robust and dual-path.
- Operator validation still required for final pass/fail on POAP elimination after another full stop/start cycle.

## Fresh Validation Run (2026-02-18)

### Actions executed
- Node targeted: `cisco_n9kv_4` (`node_id=g4hxozh`)
- Controlled cycle:
  - `running -> stopped` at `2026-02-18T17:57:31Z`
    - Sync job: `c3759f46-badf-4f26-bbdb-dd9f8901f0d2` (completed)
  - `stopped -> running` at `2026-02-18T17:58:03Z`
    - Sync job: `35d145d6-9dce-4fe0-a68a-b98b416ee023` (completed at `2026-02-18T18:00:21Z`)

### API/job evidence
- Stop job log: node stopped cleanly.
- Start job log:
  - `cisco_n9kv_4: deployed and started`
  - `Readiness timeout (120s): 1 node(s) still not ready: cisco_n9kv_4`
- Agent/API state still reported node `running` after start.

### Direct console evidence (remote agent websocket)
- Connected to agent-01 console endpoint:
  - `ws://10.14.23.181:8001/console/52c138e7-de39-4b4a-8daa-d75014ffe2e0/cisco_n9kv_4?provider_type=libvirt`
- Observed repeated POAP lines after the fresh restart:
  - `POAP-2-POAP_FAILURE ... - POAP DHCP discover phase failed`
  - `Abort Power On Auto Provisioning ... (yes/no)[no]:`
- Additional repeating POAP messages observed:
  - `Invalid DHCP OFFER: Missing Script Name`
  - `USB disk not detected`

### Outcome
- **Issue persists** on `cd98a19`: startup-config still not being consumed at boot for N9Kv in this environment, despite successful deploy/start lifecycle and prior bootflash injection hardening.
- Prior "operator validation required" item is now resolved as **FAIL** for this validation cycle.

### New secondary finding
- Agent readiness API for this node returned:
  - `is_ready=false`, `message="No console output available"`
- But live websocket console does return output. This indicates a likely gap in the readiness probe path for N9Kv/libvirt console capture (separate from startup-config injection itself).

### Local code updates prepared for next validation (not yet deployed)
- Added structured injection diagnostics capture in `agent/providers/bootflash_inject.py` (partition selected, write targets, bytes, error reason).
- Wired diagnostics into libvirt create flow in `agent/providers/libvirt.py` and surfaced as create details.
- Exposed create details in agent API response (`agent/schemas.py`, `agent/main.py`) and propagated into controller sync job logs (`api/app/tasks/node_lifecycle.py`).

## Commits Involved
- `b506659` - startup-config source priority + N9Kv config cleanup.
- `f402523` - bootflash partition detection fixes (`SEC_TYPE` + marker selection).
- `622fba5` - agent shutdown hang fixes (lingering virsh consoles).
- `cd98a19` - additional N9Kv staging hardening (largest partition fallback + dual write paths).

## Suggested Next Check
- Deploy the local diagnostics changes to API + agent and run one more fresh `stop` + `start` to capture injection details directly in job logs.
- On agent-01, verify overlay contents immediately after injection and before VM start:
  - expected files: `/startup-config` and `/bootflash/startup-config`
  - confirm expected config payload and file timestamps.
- Investigate readiness probe discrepancy (`No console output available` vs live websocket output) to improve observability and reduce false "not ready" outcomes.

## Validation With Readiness Diagnostics (2026-02-18)

### Code/version deployed
- Controller/API commit: `87024c4` (`feat: add n9kv readiness probe diagnostics`)
- Remote agent `agent-01` (`614fc24c`) updated to git SHA:
  - `87024c4aea1ac78035cf44543aed385c9339831d`

### Fresh cycle executed
- Node: `cisco_n9kv_4` (`node_id=g4hxozh`)
- Stop sync job: `401b55bf-7b0b-4590-9644-a126590d62e8` (completed)
- Start sync job: `f42f0ff8-df2f-4661-bc15-fe905c00a99e`
  - Completed at `2026-02-18T18:45:57.112382Z`

### Start job diagnostic evidence (new probe output)
- Injection still confirmed successful:
  - `Config injection: ok=True bytes=2621 partition=/dev/nbd0p4 fs=ext2 requested=/startup-config written=/startup-config,/bootflash/startup-config`
- Readiness probe now emits detailed polling lines:
  - early cycles include `console_reason=console_lock_busy` (0s, 18s, 107s)
  - active console capture cycles include `console_reason=pexpect_output`
  - serial tail samples show boot progress (kernel/init/services), e.g.:
    - `Done with Nexus 9000v initial VNIC deposit into vnicBank`
    - `Persistent log`
    - `netstack: Registration with cli server complete`
  - marker summary remained empty throughout sampled windows:
    - `markers=none`
- Job still timed out on readiness:
  - `Readiness timeout (120s): 1 node(s) still not ready: cisco_n9kv_4`
- Final node state after job:
  - `actual_state=running`, `desired_state=running`, `is_ready=false`

### What this run clarifies
- Readiness observability gap is improved: we now have direct serial evidence in the sync log instead of only `No console output available`.
- Probe lock contention is visible and likely contributes to intermittent empty reads (`console_lock_busy`).
- No `startup-config`/POAP markers were captured in the readiness polling windows (`markers=none`), so we still lack direct boot-time proof that NX-OS consumed (or rejected) staged startup-config.

## Step-1 Timeout Override Validation (2026-02-18)

### Code/deploy state
- Controller/API/worker/scheduler updated to commit:
  - `922ef05` (`fix: honor agent readiness timeout per node`)
- Change intent:
  - use agent-reported readiness timeout per node (N9Kv expected `600s`) instead of hardcoded `120s` in node sync polling.

### Fresh cycle executed
- Node: `cisco_n9kv_4` (`node_id=g4hxozh`)
- Stop job: `d9f8cba5-3211-42e8-b547-808c0fcb449c` (completed at `2026-02-18T19:00:41.614493Z`)
- Start job: `dc77dc63-c086-4795-b9a6-6d10617ad216`
  - started at `2026-02-18T19:00:44.340408Z`
  - failed at `2026-02-18T19:06:12.347328Z`
  - runtime ~`328s` (well beyond old `120s` cap)
  - error summary: `Job timed out after 300s, retrying (attempt 1)...`
- Auto-retry job created by job health monitor:
  - `77223af5-0ed1-4d30-8c9c-6035caf1792d` (completed immediately, `All nodes already in desired state`)

### Outcome
- Step-1 behavior is partially validated:
  - job no longer terminated around 120s.
- New blocker identified:
  - global sync job timeout (`job_timeout_sync=300s`) now aborts the run before node-level readiness timeout (`600s`) can be fully exercised/logged.

### Practical implication
- To continue validating N9Kv readiness/injection behavior under the new per-node timeout, controller job timeout for sync/node actions must be raised above 600s (or made adaptive to the max per-node readiness timeout in the job).

## Post-POAP-Cancel On-Box Verification (2026-02-18)

### Prompt behavior observed
- Operator observation is confirmed as expected first-boot behavior for this failure mode:
  - after POAP cancel, NX-OS requests initial credential setup (`secret` + `confirm secret`) before normal login.

### Console capture method
- Used a direct websocket client against agent console endpoint to avoid interactive timing issues:
  - `ws://10.14.23.181:8001/console/52c138e7-de39-4b4a-8daa-d75014ffe2e0/cisco_n9kv_4?provider_type=libvirt`
- Local capture artifacts:
  - script: `/tmp/n9kv_poap_capture.js`
  - log: `/tmp/n9kv-poap-capture-node.log`

### Command-level evidence
- Session reached `switch#` prompt and executed:
  - `dir bootflash:`
  - `show startup-config`
  - `show file bootflash:startup-config`
  - `show version | i NXOS|system:`
- Key outputs:
  - `dir bootflash:` includes `startup-config` with expected injected size:
    - `2621    Feb 18 19:00:46 2026  startup-config`
  - `show startup-config` returns:
    - `No startup configuration`
  - `show file bootflash:startup-config` returns full injected config content.

### Interpretation
- Bootflash file injection is succeeding (file exists with correct content and timestamp).
- NX-OS active startup-config state is still empty at runtime (`show startup-config`), so the staged bootflash file is not being auto-consumed into startup config during boot in this environment.
- This explains why POAP/first-boot behavior can still appear even when injection logs report success.

### Next targeted checks
- Capture an earlier boot window from console (before login prompt) to see explicit NX-OS decisions about loading startup config path.
- Test whether forcing `copy bootflash:startup-config startup-config` then reload changes behavior; if yes, issue is specifically auto-load path/phase, not content staging.

## Copy-to-Startup + Reload Validation (2026-02-18)

### Objective
- Execute the exact trial requested:
  - `copy bootflash:startup-config startup-config`
  - `reload`
  - verify post-reload startup behavior and startup-config state.

### Pre-reload result (successful)
- Console automation log: `/tmp/n9kv-copy-reload-verify-4.log`
- On-box sequence confirmed at `switch#`:
  - `copy bootflash:startup-config startup-config`
  - output: `Copy complete, now saving to disk (please wait)...` then `Copy complete.`
  - `show startup-config` immediately showed full config content (not empty).

### Reload confirmation and reset evidence
- Reload prompt was explicitly answered:
  - `This command will reboot the system. (y/n)?  [n]`
  - sent: `y`
- Reset evidence captured in same console transcript:
  - `%PLATFORM-2-PFM_SYSTEM_RESET: Manual system restart from Command Line Interface`

### Post-reload behavior (new blocker)
- After reboot, console repeatedly lands at `loader >` instead of NX-OS CLI/login.
- Snapshot probe output confirms loader state:
  - `loader >`
- Explicit loader boot command attempted:
  - `boot nxos bootflash:nxos64-cs.10.5.3.F.bin`
- Immediate boot failure observed:
  - `Booting nxos`
  - `Trying diskboot`
  - `Boot failed`
  - `Error 9: Unknown boot failure`
- Supporting capture:
  - `/tmp/n9kv-loader-boot-failure.txt`

### Outcome
- The requested copy+reload step was executed and copy-to-startup succeeded before reload.
- Validation is now blocked by a new issue: the node fails to boot NX-OS after reload and drops to loader with `Error 9`.
- Because NX-OS does not return to normal CLI, post-reload `show startup-config` verification cannot currently be completed.

### Implication
- Current highest-priority blocker has shifted from startup-config consumption to post-reload boot failure (`loader` / `diskboot` failure).
- Strong working hypothesis: copied startup-config lacks an explicit boot variable (`boot nxos ...`), so after reload the platform falls back to loader and cannot diskboot successfully from default path.

## Loader Command Correction + Fresh Revalidation (2026-02-18)

### Loader command correction
- Loader help output clarified syntax:
  - `usage: boot <image>`
  - from `help boot` capture (`/tmp/loader_help_boot.txt`)
- The previously used command (`boot nxos bootflash:...`) is not valid in this loader context and consistently failed with:
  - `Boot failed`
  - `Error 9: Unknown boot failure`
- Corrected command that successfully starts boot from loader:
  - `boot bootflash:nxos64-cs.10.5.3.F.bin`
  - evidence in `/tmp/loader_boot_plain_noslash.txt`:
    - `Image valid`
    - kernel boot lines begin immediately after.

### Fresh controller stop/start cycle after recovery
- Desired-state stop request created and completed:
  - Job `22c88af9-7e55-4728-addc-d93cab45419d` (`sync:node:g4hxozh`) completed.
- Desired-state running request created:
  - Job `b42bc3a5-6bfa-43b5-8240-8d4579a56627` (`sync:node:g4hxozh`)
  - remained `running` until ~300s and then failed (same timeout pattern).
- Auto-retry superseding job:
  - `d5bbf8fc-f534-4365-85bf-3974698954dc` completed with `All nodes already in desired state`.

### Console state after fresh start
- Short console snapshot after this cycle shows node back at POAP prompt:
  - `Abort Power On Auto Provisioning ... (yes/no)[no]:`
- Current-state POAP-abort capture (`/tmp/n9kv-poap-poststart.log`) reconfirmed:
  - POAP rollback + first-boot account setup prompts appeared.
  - After login with setup password, CLI checks show:
    - `show startup-config` -> `No startup configuration`
    - `show file bootflash:startup-config` -> full injected config content
    - `dir bootflash:` -> `startup-config` present (`2621` bytes; timestamp aligned with deployment cycle).

### What this changes
- Reload failure diagnosis is refined: command syntax in loader mattered.
- Core startup-config issue remains unchanged on fresh deploy:
  - file injection to bootflash succeeds
  - NX-OS active startup-config remains empty at boot
  - device still enters POAP path.

## Post-Boot Auto-Copy Mitigation Prep (2026-02-18)

### Additional on-box evidence gathered
- Live console diagnostics (`/tmp/n9kv-state-diag2.log`) showed:
  - `show boot`:
    - `NXOS variable not set`
    - `Boot POAP Disabled`
  - `show startup-config` initially empty in failing cycle.
  - `dir bootflash:` confirmed injected `startup-config` exists (`2621` bytes).
  - `dir volatile:` / `dir logflash:` showed no obvious `startup-config` file path.
- Hidden path probe (`/tmp/n9kv-hidden-dirs.log`) showed:
  - `.swtam` and `.bootupfiles` directories exist but no visible startup-config file in those paths.
  - `logflash:/vdc_1` only exposed accounting/event files in CLI listing.
- POAP log extraction (`/tmp/n9kv-poap-log-probe.log`) confirmed POAP DHCP failure details:
  - repeated `missing tlv: bootfile`
  - repeated `missing tlv: tftp server address`
  - repeated `No interface with required config`
  - no evidence in captured POAP logs that bootflash `startup-config` is auto-imported.

### Code mitigation implemented (not yet deployed to remote agent)
- Added N9Kv post-boot command automation:
  - `agent/vendors.py` (`cisco_n9kv`): `post_boot_commands = ["copy bootflash:startup-config startup-config"]`
- Hardened serial login/onboarding handling:
  - `agent/console_extractor.py` now handles first-boot NX-OS prompts in `_handle_login`:
    - POAP abort prompt
    - secure-password-standard prompt
    - `Enter/Confirm the password for "admin"` prompts
    - basic/initial configuration dialog prompt
  - Added strong fallback bootstrap password (`Archetype123!`) for first-boot password policy.
- Ensured VM post-boot command cache is cleared when a libvirt VM is removed/undefined:
  - `agent/providers/libvirt.py` clears post-boot cache in stop/destroy/stale-domain/orphan cleanup paths.

### Unit test coverage for mitigation
- Added/updated tests:
  - `agent/tests/test_n9kv_vendor_config.py` (N9Kv post-boot command presence)
  - `agent/tests/test_events_console_logging_version_batch2.py` (first-boot prompt handling in login flow)
  - `agent/tests/test_plugins_providers_batch1.py` (cache clear on VM removal)
- Targeted run result:
  - `pytest -q agent/tests/test_events_console_logging_version_batch2.py agent/tests/test_plugins_providers_batch1.py agent/tests/test_n9kv_vendor_config.py`
  - `68 passed`

### Next validation step
- Deploy these agent-side changes to `agent-01` (`10.14.23.181`) and rerun a fresh `stop` + `start` cycle.
- Success criteria:
  - no manual console intervention required
  - post-boot automation runs `copy bootflash:startup-config startup-config`
  - node reaches configured state without manual POAP cancel/password setup.

## Forward Options Under Evaluation (2026-02-18)

### Why this decision is needed
- Current post-boot mitigation improves recovery, but it does not change the earliest boot decision.
- NX-OS can still enter POAP before post-boot automation runs.
- Host moves are currently destroy/recreate, not disk-preserving migration, so first-boot behavior repeats on destination host.

### Option 1: Disk-preserving host move/migration
- Goal:
  - keep the same VM disk/NVRAM state when moving hosts so node does not re-enter first-boot path.
- Pros:
  - best continuity of runtime state across host moves.
  - avoids repeated first-boot onboarding if migration is true state transfer.
- Cons:
  - significantly larger scope (cross-host disk transfer/shared storage, libvirt migration orchestration, failure rollback, placement/state updates).
  - higher operational complexity and testing burden.

### Option 2: True pre-boot provisioning path (recommended first)
- Goal:
  - make config available in the boot path that NX-OS consumes before POAP decision point.
- Candidate implementations:
  - POAP script path (DHCP/TFTP/HTTP metadata + script serving).
  - image-level seed path validated by NX-OS for this image build.
- Pros:
  - directly addresses root requirement: no POAP dependency for config application.
  - smaller and more targeted than full host migration feature work.
- Cons:
  - requires precise NX-OS/POAP expectations per image version and careful validation.

### Recommended execution order
1. Implement Option 2 (pre-boot provisioning) and validate first-boot behavior on fresh create and after host move.
2. Keep post-boot automation as fallback/recovery guardrail.
3. Revisit Option 1 only if state-preserving host migration is still a product requirement beyond config boot behavior.

## Pre-Boot POAP Prototype Implemented (2026-02-18)

### Scope of implementation
- Added an **opt-in** pre-boot path for N9Kv POAP bootstrap on agent/libvirt side.
- This is intentionally gated to avoid changing current behavior unless enabled.

### Code changes
- `agent/config.py`
  - Added setting:
    - `ARCHETYPE_AGENT_N9KV_POAP_PREBOOT_ENABLED` (default `false`)
- `agent/providers/libvirt.py`
  - Added per-node N9Kv management network provisioning helper:
    - deterministic libvirt network + bridge naming
    - deterministic subnet allocation
    - DHCP `bootp` options with script URL and server IP
  - Added management network resolver:
    - when kind is `cisco_n9kv` and preboot flag is enabled, use POAP network
    - fallback to libvirt `default` network if POAP network setup fails
  - Added cleanup hooks:
    - remove per-node POAP network on VM remove/destroy/orphan cleanup paths
  - Updated dedicated mgmt NIC detection:
    - treat any libvirt `interface type='network'` as dedicated mgmt NIC (not only `default`)
- `agent/main.py`
  - Added unauthenticated POAP endpoints under `/poap/...` (required for DHCP bootstrap clients):
    - `GET /poap/{lab_id}/{node_name}/script.py`
    - `GET /poap/{lab_id}/{node_name}/startup-config`
  - Script endpoint generates a POAP Python script that:
    - fetches startup-config from agent endpoint
    - writes `/bootflash/startup-config`
    - runs `copy bootflash:startup-config startup-config`
    - applies/saves config (`copy startup-config running-config`, `copy running-config startup-config`)

### Test coverage added
- `agent/tests/test_n9kv_poap_endpoints.py`
  - startup-config endpoint serves workspace config
  - script endpoint contains expected config URL and copy command
  - missing config returns 404
- `agent/tests/test_auth_middleware.py`
  - `/poap/...` path is exempt from auth middleware token enforcement
- `agent/tests/test_libvirt_preflight_default_network.py`
  - management network resolution prefers POAP network when enabled
  - fallback to `default` network works
  - dedicated mgmt NIC detection works with custom network names

### Local validation status
- Targeted pytest run:
  - `61 passed` (auth middleware, POAP endpoints, libvirt preflight, provider regression set)

### Next validation step
- Deploy this change set to `agent-01`.
- Enable env on agent service:
  - `ARCHETYPE_AGENT_N9KV_POAP_PREBOOT_ENABLED=true`
- Run fresh N9Kv stop/start and host-move cycle.
- Confirm in console whether POAP auto-fetches script and applies config without manual abort/password flow.

## Deployment + Pre-Boot Activation Validation (2026-02-18)

### Remote deploy completed on agent-01
- Host: `x300` (`10.14.23.181`)
- Repo path: `/opt/archetype-agent/repo`
- Deployed commit:
  - `5e5c13b` (`git pull --ff-only origin main` on remote host)
- Agent runtime health (local-on-host check):
  - `http://127.0.0.1:8001/health` returned commit `5e5c13b77599cddab5a77d6666835c55693472e1`

### Feature flag enabled via systemd drop-in
- Drop-in file:
  - `/etc/systemd/system/archetype-agent.service.d/n9kv-poap.conf`
- Content:
  - `[Service]`
  - `Environment=ARCHETYPE_AGENT_N9KV_POAP_PREBOOT_ENABLED=true`
- `systemctl show archetype-agent` confirmed:
  - `Environment=ARCHETYPE_AGENT_N9KV_POAP_PREBOOT_ENABLED=true`
  - `DropInPaths=/etc/systemd/system/archetype-agent.service.d/n9kv-poap.conf`

### Agent-side pre-boot endpoint validation
- `GET /poap/{lab}/{node}/script.py` returned generated script content including:
  - `CONFIG_URL = "http://127.0.0.1:8001/poap/.../startup-config"`
  - `copy bootflash:startup-config startup-config`

### N9Kv cycle performed directly via agent API
- Lab/node:
  - `lab_id=52c138e7-de39-4b4a-8daa-d75014ffe2e0`
  - `node=cisco_n9kv_4`
- Direct stop:
  - success (`status=stopped`)
- Direct start immediately after stop:
  - failed as expected in per-node API flow (`Domain not found`) because libvirt stop removes domain/disks.
- Direct create + start sequence:
  - `create` succeeded with injection details:
    - `Config injection: ok=True bytes=2621 partition=/dev/nbd0p4 fs=ext2 requested=/startup-config written=/startup-config,/bootflash/startup-config`
  - `start` succeeded (`status=running`)

### Libvirt POAP network provisioning evidence
- Agent journal logged:
  - `Created N9Kv POAP network ap-poap-e9d5a7f014 ... bootfile=http://10.105.213.1:8001/poap/.../script.py`
- `virsh net-dumpxml ap-poap-e9d5a7f014` confirmed DHCP BOOTP settings:
  - `<bootp file='http://10.105.213.1:8001/poap/.../script.py' server='10.105.213.1'/>`
- This confirms the **pre-boot** POAP script delivery path is wired into the VM management network for this node.

### Remaining validation needed
- Capture console from this specific boot to confirm NX-OS actually fetches/runs the script and bypasses manual POAP cancel/password flow end-to-end.
- Repeat once with host-move/recreate scenario to verify behavior survives relocation.

## POAP DHCP Metadata Root Cause + TFTP Follow-up (2026-02-18)

### Root-cause evidence from isolated serial capture (pre-fix)
- Capture method:
  - stop/create/start node via agent API
  - temporarily stop `archetype-agent` for exclusive serial capture (to avoid console lock contention)
  - capture file: `/tmp/n9kv_virsh_console_bootsolo_20260218_125219.log`
- Critical POAP lines observed:
  - `Recieved DHCP offer from server ip  - 10.105.213.1`
  - `Invalid DHCP OFFER: Missing Script Server information`
  - `POAP DHCP discover phase failed`
- Interpretation:
  - N9Kv is receiving DHCP from the correct POAP network gateway, but rejects offer metadata.

### Fix 1 deployed: force DHCP script-server/script-name options
- Commit: `5c4bf6f` (`fix(n9kv): add dhcp script-server options for poap network`)
- Change summary:
  - Added dnsmasq options in per-node POAP network:
    - `dhcp-option-force=66,<gateway>`
    - `dhcp-option-force=67,<script>`
  - Added reconciliation logic to recreate stale existing POAP networks missing required options.
- After deploy, capture changed to:
  - `Using DHCP, valid information received over mgmt0 from 10.105.213.1`
  - `Script Server: 10.105.213.1`
  - `Script Name: http://10.105.213.1:8001/.../script.py`
  - then failure:
    - `The POAP Script is being downloaded from [copy tftp://10.105.213.1/http://10.105.213.1:8001/... ]`
    - `POAP boot file download failed`
- Interpretation:
  - Metadata was accepted, but this NX-OS POAP path still used TFTP semantics and treated the HTTP URL as a TFTP path.

### Fix 2 deployed: true TFTP bootfile staging
- Commit: `56a4344` (`fix(n9kv): stage poap script via tftp metadata`)
- Change summary:
  - Stage per-node `script.py` under deterministic TFTP root:
    - `/var/lib/archetype-agent/.poap-tftp/ap-poap-e9d5a7f014/script.py`
  - POAP network XML now includes:
    - `<tftp root='.../.poap-tftp/ap-poap-e9d5a7f014'/>`
    - `<bootp file='script.py' server='10.105.213.1'/>`
    - option `67=script.py` (instead of HTTP URL).
- Verified on host via:
  - `virsh -c qemu:///system net-dumpxml ap-poap-e9d5a7f014`

### Post-fix serial validation (agent paused for exclusive capture)
- Capture file: `/tmp/n9kv_virsh_console_tftpcheck_20260218_132032.log`
- Critical POAP lines observed:
  - `Script Name: script.py`
  - `The POAP Script is being downloaded from [copy tftp://10.105.213.1/script.py ...]`
  - `POAP_SCRIPT_DOWNLOADED ... Successfully downloaded POAP script file`
  - `POAP script execution started`
  - `POAP Script execution failed`
- Note:
  - This run intentionally had `archetype-agent` stopped during capture; script execution failure here is expected because the script fetches startup-config from agent HTTP endpoint.

### Fix 3 deployed: POAP script Python runtime compatibility
- Commit: `2d15f75` (`fix(n9kv): make poap script compatible with poap python`)
- Change summary:
  - Script switched to urllib import fallback:
    - `urllib2` (Py2) or `urllib.request` (Py3)

### Current blocker in live-agent runs
- In full live-agent cycles (agent left running), no `POAP startup-config request` logs were observed.
- Concurrently, agent logs show repeated console automation churn for this domain:
  - repeated `Retrying post-boot commands ...`
  - repeated `Post-boot commands failed ... Console connection closed unexpectedly`
  - frequent virsh console lock/process kills.
- Working hypothesis:
  - aggressive post-boot console automation is contending with first-boot POAP script execution timing and preventing stable end-to-end completion.

### Next concrete step
1. Temporarily suppress or defer libvirt post-boot command runner for `cisco_n9kv` when `ARCHETYPE_AGENT_N9KV_POAP_PREBOOT_ENABLED=true`.
2. Re-run one clean stop/create/start cycle and verify:
  - POAP script execution completes
  - `POAP startup-config request` appears in agent logs
  - node avoids fallback manual POAP/first-boot flow.

## Post-Boot Suppression + Live POAP Fetch Signal (2026-02-18)

### Code changes deployed
- Commit: `37a73be` (`fix(n9kv): defer post-boot automation and add poap debug logs`)
- Key behavior changes:
  - libvirt provider now skips console-based post-boot automation for N9Kv when:
    - `ARCHETYPE_AGENT_N9KV_POAP_PREBOOT_ENABLED=true`
  - POAP script now emits richer diagnostics and traceback to:
    - `/bootflash/poap_archetype_debug.log`
- Remote host (`10.14.23.181`) health confirmed on deployed SHA:
  - `37a73be43796275c2ab0c045d22679bf159c6864`

### Live-cycle validation (agent online, no manual console intervention)
- Stop/create/start cycle executed for:
  - lab `52c138e7-de39-4b4a-8daa-d75014ffe2e0`
  - node `cisco_n9kv_4`
- Agent logs in epoch-bounded window now show:
  - post-boot suppression active:
    - `Skipping post-boot console automation for arch-...-cisco_n9kv_4 while N9Kv pre-boot POAP is enabled`
  - critical success signal:
    - `POAP startup-config request`
    - client host: `10.105.213.166` (N9Kv POAP mgmt address on per-node subnet)

### What this confirms
- POAP path is now reaching script execution far enough to issue runtime fetch of startup-config from agent endpoint.
- The earlier blocking failure mode (POAP script download/exec failing before config fetch) has moved forward materially after:
  - DHCP option fixes
  - TFTP script staging
  - post-boot console suppression

### Remaining check
- We now have direct runtime fetch evidence, but still need one explicit final verification that on-box active startup-config is populated without manual intervention:
  - `show startup-config` should no longer be empty after boot settles.

## POAP Bypass Clarification + Hardening (2026-02-19)

### Clarification: what "bypass POAP" flag exists
- No external libvirt/QEMU "skip POAP" boot flag has been identified for this N9Kv image path.
- The practical bypass control is in-guest NX-OS config:
  - `system no poap`
- Implication:
  - we still need one successful bootstrap path on first bring-up (POAP script or manual/automated console bootstrap),
  - then persist `system no poap` so subsequent boots of the same VM state do not re-enter POAP.

### Code hardening added
- Updated POAP script generator (`agent/n9kv_poap.py`) to run:
  - `configure terminal ; system no poap ; end`
  - before copy/import/apply/save steps.
- Post-boot fallback command expansion in `agent/vendors.py` was evaluated but deferred in this turn to avoid mixing with unrelated concurrent edits in that file.

### Validation status (local)
- Targeted test runs passed:
  - `pytest -q agent/tests/test_n9kv_poap_endpoints.py agent/tests/test_n9kv_vendor_config.py`
  - `pytest -q agent/tests/test_plugins_providers_batch1.py agent/tests/test_libvirt_preflight_default_network.py`

### Remaining field validation
- Deploy and run one clean N9Kv create/start cycle, then verify on-box:
  - `show startup-config` is populated,
  - `show boot` reflects POAP disabled after bootstrap/save.
- Reboot same VM instance and confirm no POAP abort/password wizard path is presented.

## Field Validation: Recreate + Same-VM Reboot (2026-02-19)

### Recreate/start cycle (fresh overlay)
- Executed on `agent-01` (`10.14.23.181`) against:
  - lab `52c138e7-de39-4b4a-8daa-d75014ffe2e0`
  - node `cisco_n9kv_4`
- Direct agent API sequence:
  - `stop` -> success
  - `create` -> success, injection confirmed:
    - `Config injection: ok=True bytes=2621 ... written=/startup-config,/bootflash/startup-config`
  - `start` -> success (`status=running`)
- Agent logs during this cycle confirmed:
  - `Created N9Kv POAP network ... (bootfile=script.py)`
  - `POAP startup-config request` at `2026-02-19T00:31:11Z` (client `10.105.213.166`)

### Same-VM reboot check (state-preserving)
- Performed in-place reboot (no destroy/recreate):
  - `virsh -c qemu:///system reboot arch-52c138e7-de39-4b4a-8-cisco_n9kv_4 --mode acpi`
- Observation window after reboot:
  - repeated readiness/post-boot suppression logs continued,
  - **no new** `POAP script request` or `POAP startup-config request` entries appeared.
- VM remained `running` after reboot (`virsh domstate`).

### Same-VM hard reset check (state-preserving, hypervisor-forced)
- Performed hypervisor-level reset to remove ACPI-guest ambiguity:
  - `virsh -c qemu:///system reset arch-52c138e7-de39-4b4a-8-cisco_n9kv_4`
- Observation window after reset:
  - no `POAP script request` / `POAP startup-config request` log entries were emitted.
- VM remained `running` after reset (`virsh domstate`).

### Interpretation
- New evidence indicates first bootstrap still enters POAP path (expected for fresh state), but after bootstrap/save, subsequent same-state reboot/reset did not re-enter POAP endpoint flow.
- This is consistent with `system no poap` being persisted by the POAP bootstrap path.

### Remaining gap
- Automated serial probe did not reach a stable CLI prompt in this cycle (`prompt_ok=False`), so direct on-box `show running-config/startup-config` confirmation is still pending.
- A dedicated, stable console capture/interaction method is still needed for final CLI-level proof.

## Agent CLI-Verify Path + Live Evidence (2026-02-19)

### New implementation
- Added a dedicated libvirt CLI verification API path:
  - `POST /labs/{lab_id}/nodes/{node_name}/cli-verify?provider=libvirt`
- Purpose:
  - run serial-console commands through agent-managed lock/retry flow,
  - return structured per-command output (instead of ad-hoc interactive virsh sessions).
- Core commits:
  - `ad6fb95` — initial endpoint + serial capture plumbing + tests
  - `dce2b3e` — resilient login loop behavior
  - `add0750` — bound login retries to timeout budget
  - `5aaf44a` — password fallback sequencing
  - `6d1e979` — include serial buffer tail in failure details
  - `6fd35ca` — request overrides (prompt/credentials/enable/paging) for loader/advanced cases
  - `b558d6a` — treat `Login incorrect` as password fallback signal

### Loader-state diagnosis confirmed by endpoint
- Initial `cli-verify` attempts failed with error tail showing:
  - `loader >`
- This provided direct proof that prior failures were not only POAP-related; VM was in loader state in this cycle.

### Loader recovery command attempt via endpoint overrides
- Used override-capable `cli-verify` request with:
  - `prompt_pattern=loader >\\s*$`
  - `attempt_enable=false`
  - command: `boot bootflash:nxos64-cs.10.5.3.F.bin`
- Result:
  - command execution did not return to prompt before timeout (consistent with boot handoff behavior).

### Post-recovery CLI capture succeeded
- Subsequent normal `cli-verify` returned successful command capture (`commands_run=4/4` then `3/3` on extended checks).
- Key outputs captured:
  - `show version`:
    - NX-OS up (`version 10.5(3)`), uptime ~5 minutes, image `bootflash:///nxos64-cs.10.5.3.F.bin`.
  - `show running-config`:
    - full running config present (including user/feature/interface sections).
  - `show startup-config`:
    - `No startup configuration`
  - `show boot | i POAP` (and earlier `show boot | include POAP`):
    - `Boot POAP Disabled`
    - `System-wide POAP is disabled using exec command 'system no poap'`

### What this establishes now
- We now have stable, repeatable API-driven CLI evidence without manual interactive console handling.
- In this environment, a contradictory NX-OS state is visible:
  - running config exists,
  - startup config remains empty,
  - POAP is reported disabled system-wide.
- This explains why relying solely on `show startup-config` as persistence proof remains insufficient for this image/path.

## Copy/Import Behavior Clarified (2026-02-19)

### Scope and intent
- Goal in this pass:
  - validate the minimal pre-handoff flow (no extraction, no reboot) needed to place N9Kv in desired state,
  - determine exactly what `copy` step causes `startup-config` to become populated.

### Live sequence and evidence
- Node/runtime:
  - lab `52c138e7-de39-4b4a-8daa-d75014ffe2e0`
  - node `cisco_n9kv_4`
  - image `bootflash:///nxos64-cs.10.5.3.F.bin` (`show version`)
- Baseline before copy/import:
  - `show startup-config` -> `No startup configuration`
- Executed command:
  - `copy bootflash:startup-config running-config`
- Returned output from NX-OS:
  - `Copy complete, now saving to disk (please wait)...`
  - `Copy complete.`
- Post-command verification:
  - `show running-config` -> full intended config present
  - `show startup-config` -> full config present with
    - `!Startup config saved at: Thu Feb 19 01:38:51 2026`
  - `show boot | i POAP` -> `Boot POAP Disabled`

### Interpretation
- In this NX-OS path, `copy bootflash:startup-config running-config` is not just an in-memory merge; it also triggers save-to-disk behavior (as indicated by the command output and populated `show startup-config` immediately after).
- This explains why manual copy/import appears to "fix boot state" in ways simple file staging alone does not.

### Practical handoff flow (no reboot, no extraction)
- If extraction is deferred and immediate runtime state is the handoff objective:
  1. get past POAP/first-login prompts,
  2. run `copy bootflash:startup-config running-config`,
  3. verify `show startup-config` is non-empty.
- `copy running-config startup-config` can be kept as an explicit safeguard, but current evidence indicates the bootflash->running copy already persisted startup on this image/build.

### Additional operational note
- Agent `stop` for libvirt is destructive in this branch:
  - it destroys/undefines the domain and removes overlay disks.
- Therefore `stop`/`start` is not equivalent to same-VM reboot for persistence checks; use in-guest reload or hypervisor reset/reboot if true same-state validation is required.

## POAP Script Sequence Update (2026-02-19)

### Change implemented
- Updated `agent/n9kv_poap.py` script generator to execute the proven command path first and fail loudly on command errors:
  1. `configure terminal ; system no poap ; end`
  2. `copy bootflash:startup-config running-config` (preferred path)
  3. `copy running-config startup-config` (explicit persistence)
- Added compatibility fallback if direct copy fails:
  - `copy bootflash:startup-config startup-config`
  - `copy startup-config running-config`
  - then `copy running-config startup-config`

### Why this matters
- Prior script behavior swallowed command exceptions, which could mask failed import/apply steps and still continue POAP flow.
- New behavior raises on command failure, and uses the import sequence that was validated live to populate active startup-config on this image path.

### Local verification
- Tests updated/passed:
  - `pytest -q agent/tests/test_n9kv_poap_endpoints.py`
  - `pytest -q agent/tests/test_auth_middleware.py -k poap`
  - `pytest -q agent/tests/test_n9kv_vendor_config.py`
