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
async def test_reconcile_image_hosts_creates_and_removes(test_db, sample_lab, monkeypatch) -> None:
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

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)

    async def fake_delete_image_on_agent(host, reference):
        return {"success": True, "deleted": True}

    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "delete_image_on_agent",
        fake_delete_image_on_agent,
    )

    result = await image_reconciliation.reconcile_image_hosts()

    assert result.orphaned_hosts_removed == 1
    assert result.missing_hosts_created == 1

    hosts = test_db.query(models.ImageHost).filter(models.ImageHost.image_id == "img-1").all()
    assert {h.host_id for h in hosts} == {host_a.id, host_b.id}


@pytest.mark.asyncio
async def test_verify_image_status_on_agents_updates(test_db, monkeypatch) -> None:
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

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "get_agent_images",
        fake_get_agent_images,
    )
    async def fake_check_agent_has_image(host, reference, expected_sha256=None):
        return True

    monkeypatch.setattr(
        "app.tasks.image_sync.check_agent_has_image",
        fake_check_agent_has_image,
    )

    result = await image_reconciliation.verify_image_status_on_agents()

    assert result.status_updates >= 3

    refreshed = test_db.query(models.ImageHost).filter(models.ImageHost.host_id == host.id).all()
    status_by_id = {r.image_id: r.status for r in refreshed}
    assert status_by_id["docker-img"] == "synced"
    assert status_by_id["qcow2-img"] == "synced"
    assert status_by_id["iol-img"] == "synced"


@pytest.mark.asyncio
async def test_verify_image_status_on_agents_does_not_flap_missing_qcow2_to_synced(
    test_db, monkeypatch
) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["libvirt"]}),
        version="1.0.0",
    )
    test_db.add(host)

    qcow2_record = models.ImageHost(
        image_id="qcow2-img",
        host_id=host.id,
        reference="/var/lib/archetype/images/sonic-vs.img",
        status="failed",
        error_message="SHA256 mismatch — image may be corrupted",
    )
    test_db.add(qcow2_record)
    test_db.commit()

    manifest = {
        "images": [
            {
                "id": "qcow2-img",
                "reference": "/var/lib/archetype/images/sonic-vs.img",
                "kind": "qcow2",
                "sha256": "133d4cc35cd01a6eb7e45f69d9d0c9cc4ee28ccaa21c65f354720d42e5d29084",
            },
        ]
    }

    async def fake_get_agent_images(agent):
        return {"images": []}

    async def fake_check_agent_has_image(host, reference, expected_sha256=None):
        return False

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "get_agent_images",
        fake_get_agent_images,
    )
    monkeypatch.setattr(
        "app.tasks.image_sync.check_agent_has_image",
        fake_check_agent_has_image,
    )

    result = await image_reconciliation.verify_image_status_on_agents()

    assert result.status_updates == 1

    refreshed = test_db.query(models.ImageHost).filter(models.ImageHost.host_id == host.id).one()
    assert refreshed.status == "missing"
    assert refreshed.error_message == "Image not found on agent"


@pytest.mark.asyncio
async def test_cleanup_deleted_image_from_agents_removes_row_after_remote_delete(
    test_db, monkeypatch
) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    image_host = models.ImageHost(
        image_id="docker:ceos:4.28.0F",
        host_id=host.id,
        reference="ceos:4.28.0F",
        status="synced",
    )
    test_db.add_all([host, image_host])
    test_db.commit()

    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)

    async def fake_delete_image_on_agent(host, reference):
        return {"success": True, "deleted": True}

    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "delete_image_on_agent",
        fake_delete_image_on_agent,
    )

    result = await image_reconciliation.cleanup_deleted_image_from_agents(
        "docker:ceos:4.28.0F",
        "ceos:4.28.0F",
    )

    assert result.orphaned_hosts_removed == 1
    assert result.remote_deletions == 1
    assert test_db.query(models.ImageHost).count() == 0


@pytest.mark.asyncio
async def test_cleanup_unused_agent_images_deletes_stale_docker_tags(test_db, monkeypatch) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    manifest = {
        "images": [
            {"id": "docker:kept", "reference": "kept:1", "kind": "docker"},
        ]
    }

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)

    async def fake_get_agent_images(host):
        return {"images": [{"tags": ["stale:1", "kept:1"]}]}

    async def fake_delete_image_on_agent(host, reference):
        return {"success": True, "deleted": reference == "stale:1"}

    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "get_agent_images",
        fake_get_agent_images,
    )
    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "delete_image_on_agent",
        fake_delete_image_on_agent,
    )

    result = await image_reconciliation.cleanup_unused_agent_images()

    assert result.remote_deletions == 1


@pytest.mark.asyncio
async def test_cleanup_unused_agent_images_skips_in_use_docker_images(test_db, monkeypatch) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: {"images": []})
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)

    async def fake_get_agent_images(host):
        return {
            "images": [
                {"tags": ["archetype-iac-agent:latest"], "kind": "docker", "in_use": True},
            ]
        }

    async def fake_delete_image_on_agent(host, reference):
        raise AssertionError("delete_image_on_agent should not be called for in-use images")

    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "get_agent_images",
        fake_get_agent_images,
    )
    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "delete_image_on_agent",
        fake_delete_image_on_agent,
    )

    result = await image_reconciliation.cleanup_unused_agent_images()

    assert result.remote_deletions == 0


@pytest.mark.asyncio
async def test_cleanup_unused_agent_images_deletes_stale_file_reference(test_db, monkeypatch) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["docker", "libvirt"]}),
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    manifest = {
        "images": [
            {"id": "qcow2:kept", "reference": "/var/lib/archetype/images/kept.qcow2", "kind": "qcow2"},
        ]
    }

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)

    async def fake_get_agent_images(host):
        return {
            "images": [
                {"reference": "/var/lib/archetype/images/stale.qcow2", "kind": "qcow2", "tags": []},
                {"reference": "/var/lib/archetype/images/kept.qcow2", "kind": "qcow2", "tags": []},
            ]
        }

    async def fake_delete_image_on_agent(host, reference):
        return {"success": True, "deleted": reference.endswith("stale.qcow2")}

    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "get_agent_images",
        fake_get_agent_images,
    )
    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "delete_image_on_agent",
        fake_delete_image_on_agent,
    )

    result = await image_reconciliation.cleanup_unused_agent_images()

    assert result.remote_deletions == 1


@pytest.mark.asyncio
async def test_cleanup_deleted_image_from_agents_skips_host_when_reference_still_active(
    test_db, monkeypatch
) -> None:
    host = models.Host(
        id="host-1",
        name="Host 1",
        address="localhost:3",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    lab = models.Lab(id="lab-1", name="Lab 1", provider="docker", state="running")
    node = models.Node(
        id="node-1",
        lab_id=lab.id,
        gui_id="gui-1",
        display_name="Node 1",
        container_name="node-1",
        node_type="device",
        device="ceos",
        image="ceos:4.28.0F",
        host_id=host.id,
    )
    placement = models.NodePlacement(
        id="placement-1",
        lab_id=lab.id,
        node_name="node-1",
        node_definition_id=node.id,
        host_id=host.id,
        status="running",
    )
    image_host = models.ImageHost(
        image_id="docker:ceos:4.28.0F",
        host_id=host.id,
        reference="ceos:4.28.0F",
        status="synced",
    )
    test_db.add_all([host, lab, node, placement, image_host])
    test_db.commit()

    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)

    async def fake_delete_image_on_agent(host, reference):
        raise AssertionError("delete_image_on_agent should not be called while image is active")

    monkeypatch.setattr(
        image_reconciliation.agent_client,
        "delete_image_on_agent",
        fake_delete_image_on_agent,
    )

    result = await image_reconciliation.cleanup_deleted_image_from_agents(
        "docker:ceos:4.28.0F",
        "ceos:4.28.0F",
    )

    assert result.orphaned_hosts_removed == 0
    assert test_db.query(models.ImageHost).count() == 1
