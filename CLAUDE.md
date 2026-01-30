# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Archetype is a web-based network lab management platform for containerlab. It provides a drag-and-drop topology canvas, YAML import/export, lab lifecycle management (up/down/restart), and WebSocket-based node console access.

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
- **Topology**: `api/app/topology.py` - converts between GUI graph JSON and containerlab YAML

### Frontend (`web/`)
- **Framework**: React 18 + TypeScript + Vite
- **Canvas**: React Flow (`reactflow`) for topology visualization
- **Console**: xterm.js for WebSocket-based terminal access
- **Pages**: `web/src/pages/` - LabsPage (list), LabDetailPage (canvas + controls), CatalogPage (devices/images)

### Data Flow
1. GUI canvas state (nodes/links) → `POST /labs/{id}/import-graph` → converted to `topology.yml`
2. `POST /labs/{id}/deploy` → enqueues RQ job → agent runs containerlab deploy in lab workspace
3. Console: WebSocket at `/labs/{id}/nodes/{node}/console` → spawns SSH/docker exec to node

### Key Patterns
- Lab workspaces stored at `WORKSPACE` (default `/var/lib/archetype/{lab_id}/`)
- Each lab has a `topology.yml` file that containerlab reads
- Agents run with `network_mode: host` and `privileged: true` to access containerlab/docker
- Provider-specific logic isolated in `api/app/providers.py`

## Environment Variables

Copy `.env.example` to `.env`. Key settings:
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection for job queue
- `WORKSPACE`: Root directory for lab files
- `PROVIDER`: `clab` (containerlab) is the primary supported provider
- `JWT_SECRET` / `SESSION_SECRET`: Must be changed in production
- `ADMIN_EMAIL` / `ADMIN_PASSWORD`: Seeds initial admin user on startup

## Conventions

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, etc.
- Python: Follow existing FastAPI patterns in `main.py`
- TypeScript: Components in `web/src/components/`, pages in `web/src/pages/`
- Prefer adapter/strategy patterns for provider-specific logic
