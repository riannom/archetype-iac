# Deterministic Identifier Hardening Plan

## Goal
Reduce provisioning and reconciliation drift by removing runtime fallback logic that relies on mutable names or heuristic matching, and converge on deterministic identifiers.

## Approach: Schema Migration, Not Incremental Fallback Removal

This plan is structured as a **constraint-first migration**: fix creation paths, backfill nulls,
add NOT NULL constraints, then remove dead fallback code. This prevents half-migrated states
where some paths use FKs and others still use strings.

Observability-first was considered and rejected — the answer to "how often do fallbacks fire?"
is already known from current creation paths: null-FK records are still possible in active flows
(for example NodeState placeholder creation and LinkState router creation without definition FK),
so fallback branches remain exercised. Metrics would mostly confirm code paths we already know exist.

## How To Track
- Status legend: `[ ]` not started, `[~]` in progress, `[x]` completed.
- Update the checkbox and the `Status:` line for each task when work starts/completes.
- Keep links to implementation PRs or commits under each task.

## Prioritization Matrix

| Priority | Task | Benefit | Effort |
|---|---|---|---|
| P0 | Fix all FK creation paths (node, link, placement) | Very High | Medium |
| P0 | Backfill nulls + NOT NULL schema migration | Very High | Medium |
| P0 | Link identity: FK-based matching in reconciliation | Very High | Medium |
| P1 | Remove dead string-fallback code in enforcement/reconciliation | High | Low |
| P1 | VXLAN tunnel identity tightening | Medium | Medium-High |
| P2 | Image ingest hardening (deterministic device assignment) | Medium | Medium |
| P2 | Runtime image metadata lookup by stable ID only | High | High |

## Current State Assessment

### Root Cause
Three critical ForeignKeys are nullable with `SET NULL`, and active code paths still allow null-FK rows:

| Model | Field | Created Without FK | Fallback Pattern |
|-------|-------|--------------------|------------------|
| `NodeState` | `node_definition_id` | `routers/labs/_shared.py` placeholder path when Node lookup misses | `node_name == container_name` |
| `LinkState` | `link_definition_id` | `routers/labs/link_states.py` creation paths omit FK | `link_name` string matching |
| `NodePlacement` | `node_definition_id` | Creation paths still allow `None` when node lookup misses | `node_name == container_name` |

### Hot Path Impact
- **State enforcement** (every 5-30s): FK-first-then-string for every node/placement
- **Reconciliation / enforcement loops**: repeatedly backfill or resolve via names when FK links are missing
- **Link matching**: Entirely string-based (`_find_matching_link_state()` uses endpoint name tuples)
- **Cleanup handler**: Checks both `display_name` and `container_name` (historical artifact)

### Evidence of Drift
- `node_lifecycle.py` actively syncs `container_name != node_name` divergence
- Cleanup code checks both `display_name` and `container_name` because placement rows used the wrong one historically
- Agent `_link_tunnels` is in-memory only; rebuilt from OVS on startup with potential identity loss

---

## Phase 1: Fix FK Creation Paths

All state creation paths must set FKs at creation time, not rely on later backfill.

- [x] **1.1 Ensure NodeState creation always sets `node_definition_id`**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Audit every `NodeState()` constructor call. Ensure `node_definition_id` is populated.
    - Primary gap:
      - `api/app/routers/labs/_shared.py:103-107` creates placeholder NodeState with `node_definition_id=None`
        when Node lookup misses (explicit fallback branch with warning log)
    - Verify (may not be a real gap):
      - `api/app/routers/labs/crud.py:121` sets `node_definition_id=node_def_id` — trace whether
        `node_def_id` can actually be None here. This path iterates over Node objects from DB, so
        the FK should always resolve. Confirm before targeting.
    - Note: there are zero `NodeState()` constructors in `api/app/tasks/` — all creation happens
      in the router layer.
  - Target files:
    - `api/app/routers/labs/_shared.py` (confirmed gap)
    - `api/app/routers/labs/crud.py` (verify — may already be correct)
    - `api/app/tasks/state_enforcement.py` (backfill logic, not creation)
  - Completion criteria:
    - No code path creates a NodeState with `node_definition_id=None` for a node that exists in the `nodes` table.
    - Test coverage for creation paths.
  - Implementation notes:
    - `_get_or_create_node_state()` now fails fast when Node definition is missing instead of creating placeholder NodeState rows.
    - Existing rows with missing `node_definition_id` are opportunistically backfilled on read when resolvable.

- [x] **1.2 Ensure LinkState creation always sets `link_definition_id`**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Audit every `LinkState()` constructor call. Four creation sites exist:
      - `routers/labs/link_states.py:291` — **missing FK entirely** (field absent from constructor)
      - `routers/labs/link_states.py:751` — **missing FK entirely** (field absent from constructor)
      - `tasks/link_orchestration.py:190` — already sets `link_definition_id=link.id` (no change needed)
      - `tasks/reconciliation_db.py:164` — already sets `link_definition_id=link.id` (no change needed)
    - Ensure `_find_matching_link_state()` can transition to FK-based matching.
  - Target files:
    - `api/app/routers/labs/link_states.py` (two creation sites missing FK — primary fix target)
    - `api/app/tasks/link_orchestration.py` (already correct, but audit dedup logic at lines 187-226
      which uses dual `link_name`/`norm_link_name` lookup — relevant for Phase 3.2)
    - `api/app/tasks/reconciliation_db.py` (already correct)
    - `api/app/tasks/link_reconciliation.py` (consumer of LinkState, not creator — audit for teardown paths)
  - Completion criteria:
    - All LinkState rows have `link_definition_id` when the corresponding Link exists.
  - Implementation notes:
    - `_upsert_link_states()` now resolves/creates Link definitions and sets `link_definition_id` on create/update.
    - Canonicalization dedup pass now also backfills `link_definition_id` on preferred retained rows.

- [x] **1.2b Make hot-connect server-authoritative for Link + LinkState (blocker for NOT NULL)**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - **Problem**: The earlier version of this task assumed topology save always follows hot-connect,
      but that is only guaranteed for the Studio UI flow. Direct API callers (`curl`, scripts,
      integrations) can call `/hot-connect` without ever calling `update-topology`, leaving a
      permanent null FK. A deferred backfill is not acceptable when the goal is NOT NULL.
    - **Fix**: Make hot-connect server-authoritative — create/upsert the Link definition in the
      same transaction as the LinkState:
      1. Look up existing Link by canonical `link_name` (using `generate_link_name()` from
         `api/app/utils/link.py` for endpoint-order canonicalization).
      2. If no Link exists, create one in the same transaction. This requires resolving
         `source_node_id` and `target_node_id` FKs from the Node table (the endpoint already
         queries Nodes at lines 710-722 so this data is available).
      3. Set `link_definition_id` on the LinkState unconditionally.
    - **Bulk upsert** (`link_states.py:291`): the calling context processes Link definitions —
      pass the FK through instead of omitting it.
    - **Existing `_link_link_states()` backfill** (`topology.py:921-935`) remains as a safety net
      for any pre-existing orphaned rows, but is no longer relied upon for new inserts.
    - **Concurrency/idempotency requirements**:
      - Handle concurrent hot-connect and topology-save writes safely.
      - Link creation must be idempotent under race: on unique-link conflict, re-read the existing
        Link row and proceed (do not fail the request due to duplicate insert races).
      - Repeated hot-connect calls for the same logical link must converge on a single Link + LinkState
        identity without creating duplicate rows.
  - Target files:
    - `api/app/routers/labs/link_states.py` (both creation sites: lines 291 and 751)
    - `api/app/models/topology.py` (Link model — verify required fields for creation)
  - Completion criteria:
    - `/hot-connect` creates a Link definition if one doesn't exist, in the same DB transaction.
    - Both LinkState creation sites set `link_definition_id` unconditionally (never NULL).
    - Direct API callers get full identity guarantees without relying on a follow-up topology save.
    - Tests cover: hot-connect with pre-existing Link, hot-connect without Link (creates it),
      bulk upsert FK propagation.
    - Tests cover concurrent hot-connect / topology-save race and verify idempotent convergence.
  - Implementation notes:
    - `/hot-connect` now resolves/creates canonical Link definition before LinkState mutation.
    - Link and LinkState creation paths use savepoint + `IntegrityError` recovery to converge under duplicate-insert races.
    - Repeated hot-connect requests now converge on a single Link + LinkState identity.

- [x] **1.3 Ensure NodePlacement creation always sets `node_definition_id`**
  - Status: `completed`
  - Benefit: High
  - Effort: Low-Medium
  - Scope:
    - Audit NodePlacement creation — three sites all use `node_def.id if node_def else None`:
      - `tasks/jobs.py:618` — conditional FK
      - `tasks/state_enforcement.py:492` — conditional FK
      - `tasks/reconciliation_db.py:954` — conditional FK
    - All three share the same pattern: `node_definition_id=node_def.id if node_def else None`.
      Fix is uniform — ensure `node_def` is always resolved before creation, or skip
      placement creation when the Node definition doesn't exist.
  - Target files:
    - `api/app/tasks/jobs.py`
    - `api/app/tasks/state_enforcement.py`
    - `api/app/tasks/reconciliation_db.py`
  - Completion criteria:
    - No NodePlacement created without `node_definition_id` when the Node exists.
  - Implementation notes:
    - Creation sites in `jobs.py`, `state_enforcement.py`, and `reconciliation_db.py` now skip new placement creation when Node definition cannot be resolved, and log the condition.
    - Existing rows continue to be backfilled when Node definition is available.

- [x] **1.4 Remove display_name fallback in cleanup_handler.py**
  - Status: `completed`
  - Benefit: Medium
  - Effort: Very Low
  - Scope:
    - Remove the `display_name` union in `cleanup_handler.py:131-138`.
    - `display_name` is presentation-only; runtime resolution should use `container_name` exclusively.
  - Target files:
    - `api/app/tasks/cleanup_handler.py`
  - Completion criteria:
    - Cleanup resolves node identity via `container_name` (and FK where available) only.
    - Test verifying no data-loss regression.
  - Implementation notes:
    - `cleanup_handler._cleanup_lab_placements()` now matches placements only against `Node.container_name`.

## Phase 2: Backfill + NOT NULL Migration

Once creation paths are fixed, backfill existing nulls and enforce the constraint at the schema level.

### Phase 2 Migration Gates

These are hard preconditions/checks before and after schema changes.

- Pre-migration gate (must pass before 2.2):
  - `SELECT COUNT(*) FROM node_states WHERE node_definition_id IS NULL;` = `0`
  - `SELECT COUNT(*) FROM node_placements WHERE node_definition_id IS NULL;` = `0`
  - `SELECT COUNT(*) FROM link_states WHERE link_definition_id IS NULL;` = `0`
  - Orphaned FK checks (must also be `0`):
    - `SELECT COUNT(*) FROM node_states ns LEFT JOIN nodes n ON ns.node_definition_id = n.id WHERE ns.node_definition_id IS NOT NULL AND n.id IS NULL;`
    - `SELECT COUNT(*) FROM node_placements np LEFT JOIN nodes n ON np.node_definition_id = n.id WHERE np.node_definition_id IS NOT NULL AND n.id IS NULL;`
    - `SELECT COUNT(*) FROM link_states ls LEFT JOIN links l ON ls.link_definition_id = l.id WHERE ls.link_definition_id IS NOT NULL AND l.id IS NULL;`
- Post-migration gate (must pass after 2.2 in staging/prod):
  - Smoke test create/update flows for node state, placement, and hot-connect.
  - Verify no new null-FK writes are possible (insert/update attempts fail at DB boundary if violated).

- [x] **2.1 Backfill existing null FKs**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Write Alembic data migration to populate null `node_definition_id` on NodeState and NodePlacement
      by matching `(lab_id, node_name)` → `(lab_id, container_name)`.
    - Write Alembic data migration to populate null `link_definition_id` on LinkState by matching
      against Link definitions. **Canonicalization is critical here:**
      - Match on `link_name` using `generate_link_name()` from `api/app/utils/link.py`, which
        sorts endpoints alphabetically (`"nodeA:ifaceA-nodeB:ifaceB"` format).
      - Interface names must be normalized via `normalize_interface()` from
        `api/app/services/interface_naming.py` — this requires knowing the `device_type` for each
        node, so the migration must join through Nodes to resolve device types.
      - The existing `_link_link_states()` in `topology.py:921-935` matches on raw `link_name`
        only — the migration must handle cases where LinkState.link_name was stored with a
        different normalization than Link.link_name (e.g., vendor interface name vs `eth{N}`).
      - Log any rows that fail to match for manual review — do not guess.
    - Handle orphaned rows (state records whose parent Node/Link was deleted) — delete or mark for cleanup.
  - Dependencies: Phase 1 complete (no new nulls being created)
  - Completion criteria:
    - Zero null FKs in production for rows with existing parent records.
    - Orphaned rows documented or cleaned up.
    - Migration uses the same canonicalization rules as runtime code — verified by test against
      sample data with mixed naming variants (vendor names, normalized names, swapped endpoint order).
  - Implementation notes:
    - Added Alembic data migration: `api/alembic/versions/058_backfill_deterministic_state_identifiers.py`.
    - Backfill behavior:
      - `node_states`: resolve by `(lab_id, gui_id)` then `(lab_id, container_name)` then unique `display_name`.
      - `node_placements`: same resolution strategy; normalizes `node_name` to `container_name`.
      - `link_states`: canonical endpoint normalization + deterministic `link_name`, then FK match to `links`.
      - If a `LinkState` has resolvable endpoints but missing Link definition, migration creates the canonical `Link`.
      - Irrecoverable orphans are deleted during backfill.
    - Migration enforces gate queries at runtime and aborts if any null/orphan FK rows remain.
    - Added migration test coverage:
      - `api/tests/test_migration_058_deterministic_identifier_backfill.py`

- [~] **2.2 Add NOT NULL constraints via Alembic migration**
  - Status: `in progress`
  - Benefit: Very High
  - Effort: Low
  - Scope:
    - `ALTER TABLE node_states ALTER COLUMN node_definition_id SET NOT NULL`
    - `ALTER TABLE link_states ALTER COLUMN link_definition_id SET NOT NULL`
    - `ALTER TABLE node_placements ALTER COLUMN node_definition_id SET NOT NULL`
    - Change `ondelete="SET NULL"` to `ondelete="CASCADE"` (state should not outlive its definition).
    - Update SQLAlchemy model: `nullable=False`.
  - Dependencies: 2.1, 1.2b
  - Completion criteria:
    - Schema enforces FK presence at the database level.
    - Model reflects `nullable=False`.
  - Risk note:
    - Consider `ondelete="CASCADE"` carefully — if a Node is deleted, its NodeState disappears.
      This is likely correct (orphaned state is the current problem), but verify no code reads
      NodeState after deleting its Node.
    - Do not apply `link_states.link_definition_id NOT NULL` until 1.2b is complete. After 1.2b,
      hot-connect is server-authoritative (creates Link + LinkState in one transaction), so there
      is no null-FK window — NOT NULL is safe to apply.
  - Rollout / rollback guidance:
    - Rollout order:
      1. Deploy application changes from Phase 1 (creation path fixes) first.
      2. Run 2.1 backfill migration.
      3. Verify pre-migration gate queries return zero nulls.
      4. Apply 2.2 NOT NULL constraints.
    - Rollback plan:
      - If 2.2 fails or causes production breakage, revert schema migration (drop NOT NULL constraints),
        keep Phase 1 code in place, and rerun backfill diagnostics before reattempting.
      - Preserve a pre-migration snapshot/backup and document exact downgrade command in the migration PR.
  - Implementation notes:
    - Added Alembic schema migration: `api/alembic/versions/059_enforce_state_identifier_constraints.py`.
    - Migration actions:
      - Enforces pre-apply gate checks (null/orphan FK counts must be zero).
      - Sets NOT NULL on:
        - `node_states.node_definition_id`
        - `link_states.link_definition_id`
        - `node_placements.node_definition_id`
      - Replaces FK `ondelete` policy from `SET NULL` to `CASCADE` for all three relations.
    - Added migration test coverage:
      - `api/tests/test_migration_059_identifier_constraints.py`
    - Remaining gap:
      - SQLAlchemy model metadata still advertises these columns as nullable for test-harness compatibility
        (the unit-test harness uses `Base.metadata.create_all` instead of Alembic migrations). Runtime DB
        enforcement is handled by Alembic schema constraints.

## Phase 3: Remove Dead Fallback Code

With NOT NULL constraints enforced, string-matching fallbacks are unreachable. Remove them.

- [x] **3.1 Remove string-matching fallback in state enforcement**
  - Status: `completed`
  - Benefit: High (code clarity)
  - Effort: Low
  - Scope:
    - `state_enforcement.py:282-298`: Remove the `if not node_def` branch that falls back to `container_name` matching.
    - `state_enforcement.py:307-317`: Remove NodePlacement string fallback.
    - Replace with direct FK access (`session.get(Model, fk_id)`).
  - Dependencies: Phase 2.2
  - Completion criteria:
    - Enforcement loop uses FK-only resolution.
    - Regression tests pass for restart/reconcile/start/stop paths.
  - Implementation notes:
    - `_get_agent_for_node()` now resolves Node and NodePlacement via `node_definition_id` only (no `container_name`/`node_name` fallback queries).
    - Added regression coverage proving name-only placement rows are ignored without FK identity.

- [x] **3.2 Convert link matching to FK-based**
  - Status: `completed`
  - Benefit: High (eliminates normalization drift risk)
  - Effort: Medium
  - Scope:
    - Rewrite `_find_matching_link_state()` in `link_states.py` to match by `link_definition_id`.
    - Update `link_orchestration.py` dedup to use FK instead of dual `link_name`/`norm_link_name` lookup.
    - Update `link_reconciliation.py` teardown to target by FK/ID.
  - Dependencies: Phase 2.2
  - Target files:
    - `api/app/routers/labs/link_states.py`
    - `api/app/tasks/link_orchestration.py`
    - `api/app/tasks/link_reconciliation.py`
  - Completion criteria:
    - Link lifecycle operations use stable IDs throughout.
    - No string-based endpoint matching in hot paths.
  - Implementation notes:
    - `link_states._find_matching_link_state()` now matches by `link_definition_id` (deterministic FK identity).
    - `create_deployment_links()` now indexes/reuses/orphan-cleans `LinkState` rows by `link_definition_id` and removed dual-name (`link_name`/`norm_link_name`) lookup.
    - Teardown callers (`link_reconciliation.py`, `link_cleanup.py`, `hot-disconnect`) now pass `link_state_id`; `live_links.teardown_link()` resolves by row ID first, with legacy `link_name` fallback only for backward compatibility.
    - A narrow endpoint-based compatibility matcher remains only for legacy rows missing `link_definition_id` during `_upsert_link_states()` and hot-connect migration paths.

- [x] **3.3 Remove node_name fallback queries across tasks**
  - Status: `completed`
  - Benefit: Medium (consistency)
  - Effort: Low
  - Scope:
    - Grep for remaining `node_name == container_name` patterns across `api/app/tasks/`.
    - Replace with FK joins or direct lookups.
  - Dependencies: Phase 2.2
  - Target files:
    - `api/app/tasks/live_nodes.py`
    - `api/app/tasks/jobs.py`
    - `api/app/tasks/reconciliation_db.py`
  - Completion criteria:
    - No runtime node resolution depends on name-based matching.
  - Implementation notes:
    - `deploy_node_immediately()` no longer falls back to `Node.container_name == node_state.node_name`; provider resolution now uses `node_definition_id` only (with lab-provider fallback if definition is missing).
    - `_capture_node_ips()` now maps agent-reported runtime names to `Node` definitions and updates `NodeState` via `node_definition_id` instead of `NodeState.node_name` lookup.
    - `_update_node_placements()` now preloads `Node` definitions and matches existing placements by `node_definition_id` (not `node_name`), always writing canonical `node_name` from the definition.
    - `_cleanup_orphan_containers()` now derives keep-sets from placement `node_definition_id` mapped through `Node` definitions.
    - `reconciliation_db` now:
      - resolves placement/link host associations via `node_definition_id`,
      - removes runtime placement backfill-by-name from the reconciliation hot path,
      - uses `node_definition_id` keyed placement/expected-agent maps,
      - uses FK-based orphan placement pruning,
      - builds node runtime-name/state maps from `Node` definitions to avoid name-based DB resolution.

## Phase 4: VXLAN Tunnel Identity

- [x] **4.1 Tighten VXLAN tunnel matching to deterministic identity**
  - Status: `completed`
  - Benefit: Medium
  - Effort: Medium-High
  - Scope:
    - `link_validator.py:201-210` matches on EITHER `link_id` OR `interface_name`. Remove `interface_name` fallback.
    - Validate tunnel identity via link/tunnel IDs only; use computed port name as assertion, not match key.
    - Ensure agent `recover_link_tunnels()` preserves `link_id` in rebuilt state.
  - Dependencies: Phase 3.2 (link identity must be stable first)
  - Target files:
    - `api/app/services/link_validator.py`
    - `agent/network/overlay_state.py`
  - Completion criteria:
    - No operational dependency on ambiguous tunnel matching.
    - Recovery workflows pass in chaos/restart scenarios.
  - Implementation notes:
    - `verify_cross_host_link()` now matches overlay tunnel entries by `link_id` only; the `interface_name` fallback match path was removed.
    - Agent startup recovery (`recover_link_tunnels`) now preserves deterministic identity by refreshing only known `interface_name -> link_id` mappings; unresolved OVS ports are no longer imported under placeholder link IDs.
    - This prevents interface-name-only recovered state from satisfying controller validation and removes ambiguous identity matching from tunnel verification.

## Phase 5: Image Identity (Lower Priority)

These are ingest-time issues, not hot-path reconciliation risks. Pursue when bandwidth allows.

- [ ] **5.1 Deterministic device assignment at ingest**
  - Status: `not started`
  - Benefit: Medium
  - Effort: Medium
  - Scope:
    - Keep filename detection as suggestion only.
    - Require explicit confirmation/override when detection confidence is low.
    - The two-phase upload flow (`auto_confirm=False` → confirm) already handles this partially.
  - Target files:
    - `api/app/routers/images/upload_docker.py`
    - `api/app/routers/images/upload_vm.py`
    - `api/app/image_store/detection.py`
  - Completion criteria:
    - Ambiguous ingest paths require explicit operator decision.

- [ ] **5.2 Runtime metadata lookup by stable image ID only**
  - Status: `not started`
  - Benefit: High
  - Effort: High
  - Scope:
    - Eliminate basename/reference fallback in runtime metadata resolution.
    - Ensure all callers pass canonical image IDs.
  - Target files:
    - `api/app/services/device_service.py`
    - `api/app/tasks/node_lifecycle_deploy.py`
  - Dependencies: 5.1
  - Completion criteria:
    - Metadata lookup does not rely on path basename or loose candidate matching.

---

## Milestones

- [x] **M1 (No new nulls):** Phase 1 complete — all creation paths set FKs
- [~] **M2 (Schema enforced):** Phase 2 migration authored and tested; pending apply to staging/prod
- [x] **M3 (Dead code removed):** Phase 3 complete — string fallbacks deleted
- [x] **M4 (Tunnel hardened):** Phase 4 complete — VXLAN uses strict ID matching
- [ ] **M5 (Full hardening):** Phase 5 complete — image identity deterministic

## Notes
- The constraint-first approach (fix creation → backfill → NOT NULL → remove fallback) prevents
  regression at each step. The NOT NULL constraint is self-enforcing — no fallback code can silently reappear.
- `ondelete="CASCADE"` vs `SET NULL`: CASCADE is preferred (state without a definition is orphaned garbage),
  but audit code that reads state after definition deletion before switching. Specific concern:
  topology sync in `crud.py` deletes old Node rows when GUI node IDs change — CASCADE would
  delete the associated NodeState, which is then recreated. Verify this doesn't cause a visible
  flash of missing state in the UI.
- For each phase, add targeted regression tests before removing fallback behavior.
- Image hardening (Phase 5) is deferred because the two-phase upload flow already mitigates the worst cases,
  and image identity issues are confined to ingest time rather than hot reconciliation paths.
- Creation site inventory (verified via grep):
  - `NodeState()`: 2 sites, both in `api/app/routers/labs/` (_shared.py, crud.py). Zero in tasks/.
  - `LinkState()`: 4 sites — 2 in routers (link_states.py), 2 in tasks (link_orchestration.py, reconciliation_db.py).
  - `NodePlacement()`: 3 sites, all in `api/app/tasks/` (jobs.py, state_enforcement.py, reconciliation_db.py).
    Plus 1 schema-only usage in `topology.py` (not a DB model).
