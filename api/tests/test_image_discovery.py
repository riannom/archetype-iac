"""Tests for discover_unmanifested_images() in image_reconciliation."""
from __future__ import annotations

from contextlib import contextmanager
import json

import pytest

from app import models
import app.tasks.image_reconciliation as image_reconciliation


@contextmanager
def _session_ctx(test_db):
    yield test_db


def _make_host(test_db, host_id="host-1", name="Host 1"):
    host = models.Host(
        id=host_id,
        name=name,
        address=f"localhost:{host_id}",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()
    return host


# ─── Keyword fallback (existing behaviour) ───────────────────────────


@pytest.mark.asyncio
async def test_discovery_creates_manifest_entry(test_db, sample_lab, monkeypatch):
    """Unmanifested Docker image on agent gets a new manifest entry via keyword fallback."""
    _make_host(test_db)

    manifest = {"images": []}
    saved = {}

    def fake_save(data):
        saved["manifest"] = data

    async def fake_get_images(agent):
        return {"images": [{"tags": ["ceos64-lab-4.35.1f:imported"]}]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "save_manifest", fake_save)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 1
    assert "manifest" in saved
    images = saved["manifest"]["images"]
    assert len(images) == 1
    entry = images[0]
    assert entry["reference"] == "ceos64-lab-4.35.1f:imported"
    assert entry["kind"] == "docker"
    assert entry["device_id"] == "ceos"
    assert entry["source"] == "agent-discovery"


@pytest.mark.asyncio
async def test_discovery_skips_already_manifested(test_db, sample_lab, monkeypatch):
    """Images already in manifest are not duplicated."""
    _make_host(test_db)

    manifest = {
        "images": [
            {
                "id": "docker:ceos64-lab-4.35.1f:imported",
                "reference": "ceos64-lab-4.35.1f:imported",
                "kind": "docker",
                "device_id": "ceos",
            }
        ]
    }

    async def fake_get_images(agent):
        return {"images": [{"tags": ["ceos64-lab-4.35.1f:imported"]}]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 0


@pytest.mark.asyncio
async def test_discovery_skips_unrecognized_tags(test_db, sample_lab, monkeypatch):
    """Tags that don't match any device keyword are skipped (no agent metadata either)."""
    _make_host(test_db)

    manifest = {"images": []}

    async def fake_get_images(agent):
        return {"images": [{"tags": ["redis:7", "postgres:16", "myapp:latest"]}]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 0


@pytest.mark.asyncio
async def test_discovery_skips_dangling_images(test_db, sample_lab, monkeypatch):
    """Dangling images (<none>:<none>) are skipped."""
    _make_host(test_db)

    manifest = {"images": []}

    async def fake_get_images(agent):
        return {"images": [{"tags": ["<none>:<none>"]}]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 0


@pytest.mark.asyncio
async def test_discovery_deduplicates_across_agents(test_db, sample_lab, monkeypatch):
    """Same image on multiple agents produces a single manifest entry."""
    _make_host(test_db, "host-a", "Host A")
    _make_host(test_db, "host-b", "Host B")

    manifest = {"images": []}
    saved = {}

    def fake_save(data):
        saved["manifest"] = data

    async def fake_get_images(agent):
        # Both agents have the same image
        return {"images": [{"tags": ["ceos64-lab-4.35.1f:imported"]}]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "save_manifest", fake_save)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 1
    assert len(saved["manifest"]["images"]) == 1


@pytest.mark.asyncio
async def test_discovery_skips_alpine_suffix_false_positives(test_db, sample_lab, monkeypatch):
    """Images with '-alpine' base image suffix should not match the alpine device."""
    _make_host(test_db)

    manifest = {"images": []}

    async def fake_get_images(agent):
        return {"images": [{"tags": [
            "redis:7-alpine",
            "nginx:1.27-alpine",
            "postgres:16-alpine",
            "node:20-alpine",
        ]}]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 0


# ─── Deterministic device_id from agent metadata ─────────────────────


@pytest.mark.asyncio
async def test_discovery_uses_agent_reported_device_id(test_db, sample_lab, monkeypatch):
    """Agent-reported device_id is used directly without keyword guessing."""
    _make_host(test_db)

    manifest = {"images": []}
    saved = {}

    def fake_save(data):
        saved["manifest"] = data

    async def fake_get_images(agent):
        return {"images": [{
            "tags": ["my-custom-image:latest"],
            "device_id": "ceos",
        }]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "save_manifest", fake_save)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 1
    entry = saved["manifest"]["images"][0]
    assert entry["device_id"] == "ceos"
    assert entry["reference"] == "my-custom-image:latest"


@pytest.mark.asyncio
async def test_discovery_no_device_id_falls_back_to_keyword(test_db, sample_lab, monkeypatch):
    """Without agent metadata, keyword detection still works as fallback."""
    _make_host(test_db)

    manifest = {"images": []}
    saved = {}

    def fake_save(data):
        saved["manifest"] = data

    async def fake_get_images(agent):
        # No device_id reported — keyword detection should match "iosv"
        return {"images": [{
            "tags": ["iosv:15.9"],
        }]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "save_manifest", fake_save)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 1
    entry = saved["manifest"]["images"][0]
    assert entry["device_id"] == "cisco_iosv"
    assert entry["reference"] == "iosv:15.9"


@pytest.mark.asyncio
async def test_discovery_conflicting_device_ids_skipped(test_db, sample_lab, monkeypatch):
    """Conflicting device_id across agents causes the image to be skipped."""
    _make_host(test_db, "host-a", "Host A")
    _make_host(test_db, "host-b", "Host B")

    manifest = {"images": []}

    async def fake_get_images(agent):
        # Agents disagree on device_id
        if agent.name == "Host A":
            return {"images": [{
                "tags": ["ambiguous-image:1.0"],
                "device_id": "ceos",
            }]}
        else:
            return {"images": [{
                "tags": ["ambiguous-image:1.0"],
                "device_id": "srlinux",
            }]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 0  # Skipped due to conflict


@pytest.mark.asyncio
async def test_discovery_agent_metadata_overrides_keyword_mismatch(
    test_db, sample_lab, monkeypatch
):
    """Agent metadata takes precedence — even if tag wouldn't match any keyword."""
    _make_host(test_db)

    manifest = {"images": []}
    saved = {}

    def fake_save(data):
        saved["manifest"] = data

    async def fake_get_images(agent):
        # Tag "custom-router:v1" wouldn't match any keyword, but agent knows it's ceos
        return {"images": [{
            "tags": ["custom-router:v1"],
            "device_id": "ceos",
        }]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "save_manifest", fake_save)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 1
    assert saved["manifest"]["images"][0]["device_id"] == "ceos"


@pytest.mark.asyncio
async def test_discovery_consistent_device_id_across_agents(
    test_db, sample_lab, monkeypatch
):
    """When multiple agents agree on device_id, image is accepted."""
    _make_host(test_db, "host-a", "Host A")
    _make_host(test_db, "host-b", "Host B")

    manifest = {"images": []}
    saved = {}

    def fake_save(data):
        saved["manifest"] = data

    async def fake_get_images(agent):
        return {"images": [{
            "tags": ["my-ceos:4.35"],
            "device_id": "ceos",
        }]}

    monkeypatch.setattr(image_reconciliation, "load_manifest", lambda: manifest)
    monkeypatch.setattr(image_reconciliation, "save_manifest", fake_save)
    monkeypatch.setattr(image_reconciliation, "get_session", lambda: _session_ctx(test_db))
    monkeypatch.setattr(image_reconciliation.agent_client, "is_agent_online", lambda h: True)
    monkeypatch.setattr(image_reconciliation.agent_client, "get_agent_images", fake_get_images)

    count = await image_reconciliation.discover_unmanifested_images()

    assert count == 1
    assert len(saved["manifest"]["images"]) == 1
    assert saved["manifest"]["images"][0]["device_id"] == "ceos"
