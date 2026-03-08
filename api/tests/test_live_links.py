from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock

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
            is_ready=False,
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="stopped",
            is_ready=False,
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
async def test_create_link_if_ready_marks_pending_when_nodes_running_but_unready(
    test_db, sample_lab
) -> None:
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
            is_ready=False,
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="running",
            is_ready=True,
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
    assert updated.error_message is None


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
            is_ready=True,
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="running",
            is_ready=True,
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
            is_ready=True,
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="running",
            is_ready=True,
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


@pytest.mark.asyncio
async def test_create_link_if_ready_rejects_endpoint_conflict(
    test_db,
    sample_lab,
    sample_host,
    monkeypatch,
) -> None:
    existing = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:Ethernet1-r3:eth1",
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r3",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
    )
    requested = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="up",
        actual_state="unknown",
    )
    test_db.add(existing)
    test_db.add(requested)

    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="running",
            is_ready=True,
        )
    )
    test_db.add(
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="r2",
            desired_state="running",
            actual_state="running",
            is_ready=True,
        )
    )
    test_db.commit()

    called = {"same_host": False}

    monkeypatch.setattr(
        "app.tasks.live_links.lookup_endpoint_hosts",
        lambda session, link_state: (sample_host.id, sample_host.id),
    )

    async def fake_same_host(*args, **kwargs):
        called["same_host"] = True
        return True

    monkeypatch.setattr("app.tasks.live_links.create_same_host_link", fake_same_host)

    ok = await live_links.create_link_if_ready(
        test_db,
        sample_lab.id,
        requested,
        host_to_agent={sample_host.id: sample_host},
    )

    assert not ok
    assert called["same_host"] is False

    updated = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.id == requested.id)
        .first()
    )
    assert updated is not None
    assert updated.actual_state == "error"
    assert "Endpoint already in use" in (updated.error_message or "")
    assert existing.link_name in (updated.error_message or "")


@pytest.mark.asyncio
async def test_teardown_link_updates_persisted_state_same_host(
    test_db,
    sample_lab,
    sample_host,
    monkeypatch,
) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
        source_host_id=sample_host.id,
        target_host_id=sample_host.id,
        is_cross_host=False,
        source_carrier_state="on",
        target_carrier_state="on",
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

    async def fake_delete_link(*args, **kwargs):
        return {"success": True}

    monkeypatch.setattr("app.tasks.live_links.agent_client.delete_link_on_agent", fake_delete_link)

    ok = await live_links.teardown_link(
        test_db,
        sample_lab.id,
        {
            "link_name": link_state.link_name,
            "source_node": "r1",
            "source_interface": "eth1",
            "target_node": "r2",
            "target_interface": "eth1",
            "is_cross_host": False,
            "actual_state": "up",
            "source_host_id": sample_host.id,
            "target_host_id": sample_host.id,
            "vni": None,
        },
        host_to_agent={sample_host.id: sample_host},
    )
    assert ok

    updated = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id, models.LinkState.link_name == link_state.link_name)
        .first()
    )
    assert updated is not None
    assert updated.actual_state == "down"
    assert updated.source_carrier_state == "off"
    assert updated.target_carrier_state == "off"
    assert updated.source_oper_reason == "local_interface_down"
    assert updated.target_oper_reason == "local_interface_down"
    assert updated.oper_epoch >= 1


@pytest.mark.asyncio
async def test_teardown_link_idempotent_when_already_down(test_db, sample_lab) -> None:
    ok = await live_links.teardown_link(
        test_db,
        sample_lab.id,
        {
            "link_name": "r1:eth1-r2:eth1",
            "actual_state": "down",
            "is_cross_host": False,
        },
        host_to_agent={},
    )
    assert ok is True


@pytest.mark.asyncio
async def test_teardown_link_defers_when_same_host_agent_unavailable(
    test_db,
    sample_lab,
) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="deleted",
        actual_state="up",
        source_host_id="offline-host",
        target_host_id="offline-host",
        is_cross_host=False,
    )
    test_db.add(link_state)
    test_db.commit()

    ok = await live_links.teardown_link(
        test_db,
        sample_lab.id,
        {
            "link_name": link_state.link_name,
            "actual_state": "up",
            "is_cross_host": False,
            "source_host_id": "offline-host",
            "target_host_id": "offline-host",
        },
        host_to_agent={},
    )
    assert ok is False

    updated = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id, models.LinkState.link_name == link_state.link_name)
        .first()
    )
    assert updated is not None
    assert updated.actual_state == "error"
    assert "Teardown deferred" in (updated.error_message or "")


@pytest.mark.asyncio
async def test_process_link_changes_keeps_linkstate_when_teardown_incomplete(
    test_db,
    sample_lab,
    sample_host,
    monkeypatch,
) -> None:
    link_name = "r1:eth1-r2:eth1"
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name=link_name,
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="deleted",
        actual_state="up",
        source_host_id="offline-host",
        target_host_id="offline-host",
        is_cross_host=False,
    )
    test_db.add(link_state)
    test_db.commit()

    @contextmanager
    def _test_session():
        yield test_db

    @contextmanager
    def _acquired_lock(_lab_id: str):
        yield True

    async def _fake_build_host_to_agent_map(*_args, **_kwargs):
        return {sample_host.id: sample_host}

    monkeypatch.setattr("app.tasks.live_links.get_session", _test_session)
    monkeypatch.setattr("app.tasks.live_links.link_ops_lock", _acquired_lock)
    monkeypatch.setattr(
        "app.tasks.live_links._build_host_to_agent_map",
        _fake_build_host_to_agent_map,
    )
    monkeypatch.setattr(
        "app.tasks.live_links.teardown_link",
        AsyncMock(return_value=False),
    )

    await live_links.process_link_changes(
        sample_lab.id,
        added_link_names=[],
        removed_link_info=[
            {
                "link_name": link_name,
                "source_node": "r1",
                "source_interface": "eth1",
                "target_node": "r2",
                "target_interface": "eth1",
                "is_cross_host": False,
                "actual_state": "up",
                "source_host_id": "offline-host",
                "target_host_id": "offline-host",
                "vni": None,
            }
        ],
        user_id=None,
    )

    remaining = (
        test_db.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == sample_lab.id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    assert remaining is not None
    assert remaining.desired_state == "deleted"

    job = (
        test_db.query(models.Job)
        .filter(models.Job.lab_id == sample_lab.id)
        .order_by(models.Job.created_at.desc())
        .first()
    )
    assert job is not None
    assert job.status == "completed_with_warnings"
    assert "Completed with 1 error(s)" in (job.log_path or "")
