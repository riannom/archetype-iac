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


def test_admin_runtime_identity_audit_reports_missing_identity(
    test_client, test_db, admin_user, admin_auth_headers, monkeypatch
) -> None:
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
        owner_id=admin_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
        agent_id=host.id,
    )
    test_db.add_all([host, lab])
    test_db.flush()
    node = models.Node(
        lab_id=lab.id,
        gui_id="node-r1",
        display_name="R1",
        container_name="r1",
    )
    test_db.add(node)
    test_db.flush()
    placement = models.NodePlacement(
        lab_id=lab.id,
        node_name="r1",
        node_definition_id=node.id,
        host_id=host.id,
        status="deployed",
        runtime_id=None,
    )
    test_db.add(placement)
    test_db.commit()

    async def fake_status(agent, lab_id):
        return {
            "nodes": [
                {
                    "name": "r1",
                    "node_definition_id": None,
                    "runtime_id": None,
                }
            ]
        }

    monkeypatch.setattr("app.routers.admin.agent_client.get_lab_status_from_agent", fake_status)

    resp = test_client.get(
        f"/labs/{lab.id}/runtime-identity-audit",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["reported_nodes"] == 1
    assert body["summary"]["missing_node_definition_id"] == 1
    assert body["summary"]["missing_runtime_id"] == 1
    assert body["summary"]["active_placements_missing_runtime_id"] == 1


def test_admin_runtime_identity_audit_reports_runtime_drift(
    test_client, test_db, admin_user, admin_auth_headers, monkeypatch
) -> None:
    host = models.Host(
        id="agent-2",
        name="Agent 2",
        address="localhost:2",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    lab = models.Lab(
        name="Lab Drift",
        owner_id=admin_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/lab-drift",
        agent_id=host.id,
    )
    test_db.add_all([host, lab])
    test_db.flush()
    node = models.Node(
        lab_id=lab.id,
        gui_id="node-r1",
        display_name="R1",
        container_name="r1",
    )
    test_db.add(node)
    test_db.flush()
    placement = models.NodePlacement(
        lab_id=lab.id,
        node_name="r1",
        node_definition_id=node.id,
        host_id=host.id,
        status="drifted",
        runtime_id="runtime-old",
    )
    test_db.add(placement)
    test_db.commit()

    async def fake_status(agent, lab_id):
        return {
            "nodes": [
                {
                    "name": "wrong-name",
                    "node_definition_id": node.id,
                    "runtime_id": "runtime-new",
                }
            ]
        }

    monkeypatch.setattr("app.routers.admin.agent_client.get_lab_status_from_agent", fake_status)

    resp = test_client.get(
        f"/labs/{lab.id}/runtime-identity-audit",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["metadata_name_mismatch"] == 1
    assert body["summary"]["runtime_id_mismatch"] == 1
    assert body["summary"]["drifted_placements"] == 1
    assert body["nodes"][0]["placement_status"] == "drifted"
    assert body["nodes"][0]["expected_runtime_id"] == "runtime-old"


def test_admin_runtime_identity_audit_aggregates_agents(
    test_client, test_db, admin_auth_headers, monkeypatch
) -> None:
    host_a = models.Host(
        id="agent-a",
        name="Agent A",
        address="localhost:10",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    host_b = models.Host(
        id="agent-b",
        name="Agent B",
        address="localhost:11",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    test_db.add_all([host_a, host_b])
    test_db.commit()

    async def fake_audit(agent):
        if agent.id == "agent-a":
            return {
                "providers": [
                    {
                        "provider": "docker",
                        "managed_runtimes": 2,
                        "resolved_by_metadata": 1,
                        "name_only": 1,
                        "missing_node_definition_id": 1,
                        "missing_runtime_id": 0,
                        "inconsistent_metadata": 0,
                        "nodes": [],
                    }
                ],
                "errors": [],
            }
        return {
            "providers": [
                {
                    "provider": "libvirt",
                    "managed_runtimes": 1,
                    "resolved_by_metadata": 0,
                    "name_only": 1,
                    "missing_node_definition_id": 1,
                    "missing_runtime_id": 0,
                    "inconsistent_metadata": 1,
                    "nodes": [],
                }
            ],
            "errors": ["libvirt metadata missing"],
        }

    monkeypatch.setattr("app.routers.admin.agent_client.get_runtime_identity_audit", fake_audit)

    resp = test_client.get("/runtime-identity-audit", headers=admin_auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["agents_queried"] == 2
    assert body["summary"]["providers_reported"] == 2
    assert body["summary"]["managed_runtimes"] == 3
    assert body["summary"]["name_only"] == 2
    assert body["summary"]["inconsistent_metadata"] == 1
    assert len(body["agents"]) == 2
    assert "libvirt metadata missing" in body["errors"][0]


def test_admin_runtime_identity_backfill_groups_entries_by_agent(
    test_client, test_db, admin_user, admin_auth_headers, monkeypatch
) -> None:
    host = models.Host(
        id="agent-backfill",
        name="Agent Backfill",
        address="localhost:12",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    lab = models.Lab(
        name="Lab Backfill",
        owner_id=admin_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/lab-backfill",
        agent_id=host.id,
    )
    test_db.add_all([host, lab])
    test_db.flush()
    node = models.Node(
        lab_id=lab.id,
        gui_id="node-r1",
        display_name="R1",
        container_name="r1",
        image="docker://example",
    )
    test_db.add(node)
    test_db.flush()
    placement = models.NodePlacement(
        lab_id=lab.id,
        node_name="r1",
        node_definition_id=node.id,
        host_id=host.id,
        status="deployed",
    )
    test_db.add(placement)
    test_db.commit()

    monkeypatch.setattr("app.routers.admin.agent_client.is_agent_online", lambda agent: True)
    monkeypatch.setattr("app.utils.lab.get_node_provider", lambda node, db: "docker")

    async def fake_backfill(agent, entries, dry_run=True):
        assert dry_run is True
        assert entries == [{
            "lab_id": lab.id,
            "node_name": "r1",
            "node_definition_id": node.id,
            "provider": "docker",
        }]
        return {
            "providers": [
                {
                    "provider": "docker",
                    "updated": 0,
                    "recreate_required": 1,
                    "missing": 0,
                    "skipped": 0,
                    "nodes": [],
                    "errors": [],
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr("app.routers.admin.agent_client.backfill_runtime_identity", fake_backfill)

    resp = test_client.post("/runtime-identity-backfill", headers=admin_auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["dry_run"] is True
    assert body["summary"]["agents_targeted"] == 1
    assert body["summary"]["entries_considered"] == 1
    assert body["summary"]["recreate_required"] == 1


def test_admin_runtime_id_readiness_reports_missing_active_placements(
    test_client, test_db, admin_user, admin_auth_headers
) -> None:
    host = models.Host(
        id="agent-ready",
        name="Agent Ready",
        address="localhost:13",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    lab = models.Lab(
        name="Lab Ready",
        owner_id=admin_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/lab-ready",
        agent_id=host.id,
    )
    test_db.add_all([host, lab])
    test_db.flush()
    missing = models.NodePlacement(
        lab_id=lab.id,
        node_name="r1",
        node_definition_id="node-def-r1",
        host_id=host.id,
        status="deployed",
        runtime_id=None,
    )
    healthy = models.NodePlacement(
        lab_id=lab.id,
        node_name="r2",
        node_definition_id="node-def-r2",
        host_id=host.id,
        status="deployed",
        runtime_id="runtime-r2",
    )
    ignored = models.NodePlacement(
        lab_id=lab.id,
        node_name="r3",
        node_definition_id="node-def-r3",
        host_id=host.id,
        status="failed",
        runtime_id=None,
    )
    test_db.add_all([missing, healthy, ignored])
    test_db.commit()

    resp = test_client.get("/runtime-id-readiness", headers=admin_auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["active_placements"] == 2
    assert body["summary"]["active_placements_missing_runtime_id"] == 1
    assert body["summary"]["constraint_eligible_now"] is False
    assert body["summary"]["recommended_next_step"] == "backfill_or_redeploy_remaining_placements"
    assert len(body["placements"]) == 1
    assert body["placements"][0]["node_name"] == "r1"


def test_admin_runtime_id_readiness_reports_constraint_eligible_when_zero_missing(
    test_client, test_db, admin_user, admin_auth_headers
) -> None:
    host = models.Host(
        id="agent-ready-zero",
        name="Agent Ready Zero",
        address="localhost:14",
        status="online",
        capabilities="{}",
        version="1.0.0",
    )
    lab = models.Lab(
        name="Lab Ready Zero",
        owner_id=admin_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/lab-ready-zero",
        agent_id=host.id,
    )
    test_db.add_all([host, lab])
    test_db.flush()
    placement = models.NodePlacement(
        lab_id=lab.id,
        node_name="r1",
        node_definition_id="node-def-r1",
        host_id=host.id,
        status="deployed",
        runtime_id="runtime-r1",
    )
    test_db.add(placement)
    test_db.commit()

    resp = test_client.get("/runtime-id-readiness", headers=admin_auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["active_placements_missing_runtime_id"] == 0
    assert body["summary"]["constraint_eligible_now"] is True
    assert body["summary"]["recommended_next_step"] == "observe_zero_count_window"
    assert body["placements"] == []


def test_admin_first_init_reliability_report_summarizes_failures(
    test_client, test_db, admin_user, admin_auth_headers,
) -> None:
    lab = models.Lab(
        name="Reliability Lab",
        owner_id=admin_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/reliability-lab",
    )
    test_db.add(lab)
    test_db.flush()
    test_db.add_all([
        models.Job(
            lab_id=lab.id,
            user_id=admin_user.id,
            action="sync:lab",
            status="failed",
            log_path="assigned host agent-01 is offline",
        ),
        models.Job(
            lab_id=lab.id,
            user_id=admin_user.id,
            action="sync:node",
            status="failed",
            log_path="Post-op link reconciliation failed: statement timeout on link_states",
        ),
        models.Job(
            lab_id=lab.id,
            user_id=admin_user.id,
            action="up",
            status="completed_with_warnings",
            log_path="link repair attempted during reconcile",
        ),
    ])
    test_db.add_all([
        models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="error",
            error_message="Cannot find OVS port",
        ),
        models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth2-r3:eth1",
            source_node="r1",
            source_interface="eth2",
            target_node="r3",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            error_message="Waiting for interface readiness: r3:eth1",
        ),
    ])
    test_db.commit()

    resp = test_client.get("/first-init-reliability-report", headers=admin_auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["jobs_examined"] == 3
    assert body["summary"]["failed_jobs"] == 2
    assert body["summary"]["post_op_reconciliation_failures"] == 1
    assert body["summary"]["statement_timeouts"] == 1
    assert body["summary"]["link_repair_mentions"] == 1
    assert body["summary"]["unexpected_corrective_links"] == 1
    assert body["first_init_failure_classes"]["host_unavailable"] == 1
    assert body["first_init_failure_classes"]["other"] == 1
    assert body["soak_gate"]["required_consecutive_hours"] == 72
    assert body["soak_gate"]["eligible_now"] is False
