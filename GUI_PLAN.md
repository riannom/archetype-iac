# Archetype Plan (CML-like + IaC)

## Scope
- CML-like GUI for containerlab: drag/drop devices, link creation, labels, console access, external network connectivity, container images, multi-lab workspaces.
- IaC enhancement: generate containerlab YAML from GUI and import YAML into GUI.
- Deployment: containerized GUI on same host as netlab + containerlab, multi-user.

## Recommended Stack
- Frontend: React + TypeScript + Vite; React Flow for canvas; xterm.js for console; Radix UI + Tailwind.
- Backend: FastAPI + Pydantic; Redis + RQ for jobs; Postgres for metadata.
- Runtime: netlab + containerlab installed in API/worker image; bind `/var/run/docker.sock`.

## Core Features
- Topology canvas with device palette and version/image selection.
- Link editor supporting p2p/lan/loopback/tunnel types; labels and annotations.
- Per-node properties panel for device, version, image, vars.
- Multi-lab workspace: save/load, clone, templates, YAML export/import.
- Console/terminal via websocket to backend (netlab connect, docker exec, SSH).
- External connectivity: UI to select bridge and host network exposure.

## Multi-User Model
- Per-user lab workspaces with ACLs and quotas.
- OIDC + local auth; lab ownership and optional sharing.
- Job isolation with per-user queue limits.

## Data Model (GUI)
- TopologyGraph JSON: `nodes[]`, `links[]`, `defaults`, `metadata`.
- Node: `id`, `name`, `device`, `version`, `image`, `role`, `mgmt`, `vars`.
- Link: `endpoints[]` (node + iface), `type`, `name`, `pool`, `prefix`, `bridge`, `mtu`, `bandwidth`.
- Canonical storage: `topology.yml` (netlab); GUI JSON cached.

## API (MVP)
- Auth: `POST /auth/login` (local), `GET /auth/oidc/*` (OIDC).
- Catalog: `GET /devices`, `GET /images`.
- Labs: `POST /labs`, `GET /labs`, `GET/PUT /labs/{id}`.
- YAML: `POST /labs/{id}/update-topology-from-yaml`, `GET /labs/{id}/export-yaml`.
- Lifecycle: `POST /labs/{id}/up`, `POST /labs/{id}/down`, `GET /labs/{id}/status`.
- Console: `GET /labs/{id}/nodes/{node}/console` (websocket).

## Roadmap
1. MVP: canvas + YAML import/export + `netlab up/down/status`.
2. Device catalog + image/version selection + link attribute editor.
3. Console integration + external network bridge.
4. Auth + per-user labs + quotas.
5. Templates, snapshots, validation rules.

## Constraints
- Provider focus: containerlab first.
- Lab scale: 10-30 nodes, 1-4 concurrent labs (often 1).
- Deployment: dedicated server, multi-user GUI.
