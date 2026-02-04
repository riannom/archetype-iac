# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Archetype is a web-based network lab management platform. It provides a drag-and-drop topology canvas, YAML import/export, lab lifecycle management (up/down/restart), and WebSocket-based node console access.

## Development Commands

### Full Stack (Docker Compose)
```bash
# Start all services (api, web, worker, postgres, redis)
docker compose -f docker-compose.gui.yml up -d --build

# Rebuild after code changes
docker compose -f docker-compose.gui.yml up -d --build
```

### API Development (without Docker)
```bash
cd api
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Worker (RQ job queue)
```bash
cd api
rq worker archetype
```

### Frontend Development
```bash
cd web
npm install
npm run dev      # Dev server with hot reload
npm run build    # Production build
```

### Database Migrations
```bash
cd api
alembic upgrade head
alembic revision --autogenerate -m "description"
```

### Backup/Restore
```bash
./scripts/backup.sh   # Creates timestamped backup of DB and workspaces
./scripts/restore.sh  # Restores from backup
```

## Architecture

### Backend (`api/`)
- **Framework**: FastAPI + Pydantic + SQLAlchemy
- **Entry point**: `api/app/main.py` - defines all routes inline (no separate router files except auth)
- **Models**: `api/app/models.py` - User, Lab, Job, Permission, LabFile
- **Auth**: `api/app/auth.py` and `api/app/routers/auth.py` - JWT + session cookies, local auth + OIDC
- **Job queue**: Redis + RQ (`api/app/jobs.py`) - async execution of lab deploy/destroy
- **Topology**: `api/app/topology.py` - converts between GUI graph JSON and topology YAML

### Agent (`agent/`)
- **Framework**: FastAPI (runs on each compute host)
- **Entry point**: `agent/main.py` - REST API for lab operations
- **Providers**: `agent/providers/` - DockerProvider (containers), LibvirtProvider (VMs)
- **Networking**: `agent/network/` - OVS-based networking (see Multi-Host Networking below)
- **Vendors**: `agent/vendors.py` - Device-specific configurations (cEOS, SR Linux, etc.)

### Multi-Host Networking (`agent/network/`)

The agent uses Open vSwitch (OVS) for all container networking. All containers on a host share a single `arch-ovs` bridge, with VLAN tags providing isolation.

- **`docker_plugin.py`**: OVS Docker network plugin for pre-boot interface provisioning
  - All container interfaces connect to the shared `arch-ovs` bridge
  - Each interface gets a unique VLAN tag initially (isolated until linked)
  - `hot_connect()` matches VLAN tags to create L2 links between interfaces
  - Required for cEOS which enumerates interfaces at boot time

- **`ovs.py`**: OVSNetworkManager for external connections and VXLAN
  - Manages the shared `arch-ovs` bridge for VXLAN tunnels and external interfaces

- **`overlay.py`**: OverlayManager for cross-host VXLAN tunnels
  - Creates VXLAN ports on `arch-ovs` (not Linux bridge - Linux bridge has unicast forwarding issues)
  - Uses VLAN tags (3000-4000 range) for overlay link isolation

**Key requirements**:
- OVS bridge must use `fail_mode: standalone`. The default `secure` mode drops all traffic without explicit OpenFlow rules.
- Same-host link creation uses `DockerOVSPlugin.hot_connect()`, not `OVSNetworkManager.hot_connect()`

### Frontend (`web/`)
- **Framework**: React 18 + TypeScript + Vite
- **Canvas**: React Flow (`reactflow`) for topology visualization
- **Console**: xterm.js for WebSocket-based terminal access
- **Pages**: `web/src/pages/` - LabsPage (list), LabDetailPage (canvas + controls), CatalogPage (devices/images)

### Data Flow
1. GUI canvas state (nodes/links) → `POST /labs/{id}/update-topology` → syncs to database, triggers live operations
2. `POST /labs/{id}/deploy` → enqueues RQ job → agent deploys containers via DockerProvider
3. Console: WebSocket at `/labs/{id}/nodes/{node}/console` → spawns SSH/docker exec to node

### Key Patterns
- Lab workspaces stored at `WORKSPACE` (default `/var/lib/archetype/{lab_id}/`)
- Each lab has a `topology.yml` file defining the network topology
- Agents run with `network_mode: host` and `privileged: true` to manage containers/networking
- Provider-specific logic isolated in `agent/providers/`
- Vendor-specific configs (console shell, boot detection) in `agent/vendors.py`

### Workspace Architecture

There are **two separate workspaces** that store startup configs:

| Workspace | Path | Owner | Purpose |
|-----------|------|-------|---------|
| API workspace | `/var/lib/archetype/{lab_id}/` | API container | Config snapshots, extracted configs |
| Agent workspace | `/var/lib/archetype-agent/{lab_id}/` | Agent container | Configs used during container deployment |

**Config sync flow**:
1. "Extract Configs" pulls running configs from containers via agent
2. Agent saves to agent workspace AND returns to API
3. API saves to API workspace and database (config_snapshots table)
4. API pushes configs back to agents to ensure sync (`update_config_on_agent()`)

On container deploy, the agent reads startup configs from its workspace (`configs/{node}/startup-config`).
The `config_snapshots` database table is for history/backup only - not used during deployment.

## Environment Variables

Copy `.env.example` to `.env`. Key settings:
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection for job queue
- `WORKSPACE`: Root directory for lab files
- `JWT_SECRET` / `SESSION_SECRET`: Must be changed in production
- `ADMIN_EMAIL` / `ADMIN_PASSWORD`: Seeds initial admin user on startup

### Agent Settings (ARCHETYPE_AGENT_* prefix)
- `ARCHETYPE_AGENT_ENABLE_DOCKER`: Enable DockerProvider (default: true)
- `ARCHETYPE_AGENT_ENABLE_LIBVIRT`: Enable LibvirtProvider for VMs (default: false)
- `ARCHETYPE_AGENT_ENABLE_VXLAN`: Enable VXLAN overlay for multi-host (default: true)
- `ARCHETYPE_AGENT_ENABLE_OVS`: Enable OVS-based networking (default: true)
- `ARCHETYPE_AGENT_OVS_BRIDGE_NAME`: OVS bridge name (default: "arch-ovs")

## Libvirt/VM Host Requirements

To run VM-based network devices (IOSv, CSR1000v, ASAv, Nexus 9000v, etc.), the host must have libvirt/KVM configured.

### Host Packages
```bash
# Debian/Ubuntu
apt install qemu-kvm libvirt-daemon-system libvirt-clients virtinst

# RHEL/Rocky/Alma
dnf install qemu-kvm libvirt virt-install
```

### CPU Virtualization
- **Intel**: VT-x must be enabled in BIOS (`grep vmx /proc/cpuinfo`)
- **AMD**: AMD-V must be enabled in BIOS (`grep svm /proc/cpuinfo`)
- Nested virtualization must be enabled if running in a VM

### Permissions
The agent needs access to the libvirt socket:
- **Docker agent**: Mount `/var/run/libvirt:/var/run/libvirt` in docker-compose
- **User running agent**: Must be in the `libvirt` group, or run as root

```bash
# Add user to libvirt group
sudo usermod -aG libvirt $USER
```

### Image Storage
VM images (qcow2 files) must be accessible from the host, not just from Docker:
- Default path: `/var/lib/archetype/images/`
- Set `ARCHETYPE_HOST_IMAGE_PATH` if using a different host path
- Libvirt runs on the host and needs direct file access to disk images

### OVS Bridge for VM Networking
VMs connect to the same OVS bridge as containers:
```bash
# Verify OVS bridge exists
ovs-vsctl show
# Should show arch-ovs bridge
```

### Docker Compose Configuration
Required mounts for the agent container:
```yaml
agent:
  volumes:
    - /var/run/libvirt:/var/run/libvirt      # Libvirt socket
    - archetype_workspaces:/var/lib/archetype:ro  # Shared images
  environment:
    - ARCHETYPE_AGENT_ENABLE_LIBVIRT=true
```

### VM Resource Requirements by Device

| Device | RAM | vCPUs | NIC Driver | Notes |
|--------|-----|-------|------------|-------|
| Cisco IOSv | 512MB-2GB | 1 | e1000 | Lightweight |
| Cisco IOSvL2 | 768MB | 1 | e1000 | Layer 2 switch |
| Cisco CSR1000v | 4GB | 1-2 | virtio | IOS-XE router |
| Cisco Cat8000v | 4GB | 2 | virtio | Catalyst 8000 |
| Cisco ASAv | 2GB | 1 | virtio | Firewall |
| Cisco Nexus 9000v | 8GB | 2 | virtio | NX-OS switch |
| Cisco XRv9k | 16GB | 4 | virtio | IOS-XR router |

### Troubleshooting

**VM not starting:**
```bash
# Check libvirt is running
systemctl status libvirtd

# Check for KVM access
ls -la /dev/kvm

# View VM domain status
virsh list --all
```

**Console not connecting:**
```bash
# Verify virsh is installed in agent container
docker exec archetype-agent virsh --version

# Try manual console connection
virsh console <domain-name> --force
```

**Image not found:**
```bash
# Check image path translation
# Container path /var/lib/archetype/images/ should map to host path
docker inspect archetype-agent | grep -A5 Mounts
```

## Data Sources of Truth

This section documents the canonical/authoritative source for each data type in the system.

### Database (PostgreSQL)

| Data Type | Table | Notes |
|-----------|-------|-------|
| Lab definitions | `labs` | Core lab metadata (name, owner, state) |
| Node definitions | `nodes` | Nodes within labs (name, kind, image, host) |
| Link definitions | `links` | Point-to-point connections between nodes |
| Node runtime state | `node_states` | actual_state, is_ready, boot_started_at |
| Link runtime state | `link_states` | actual_state for link up/down |
| Node placements | `node_placements` | Which agent hosts which node |
| Image host status | `image_hosts` | Which agents have which images |
| Jobs | `jobs` | Deploy/destroy operations |
| Users/Permissions | `users`, `permissions` | Authentication and authorization |
| Agents | `hosts` | Registered compute agents |

### File-Based Storage (`{WORKSPACE}/images/`)

| File | Purpose | Reconciliation |
|------|---------|----------------|
| `manifest.json` | **Source of truth** for image metadata (id, reference, device_id, version) | ImageHost table tracks agent presence |
| `custom_devices.json` | User-defined device types | Merged with vendor registry in `/vendors` API |
| `hidden_devices.json` | Hidden device IDs | Filters `/vendors` API output |
| `device_overrides.json` | Per-device config overrides | Merged in `/vendors/{id}/config` API |
| `rules.json` | Regex rules for device detection | Used during image import |

### Agent Registry (`agent/vendors.py`)

| Data Type | Notes |
|-----------|-------|
| Device catalog | **Single source of truth** for vendor configs (console shell, port naming, boot detection) |
| Interface patterns | `portNaming`, `portStartIndex`, `maxPorts` per device |
| Container runtime config | Environment vars, capabilities, sysctls, mounts |

The frontend fetches device data from `/vendors` API (which sources from `agent/vendors.py`) rather than maintaining hardcoded duplicates.

### Runtime State

| State | Location | Persistence |
|-------|----------|-------------|
| Container status | Docker daemon (agent) | Reconciled to `node_states` via background task |
| Deploy locks | Redis (`deploy_lock:{lab_id}`) | TTL-based, auto-expires |
| Job queue | Redis (RQ queue "archetype") | Lost on Redis restart |
| Upload sessions | API process memory | Lost on API restart |

### Reconciliation Tasks

Background tasks run periodically to reconcile state:

- **State Reconciliation** (`app/tasks/reconciliation.py`): Syncs `node_states` and `link_states` with actual container status
- **Image Reconciliation** (`app/tasks/image_reconciliation.py`): Syncs `image_hosts` table with `manifest.json`
- **Job Health** (`app/tasks/job_health.py`): Detects stuck jobs and marks them failed

## Known Issues & Vendor Quirks

### Arista cEOS
- **iptables DROP rule**: cEOS adds `iptables -A EOS_FORWARD -i eth1 -j DROP` which blocks data plane traffic on eth1+. This rule is recreated on container restart.
  - Workaround: `docker exec <container> iptables -D EOS_FORWARD -i eth1 -j DROP`
- **IP routing disabled**: cEOS has `no ip routing` by default. Must enable via CLI: `configure terminal` → `ip routing` → `end`
- **Interface naming**: Uses `INTFTYPE=eth` env var so Linux eth1 maps to EOS Ethernet1

### Linux Bridge vs OVS
- Linux bridge has issues forwarding unicast packets to VXLAN ports (broadcast works, unicast doesn't)
- Always use OVS for VXLAN overlay - the `OverlayManager` in `overlay.py` uses OVS

## Conventions

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, etc.
- Python: Follow existing FastAPI patterns in `main.py`
- TypeScript: Components in `web/src/components/`, pages in `web/src/pages/`
- Prefer adapter/strategy patterns for provider-specific logic
