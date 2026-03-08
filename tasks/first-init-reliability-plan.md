# First-Initialization Reliability Plan

Status: Code complete; non-prod soak pending
Owner: Codex + operator review
Scope: Make initial lab provisioning trustworthy and deterministic so reconciliation is a safety net, not the primary mechanism for successful bring-up.

## Goal

A newly initialized lab should either:

1. provision correctly on the first attempt, including node bring-up and eligible link creation, or
2. fail fast with explicit prerequisite errors before partial runtime/link churn begins.

## Current Root Causes

### RC1. Async DB session misuse in reconciliation/provisioning

Long-lived SQLAlchemy sessions are used across awaited agent calls in:

- `api/app/tasks/link_reconciliation.py`
- `api/app/services/interface_mapping.py`
- `api/app/tasks/node_lifecycle.py`

Observed effect:

- `idle in transaction` sessions
- row-lock chains on `link_states` and `interface_mappings`
- statement timeouts
- stale/stuck job and node state after rollback paths

### RC2. Missing first-init hard preflight gates

Initial provisioning still proceeds when key prerequisites are not satisfied:

- assigned host offline
- required image missing on assigned host
- stale runtime namespace conflicts already present on host
- insufficient placement/capacity conditions
- insufficient VLAN / overlay capacity for cross-host links

Observed effect:

- partial node bring-up
- link creation attempted against not-ready endpoints
- retries and reconciliation doing work that should have been blocked up front

### RC3. Provision success is declared before endpoint/link readiness is authoritative

Node create/start can succeed before:

- metadata-backed status visibility is stable
- OVS/interface mapping is fully populated
- cross-host port state is converged
- same-host links are actually attachable

Observed effect:

- first-pass lab appears partially deployed but is not operational

### RC4. Recovery/reconciliation scope is too broad

Reconciliation still compensates for normal initialization failures rather than true drift.

Observed effect:

- noisy repeated repair cycles
- harder root-cause analysis
- DB churn and repeated link state mutation

## Success Criteria

- No `idle in transaction` controller sessions persist across awaited agent operations during lab deploy/reconcile flows.
- Initial provisioning fails fast before runtime creation when host, image, or capacity prerequisites are not met.
- Initial provisioning does not attempt cross-host link creation until required endpoint/interface mapping readiness is verified.
- A successful first lab initialization yields:
  - all expected runtimes visible through authoritative status
  - no stale `running` jobs after completion
  - no post-op reconciliation statement timeouts
  - no unexpected link repair churn within the first 10 minutes
- Reconciliation after first successful initialization performs zero corrective actions for healthy labs for 72 consecutive hours in non-prod.

## Execution Order

1. Fix transaction/session boundaries in async reconciliation and post-op convergence.
2. Add first-init hard preflight gates.
3. Gate node success and link creation on authoritative readiness.
4. Narrow reconciliation scope and reduce corrective churn.
5. Add regression tests, soak checks, and rollout runbooks.

## Phase 1. Transaction Safety

### 1.1 Refactor async DB usage in `link_reconciliation.py`

Tasks:

- [x] Audit every function that mixes `session.query(...)` with `await agent_client...`.
- [x] Split each affected flow into:
  - collect DB state
  - release/close transaction
  - perform awaited agent calls
  - reopen short write transaction for updates
- [x] Ensure `refresh_interface_mappings()` does not hold ORM transaction state while awaiting per-agent port-state fetches.
- [x] Ensure `run_same_host_convergence()` and `run_cross_host_port_convergence()` do not reuse stale ORM objects across awaited RPCs.
- [x] Add explicit rollback/commit boundaries around per-agent batches.

Deliverables:

- [x] No known `idle in transaction` leaks in reconciliation helpers.
- [x] Code comments documenting the transaction-boundary rule near helper entry points.

Priority: P0

### 1.2 Refactor post-op cleanup/convergence in `node_lifecycle.py`

Tasks:

- [x] Split `_post_operation_cleanup()` into isolated transactional phases.
- [x] Do not reuse the same ORM transaction across:
  - link reconciliation
  - overlay convergence
  - interface mapping refresh
  - cross-host port convergence
- [x] Convert failure handling so one failed post-op step cannot leave the session poisoned for the rest.

Deliverables:

- [x] Post-op cleanup can fail cleanly without cascading session rollback damage.

Priority: P0

### 1.3 Add instrumentation for transaction/lock failures

Tasks:

- [x] Emit metrics/log counters for:
  - statement timeouts by table/function
  - rollback-after-flush failures
- [x] Emit lock wait durations when available.
- [x] Add structured log fields with `lab_id`, `job_id`, `phase`, and target table.
  Current status: release-duration metrics and structured DB-contention context now flow through shared session-release helpers, `node_lifecycle`, `link_reconciliation`, and `interface_mapping`. Non-job paths still omit `job_id` when no job exists.

Deliverables:

- [x] We can tell whether remaining failures are DB contention vs agent/runtime errors.

Priority: P1

## Phase 2. First-Init Hard Preflight Gates

### 2.1 Host assignment health gate

Tasks:

- [x] Before any node create/start, verify all explicitly assigned hosts required for the requested operation are online and healthy.
- [x] Fail the job before mutation if any required host is unavailable.
- [x] Surface a clear operator-facing summary of blocked nodes and hosts.

Deliverables:

- [x] No runtime creation starts when required assigned hosts are offline.

Priority: P0

### 2.2 Image availability gate

Tasks:

- [x] For every node in the requested provisioning set, verify the exact required image is present or syncable on the assigned host before runtime creation.
- [x] Distinguish:
  - image missing
  - image syncing
  - image synced but unreadable/invalid
- [x] Fail fast if unsatisfied.

Deliverables:

- [x] Nodes like `iol_xe_2` fail before create/start attempts and before link churn begins.

Priority: P0

### 2.3 Stale runtime conflict gate

Tasks:

- [x] Reject Docker create when the target runtime namespace is occupied by a foreign container or a managed container with mismatched identity, instead of removing/reusing it blindly.
- [x] Reject libvirt create when the target domain name is occupied by a foreign domain or a managed domain with mismatched identity, instead of undefining/recreating it blindly.
- [x] Before create, verify the target runtime namespace/name is free or belongs to the expected placement/runtime identity.
- [x] If a conflicting runtime exists, classify it as:
  - exact expected runtime
  - stale managed runtime
  - foreign/unmanaged conflict
- [x] Block and report instead of blindly attempting create.
- [x] Add an agent/provider preflight probe for runtime namespace conflicts so Docker/libvirt can classify conflicts before create instead of relying on create-time side effects.

Deliverables:

- [x] No Docker 409 name-conflict surprises during first-init deploy.
- [x] No libvirt domain-name collision surprises during first-init deploy.

Priority: P0

### 2.4 Capacity and overlay feasibility gate

Tasks:

- [x] Upgrade current soft resource checks into a configurable hard gate for first initialization.
- [x] Add a preflight check for VLAN / overlay allocation capacity for required cross-host links.
- [x] Fail before partial provisioning if the fabric cannot allocate required tags/tunnels.

Deliverables:

- [x] No “No available VLAN tags” failures after partial node deployment for capacity-exhausted first-init paths; the deploy is now blocked before node churn.

Priority: P1

## Phase 3. Authoritative Readiness Before Success

### 3.1 Make node success depend on authoritative runtime visibility

Tasks:

- [x] Keep runtime-identity verification as mandatory for success.
- [x] Add a bounded stabilization window for metadata-backed status to appear after start.
- [x] Treat “running but not visible in authoritative status” as create/start failure, not reconciliation work.

Deliverables:

- [x] First-pass success means the runtime is actually visible through authoritative status.

Priority: P0

### 3.2 Make endpoint/link eligibility depend on live interface readiness

Tasks:

- [x] Require required data interfaces to exist in live port/interface state before same-host or cross-host link creation.
- [x] Distinguish:
  - node running but interface not present
  - node running and interface present but VLAN/tag missing
  - node and interface ready
- [x] Delay or fail link creation explicitly instead of optimistic create-then-repair.

Deliverables:

- [x] Link creation only runs when endpoints are actually attachable.

Priority: P0

### 3.3 Introduce an explicit initial-provision decision table

Tasks:

- [x] Define controller behavior for combinations of:
  - host ready / not ready
  - image ready / not ready
  - runtime visible / not visible
  - interface ready / not ready
  - link capacity available / unavailable
- [x] Enforce this before any broader rollout.

Deliverables:

- [x] A documented and implemented decision table for first-init transitions.

Priority: P1

## Phase 4. Reconciliation Scope Reduction

### 4.1 Stop using reconciliation as normal initialization glue

Tasks:

- [x] Identify reconciliation behaviors currently compensating for missing initial-provision guarantees.
- [x] Move required first-pass checks into provisioning code paths.
- [x] Keep reconciliation focused on:
  - runtime drift
  - stale controller state
  - orphan cleanup
  - unexpected host-side changes

Deliverables:

- [x] Healthy new labs do not need corrective reconciliation to become usable.

Priority: P1

### 4.2 Reduce repeated link churn for intentionally undeployed/stopped nodes

Tasks:

- [x] Ensure links tied to intentionally undeployed/stopped nodes remain quietly queued/down without repeated noisy recovery attempts.
- [x] Avoid repeated state flips for links that are correctly waiting on intentional node absence.

Deliverables:

- [x] Scheduler logs stay quiet for known-intent down links in the reconciliation hot path.

Priority: P1

## Phase 5. Testing and Rollout

### 5.1 Regression tests for transaction safety

Tasks:

- [x] Add tests that simulate:
  - awaited agent calls inside convergence loops
  - lock-sensitive `link_states` updates
  - lock-sensitive `interface_mappings` updates
- [x] Ensure no helper leaves the session in a poisoned transactional state after failure.

Priority: P0

### 5.2 First-init scenario tests

Tasks:

- [x] Add integration-style tests for:
  - host offline at initialization
  - image missing at initialization
  - stale runtime conflict at initialization
  - libvirt runtime not visible after start
  - link capacity exhausted
  - mixed-provider partial-success scenario

Priority: P0

### 5.3 Soak and observability gates

Tasks:

- [x] Add dashboards or reports for:
  - first-init failure classes
  - post-op reconciliation failures
  - statement timeouts
  - repeated link repair counts
- [x] Define a non-prod soak window of 72 consecutive hours with zero unexpected corrective reconciliation for newly provisioned healthy labs.

Priority: P1

## Task Tracker

### P0

- [x] 1.1 Refactor async DB usage in `link_reconciliation.py`
- [x] 1.2 Refactor post-op cleanup/convergence in `node_lifecycle.py`
- [x] 2.1 Host assignment health gate
- [x] 2.2 Image availability gate
- [x] 2.3 Stale runtime conflict gate
- [x] 3.1 Make node success depend on authoritative runtime visibility
- [x] 3.2 Make endpoint/link eligibility depend on live interface readiness
- [x] 5.1 Regression tests for transaction safety
- [x] 5.2 First-init scenario tests

### P1

- [x] 1.3 Add instrumentation for transaction/lock failures
- [x] 2.4 Capacity and overlay feasibility gate
- [x] 3.3 Initial-provision decision table
- [x] 4.1 Stop using reconciliation as normal initialization glue
- [x] 4.2 Reduce repeated link churn for intentionally undeployed/stopped nodes
- [x] 5.3 Soak and observability gates

## Definition Of Done

- [x] First-init for a healthy lab succeeds without corrective reconciliation.
- [x] First-init for an unhealthy lab fails before partial runtime/link churn.
- [x] No reconciliation/provisioning helper holds a DB transaction open across awaited agent operations.
- [ ] No post-op statement timeout occurs during normal lab deploy in non-prod soak.
- [x] Remaining reconciliation actions on healthy labs are exceptional, not routine.

## Remaining External Validation

- [ ] Run the defined 72-hour non-prod soak using `/first-init-reliability-report` and confirm:
  - zero unexpected corrective reconciliation for newly provisioned healthy labs
  - zero post-op reconciliation failures
  - zero statement timeouts during normal deploy
