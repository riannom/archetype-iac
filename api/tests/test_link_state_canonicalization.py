from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models, schemas
from app.routers.labs import _upsert_link_states


def test_upsert_link_states_deduplicates_vendor_interface_variants(
    test_db: Session,
    sample_lab: models.Lab,
    sample_host: models.Host,
) -> None:
    node1 = models.Node(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        gui_id="node-r1",
        display_name="R1",
        container_name="r1",
        device="ceos",
        host_id=sample_host.id,
    )
    node2 = models.Node(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        gui_id="node-r2",
        display_name="R2",
        container_name="r2",
        device="cisco_iosv",
        host_id=sample_host.id,
    )
    test_db.add_all([node1, node2])
    test_db.flush()

    # Existing duplicate rows for the same physical link.
    canonical = models.LinkState(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    legacy = models.LinkState(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_name="r1:Ethernet1-r2:GigabitEthernet0/1",
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r2",
        target_interface="GigabitEthernet0/1",
        desired_state="up",
        actual_state="up",
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    test_db.add_all([canonical, legacy])
    test_db.flush()

    graph = schemas.TopologyGraph(
        nodes=[
            schemas.GraphNode(id="node-r1", name="R1", container_name="r1", device="ceos"),
            schemas.GraphNode(id="node-r2", name="R2", container_name="r2", device="cisco_iosv"),
        ],
        links=[
            schemas.GraphLink(
                endpoints=[
                    schemas.GraphEndpoint(node="node-r1", ifname="Ethernet1"),
                    schemas.GraphEndpoint(node="node-r2", ifname="GigabitEthernet0/1"),
                ]
            )
        ],
    )

    created, updated, _, _ = _upsert_link_states(test_db, sample_lab.id, graph)
    test_db.commit()

    assert created == 0
    assert updated >= 1

    states = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id)
        .order_by(models.LinkState.created_at.asc())
        .all()
    )
    active = [s for s in states if s.desired_state != "deleted"]
    deleted = [s for s in states if s.desired_state == "deleted"]

    assert len(active) == 1
    assert len(deleted) == 1
    assert active[0].link_name == "r1:eth1-r2:eth1"
    assert active[0].source_interface == "eth1"
    assert active[0].target_interface == "eth1"


def test_links_reconcile_endpoint_uses_db_session_dependency(
    test_client: TestClient,
    auth_headers: dict,
    sample_lab: models.Lab,
) -> None:
    with patch("app.routers.labs.reconcile_lab_links", new=AsyncMock(return_value={
        "checked": 1,
        "valid": 1,
        "repaired": 0,
        "recovered": 0,
        "errors": 0,
        "skipped": 0,
    })):
        response = test_client.post(
            f"/labs/{sample_lab.id}/links/reconcile",
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["checked"] == 1
    assert data["errors"] == 0
