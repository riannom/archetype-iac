# Runtime Identity Hardening Plan

## Goal
Make node and link reconciliation key off deterministic topology identifiers plus provider-attested runtime identity, not runtime naming conventions or fallback prefix matching.

## Problem Statement
The controller-side model is mostly hardened already:

- `NodeState.node_definition_id`
- `NodePlacement.node_definition_id`
- `LinkState.link_definition_id`

These identifiers are the intended-state source of truth. The remaining weakness is in actual-state discovery and runtime-to-topology mapping:

- Docker status/discovery is label-first, but still has a prefix/name fallback in the hot path.
- Libvirt status/discovery relies on deterministic domain naming, not embedded VM metadata.
- `NodePlacement.runtime_id` exists, but is not treated as a first-class reconciliation invariant.
- Managed runtime objects do not consistently carry provider-native metadata tying them back to `node_definition_id`.

This means the system is deterministic at the DB layer, but not fully self-authenticating at the runtime layer.

## Non-Goals
- Replacing `container_name` as the topology-facing stable node handle.
- Reworking provider selection or mixed-provider routing.
- Large schema redesign outside node/link/runtime identity.

## Desired End State

### Node Identity
Every managed runtime object can be resolved by exact metadata to one `Node.id`, and every `NodePlacement` can verify that the runtime object it references is the correct one.

Required invariants:

- One active `NodePlacement` per `(lab_id, node_definition_id)`.
- Each managed runtime carries:
  - `archetype.lab_id`
  - `archetype.node_definition_id`
  - `archetype.node_name`
  - `archetype.provider`
- `runtime_id` in `NodePlacement` is populated on create/start and refreshed on reconciliation when the exact object is found.
- Reconciliation treats metadata mismatch as drift, not as a hint to guess by name.

### Link Identity
Link reconciliation continues to use `link_definition_id` as the authoritative key, with no new string-only matching introduced.

### Runtime Discovery
- Docker: exact label match only in hot paths.
- Libvirt: exact metadata match only in hot paths.
- Name-based lookup remains available only for recovery, migration, and admin diagnostics.

## Current State Summary

### Strengths
- Topology node identity is already anchored on immutable `container_name` and DB row ID.
- Node/link/placement FKs are enforced by recent deterministic identifier work.
- Docker already stamps useful labels (`archetype.lab_id`, `archetype.node_name`, `archetype.provider`).

### Gaps
- Docker hot-path status still falls back to prefix matching when label query fails.
- Libvirt uses generated domain names as effective identity instead of embedded metadata.
- `runtime_id` is stored but not enforced as a reconciliation contract.
- Provider-created runtime objects do not uniformly carry `node_definition_id`.
- Agent discovery/status payloads do not yet provide enough identity data for the API to reconcile by metadata without extra inference.

## Approach
Treat runtime identity as a second layer of truth:

1. Topology FK identifies what should exist.
2. Provider metadata identifies which runtime object is ours.
3. `runtime_id` verifies we are looking at the exact expected runtime instance.

This is a migration-first hardening plan. We should add metadata and verification before removing fallbacks, so rollout is observable and reversible.

## How To Track
- Status legend: `[ ]` not started, `[~]` in progress, `[x]` completed.
- Update the checkbox and `Status:` line for each task.
- Record follow-up PRs/commits under the relevant section.

## Prioritization Matrix

| Priority | Task | Benefit | Effort |
|---|---|---:|---:|
| P0 | Docker label stamping with `node_definition_id` | Very High | Medium |
| P0 | Libvirt `<metadata>` stamping with `node_definition_id` | Very High | Medium-High |
| P0 | Agent discovery/status payloads include `node_definition_id` | Very High | Medium |
| P0 | Persist full `runtime_id` on placement create/update | Very High | Medium |
| P1 | Add verification + drift metrics while fallback remains active | High | Medium |
| P1 | Docker label-resolvability audit and fallback removal | High | Low-Medium |
| P1 | Libvirt metadata audit and name-fallback removal | High | Medium |
| P2 | Optional link-side runtime identity metadata | Medium | Medium |

## Phase 1: Stamp Exact Identity Metadata at Runtime Creation

Implementation note:
- Finalize the field contract in code comments while implementing this phase.
- Do not create a separate design-only phase.

- [x] **1.1 Docker: stamp `node_definition_id` on every managed container**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Update Docker provider create/deploy paths to include:
      - `archetype.lab_id`
      - `archetype.node_definition_id`
      - `archetype.node_name`
      - `archetype.provider`
    - Verify create, redeploy, and start paths all converge on the same labels.
    - Audit any container recreation logic that may omit labels.
  - Completion criteria:
    - Every managed Docker container is queryable by exact `node_definition_id` label.

- [x] **1.2 Libvirt: embed `node_definition_id` in domain `<metadata>`**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium-High
  - Scope:
    - Use libvirt domain `<metadata>` with a custom XML namespace.
    - Persist:
      - `lab_id`
      - `node_definition_id`
      - `provider`
      - `node_name`
    - Read metadata via libvirt metadata APIs in discovery/status paths.
    - Ensure metadata survives define/start/stop/reboot.
  - Completion criteria:
    - Libvirt discovery can resolve a VM without parsing the domain name.

- [x] **1.3 Agent status/discovery endpoints include `node_definition_id` in response payloads**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Extend provider `NodeInfo` or equivalent response model to carry `node_definition_id`.
    - Ensure Docker and libvirt discovery/status endpoints return this field when metadata exists.
    - Update API consumers to prefer returned `node_definition_id` over name-based joins.
  - Completion criteria:
    - API reconciliation can consume provider-returned identity directly without extra inference calls.

## Phase 2: Make `runtime_id` First-Class

- [x] **2.1 Define and persist full `runtime_id` on placement create/update**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Docker: store full container ID, not shortened display form.
    - Libvirt: store full domain UUID, not shortened display form.
    - Populate `NodePlacement.runtime_id` everywhere a node is created, started, or rediscovered.
    - Use shortened IDs only for UI/logging.
  - Completion criteria:
    - New placements always have full `runtime_id`.

- [x] **2.2 Verify runtime object identity during reconciliation and handle replacement explicitly**
  - Status: `completed`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Reconciliation should validate:
      - topology FK
      - provider metadata
      - stored `runtime_id` when present
    - If metadata matches but `runtime_id` differs, classify as intentional replacement vs drift.
    - Update placement/runtime ID only through explicit “runtime replaced” logic.
    - Handle expected recreate cases cleanly:
      - node redeploy
      - migration
      - orphan cleanup followed by re-create
    - Produce and check in a reconciliation decision table covering:
      - metadata present/missing
      - runtime ID match/mismatch/missing
      - legacy name match/mismatch
      - resulting action
  - Completion criteria:
    - No silent reassociation to a different runtime object.
    - Runtime ID changes are intentional and auditable.

## Phase 3: Observability and Verification

- [x] **3.1 Add identity drift metrics and logs while keeping legacy fallback active**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Count:
      - metadata mismatch
      - missing metadata
      - runtime ID mismatch
      - legacy fallback used
      - active placements with `runtime_id IS NULL`
    - Emit structured logs with:
      - `lab_id`
      - `node_definition_id`
      - provider
      - expected runtime ID
      - observed runtime ID
  - Completion criteria:
    - Drift and fallback use are measurable before removal.

- [x] **3.2 Define operational response to identity drift**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Represent drift as:
      - placement status flag
      - audit event
    - Do not set node `actual_state=error` for metadata/runtime ID mismatch alone.
    - Set node `actual_state=error` only when the runtime object is genuinely missing or unusable.
  - Completion criteria:
    - Drift handling is deterministic and documented in code comments/tests.

## Phase 4: Audit Existing Runtime Objects

- [x] **4.1 Docker: quantify prefix-fallback removal risk**
  - Status: `completed`
  - Benefit: High
  - Effort: Low-Medium
  - Scope:
    - Add an audit path that queries all managed containers and counts:
      - label-resolvable containers
      - name-only containers
      - containers missing `node_definition_id`
      - containers with inconsistent metadata
    - Record the counts in logs and/or a report.
  - Completion criteria:
    - Docker fallback removal is gated by data, not assumption.

- [x] **4.2 Libvirt: audit metadata coverage**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Query all managed domains and count:
      - metadata-resolvable VMs
      - name-only VMs
      - VMs missing `node_definition_id`
      - VMs with inconsistent metadata
  - Completion criteria:
    - Libvirt name-fallback removal is gated by measured metadata coverage.

## Phase 5: Backfill and Migrate Existing Runtimes

- [x] **5.1 Backfill metadata on existing runtimes where safe**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Docker: labels are immutable after container creation, so backfill requires controlled rolling recreate; there is no in-place relabel path.
    - Libvirt: update domain metadata in place where possible.
    - Define when backfill is automatic vs admin-invoked.
  - Completion criteria:
    - Existing fleets can be migrated without requiring full lab teardown.
    - Backfill occurs only after verification logic exists.

- [x] **5.2 Move toward required `runtime_id` with a NULL-safe migration path**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Stage 1:
      - warn when `runtime_id` is `NULL` for an active placement
      - track the count
    - Stage 2:
      - once the count reaches zero in production and remains there, add DB constraint(s)
    - Do not make the column `NOT NULL` immediately.
  - Completion criteria:
    - `runtime_id` can become required safely, based on observed production readiness.

## Phase 6: Remove Hot-Path Fallbacks

- [x] **6.1 Docker: remove prefix/name fallback from status and discovery hot paths**
  - Status: `completed`
  - Benefit: High
  - Effort: Low-Medium
  - Scope:
    - Replace prefix fallback in status/reconcile paths with exact label-only lookup.
    - Preserve fallback only in an explicit admin recovery endpoint or diagnostic tool.
  - Gate:
    - zero name-only containers in the Docker audit
    - zero name-only resolutions in production reconciliation logs for 72 consecutive hours
  - Completion criteria:
    - Normal reconciliation never adopts a container by name prefix alone.

- [x] **6.2 Libvirt: remove name-based identity from status and discovery hot paths**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Discovery/status should read `<metadata>` first.
    - Generated domain name remains an implementation detail and convenience handle.
    - Name-based lookup may remain for legacy cleanup and single-node actions during transition.
  - Gate:
    - zero metadata-missing VMs in the libvirt audit
    - zero name-only resolutions in production reconciliation logs for 72 consecutive hours
  - Completion criteria:
    - Reconciliation can operate correctly even if domain naming is no longer the sole identity source.

- [x] **6.3 Move legacy lookup into recovery-only code paths**
  - Status: `completed`
  - Benefit: High
  - Effort: Medium
  - Scope:
    - Explicitly isolate “find by name/prefix” helpers into:
      - admin/discovery repair
      - migration cleanup
      - support bundle diagnostics
    - Add comments stating they are not valid hot-path identity mechanisms.
  - Completion criteria:
    - Name-based adoption is impossible during routine reconciliation.

## Phase 7: Tests, Rollout, and Rollback

- [ ] **7.1 Add provider contract tests in two waves**
  - Status: `not started`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Wave 1, written alongside Phases 1-2:
      - Docker deploy stamps exact labels
      - Libvirt define/deploy writes exact metadata
      - agent status/discovery payloads include `node_definition_id`
      - placement creation/update persists full `runtime_id`
    - Wave 2, written alongside Phase 6:
      - Docker name-only container is ignored in hot path
      - Libvirt name-only VM is ignored in hot path
  - Completion criteria:
    - Provider identity contract is covered directly by tests.

- [ ] **7.2 Add reconciliation non-regression tests**
  - Status: `not started`
  - Benefit: Very High
  - Effort: Medium
  - Scope:
    - Mismatched runtime ID does not get silently adopted.
    - Metadata mismatch is surfaced as drift.
    - Legitimate redeploy updates placement/runtime ID cleanly.
    - Mixed-provider partial migration case:
      - one provider returns metadata
      - the other is still missing metadata
      - reconciliation behaves deterministically and does not over-adopt by name
  - Completion criteria:
    - Hot path behaves deterministically under replacement, drift, and partial-migration cases.

- [ ] **7.3 Rollout stages with explicit rollback criteria**
  - Status: `not started`
  - Benefit: High
  - Effort: Low
  - Scope:
    - Stage 1:
      - metadata stamping
      - runtime ID persistence
      - verification and metrics
      - legacy fallback still active
    - Stage 2:
      - remove hot-path fallbacks after audit gates pass
    - Define:
      - minimum Stage 1 soak duration before Stage 2 eligibility: 72 hours
      - metric/log thresholds that trigger rollback
      - manual override to re-enable name-based lookup in an emergency
  - Recommended rollback triggers:
    - sustained increase in identity-drift events above agreed threshold
    - any production reconciliation failures attributable to missing metadata on managed runtimes
    - any non-zero name-only adoption after Stage 2 enablement
  - Completion criteria:
    - Fallback removal happens only after measured readiness, with a defined escape hatch.

## Open Design Questions

1. Should `NodePlacement.runtime_id` eventually become required?
   - Yes, but only via the NULL-safe migration path in Phase 5.2.

2. Should libvirt identity use UUID only, metadata only, or both?
   - Both.
   - Metadata answers “whose VM is this?”
   - UUID answers “is this the same VM instance as before?”

3. Should we add `node_definition_id` directly to any agent callback payloads?
   - Yes where practical, to reduce string joins in callback processing.

## Recommended Execution Order

1. Stamp Docker labels and libvirt `<metadata>` fields, finalizing field names in code.
2. Update agent discovery/status endpoints to return `node_definition_id`.
3. Persist full `runtime_id` on placement create/update.
4. Add verification and drift metrics while keeping legacy fallback active.
5. Audit existing runtimes and count label/metadata-resolvable vs name-only objects.
6. Backfill/migrate existing runtimes.
7. Remove Docker prefix fallback.
8. Remove libvirt name-based identity assumptions.

## Success Criteria

- Routine reconciliation never needs to guess by runtime name or prefix.
- Every managed runtime object can be mapped to exactly one topology node by exact metadata.
- `runtime_id` changes are intentional, logged, and validated.
- Drift is surfaced explicitly instead of being papered over by fallback adoption.
- Docker and libvirt follow the same identity model even though their runtime APIs differ.
- Zero name-only resolutions in production reconciliation logs for 72 consecutive hours before fallback removal.
