# Cross-Host Reliability Issues — Feb 7, 2026

Observed during investigation of repeated cross-host ping failures. Multiple
independent issues compound to make the system fragile after any disruption.

---

## Issue 1: Startup Config Not Pushed to Remote Agents on Deploy

**Severity:** High
**Symptom:** After lab restart, remote cEOS containers boot with minimal default
config (no IP addresses, `no ip routing`). Local containers get their configs.

**Root Cause:** The deploy flow (`update_config_on_agent()`) pushes configs from
the API file workspace (`/var/lib/archetype/{lab_id}/configs/`) to agent
workspaces. But configs may only exist in the `config_snapshots` DB table (from
a previous "Extract Configs" operation), not in the API filesystem workspace.

**Evidence:**
- DB `config_snapshots` table has correct configs (with IPs) for ceos_2/ceos_4
- Remote agent workspace has minimal configs (just hostname + interfaces, no IPs)
- Local agent's containers get configs correctly (API filesystem workspace exists locally)

**Fix Needed:** During deploy, fall back to `config_snapshots` DB table when
the API workspace file doesn't exist. Or ensure Extract Configs always writes
to both the DB and the API filesystem.

**Files:** `api/app/tasks/jobs.py` (deploy flow), `api/app/agent_client.py`
(`update_config_on_agent()`), `api/app/tasks/config_service.py`

---

## Issue 2: Docker OVS Plugin Socket Race on Host Reboot

**Severity:** High
**Symptom:** After host reboot, containers fail with exit code 255:
`failed to set up container networking: /run/docker/plugins/archetype-ovs.sock:
connect: no such file or directory`

**Root Cause:** Docker starts containers (with `restart: unless-stopped`) before
the archetype agent container creates the OVS plugin socket. The agent must:
1. Start up
2. Initialize the Docker OVS plugin
3. Create the Unix socket at `/run/docker/plugins/archetype-ovs.sock`

By the time this happens, Docker has already tried and failed to restart the
lab containers.

**Evidence:**
- `docker inspect` shows exit code 255 with OVS socket error
- Socket exists after agent starts, but containers are already in "exited" state
- Happens consistently on every host reboot

**Fix Options:**
1. Set `restart: no` on lab containers (agent manages lifecycle, not Docker)
2. Agent detects containers in "exited" state with OVS socket error and restarts them
3. Add a systemd dependency or health-check loop that delays container restart
   until the OVS plugin socket exists

**Files:** `agent/network/docker_plugin.py` (socket creation),
`agent/main.py` (startup), container creation in `agent/providers/docker.py`

---

## Issue 3: Link Connection Not Happening After Deploy

**Severity:** High
**Symptom:** After lab restart, links stay in `down` (cross-host) or `unknown`
(same-host) state. Containers are running but no L2 connectivity.

**Evidence:**
- `link_states` table shows `desired_state=up` but `actual_state=down/unknown`
- cEOS interfaces show `down/down` Protocol status
- Old links (eth naming) marked `desired_state=deleted`, new links (Ethernet
  naming) created but never connected
- No VXLAN tunnels created for cross-host links after redeploy

**Root Cause:** TBD — need to investigate why the deploy job doesn't connect
links, or why the reconciliation/auto-connect isn't picking them up. Possibly
related to the link naming change (eth → Ethernet) creating confusion.

**Files:** `api/app/tasks/jobs.py` (deploy flow), `api/app/tasks/live_links.py`
(auto-connect), `api/app/tasks/link_orchestration.py` (cross-host link setup),
`api/app/tasks/reconciliation.py` (periodic reconciliation)

---

## Issue 4: State Drift — DB Shows "running" When Containers Are Stopped

**Severity:** Medium
**Symptom:** DB `node_states` shows `actual_state=running` for ceos_2/ceos_4,
but containers are actually stopped on the remote agent.

**Root Cause:** Reconciliation runs every ~60s but only queries agents for labs
in transitional states. If a container stops between reconciliation cycles, and
the lab was already in "running" state, the state drift persists until something
triggers re-reconciliation.

**Fix Needed:** Reconciliation should periodically verify ALL running nodes,
not just transitional ones. Or the agent heartbeat should report container
state changes proactively.

**Files:** `api/app/tasks/reconciliation.py` (`refresh_states_from_agents()`),
`agent/main.py` (heartbeat)

---

## Issue 5: Stale VXLAN Port Accumulation (FIXED)

**Severity:** High (was causing unicast blackholing)
**Status:** Fixed in commit 3527e89

**Root Cause:** After agent restart, in-memory VXLAN tracking was empty.
Stale ports from old deployments shared VLAN tags with valid ports, causing
OVS to split traffic between tunnels with different VNIs.

**Fix:** API-driven VXLAN port reconciliation. The API sends valid port names
to each agent every ~5 min; agent deletes anything not in the list.

---

## Issue 6: Remote Host Crash Under Load

**Severity:** Unknown — needs investigation
**Symptom:** Remote host became completely unreachable (no ping response) during
lab operations. Required hard reboot.

**Evidence:**
- 58GB RAM, only 1.3GB used after reboot (not OOM)
- No OOM killer messages in dmesg
- Only archetype agent running on the host
- Happened while applying configs via debug/exec endpoint

**Possible Causes:**
- Kernel panic from OVS operations
- Docker daemon crash cascading to network loss
- OVS bridge misconfiguration taking down the host network stack

**Investigation Needed:** Check `/var/log/kern.log`, `journalctl -b -1`,
and OVS logs from before the crash.

---

## Priority Order

1. **Issue 3** (links not connecting) — Lab is non-functional without this
2. **Issue 1** (config sync) — Nodes deploy with wrong config
3. **Issue 2** (OVS socket race) — Containers fail on every reboot
4. **Issue 4** (state drift) — Misleading UI, delayed recovery
5. **Issue 6** (host crash) — Needs investigation first
