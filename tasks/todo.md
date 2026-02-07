# Close Orphan Cleanup Gaps

## Changes

### Gap 1: Misplaced containers — remove from wrong agent
**File:** `api/app/tasks/reconciliation.py` — `_do_reconcile_lab()`

- [x] Initialize `misplaced_containers: dict[str, str]` before placement loop
- [x] Populate dict when misplaced container detected (node on wrong agent per host_id)
- [x] After placement loop, destroy each misplaced container on the wrong agent
- [x] Guard cleanup with `not check_active_job` to avoid interfering with deploys

### Gap 2: Lab-less containers — periodic global cleanup
**File:** `api/app/tasks/reconciliation.py` — `_maybe_cleanup_labless_containers()`

- [x] Add `_maybe_cleanup_labless_containers()` function with counter-based throttle
- [x] Runs every 10th reconciliation cycle (~5 min at 30s interval)
- [x] Queries all valid lab IDs from DB, sends to each online agent
- [x] Agents remove containers belonging to labs not in the valid list
- [x] Wire into `refresh_states_from_agents()` after per-lab reconciliation loop

### Gap 3: Topology delete cleanup (no change needed)
- [x] Already handled by `process_node_changes()` + reconciliation fallback

## Verification

- [x] Python syntax check passes
- [ ] Rebuild: `docker compose -f docker-compose.gui.yml up -d --build api`
- [ ] Monitor logs for "Removed misplaced container" messages
- [ ] Monitor logs for "Removed N lab-less container(s)" messages
