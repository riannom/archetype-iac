from __future__ import annotations

import pytest

from app import models
import app.tasks.live_links as live_links


@pytest.mark.asyncio
async def test_create_link_if_ready_marks_pending(test_db, sample_lab) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        actual_state="unknown",
    )
    test_db.add(link_state)

    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="stopped",
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="stopped",
        )
    )
    test_db.commit()

    ok = await live_links.create_link_if_ready(
        test_db,
        sample_lab.id,
        link_state,
        host_to_agent={},
    )
    assert not ok

    updated = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id)
        .first()
    )
    assert updated.actual_state == "pending"


@pytest.mark.asyncio
async def test_create_link_if_ready_missing_hosts(test_db, sample_lab, monkeypatch) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        actual_state="unknown",
    )
    test_db.add(link_state)

    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="running",
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="running",
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        "app.tasks.live_links.lookup_endpoint_hosts",
        lambda session, link_state: (None, None),
    )

    ok = await live_links.create_link_if_ready(
        test_db,
        sample_lab.id,
        link_state,
        host_to_agent={},
    )
    assert not ok

    updated = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id)
        .first()
    )
    assert updated.actual_state == "error"
    assert "host placement" in (updated.error_message or "")


@pytest.mark.asyncio
async def test_create_link_if_ready_same_host_success(test_db, sample_lab, sample_host, monkeypatch) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        actual_state="unknown",
    )
    test_db.add(link_state)

    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="running",
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="running",
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        "app.tasks.live_links.lookup_endpoint_hosts",
        lambda session, link_state: (sample_host.id, sample_host.id),
    )

    async def fake_same_host(*args, **kwargs):
        return True

    monkeypatch.setattr("app.tasks.live_links.create_same_host_link", fake_same_host)

    ok = await live_links.create_link_if_ready(
        test_db,
        sample_lab.id,
        link_state,
        host_to_agent={sample_host.id: sample_host},
    )
    assert ok

    updated = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id)
        .first()
    )
    assert not updated.is_cross_host
    assert updated.source_host_id == sample_host.id
    assert updated.target_host_id == sample_host.id
