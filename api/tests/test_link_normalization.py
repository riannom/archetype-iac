"""Tests for link normalization/backfill."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app import models
from app.services.topology import TopologyService
from app.utils.link import generate_link_name


def test_normalize_links_for_lab(test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
    """Backfill should normalize interfaces and link names."""
    node1 = models.Node(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        gui_id="n1",
        display_name="R1",
        container_name="r1",
        device="linux",
        host_id=sample_host.id,
    )
    node2 = models.Node(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        gui_id="n2",
        display_name="R2",
        container_name="r2",
        device="linux",
        host_id=sample_host.id,
    )
    test_db.add_all([node1, node2])
    test_db.flush()

    link = models.Link(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_name="r1:Ethernet1-r2:Ethernet1",
        source_node_id=node1.id,
        source_interface="Ethernet1",
        target_node_id=node2.id,
        target_interface="Ethernet1",
    )
    test_db.add(link)
    test_db.flush()

    link_state = models.LinkState(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_definition_id=link.id,
        link_name=link.link_name,
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r2",
        target_interface="Ethernet1",
        actual_state="down",
    )
    test_db.add(link_state)
    test_db.commit()

    topo_service = TopologyService(test_db)
    updated = topo_service.normalize_links_for_lab(sample_lab.id)

    assert updated > 0

    test_db.refresh(link)
    test_db.refresh(link_state)

    assert link.source_interface == "eth1"
    assert link.target_interface == "eth1"
    assert link.link_name == generate_link_name("r1", "eth1", "r2", "eth1")

    assert link_state.source_interface == "eth1"
    assert link_state.target_interface == "eth1"
    assert link_state.link_name == link.link_name
