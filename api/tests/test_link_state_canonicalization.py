from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
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
    # For cisco_iosv: GigabitEthernet0/1 → port_start_index=0 → eth{1-0+1} = eth2
    canonical = models.LinkState(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth2",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth2",
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
    # Duplicate was hard-deleted, so only 1 row remains
    assert len(states) == 1
    assert states[0].link_name == "r1:eth1-r2:eth2"
    assert states[0].source_interface == "eth1"
    assert states[0].target_interface == "eth2"
    assert states[0].link_definition_id is not None

    link_defs = (
        test_db.query(models.Link)
        .filter(models.Link.lab_id == sample_lab.id)
        .all()
    )
    assert len(link_defs) == 1
    assert link_defs[0].id == states[0].link_definition_id


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


def test_upsert_link_states_recovers_when_concurrent_insert_wins(
    test_db: Session,
    test_engine,
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
        device="ceos",
        host_id=sample_host.id,
    )
    test_db.add_all([node1, node2])
    test_db.commit()

    graph = schemas.TopologyGraph(
        nodes=[
            schemas.GraphNode(id="node-r1", name="R1", container_name="r1", device="ceos"),
            schemas.GraphNode(id="node-r2", name="R2", container_name="r2", device="ceos"),
        ],
        links=[
            schemas.GraphLink(
                endpoints=[
                    schemas.GraphEndpoint(node="node-r1", ifname="eth1"),
                    schemas.GraphEndpoint(node="node-r2", ifname="eth1"),
                ]
            )
        ],
    )

    original_get_or_create = _upsert_link_states.__globals__["_get_or_create_link_definition"]
    injected = {"done": False}

    def _inject_concurrent_insert(*args, **kwargs):
        link_def = original_get_or_create(*args, **kwargs)
        if not injected["done"]:
            injected["done"] = True
            competing_session = sessionmaker(
                bind=test_engine, autoflush=False, autocommit=False
            )()
            try:
                competing_session.add(
                    models.LinkState(
                        id=str(uuid4()),
                        lab_id=sample_lab.id,
                        link_definition_id=link_def.id,
                        link_name="r1:eth1-r2:eth1",
                        source_node="r1",
                        source_interface="eth1",
                        target_node="r2",
                        target_interface="eth1",
                        source_host_id=sample_host.id,
                        target_host_id=sample_host.id,
                        desired_state="up",
                        actual_state="unknown",
                    )
                )
                competing_session.commit()
            finally:
                competing_session.close()
        return link_def

    with patch.dict(
        _upsert_link_states.__globals__,
        {"_get_or_create_link_definition": _inject_concurrent_insert},
    ):
        created, updated, _, _ = _upsert_link_states(test_db, sample_lab.id, graph)
        test_db.commit()

    assert created == 0
    assert updated >= 1

    states = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id)
        .all()
    )
    assert len(states) == 1
    assert states[0].link_name == "r1:eth1-r2:eth1"
    assert states[0].link_definition_id is not None
