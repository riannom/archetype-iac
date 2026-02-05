from __future__ import annotations

from contextlib import contextmanager
import json

import pytest

from app import models
import app.tasks.image_reconciliation as image_reconciliation


@contextmanager
def _session_ctx(test_db):
    yield test_db


@pytest.mark.asyncio
async def test_reconcile_image_hosts_creates_and_removes(test_db, sample_lab) -> None:
    host_a = models.Host(
        id="host-a",
        name="Host A",
        address="localhost:1",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    host_b = models.Host(
        id="host-b",
        name="Host B",
        address="localhost:2",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    test_db.add_all([host_a, host_b])

    existing = models.ImageHost(
        image_id="img-1",
        host_id=host_a.id,
        reference="repo:1",
        status="synced",
    )
    orphan = models.ImageHost(
        image_id="img-orphan",
        host_id=host_a.id,
        reference="old:1",
        status="unknown",
    )
    test_db.add_all([existing, orphan])
    test_db.commit()

    manifest = {
        "images": [
            {"id": "img-1", "reference": "repo:1", "kind": "docker"},
        ]
    }

    image_reconciliation.load_manifest = lambda: manifest
    image_reconciliation.get_session = lambda: _session_ctx(test_db)

    result = await image_reconciliation.reconcile_image_hosts()

    assert result.orphaned_hosts_removed == 1
    assert result.missing_hosts_created == 1

    hosts = test_db.query(models.ImageHost).filter(models.ImageHost.image_id == "img-1").all()
    assert {h.host_id for h in hosts} == {host_a.id, host_b.id}


@pytest.mark.asyncio
async def test_verify_image_status_on_agents_updates(test_db) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["docker", "libvirt"]}),
        version="1.0.0",
    )
    test_db.add(host)

    docker_record = models.ImageHost(
        image_id="docker-img",
        host_id=host.id,
        reference="repo:tag",
        status="missing",
    )
    qcow2_record = models.ImageHost(
        image_id="qcow2-img",
        host_id=host.id,
        reference="disk.qcow2",
        status="missing",
    )
    iol_record = models.ImageHost(
        image_id="iol-img",
        host_id=host.id,
        reference="iol:1",
        status="missing",
    )
    test_db.add_all([docker_record, qcow2_record, iol_record])
    test_db.commit()

    manifest = {
        "images": [
            {"id": "docker-img", "reference": "repo:tag", "kind": "docker"},
            {"id": "qcow2-img", "reference": "disk.qcow2", "kind": "qcow2"},
            {"id": "iol-img", "reference": "iol:1", "kind": "iol"},
        ]
    }

    async def fake_get_agent_images(agent):
        return {"images": [{"tags": ["repo:tag"]}]}

    image_reconciliation.load_manifest = lambda: manifest
    image_reconciliation.get_session = lambda: _session_ctx(test_db)
    image_reconciliation.agent_client.is_agent_online = lambda h: True
    image_reconciliation.agent_client.get_agent_images = fake_get_agent_images

    result = await image_reconciliation.verify_image_status_on_agents()

    assert result.status_updates >= 3

    refreshed = test_db.query(models.ImageHost).filter(models.ImageHost.host_id == host.id).all()
    status_by_id = {r.image_id: r.status for r in refreshed}
    assert status_by_id["docker-img"] == "synced"
    assert status_by_id["qcow2-img"] == "synced"
    assert status_by_id["iol-img"] == "synced"
