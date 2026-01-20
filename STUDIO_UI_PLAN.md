# Studio UI Integration Plan (Detailed)

Goal: use the Visual Studio UI (from the aura zip) as the primary Studio experience, replacing mock data with real backend data, while preserving the original look/feel and interaction model. Provide a terminal console UI that matches the original design: tabbed consoles within a single window, plus popout capability. Keep the experience intentional, bold, and faithful to the zip UI.

---

## 0) Vision and non-negotiables

- **Visual fidelity:** Keep the stylistic direction of the zip UI (bold typography, dark tech palette, glassy surfaces, animated gradient background, canvas grid). Avoid generic UI rework.
- **Data fidelity:** Replace mock data structures with real data from the existing API. Mock structures should not remain in the UI flow.
- **Console fidelity:** Use a realistic terminal via xterm, matching the original console UI and enabling tabbed + popout usage.
- **Interaction parity:** Preserve drag/drop on canvas, context menu actions, and property editing layout.
- **No re-architecture:** Integrate into the existing React app without rewriting the overall app shell.

---

## 1) Key data sources (real API endpoints)

- Labs
  - `GET /labs` -> list labs
  - `POST /labs` -> create lab
  - `DELETE /labs/:labId` -> delete
- Topology
  - `GET /labs/:labId/export-graph` -> graph nodes/links for canvas
  - `POST /labs/:labId/import-graph` -> deploy graph to backend
  - `GET /labs/:labId/export-yaml` -> YAML preview
- Runtime
  - `GET /labs/:labId/jobs` -> status of actions
  - `POST /labs/:labId/nodes/:nodeId/{start|stop|restart}` -> lifecycle
- Device catalog
  - `GET /devices` -> device families
  - `GET /images` -> catalog mapping info per device
  - `GET /images/library` -> uploaded images
  - `POST /images/library/:id` -> assign device or set version
  - `POST /images/load` -> upload image tarball
  - `POST /images/qcow2` -> upload qcow2

---

## 2) Current implementation status

### 2.1 Studio page and routing
- `web/src/studio/StudioPage.tsx` is the primary Studio controller.
- Routes:
  - `/studio` -> StudioPage
  - `/studio/console/:labId/:nodeId` -> popout console
- Sidebar nav includes Studio entry.

### 2.2 Real data wiring
- Device models are built from `/devices` + `/images/library`.
- Device categories in the UI are derived from real data (single category for now).
- Graph is loaded from `/labs/:id/export-graph` and mapped to canvas nodes/links.
- Graph deploy posts to `/labs/:id/import-graph`.

### 2.3 Console integration
- `TerminalSession` uses xterm and connects to `/labs/:id/nodes/:node/console` via WS.
- `ConsoleManager` manages draggable windows and tabs.
- Popout page uses `TerminalSession` for consistency.

---

## 3) Visual and UX alignment to original zip UI

### 3.1 Typography and layout
- Use Inter (as in original zip) for main UI.
- Keep bold headings, uppercase micro-labels, and compact font sizes.
- Preserve left sidebar + canvas + right properties panel layout.

### 3.2 Color, background, and visual accents
- Maintain dark primary theme with gradient-animated background.
- Retain glassy panes, subtle borders, and glow accents for active state.
- Canvas uses dot grid background.
- Use FontAwesome icons for consistency with zip UI.

### 3.3 Motion and micro-interactions
- Keep subtle transitions: fade-in view changes, hover highlights, active tab top bar.
- Avoid heavy animations beyond gradient shift.

---

## 4) Detailed data mapping plan

### 4.1 Device models
- Source: `/devices` plus `/images/library` (versions).
- For each device:
  - `id` -> device id from catalog
  - `name` -> device label from catalog
  - `type` -> guessed from id/label (router/switch/firewall/host/container)
  - `versions` -> versions from library images assigned to that device
  - `vendor` -> `support` field from catalog
  - `icon` -> default `fa-microchip` (later map by vendor)

### 4.2 Graph mapping
- Graph nodes:
  - node id -> graph node id
  - name -> graph node name
  - device -> node model id
  - version -> node version
- Canvas layout:
  - Apply simple grid layout on load (row/column based on index)
  - Later: preserve positions if backend can store them

### 4.3 Links
- Graph links:
  - Each link endpoints -> canvas link
  - use source/target node IDs
  - map interface names if present

### 4.4 Runtime status
- Use `/labs/:id/jobs`
- Map job actions to runtime states:
  - start/up -> running when completed, booting otherwise
  - stop/down -> stopped
  - restart -> running when completed, booting otherwise

---

## 5) Console plan (tabbed + popout)

### 5.1 Tabbed consoles
- Each console window can contain multiple node tabs.
- Active tab is visible; others are kept mounted but hidden to preserve session.
- Tabs show node name and include close action.
- Default size 520x360; resizable.

### 5.2 Popout consoles
- Popout opens `/studio/console/:labId/:nodeId` in new window.
- Uses same `TerminalSession` for consistent behavior.
- Avoids custom window HTML and stays within router.

### 5.3 WebSocket handling
- Use `ws://` or `wss://` based on current protocol.
- If `API_BASE_URL` is a full URL, honor it.
- Binary mode enabled for correct terminal data handling.

---

## 6) Device/image management plan

### 6.1 Upload flows
- Upload tarball -> `/images/load`
- Upload qcow2 -> `/images/qcow2`
- Show progress bars (as in catalog page) and success messages.

### 6.2 Assigning images
- Use `/images/library/:id` to set `device_id`.
- Unassign -> `device_id: null`.
- Set version on blur to avoid spamming API.

### 6.3 Display behavior
- Show selected device metadata, assigned image list, and unassigned list.
- Show catalog data (clab/libvirt/virtualbox/caveats) for the device if present.

---

## 7) Missing persistence items (future)

These are intentionally deferred but required for full fidelity:
- Canvas element positions and annotation persistence.
- Editable startup configs saved server-side.
- Advanced device/port settings in properties panel.

---

## 8) Implementation checklist (granular)

### Studio shell
- [x] Add `/studio` and `/studio/console/:labId/:nodeId` routes.
- [x] Add Studio to sidebar nav.
- [x] Add `studio.css` and style assets.

### Data wiring
- [x] Load labs, devices, images, library.
- [x] Build models from catalog + library.
- [x] Load graph from `export-graph`.
- [x] Deploy graph to `import-graph`.
- [x] YAML export modal.
- [x] Runtime status from jobs.

### Consoles
- [x] Implement xterm session via WebSocket.
- [x] Tabbed consoles within a window.
- [x] Popout window route.
- [ ] Confirm multi-tab session stability (no WS drops).

### Images
- [x] Upload + progress display.
- [x] Assign/unassign images.
- [x] Edit image version.

### UX alignment
- [x] Canvas grid and gradient animation.
- [x] Dark theme with glass panes.
- [ ] Confirm no layout regressions from Tailwind CDN.

---

## 9) QA and verification steps

- Studio loads without errors.
- Devices list and images load successfully.
- Canvas renders nodes/links after selecting a lab.
- Export/Deploy actions complete without errors.
- Runtime controls work and status updates.
- Consoles open and show real terminal output.
- Popout console works and persists even if Studio tab is closed.

---

## 10) Known risks and mitigations

- Tailwind CDN could affect existing app CSS.
  - Mitigation: limit usage to Studio and keep global styles minimal.
- No persistence of canvas state.
  - Mitigation: treat as a phase-2 feature.
- Device type icons are generic.
  - Mitigation: map vendor/type to icons later.

---

## 11) Next high-impact upgrades (post-stabilization)

1) Persist canvas positions and annotations to backend.
2) Add per-node config editor storage and applied config view.
3) Improve device icons and category breakdown.
4) Add advanced console features: split panes, download logs.

