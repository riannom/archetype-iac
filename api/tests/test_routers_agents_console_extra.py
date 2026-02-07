from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.routers.agents as agents_router  # noqa: F401
import pytest

from app import models


def test_register_agent_creates_and_updates(test_client, test_db, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.image_sync_enabled", False)

    # Use naive datetime string (no timezone suffix) to avoid SQLite
    # timezone-stripping causing offset-naive vs offset-aware comparison errors
    started_at = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "agent": {
            "agent_id": "agent-1",
            "name": "Agent One",
            "address": "localhost:1",
            "capabilities": {"providers": ["docker"], "max_concurrent_jobs": 4, "features": []},
            "version": "1.0.0",
            "started_at": started_at,
            "is_local": False,
        }
    }

    resp = test_client.post("/agents/register", json=payload)
    assert resp.status_code == 200

    # Re-register same agent ID with same started_at (no restart detection)
    payload["agent"]["version"] = "1.0.1"
    resp = test_client.post("/agents/register", json=payload)
    assert resp.status_code == 200

    host = test_db.get(models.Host, "agent-1")
    assert host.version == "1.0.1"


def test_register_agent_restart_marks_job_failed(test_client, test_db, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.image_sync_enabled", False)

    # Use naive datetime strings (no timezone suffix) to avoid SQLite
    # timezone-stripping causing offset-naive vs offset-aware comparison errors
    old_started_at = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    initial_payload = {
        "agent": {
            "agent_id": "agent-2",
            "name": "Agent Two",
            "address": "localhost:2",
            "capabilities": {"providers": ["docker"], "max_concurrent_jobs": 4, "features": []},
            "version": "1.0.0",
            "started_at": old_started_at,
            "is_local": False,
        }
    }
    resp = test_client.post("/agents/register", json=initial_payload)
    assert resp.status_code == 200

    # Create lab and running job associated with this agent
    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.flush()
    job = models.Job(
        lab_id=lab.id,
        user_id=None,
        action="up",
        status="running",
        agent_id="agent-2",
    )
    test_db.add(job)
    test_db.commit()

    # Re-register with a newer started_at (simulating agent restart)
    new_started_at = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
    restart_payload = {
        "agent": {
            "agent_id": "agent-2",
            "name": "Agent Two",
            "address": "localhost:2",
            "capabilities": {"providers": ["docker"], "max_concurrent_jobs": 4, "features": []},
            "version": "1.0.0",
            "started_at": new_started_at,
            "is_local": False,
        }
    }

    resp = test_client.post("/agents/register", json=restart_payload)
    assert resp.status_code == 200

    test_db.refresh(job)
    test_db.refresh(lab)
    assert job.status == "failed"
    assert lab.state == "error"


def test_heartbeat_and_list_get_delete(test_client, test_db) -> None:
    host = models.Host(
        id="agent-3",
        name="Agent Three",
        address="localhost:3",
        status="offline",
        capabilities="{}",
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    hb = test_client.post(
        "/agents/agent-3/heartbeat",
        json={"agent_id": "agent-3", "status": "online", "resource_usage": {"cpu_percent": 1}},
    )
    assert hb.status_code == 200

    agents = test_client.get("/agents")
    assert agents.status_code == 200
    assert len(agents.json()) == 1

    agent = test_client.get("/agents/agent-3")
    assert agent.status_code == 200

    deleted = test_client.delete("/agents/agent-3")
    assert deleted.status_code == 200


def test_sync_strategy_validation(test_client, test_db) -> None:
    host = models.Host(
        id="agent-4",
        name="Agent Four",
        address="localhost:4",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    bad = test_client.put("/agents/agent-4/sync-strategy", json={"strategy": "bad"})
    assert bad.status_code == 400

    good = test_client.put("/agents/agent-4/sync-strategy", json={"strategy": "push"})
    assert good.status_code == 200


def test_agent_images_and_reconcile(test_client, test_db, monkeypatch) -> None:
    host = models.Host(
        id="agent-5",
        name="Agent Five",
        address="localhost:5",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    test_db.add(host)
    test_db.add(
        models.ImageHost(
            image_id="img1",
            host_id=host.id,
            reference="ref",
            status="synced",
        )
    )
    test_db.commit()

    images = test_client.get("/agents/agent-5/images")
    assert images.status_code == 200
    assert images.json()["images"][0]["image_id"] == "img1"

    async def fake_reconcile(agent_id, database):
        return None

    monkeypatch.setattr("app.tasks.image_sync.reconcile_agent_images", fake_reconcile)

    rec = test_client.post("/agents/agent-5/images/reconcile")
    assert rec.status_code == 200


def test_agent_interfaces_offline(test_client, test_db) -> None:
    host = models.Host(
        id="agent-6",
        name="Agent Six",
        address="localhost:6",
        status="offline",
        capabilities="{}",
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    resp = test_client.get("/agents/agent-6/interfaces")
    assert resp.status_code == 503
