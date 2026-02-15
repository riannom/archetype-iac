# API-Owned Network State Convergence

## Phase 0: Pre-work (Refactor + Cleanup)
- [x] 0.1 Split `link_reconciliation.py` into `link_repair.py` + `link_cleanup.py`
- [x] 0.2 Remove dead `VniAllocator` from agent (~303 lines)
- [x] 0.3 Add `partial_state` tracking to `setup_cross_host_link_v2`

## Phase 1: VxlanTunnel port_name
- [x] 1.1 Migration 050: add `port_name` column with backfill
- [x] 1.2 Model: `port_name: Mapped[str | None]` + `link_state` relationship
- [x] 1.3 Populate on link creation (3 sites in link_orchestration + link_manager)

## Phase 2: Declare-state for VXLAN Tunnels (Core Convergence)
- [x] 2.1 Agent schemas: `DeclaredTunnel`, `DeclareOverlayStateRequest/Response`
- [x] 2.2 Agent `OverlayManager.declare_state()` — batch OVS read, per-tunnel converge, orphan cleanup
- [x] 2.3 Agent local cache: `_write_declared_state_cache()`, `load_declared_state_cache()`
- [x] 2.4 Agent endpoint: `POST /overlay/declare-state`
- [x] 2.5 API client: `declare_overlay_state_on_agent()` with 404 fallback
- [x] 2.6 API integration: `run_overlay_convergence()` replaces whitelist in monitor loop
- [x] 2.7 Tests: 15 API-side (`test_overlay_convergence.py`), 12 agent-side (`test_declare_overlay_state.py`)

## Phase 3: InterfaceMapping Freshness + Same-Host Convergence
- [x] 3.1 Migration 051: add `last_verified_at` to `interface_mappings`
- [x] 3.2 Model: `last_verified_at: Mapped[datetime | None]`
- [x] 3.3 Agent endpoint: `GET /labs/{lab_id}/port-state`
- [x] 3.4 Agent endpoint: `POST /ports/declare-state`
- [x] 3.5 API client: `get_lab_port_state()`, `declare_port_state_on_agent()`
- [x] 3.6 API integration: `refresh_interface_mappings()`, `run_same_host_convergence()`
- [x] 3.7 Monitor loop: mapping refresh + same-host convergence on 5th cycle
- [x] 3.8 Tests: 9 API-side (`test_port_convergence.py`), 7 agent-side (`test_port_state.py`)

## File Change Summary

### New Files (10)
| File | Lines | Purpose |
|------|-------|---------|
| `api/app/tasks/link_repair.py` | ~290 | Extracted repair functions |
| `api/app/tasks/link_cleanup.py` | ~280 | Extracted cleanup functions |
| `api/alembic/versions/050_*.py` | 63 | VxlanTunnel port_name migration |
| `api/alembic/versions/051_*.py` | 32 | InterfaceMapping last_verified_at migration |
| `api/tests/test_overlay_convergence.py` | ~430 | Overlay convergence tests (15) |
| `api/tests/test_port_convergence.py` | ~340 | Same-host convergence tests (9) |
| `agent/tests/test_declare_overlay_state.py` | ~340 | Agent declare-state tests (12) |
| `agent/tests/test_port_state.py` | ~250 | Agent port-state tests (7) |

### Modified Files (8)
| File | Changes |
|------|---------|
| `api/app/tasks/link_reconciliation.py` | Shrunk, added run_overlay_convergence, refresh_interface_mappings, run_same_host_convergence |
| `api/app/models.py` | VxlanTunnel.port_name, VxlanTunnel.link_state relationship, InterfaceMapping.last_verified_at |
| `api/app/agent_client.py` | declare_overlay_state_on_agent, get_lab_port_state, declare_port_state_on_agent, partial_state |
| `api/app/tasks/link_orchestration.py` | port_name on VxlanTunnel creation, PARTIAL_STATE prefix |
| `api/app/services/link_manager.py` | port_name on VxlanTunnel creation |
| `agent/network/overlay.py` | Removed VniAllocator (~303 lines), added declare_state + cache |
| `agent/main.py` | POST /overlay/declare-state, GET /labs/{lab_id}/port-state, POST /ports/declare-state |
| `agent/schemas.py` | 8 new schema classes for convergence |

### Deleted Files (2)
| File | Reason |
|------|--------|
| `agent/tests/test_vni_allocator.py` | VniAllocator removed |
| `agent/tests/test_vni_allocator_recovery.py` | VniAllocator removed |

## Review
All 4 phases implemented. 43 new tests across 4 test files. Key architecture:
- **Convergence pattern**: API declares full desired state → agent converges (create/update/delete)
- **Local cache**: Agent writes declared state to JSON for API-less recovery on restart
- **404 fallback**: Old agents without declare-state endpoint fall back to whitelist reconciliation
- **In-progress protection**: Creating/connecting links are included in declared set to prevent orphan cleanup
- **Lab-scoped orphans**: Only ports from declared labs are subject to orphan cleanup
- **Same schedule**: All convergence runs on 5th cycle (~5 min) alongside existing reconciliation
