from __future__ import annotations

import app.routers.admin as admin_router  # noqa: F401

from app import models


def test_admin_reconcile_requires_admin(test_client, auth_headers) -> None:
    resp = test_client.post("/reconcile", headers=auth_headers)
    assert resp.status_code == 403


def test_admin_reconcile_no_agents(test_client, test_db, admin_auth_headers) -> None:
    resp = test_client.post("/reconcile", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert "No healthy agents" in resp.json()["errors"][0]


def test_admin_reconcile_updates_labs(test_client, test_db, admin_auth_headers, monkeypatch) -> None:
    host = models.Host(
        id="agent-1",
        name="Agent",
        address="localhost:1",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add_all([host, lab])
    test_db.commit()

    async def fake_discover(agent):
        return {"labs": [{"lab_id": lab.id, "nodes": [{"status": "running"}]}]}

    async def fake_cleanup(agent, lab_ids):
        return {"removed_containers": []}

    monkeypatch.setattr("app.routers.admin.agent_client.discover_labs_on_agent", fake_discover)
    monkeypatch.setattr("app.routers.admin.agent_client.cleanup_orphans_on_agent", fake_cleanup)

    resp = test_client.post("/reconcile", headers=admin_auth_headers)
    assert resp.status_code == 200
    test_db.refresh(lab)
    assert lab.state == "running"


def test_admin_refresh_state_no_agents(test_client, test_db, admin_user, admin_auth_headers, monkeypatch) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=admin_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    async def fake_get_healthy_agent(database):
        return None

    monkeypatch.setattr("app.routers.admin.agent_client.get_healthy_agent", fake_get_healthy_agent)

    resp = test_client.post(f"/labs/{lab.id}/refresh-state", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert "No healthy agent" in resp.json().get("error", "")
