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
- **Networking**: `agent/network/` - LocalNetworkManager (veth pairs), OverlayManager (VXLAN)
- **Vendors**: `agent/vendors.py` - Device-specific configurations (cEOS, SR Linux, etc.)

### Frontend (`web/`)
- **Framework**: React 18 + TypeScript + Vite
- **Canvas**: React Flow (`reactflow`) for topology visualization
- **Console**: xterm.js for WebSocket-based terminal access
- **Pages**: `web/src/pages/` - LabsPage (list), LabDetailPage (canvas + controls), CatalogPage (devices/images)

### Data Flow
1. GUI canvas state (nodes/links) → `POST /labs/{id}/import-graph` → converted to `topology.yml`
2. `POST /labs/{id}/deploy` → enqueues RQ job → agent deploys containers via DockerProvider
3. Console: WebSocket at `/labs/{id}/nodes/{node}/console` → spawns SSH/docker exec to node

### Key Patterns
- Lab workspaces stored at `WORKSPACE` (default `/var/lib/archetype/{lab_id}/`)
- Each lab has a `topology.yml` file defining the network topology
- Agents run with `network_mode: host` and `privileged: true` to manage containers/networking
- Provider-specific logic isolated in `agent/providers/`
- Vendor-specific configs (console shell, boot detection) in `agent/vendors.py`

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

## Conventions

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, etc.
- Python: Follow existing FastAPI patterns in `main.py`
- TypeScript: Components in `web/src/components/`, pages in `web/src/pages/`
- Prefer adapter/strategy patterns for provider-specific logic
