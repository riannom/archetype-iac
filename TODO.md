# Archetype-IAC TODO

## Current Status (2026-01-19)

### Working
- [x] Unified installer (`install.sh`) - handles controller, agent, or both
- [x] Agent registration with correct IP addresses
- [x] Single-host lab deployment via containerlab
- [x] Topology parsing strips `host` field for containerlab compatibility
- [x] JWT authentication
- [x] Database auto-creation on startup
- [x] Multi-host deployment (nodes go to correct agents based on `host` field)
- [x] VXLAN overlay networking between hosts (tested and verified)
- [x] Unique MAC addresses for overlay interfaces (fixed 2026-01-20)
- [x] Automatic IP assignment on overlay interfaces (from topology)
- [x] Stale agent cleanup (marks agents offline after 90s without heartbeat)
- [x] Console access for multi-host labs (routes to correct agent)

### Not Working / Incomplete
- [ ] Console access not tested end-to-end (code reviewed, enhanced for multi-host)

### Improved (Workarounds)
- [x] Agent installer interactive mode - gives helpful error with correct command when piped

---

## Priority 1: Multi-Host Deployment ✅ COMPLETE

**Goal:** When topology specifies `host: agent-name` on nodes, deploy those nodes to the specified agent.

### Implementation (Completed 2026-01-20)

The multi-host deployment is fully implemented and tested:

1. **API Detection** (`api/app/main.py`)
   - `lab_up()` analyzes topology using `analyze_topology()` from topology.py
   - If `single_host=False`, routes to `run_multihost_deploy()`
   - `lab_down()` uses `run_multihost_destroy()` for multi-host labs

2. **Topology Splitting** (`api/app/topology.py`)
   - `analyze_topology()` detects host assignments and cross-host links
   - `split_topology_by_host()` creates per-host sub-topologies
   - Cross-host links excluded from sub-topologies (handled by overlay)

3. **Multi-Host Deploy** (`api/app/main.py:run_multihost_deploy()`)
   - Maps host names in topology to registered agents via `get_agent_by_name()`
   - Deploys sub-topology to each agent in parallel
   - Sets up VXLAN overlay links for cross-host connections

4. **VXLAN Overlay** (`agent/network/overlay.py`)
   - Agent has `/overlay/tunnel`, `/overlay/attach`, `/overlay/cleanup` endpoints
   - Controller calls `setup_cross_host_link()` to create bidirectional VXLAN tunnels
   - Veth pairs use random suffixes to ensure unique MAC addresses

### Usage

Create a topology with `host:` and `ipv4:` fields:

```yaml
nodes:
  r1:
    kind: linux
    image: alpine:latest
    host: local-agent
  r2:
    kind: linux
    image: alpine:latest
    host: host-b
links:
  - r1:
      ifname: eth1
      ipv4: 10.0.0.1/24
    r2:
      ifname: eth1
      ipv4: 10.0.0.2/24
```

The system will:
1. Deploy r1 to local-agent, r2 to host-b
2. Create VXLAN tunnel between agents (VNI auto-allocated)
3. Attach container interfaces to overlay bridge
4. Configure IP addresses automatically from topology

---

## Priority 2: Automatic IP Assignment ✅ COMPLETE

**Goal:** Automatically assign IP addresses to cross-host link interfaces

### Implementation (Completed 2026-01-19)

IP addresses are now parsed from the topology YAML and configured automatically:

1. **Schema Updates**
   - `GraphEndpoint` now has `ipv4` and `ipv6` fields
   - `CrossHostLink` now has `ip_a` and `ip_b` fields

2. **Topology Parsing** (`api/app/topology.py`)
   - `_parse_link_item()` extracts `ipv4`/`ipv6` from link endpoints
   - `analyze_topology()` includes IPs in `CrossHostLink` objects

3. **Agent Configuration** (`agent/network/overlay.py`)
   - `attach_container()` accepts optional `ip_address` parameter
   - Uses `nsenter` to configure IP inside container namespace

4. **Data Flow**
   - Controller extracts IPs from topology
   - Passes IPs through `setup_cross_host_link()`
   - Agent configures IPs after attaching container to bridge

### Usage

Specify IPs in your topology:

```yaml
links:
  - r1:
      ifname: eth1
      ipv4: 192.168.100.1/30
    r2:
      ifname: eth1
      ipv4: 192.168.100.2/30
```

IPs are configured automatically during deployment - no manual configuration needed.

---

## Priority 3: Stale Agent Cleanup ✅ COMPLETE

**Goal:** Agents that stop sending heartbeats should be marked "offline"

### Implementation (Completed 2026-01-19)

Fixed timezone-aware datetime handling and NULL heartbeat edge case:

1. **Timezone Fix**
   - Changed from `datetime.utcnow()` to `datetime.now(timezone.utc)`
   - Applied to all heartbeat comparisons and assignments

2. **NULL Heartbeat Handling**
   - `update_stale_agents()` now marks agents offline if `last_heartbeat` is NULL
   - Catches agents that registered but never sent a heartbeat

3. **Files Changed**
   - `api/app/agent_client.py` - Fixed datetime comparisons
   - `api/app/routers/agents.py` - Fixed datetime assignments

### Behavior

- Agents are marked offline if:
  - `last_heartbeat` is older than 90 seconds, OR
  - `last_heartbeat` is NULL
- Health check runs every 30 seconds

---

## Priority 4: Console Access ✅ ENHANCED

**Goal:** WebSocket console access to nodes regardless of which host they're on

### Implementation (Completed 2026-01-19)

Enhanced console proxy for multi-host support:

1. **Multi-Host Routing** (`api/app/main.py:console_ws()`)
   - Reads lab topology to determine which agent hosts the node
   - Routes console WebSocket to the correct agent
   - Falls back to lab's primary agent for single-host labs

2. **Components**
   - Controller proxy: `api/app/main.py` (`console_ws` function)
   - Agent handler: `agent/console/docker_exec.py` (`DockerConsole` class)
   - Agent endpoint: `agent/main.py` (`/console/{lab_id}/{node_name}`)

### Usage

Connect to console via WebSocket:
```
ws://controller:8000/labs/{lab_id}/nodes/{node_name}/console
```

For multi-host labs, the controller automatically routes to the correct agent.

---

## Installation

### Fresh Install (Controller + Agent)

```bash
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | sudo bash
```

### Multi-Host Setup

```bash
# On controller host
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | sudo bash

# On agent hosts
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | \
  sudo bash -s -- --agent --controller-url http://CONTROLLER_IP:8000 --name host-b
```

### Clean Reinstall (resets database)

```bash
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | \
  sudo bash -s -- --fresh
```

### Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | \
  sudo bash -s -- --uninstall
```

### Update Existing Installation

```bash
# Controller
cd /opt/archetype-controller
sudo git pull origin main
sudo docker compose -f docker-compose.gui.yml up -d --build

# Standalone agent
cd /opt/archetype-agent/repo
sudo git pull origin main
sudo systemctl restart archetype-agent
```

---

## Test Environment

- **Host A (Controller + Agent):** 10.14.23.36
- **Host B (Agent only):** 10.14.23.11
- **Admin:** admin@localhost / (check /opt/archetype-controller/.env)

---

## Commands Cheatsheet

```bash
# Check agents
curl -s http://localhost:8000/agents | jq '.[] | {name, address, status}'

# Login and get token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@localhost&password=PASSWORD" | jq -r '.access_token')

# Create lab
LAB_ID=$(curl -s -X POST http://localhost:8000/labs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "test-lab"}' | jq -r '.id')

# Import multi-host topology with IPs
curl -s -X POST "http://localhost:8000/labs/${LAB_ID}/import-yaml" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "nodes:\n  r1:\n    kind: linux\n    image: alpine:latest\n    host: local-agent\n  r2:\n    kind: linux\n    image: alpine:latest\n    host: host-b\nlinks:\n  - r1:\n      ifname: eth1\n      ipv4: 10.0.0.1/24\n    r2:\n      ifname: eth1\n      ipv4: 10.0.0.2/24"}'

# Deploy lab
curl -s -X POST "http://localhost:8000/labs/${LAB_ID}/up" \
  -H "Authorization: Bearer $TOKEN" | jq

# Check job status
curl -s "http://localhost:8000/labs/${LAB_ID}/jobs" \
  -H "Authorization: Bearer $TOKEN" | jq '.jobs[0]'

# Destroy lab
curl -s -X POST "http://localhost:8000/labs/${LAB_ID}/down" \
  -H "Authorization: Bearer $TOKEN" | jq

# Controller logs
docker compose -f /opt/archetype-controller/docker-compose.gui.yml logs -f api

# Agent logs (systemd)
journalctl -u archetype-agent -f

# Agent logs (docker)
docker compose -f /opt/archetype-controller/docker-compose.gui.yml logs -f agent
```

---

## Files Reference

### Unified Installer
```
install.sh                # Handles controller, agent, or both
```

### Controller/API
```
api/app/main.py           # Main API, job dispatch, multi-host orchestration
api/app/agent_client.py   # Agent communication, health checks, overlay setup
api/app/topology.py       # Topology parsing, analysis, splitting
api/app/schemas.py        # Data models including CrossHostLink with IPs
api/app/models.py         # Database models (Host, Lab, Job, etc.)
```

### Agent
```
agent/main.py                    # Agent server, registration, heartbeat
agent/providers/containerlab.py  # Containerlab deploy/destroy
agent/network/overlay.py         # VXLAN overlay management with IP config
agent/console/docker_exec.py     # Console access via docker exec
agent/schemas.py                 # Agent request/response schemas
```
