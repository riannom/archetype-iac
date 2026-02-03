# Link State Management Improvements Plan

## Overview

This plan addresses link state synchronization issues discovered during the VTEP refactor:
- Links marked "up" without actual VLAN tag matching
- Duplicate link entries with different naming conventions
- No reconciliation for link state
- Missing interface name mapping between OVS/Linux and vendor nomenclature

## Components

### 1. Interface Mapping Database

**Purpose:** Extensible mapping between OVS ports, Linux interfaces, and vendor-specific names.

**Schema:**
```sql
CREATE TABLE interface_mappings (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid(),
    lab_id VARCHAR(36) NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    node_id VARCHAR(36) NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,

    -- OVS layer
    ovs_port VARCHAR(20),              -- "vh614ed63ed40"
    ovs_bridge VARCHAR(50),            -- "arch-ovs" or "ovs-{lab_id}"
    vlan_tag INT,

    -- Linux layer
    linux_interface VARCHAR(20),       -- "eth1"

    -- Vendor layer
    vendor_interface VARCHAR(50),      -- "Ethernet1", "ge-0/0/0", "GigabitEthernet0/0"
    device_type VARCHAR(50),           -- "arista_ceos", "juniper_vmx"

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(lab_id, node_id, linux_interface)
);

CREATE INDEX ix_interface_mappings_lab ON interface_mappings(lab_id);
CREATE INDEX ix_interface_mappings_node ON interface_mappings(node_id);
CREATE INDEX ix_interface_mappings_ovs_port ON interface_mappings(ovs_port);
```

**Files to modify:**
- `api/app/models.py` - Add InterfaceMapping model
- `api/alembic/versions/` - Migration script
- `agent/network/docker_plugin.py` - Report mappings when creating endpoints
- `api/app/services/topology.py` - Use mappings for translation

**API endpoints:**
- `GET /labs/{lab_id}/interface-mappings` - List all mappings for a lab
- `GET /labs/{lab_id}/nodes/{node}/interfaces` - List interfaces for a node with both names

---

### 2. Link State Validation

**Purpose:** Only mark links as "up" after verifying OVS state matches.

**Implementation:**

```python
# api/app/services/link_validator.py

async def verify_link_connected(
    session: Session,
    link_state: LinkState,
    agents: dict[str, Host],
) -> tuple[bool, str | None]:
    """Verify a link is actually connected by checking OVS VLAN tags.

    Returns:
        (is_valid, error_message)
    """
    # Get interface mappings for both endpoints
    source_mapping = get_interface_mapping(
        session, link_state.lab_id,
        link_state.source_node, link_state.source_interface
    )
    target_mapping = get_interface_mapping(
        session, link_state.lab_id,
        link_state.target_node, link_state.target_interface
    )

    if not source_mapping or not target_mapping:
        return False, "Interface mapping not found"

    # Query actual VLAN tags from agents
    source_agent = agents.get(link_state.source_host_id)
    target_agent = agents.get(link_state.target_host_id)

    source_vlan = await get_port_vlan(source_agent, source_mapping.ovs_port)
    target_vlan = await get_port_vlan(target_agent, target_mapping.ovs_port)

    if source_vlan is None or target_vlan is None:
        return False, f"Could not read VLAN tags (source={source_vlan}, target={target_vlan})"

    if source_vlan != target_vlan:
        return False, f"VLAN mismatch: source={source_vlan}, target={target_vlan}"

    return True, None
```

**Integration points:**
- `api/app/tasks/link_orchestration.py` - Call after hot_connect/VTEP attach
- `api/app/services/link_manager.py` - Call before setting actual_state="up"

**Agent endpoint:**
```python
# agent/main.py
@app.get("/ovs/port/{port_name}/vlan")
async def get_port_vlan(port_name: str) -> dict:
    """Get VLAN tag for an OVS port."""
    code, stdout, _ = await run_cmd(["ovs-vsctl", "get", "port", port_name, "tag"])
    if code != 0:
        return {"vlan_tag": None, "error": "Port not found"}
    tag = stdout.strip()
    return {"vlan_tag": int(tag) if tag and tag != "[]" else None}
```

---

### 3. Link Reconciliation Task

**Purpose:** Periodically verify link state matches actual OVS configuration.

**Implementation:**

```python
# api/app/tasks/link_reconciliation.py

async def reconcile_link_states(session: Session) -> dict:
    """Reconcile link_states with actual OVS configuration.

    For each link marked as "up":
    1. Query VLAN tags from both agents
    2. Compare with stored vlan_tag
    3. If mismatch: attempt repair or mark as error
    """
    results = {
        "checked": 0,
        "valid": 0,
        "repaired": 0,
        "errors": 0,
    }

    # Get all links marked as "up"
    up_links = session.query(LinkState).filter(
        LinkState.actual_state == "up"
    ).all()

    for link in up_links:
        results["checked"] += 1

        is_valid, error = await verify_link_connected(session, link, agents)

        if is_valid:
            results["valid"] += 1
        else:
            # Attempt repair
            repaired = await attempt_link_repair(session, link, agents)
            if repaired:
                results["repaired"] += 1
            else:
                link.actual_state = "error"
                link.error_message = error
                results["errors"] += 1

    session.commit()
    return results


async def attempt_link_repair(
    session: Session,
    link: LinkState,
    agents: dict[str, Host],
) -> bool:
    """Try to repair a broken link by re-calling hot_connect or VTEP attach."""
    if link.is_cross_host:
        # Re-attach with correct VLAN
        result = await setup_cross_host_link_v2(...)
        return result.get("success", False)
    else:
        # Re-call hot_connect
        result = await create_link_on_agent(...)
        return result.get("success", False)
```

**Scheduling:**
- Add to `api/app/tasks/__init__.py` background tasks
- Run every 60 seconds (configurable)
- Integrate with existing reconciliation framework

---

### 4. VTEP Reference Counting

**Purpose:** Track which links use each VTEP so we know when to delete.

**Agent-side tracking:**

```python
# agent/network/overlay.py

@dataclass
class Vtep:
    # ... existing fields ...
    links: set[str] = field(default_factory=set)  # link_ids using this VTEP

class OverlayManager:
    def __init__(self):
        # ... existing ...
        self._vtep_links: dict[str, set[str]] = {}  # remote_ip -> set of link_ids

    async def attach_overlay_interface(
        self,
        lab_id: str,
        link_id: str,  # NEW: track which link this is for
        container_name: str,
        interface_name: str,
        vlan_tag: int,
        remote_ip: str,  # NEW: which VTEP this link uses
    ) -> bool:
        # ... existing logic ...

        # Track link -> VTEP association
        if remote_ip not in self._vtep_links:
            self._vtep_links[remote_ip] = set()
        self._vtep_links[remote_ip].add(link_id)

    async def detach_overlay_interface(
        self,
        link_id: str,
        remote_ip: str,
    ) -> bool:
        """Detach a link and potentially delete VTEP if no more links."""
        if remote_ip in self._vtep_links:
            self._vtep_links[remote_ip].discard(link_id)

            # Delete VTEP if no more links use it
            if not self._vtep_links[remote_ip]:
                await self.delete_vtep(remote_ip)
                del self._vtep_links[remote_ip]
```

**Database tracking (optional, for persistence across agent restarts):**

```sql
ALTER TABLE vxlan_tunnels ADD COLUMN vtep_interface VARCHAR(20);
-- Query: SELECT vtep_interface, COUNT(*) FROM vxlan_tunnels GROUP BY vtep_interface
```

---

### 5. Atomic Link Creation

**Purpose:** Ensure link creation either fully succeeds or fully rolls back.

**Implementation:**

```python
# api/app/tasks/link_orchestration.py

async def create_same_host_link(
    session: Session,
    lab_id: str,
    link_state: LinkState,
    host_to_agent: dict[str, Host],
    log_parts: list[str],
) -> bool:
    """Create same-host link with atomic semantics."""

    # Don't set actual_state until we verify
    link_state.actual_state = "creating"
    session.flush()

    try:
        # Call hot_connect
        result = await agent_client.create_link_on_agent(...)

        if not result.get("success"):
            link_state.actual_state = "error"
            link_state.error_message = result.get("error")
            return False

        # Verify the connection actually worked
        vlan_tag = result.get("vlan_tag")
        is_valid, error = await verify_link_connected(session, link_state, host_to_agent)

        if not is_valid:
            link_state.actual_state = "error"
            link_state.error_message = f"Verification failed: {error}"
            return False

        # Only now mark as up
        link_state.vlan_tag = vlan_tag
        link_state.actual_state = "up"
        link_state.error_message = None
        return True

    except Exception as e:
        link_state.actual_state = "error"
        link_state.error_message = str(e)
        return False
```

**State machine:**
```
pending -> creating -> up
                   \-> error
```

---

### 6. Cleanup Duplicate Links

**Purpose:** Remove duplicate link entries with different naming conventions.

**Migration script:**

```python
# api/alembic/versions/xxx_cleanup_duplicate_links.py

def upgrade():
    """Deduplicate link_states with Ethernet vs eth naming."""

    # Find duplicates: same endpoints but different naming
    # e.g., "eos_1:Ethernet1-eos_2:Ethernet1" vs "eos_1:eth1-eos_2:eth1"

    conn = op.get_bind()

    # Get all link_states
    result = conn.execute(text("""
        SELECT id, lab_id, link_name, source_node, source_interface,
               target_node, target_interface, actual_state, vlan_tag
        FROM link_states
        ORDER BY created_at
    """))

    links = result.fetchall()
    seen = {}  # normalized_key -> first link id
    duplicates = []

    for link in links:
        # Normalize interface names
        source_if = normalize_interface(link.source_interface)
        target_if = normalize_interface(link.target_interface)

        # Create canonical key (sorted to handle direction)
        endpoints = sorted([
            (link.source_node, source_if),
            (link.target_node, target_if)
        ])
        key = f"{link.lab_id}:{endpoints[0][0]}:{endpoints[0][1]}-{endpoints[1][0]}:{endpoints[1][1]}"

        if key in seen:
            duplicates.append(link.id)
        else:
            seen[key] = link.id

    # Delete duplicates (keep oldest)
    if duplicates:
        conn.execute(text(
            "DELETE FROM link_states WHERE id = ANY(:ids)"
        ), {"ids": duplicates})

    print(f"Removed {len(duplicates)} duplicate link_states")


def normalize_interface(name: str) -> str:
    """Ethernet1 -> eth1, GigabitEthernet0 -> eth0"""
    import re
    match = re.search(r'\d+', name)
    if match:
        return f"eth{match.group()}"
    return name
```

---

## Implementation Order

1. **Interface Mapping Database** (foundation for everything else) ✅ DONE
   - Database migration (`027_add_interface_mappings.py`)
   - Model (`InterfaceMapping` in models.py)
   - API endpoints (`/labs/{lab_id}/interface-mappings`, `/labs/{lab_id}/nodes/{node_id}/interfaces`)
   - Sync endpoint (`/labs/{lab_id}/interface-mappings/sync`)
   - Service (`api/app/services/interface_mapping.py`)
   - Agent support (`bridge_name` field added to PluginPortInfo)
   - Agent client functions (`get_lab_ports_from_agent`, `get_interface_vlan_from_agent`)

2. **Atomic Link Creation** (prevents new bad state) ✅ DONE
   - Added "creating" transitional state to LinkState model
   - Updated `create_same_host_link()` with atomic semantics
   - Updated `create_cross_host_link()` with atomic semantics
   - Created link validation service (`api/app/services/link_validator.py`)
   - Links only marked "up" after VLAN tag verification passes
   - Interface mappings updated after successful link creation

3. **Link State Validation** (enables verification) ✅ DONE (merged into Phase 2)
   - Agent endpoint already exists: `/labs/{lab_id}/interfaces/{node}/{interface}/vlan`
   - Added `get_interface_vlan_from_agent()` in agent_client.py
   - Created `verify_link_connected()`, `verify_same_host_link()`, `verify_cross_host_link()` in link_validator.py

4. **Cleanup Duplicate Links** (fix existing bad data) ✅ DONE
   - Migration script (`028_cleanup_duplicate_links.py`)
   - Normalizes interface names (Ethernet1 -> eth1) to find duplicates
   - Keeps oldest entry, deletes duplicates

5. **Link Reconciliation Task** (ongoing health) ✅ DONE
   - Background task (`api/app/tasks/link_reconciliation.py`)
   - `link_reconciliation_monitor()` runs every 60 seconds
   - `reconcile_lab_links()` for on-demand reconciliation
   - API endpoint: `POST /labs/{lab_id}/links/reconcile`
   - Verifies VLAN tags match, attempts repair if mismatch

6. **VTEP Reference Counting** (cleanup optimization) ✅ DONE
   - Added `links: set[str]` to `Vtep` dataclass for tracking
   - `attach_overlay_interface()` now tracks link -> VTEP associations
   - New `detach_overlay_interface()` method removes links and deletes unused VTEPs
   - New `delete_vtep()` method for explicit VTEP cleanup
   - Agent API: `POST /overlay/detach-link` for controller to trigger detach
   - API client: `detach_overlay_interface_on_agent()` function
   - `get_tunnel_status()` now includes link_count and links list for VTEPs

---

## Testing Plan

1. **Unit tests:**
   - Interface mapping CRUD
   - Link validation logic
   - Duplicate detection

2. **Integration tests:**
   - Deploy lab, verify mappings populated
   - Create link, verify VLAN match
   - Kill link, verify reconciliation detects

3. **Manual tests:**
   - Stop/Deploy All with cross-host nodes
   - Verify all links work (ping tests)
   - Manually break VLAN tag, verify reconciliation repairs

---

## Estimated Effort

| Component | Files | Complexity |
|-----------|-------|------------|
| Interface Mapping DB | 4-5 | Medium |
| Link State Validation | 2-3 | Low |
| Atomic Link Creation | 2 | Low |
| Duplicate Cleanup | 1 | Low |
| Link Reconciliation | 2-3 | Medium |
| VTEP Reference Counting | 2 | Medium |

Total: ~15 files, 2-3 days of focused work
