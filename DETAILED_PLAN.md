# Archetype Detailed Implementation Plan

## Phase A — Repo & Foundations
- Create `api/` + `web/` directories if missing.
- Add `docker-compose` + `.env.example`.
- Add `Dockerfile.api` and `Dockerfile.web`.
- Add minimal `web/` React + Vite scaffold.
- Add minimal `api/` FastAPI scaffold with `/health`.

## Phase B — Backend: Auth & Users
- Add SQLAlchemy models: `User`, `Lab`, `LabFile`, `Job`, `Permission`.
- Add Alembic migrations setup.
- Add local auth: register/login/logout endpoints.
- Add password hashing (bcrypt/argon2).
- Add JWT or session cookies.
- Add OIDC login endpoints and callback handler.
- Add user middleware to inject current user.

## Phase C — Backend: Lab Storage
- Add workspace root config `WORKSPACE`.
- Add lab workspace creation on `POST /labs`.
- Persist `topology.yml` on lab create with minimal stub.
- Add `GET /labs` with pagination.
- Add `GET /labs/{id}`.
- Add `DELETE /labs/{id}` with workspace cleanup.
- Add `POST /labs/{id}/clone`.
- Add `POST /labs/{id}/update-topology-from-yaml`.
- Add `GET /labs/{id}/export-yaml`.

## Phase D — Backend: Topology Mapping
- Add `topology_graph.json` schema (nodes/links/defaults).
- Add `POST /labs/{id}/update-topology`.
- Add `GET /labs/{id}/export-graph`.
- Add graph→YAML serializer.
- Add YAML→graph parser.

## Phase E — Backend: Netlab CLI Integration
- Add wrapper for `netlab show devices`.
- Add wrapper for `netlab show images`.
- Add wrapper for `netlab up/down/restart/status`.
- Add job queue with Redis + RQ.
- Add job logs capture and tail endpoint.

## Phase F — Backend: Console
- Add websocket endpoint `/labs/{id}/nodes/{node}/console`.
- Integrate with `netlab connect` or docker exec.
- Stream stdout/stderr via websocket.

## Phase G — Frontend: App Shell
- Create layout (sidebar + topbar + main).
- Add auth screens (login/register).
- Add lab list + create modal.
- Add lab detail route.

## Phase H — Frontend: Topology Canvas
- Add React Flow canvas.
- Add device palette.
- Add drag/drop device creation.
- Add link creation.
- Add node/link labels.
- Add properties panel (device, version, image, vars).
- Add YAML export/import UI.

## Phase I — Frontend: Runtime
- Add lab status panel.
- Add action buttons: up/down/restart.
- Add job logs panel.
- Add console drawer (xterm.js).

## Phase J — Multi‑User & Governance
- Add permissions UI (share lab).
- Add quotas and concurrency errors display.
- Add audit log view.

## Phase K — Ops
- Add health checks to compose.
- Add log forwarding.
- Add backup scripts.

## Phase L — Networking Stability (2026-02-02)
- Enforce shared OVS bridge usage (`arch-ovs`) for all labs.
- Migrate legacy per-lab bridges (`ovs-*`) to shared bridge on agent startup.
- Add tests covering per-lab bridge migration behavior.
- Verified in Project_1: EOS-5 ↔ EOS-2 ping succeeds after migration.
