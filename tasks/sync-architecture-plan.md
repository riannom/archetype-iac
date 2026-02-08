# Sync Architecture Improvements Plan

**Created:** 2026-02-07
**Status:** APPROVED — Ready for implementation
**Scope:** 16 items across Architecture, Code Quality, Tests, Performance
**Estimated effort:** ~900-1,200 lines across ~10 files + 1 new test file

## Context

After completing the state management overhaul (Phases 0-6), a sync architecture review identified 16 improvements that are NOT covered by any existing plan. These address:
- Event-driven state convergence (currently only cleanup events exist)
- Enforcement/reconciliation interaction bugs
- Cleanup handler reliability
- N+1 query patterns in hot sync paths
- Test coverage gaps for sync layer

**Key findings from review:**
- NLM already broadcasts state immediately (11 broadcast points) — event-driven state is an optimization for edge cases (crashes, agent failures), not critical path
- Phase 4 (frontend state management) is COMPLETE
- Phase 6.4 (batch parallel agent reconcile) is being handled separately

---

## Implementation Order

Dependencies flow: Code Quality fixes → Tests (TDD) → Architecture changes → Performance.
Recommended order minimizes risk by fixing bugs first, adding tests, then making architectural changes.

### Phase A: Foundation Fixes (Code Quality #5, #6, #7, #4)

**Rationale:** Fix bugs and DRY violations before adding new features. These are small, targeted changes.

#### A.1: Extract interval selection to Settings method (#5)
**Files:** `api/app/config.py`, `api/app/tasks/reconciliation.py`, `api/app/tasks/state_enforcement.py`, `api/app/tasks/disk_cleanup.py`

**What:** Add a `get_interval()` method to Settings that encapsulates the `X_extended if cleanup_event_driven_enabled else X` pattern.

**Implementation:**
```python
# config.py - Add method to Settings class
def get_interval(self, name: str) -> int:
    """Get monitor interval, using extended value when event-driven cleanup is active."""
    intervals = {
        "reconciliation": (self.reconciliation_interval, self.reconciliation_interval_extended),
        "state_enforcement": (self.state_enforcement_interval, self.state_enforcement_interval_extended),
        "cleanup": (self.cleanup_interval, self.cleanup_interval_extended),
    }
    normal, extended = intervals[name]
    return extended if self.cleanup_event_driven_enabled else normal
```

Then replace the 3 instances in monitor functions:
```python
# Before (repeated 3x):
interval = (
    settings.reconciliation_interval_extended
    if settings.cleanup_event_driven_enabled
    else settings.reconciliation_interval
)

# After:
interval = settings.get_interval("reconciliation")
```

**Lines changed:** ~15 across 4 files

#### A.2: Increment enforcement_attempts on exception (#6)
**Files:** `api/app/tasks/state_enforcement.py`

**What:** When `enforce_node_state()` throws an exception (not a job failure, but an actual exception), increment `enforcement_attempts` so the node eventually hits max retries instead of infinite-looping.

**Implementation:** In `enforce_lab_states()` around lines 476-484:
```python
except Exception as e:
    logger.error(
        f"Error enforcing state for {node_state.node_name} "
        f"in lab {node_state.lab_id}: {e}"
    )
    try:
        # Increment attempts to prevent infinite loop on persistent exceptions
        node_state.enforcement_attempts += 1
        node_state.last_enforcement_at = datetime.now(timezone.utc)
        if node_state.enforcement_attempts >= settings.state_enforcement_max_retries:
            node_state.enforcement_failed_at = datetime.now(timezone.utc)
            node_state.error_message = f"Enforcement exception after {node_state.enforcement_attempts} attempts: {e}"
        session.commit()
    except Exception:
        session.rollback()
```

**Nuance:** If the exception IS a DB error, the commit will also fail — that's fine, the rollback catches it. On the next cycle, the stale enforcement_attempts means it retries, which is correct behavior for transient DB issues.

**Lines changed:** ~10

#### A.3: Reconciliation respects enforcement_failed_at (#7)
**Files:** `api/app/tasks/reconciliation.py`

**What:** When reconciliation updates `actual_state`, skip nodes where `enforcement_failed_at` is set. This prevents reconciliation from overwriting enforcement's ERROR state, which would cause an infinite retry oscillation.

**Implementation:** In the node state update section (around lines 893-1040), add a guard:
```python
# Skip nodes where enforcement has permanently failed — enforcement owns their state
if ns.enforcement_failed_at is not None:
    logger.debug(
        f"Skipping reconciliation for {ns.node_name}: enforcement_failed_at set"
    )
    continue
```

**Important:** This ONLY applies to `actual_state` updates. Reconciliation should still update `is_ready` and other non-state fields if needed.

**Also:** When the user manually resets a node (clicks "Start" on error node), `enforcement_failed_at` is cleared (already implemented in labs.py:924-927), so reconciliation will resume for that node.

**Lines changed:** ~10

#### A.4: Tighten cleanup safety-net interval (#4)
**Files:** `api/app/config.py`

**What:** Change `cleanup_interval_extended` from 14400 (4 hours) to 3600 (1 hour).

```python
# Before:
cleanup_interval_extended: int = 14400  # 4 hours

# After:
cleanup_interval_extended: int = 3600   # 1 hour (same as normal - safety net for lost events)
```

**Lines changed:** 1

---

### Phase B: Tests Before Architecture (TDD for #9, #10, #11, #12) — COMPLETE

**Rationale:** Write tests first (TDD) for the behaviors we're about to implement/fix. Tests for Phase A fixes validate they work. Tests for Phase C features define expected behavior.

**Result:** 46 new tests across 3 files (96 total pass, 1 skipped integration test).

#### B.1: Enforcement exception handling tests (#9)
**File:** `api/tests/test_state_enforcement.py`

**Add 4-5 test cases:**
1. `test_exception_during_job_creation_increments_attempts` — Exception in enforce_node_state → enforcement_attempts += 1
2. `test_exception_updates_last_enforcement_at` — Timestamp updated even on exception
3. `test_consecutive_exceptions_hit_max_retries` — After max exceptions, enforcement_failed_at is set
4. `test_db_error_exception_rolls_back_cleanly` — DB exception doesn't corrupt state
5. `test_transient_exception_allows_next_cycle_retry` — After 1 exception, next cycle still tries

**Lines:** ~60-80

#### B.2: Reconciliation-enforcement interaction tests (#10)
**File:** `api/tests/test_tasks_reconciliation.py`

**Add 4-5 test cases:**
1. `test_reconciliation_skips_enforcement_failed_nodes` — Nodes with enforcement_failed_at set are not updated
2. `test_reconciliation_does_not_overwrite_error_state` — ERROR state preserved when enforcement_failed_at set
3. `test_reconciliation_updates_nodes_without_enforcement_failed` — Normal nodes still updated (no false positives)
4. `test_user_reset_allows_reconciliation_again` — After clearing enforcement_failed_at, reconciliation resumes
5. `test_reconciliation_still_updates_is_ready_for_failed_nodes` — Only actual_state is skipped, not other fields (if applicable)

**Lines:** ~80-100

#### B.3: Targeted reconciliation tests (#11)
**File:** `api/tests/test_tasks_reconciliation.py`

**Add 10-15 test cases covering gaps:**
1. `test_reconciliation_on_transitional_state_starting` — Node in STARTING state during reconciliation
2. `test_reconciliation_on_transitional_state_stopping` — Node in STOPPING state during reconciliation
3. `test_reconciliation_on_transitional_state_pending` — Node in PENDING state during reconciliation
4. `test_reconciliation_idempotent_same_result` — Run twice on same lab, same result
5. `test_reconciliation_agent_unreachable_mid_cycle` — Agent goes offline during query
6. `test_reconciliation_partial_agent_responses` — Some agents respond, some don't
7. `test_reconciliation_link_state_error_handling` — Link reconciliation failure isolated
8. `test_reconciliation_stale_node_recovery` — Node stuck in PENDING past threshold
9. `test_reconciliation_with_active_job_skips_node` — Active job prevents state update
10. `test_reconciliation_orphan_container_cleanup` — Containers not in DB are cleaned up
11. `test_reconciliation_broadcast_on_state_change` — State changes trigger WS broadcast
12. `test_reconciliation_no_broadcast_when_unchanged` — No broadcast when state is same

**Lines:** ~200-300

#### B.4: Cleanup handler test suite (#12)
**File:** `api/tests/test_cleanup_handler.py` (NEW)

**TDD for circuit breaker + idempotency (Phase C.2), plus dispatch routing:**

**Dispatch routing (5 tests):**
1. `test_lab_deleted_dispatches_workspace_cleanup` — LAB_DELETED → _cleanup_lab_workspace
2. `test_node_removed_dispatches_placement_cleanup` — NODE_REMOVED → _cleanup_node_placement
3. `test_agent_offline_dispatches_image_host_cleanup` — AGENT_OFFLINE → _cleanup_agent_image_hosts
4. `test_deploy_finished_dispatches_correctly` — DEPLOY_FINISHED routing
5. `test_unknown_event_type_logged_and_skipped` — Unknown type doesn't crash

**Circuit breaker (4 tests):**
6. `test_circuit_breaker_opens_after_consecutive_failures` — 3 failures → stop trying for 60s
7. `test_circuit_breaker_resets_on_success` — Success after failure resets counter
8. `test_circuit_breaker_reopens_after_cooldown` — After 60s, retries again
9. `test_circuit_breaker_per_handler_type` — One handler failing doesn't block others

**Idempotency (3 tests):**
10. `test_duplicate_lab_deleted_safe` — Second LAB_DELETED for same lab is no-op
11. `test_duplicate_node_removed_safe` — Second NODE_REMOVED for same node is no-op
12. `test_cleanup_after_already_cleaned` — Handler runs on already-cleaned state

**Error isolation (3 tests):**
13. `test_handler_error_doesnt_crash_monitor` — Exception in one handler doesn't stop others
14. `test_retry_on_transient_failure` — Single retry on first failure
15. `test_backoff_between_retries` — Delay between retry attempts

**Lines:** ~250-350

---

### Phase C: Architecture Changes (#1+#2, #3, #8)

**Rationale:** Now that tests are in place, implement the architectural improvements.

#### C.1: Event-driven state convergence (#1+#2)
**Files:** `api/app/config.py`, `api/app/events/cleanup_events.py`, `api/app/events/publisher.py`, `api/app/tasks/cleanup_handler.py`

**What:** Make events trigger immediate state reconciliation/enforcement in addition to cleanup. Decouple cleanup intervals from state intervals.

**Implementation steps:**

1. **Decouple intervals in config.py** — Since A.1 already created `get_interval()`, update it:
   - State intervals (reconciliation, enforcement) always use their NORMAL values
   - Only cleanup interval uses the extended value when event-driven is enabled
   - This reverses the current coupling where enabling cleanup events slows state polling

```python
def get_interval(self, name: str) -> int:
    """Get monitor interval."""
    intervals = {
        "reconciliation": (self.reconciliation_interval, self.reconciliation_interval),  # Never extended
        "state_enforcement": (self.state_enforcement_interval, self.state_enforcement_interval),  # Never extended
        "cleanup": (self.cleanup_interval, self.cleanup_interval_extended),  # Only cleanup extends
    }
    normal, extended = intervals[name]
    return extended if self.cleanup_event_driven_enabled else normal
```

2. **Add state trigger events** — Add new event types to `cleanup_events.py` (or create a separate `state_events.py`):
   - `STATE_CHECK_REQUESTED` — triggers immediate reconciliation + enforcement for a specific lab

3. **Emit state triggers from existing events** — In `cleanup_handler.py`, after handling DEPLOY_FINISHED, DESTROY_FINISHED, and JOB_FAILED, also trigger immediate state reconciliation for the affected lab.

4. **Add state reconciliation handler** — In `cleanup_handler.py` (or separate handler), subscribe to state triggers and call `refresh_states_from_agents()` for the specific lab, then call `enforce_lab_states()` for that lab.

**Design decision:** The state trigger should call existing functions (`refresh_states_from_agents` filtered to one lab, then `enforce_lab_states` filtered to one lab). This reuses existing logic without duplication.

**Important:** The existing reconciliation/enforcement monitors continue running at their normal intervals as a safety net. The event-triggered checks are an optimization for faster convergence.

**Lines:** ~50-70

#### C.2: Circuit breaker + idempotent cleanup handlers (#8)
**Files:** `api/app/tasks/cleanup_handler.py`

**What:** Add circuit breaker pattern and idempotent guards to the cleanup event handler.

**Implementation:**

1. **Circuit breaker state** — Track consecutive failures per handler type:
```python
class CircuitBreaker:
    def __init__(self, max_failures: int = 3, cooldown: float = 60.0):
        self.max_failures = max_failures
        self.cooldown = cooldown
        self._failures: dict[str, int] = {}  # handler_type → count
        self._last_failure: dict[str, float] = {}  # handler_type → timestamp

    def is_open(self, handler_type: str) -> bool:
        """Return True if circuit is open (should NOT process)."""
        if self._failures.get(handler_type, 0) < self.max_failures:
            return False
        elapsed = time.time() - self._last_failure.get(handler_type, 0)
        if elapsed > self.cooldown:
            # Half-open: reset and allow retry
            self._failures[handler_type] = 0
            return False
        return True

    def record_failure(self, handler_type: str):
        self._failures[handler_type] = self._failures.get(handler_type, 0) + 1
        self._last_failure[handler_type] = time.time()

    def record_success(self, handler_type: str):
        self._failures[handler_type] = 0
```

2. **Idempotent guards** — Add existence checks before destructive operations:
```python
async def _cleanup_lab_workspace(self, lab_id: str):
    workspace = Path(settings.workspace) / lab_id
    if not workspace.exists():
        logger.debug(f"Lab workspace already cleaned: {lab_id}")
        return
    shutil.rmtree(workspace)
```

3. **Exponential backoff on retry** — Replace single immediate retry with delayed retry:
```python
except Exception as first_error:
    await asyncio.sleep(1.0)  # Brief backoff before retry
    try:
        await handler(event)
    except Exception as retry_error:
        circuit_breaker.record_failure(event.event_type.value)
        logger.error(f"Handler failed after retry: {retry_error}")
```

**Lines:** ~40

#### C.3: Auto-extract configs before enforcement restart (#3)
**Files:** `api/app/tasks/state_enforcement.py`, possibly `api/app/agent_client.py`

**What:** When enforcement detects a crashed container (actual_state in {exited, error, undeployed} but desired_state=running), attempt to extract configs BEFORE creating the restart job. This prevents losing unsaved runtime configurations.

**Implementation:**
```python
# In enforce_node_state(), before creating the restart job:
if action in ("start", "deploy") and actual_state in ("exited", "error"):
    try:
        # Try to extract configs before restart (container may still exist)
        await _try_extract_configs(session, lab_id, node_name, agent)
    except Exception as e:
        logger.debug(f"Config extraction before restart failed (expected if container gone): {e}")
```

**The helper function:**
```python
async def _try_extract_configs(session, lab_id: str, node_name: str, agent):
    """Best-effort config extraction before enforcement restart.

    Fails silently if container is already gone — that's expected for crashes.
    Only extracts if the container still exists (exited state, not destroyed).
    """
    try:
        configs = await agent_client.extract_configs_on_agent(agent, lab_id)
        if node_name in configs:
            # Save to config snapshot (same as manual extract)
            _save_config_snapshot(session, lab_id, node_name, configs[node_name])
    except Exception:
        pass  # Best effort — container may be gone
```

**Important:** This is best-effort. If the container is already destroyed (actual_state=undeployed), extraction will fail silently. The goal is to catch the case where the container exited but still exists on disk.

**Lines:** ~30-50

---

### Phase D: Performance Optimizations (#13, #14, #15, #16)

**Rationale:** With correctness fixes and tests in place, optimize the hot paths.

#### D.1: Batch-load enforcement agent lookups (#13)
**Files:** `api/app/tasks/state_enforcement.py`

**What:** Replace per-node `_get_agent_for_node()` (7+ queries each) with batch-loaded dicts at cycle start.

**Implementation:**
```python
async def enforce_lab_states():
    with get_session() as session:
        # Batch load all data needed for enforcement
        mismatched = _get_mismatched_nodes(session)
        if not mismatched:
            return

        # Collect unique lab_ids from mismatched nodes
        lab_ids = {ns.lab_id for ns in mismatched}

        # Batch load nodes, placements, and hosts for all affected labs
        nodes = session.query(models.Node).filter(
            models.Node.lab_id.in_(lab_ids)
        ).all()
        nodes_by_lab_name = {(n.lab_id, n.container_name): n for n in nodes}

        placements = session.query(models.NodePlacement).filter(
            models.NodePlacement.lab_id.in_(lab_ids)
        ).all()
        placements_by_lab_node = {(p.lab_id, p.node_name): p for p in placements}

        host_ids = {p.host_id for p in placements if p.host_id} | {n.host_id for n in nodes if n.host_id}
        hosts = {h.id: h for h in session.query(models.Host).filter(models.Host.id.in_(host_ids)).all()} if host_ids else {}

        # Also batch load active jobs
        active_jobs = session.query(models.Job).filter(
            models.Job.lab_id.in_(lab_ids),
            models.Job.status.in_(["pending", "running"]),
        ).all()
        active_jobs_by_lab_node = _index_jobs(active_jobs)

        # Now iterate — lookups are O(1) dict access
        for ns in mismatched:
            node_def = nodes_by_lab_name.get((ns.lab_id, ns.node_name))
            placement = placements_by_lab_node.get((ns.lab_id, ns.node_name))
            agent = _resolve_agent(node_def, placement, hosts)
            # ... enforce with pre-loaded data
```

**Impact:** Reduces 350+ queries (50 nodes) to ~5 batch queries. From O(7N) to O(5).

**Lines:** ~40

#### D.2: Index reconciliation node placements (#14)
**Files:** `api/app/tasks/reconciliation.py`

**What:** `db_nodes` is already loaded at line 713. Build a dict index and reuse it at line 1078 instead of re-querying per container.

**Implementation:**
```python
# At line 713-719, after loading db_nodes, add:
db_nodes = session.query(models.Node).filter(models.Node.lab_id == lab_id).all()
nodes_by_container_name = {n.container_name: n for n in db_nodes}

# Also batch load placements:
placements = session.query(models.NodePlacement).filter(
    models.NodePlacement.lab_id == lab_id
).all()
placements_by_node_name = {p.node_name: p for p in placements}

# At line 1078-1127, replace per-item queries:
# Before:
for node_name, agent_id in container_agent_map.items():
    node_def = session.query(models.Node).filter(...).first()  # REMOVED
    existing = session.query(models.NodePlacement).filter(...).first()  # REMOVED

# After:
for node_name, agent_id in container_agent_map.items():
    node_def = nodes_by_container_name.get(node_name)
    existing = placements_by_node_name.get(node_name)
```

**Impact:** Eliminates 200 queries per cycle for 100-node lab. From O(2N) to O(2).

**Lines:** ~15

#### D.3: CycleContext for per-cycle caching (#15)
**Files:** `api/app/tasks/reconciliation.py`, `api/app/tasks/state_enforcement.py`

**What:** Create a lightweight context object that holds pre-loaded and indexed data for a single sync cycle.

**Implementation:**
```python
@dataclass
class ReconciliationContext:
    """Pre-loaded data for a single reconciliation cycle."""
    nodes_by_name: dict[str, models.Node]
    placements_by_node: dict[str, models.NodePlacement]
    hosts_by_id: dict[str, models.Host]
    agent_online_cache: dict[str, bool]  # agent_id → is_online
    active_jobs_by_lab: dict[str, list[models.Job]]

    @classmethod
    def load(cls, session, lab_id: str) -> "ReconciliationContext":
        nodes = session.query(models.Node).filter(models.Node.lab_id == lab_id).all()
        placements = session.query(models.NodePlacement).filter(models.NodePlacement.lab_id == lab_id).all()
        host_ids = {p.host_id for p in placements if p.host_id} | {n.host_id for n in nodes if n.host_id}
        hosts = {h.id: h for h in session.query(models.Host).filter(models.Host.id.in_(host_ids)).all()} if host_ids else {}

        return cls(
            nodes_by_name={n.container_name: n for n in nodes},
            placements_by_node={p.node_name: p for p in placements},
            hosts_by_id=hosts,
            agent_online_cache={},
            active_jobs_by_lab={},
        )
```

Then pass this context to helper functions instead of having them re-query.

**Impact:** Eliminates 50-100 redundant queries per cycle from repeated lookups.

**Lines:** ~50

#### D.4: Split reconciliation into read and write phases (#16)
**Files:** `api/app/tasks/reconciliation.py`

**What:** Refactor `_do_reconcile_lab()` to separate agent queries (no DB session needed) from state updates (DB session needed). This minimizes DB session lifetime from 30+ seconds to ~2-5 seconds.

**Implementation:**

```python
async def _do_reconcile_lab(lab_id: str):
    # Phase 1: READ (no DB session — just agent queries)
    agent_states = await _query_all_agents_for_lab(lab_id)  # asyncio.gather, 0-30s

    # Phase 2: WRITE (short DB session — compare and update)
    with get_session() as session:
        ctx = ReconciliationContext.load(session, lab_id)  # 3-5 batch queries
        changes = _compute_state_changes(ctx, agent_states)  # Pure computation
        _apply_state_changes(session, changes)  # Batch updates
        session.commit()  # Single commit

    # Phase 3: BROADCAST (no DB session — fire and forget)
    await _broadcast_changes(lab_id, changes)
```

**Benefits:**
- DB session held for ~2-5s instead of 30+s
- Agent network calls don't hold DB connections
- Clear separation of concerns (read vs compute vs write vs broadcast)
- Single commit point (atomic state update)

**Risk:** This is the largest refactor. The current code interleaves agent queries with DB reads. Separating them requires extracting agent query results into an intermediate data structure.

**Lines:** ~60-80 (mostly moving existing code into new functions)

---

## Dependency Graph

```
Phase A (Foundation) ──┐
                       ├──► Phase B (Tests) ──► Phase C (Architecture) ──► Phase D (Performance)
                       │
A.1 (DRY intervals)   │
A.2 (enforcement exc)  │    B.1 (tests for A.2)
A.3 (reconciliation)   │    B.2 (tests for A.3)
A.4 (cleanup interval) │    B.3 (reconciliation tests)
                       │    B.4 (cleanup handler tests)
                       │
                       │    C.1 (event-driven state) ◄── depends on A.1
                       │    C.2 (circuit breaker) ◄── tests in B.4
                       │    C.3 (auto-extract configs)
                       │
                       │    D.1 (batch enforcement)
                       │    D.2 (index reconciliation) ◄── can merge with D.3
                       │    D.3 (CycleContext)
                       │    D.4 (read/write split) ◄── depends on D.3
```

## Files Modified

| File | Phases | Changes |
|------|--------|---------|
| `api/app/config.py` | A.1, A.4, C.1 | get_interval(), cleanup_interval_extended |
| `api/app/tasks/state_enforcement.py` | A.2, C.3, D.1 | Exception handling, auto-extract, batch loading |
| `api/app/tasks/reconciliation.py` | A.3, D.2, D.3, D.4 | Skip failed nodes, indexing, CycleContext, read/write split |
| `api/app/tasks/disk_cleanup.py` | A.1 | Use get_interval() |
| `api/app/tasks/cleanup_handler.py` | C.1, C.2 | State triggers, circuit breaker, idempotency |
| `api/app/events/cleanup_events.py` | C.1 | New event type(s) for state triggers |
| `api/app/events/publisher.py` | C.1 | New emit functions for state triggers |
| `api/tests/test_state_enforcement.py` | B.1 | 4-5 new tests |
| `api/tests/test_tasks_reconciliation.py` | B.2, B.3 | 15-20 new tests |
| `api/tests/test_cleanup_handler.py` | B.4 | NEW FILE — 15 tests |

## Verification Checklist

After each phase:
- [ ] `python3 -c "import ast; ast.parse(open('file').read())"` for each modified Python file
- [ ] Existing tests still pass (pytest if available, otherwise syntax check)
- [ ] New tests pass
- [ ] No regressions in WebSocket state flow
- [ ] Check `git diff` for unintended changes

## Notes

- **Phase 6.4 (batch parallel agent reconcile)** is handled by a separate workflow — do NOT overlap
- **Phase 4 (frontend state management)** is COMPLETE — no frontend changes needed
- The NLM already broadcasts state directly (11 broadcast points) — event-driven state convergence is for edge cases (crashes, agent failures, out-of-band changes)
- All architecture decisions were reviewed with the greenfield context in mind — investing in correct patterns now is cheaper than retrofitting later
