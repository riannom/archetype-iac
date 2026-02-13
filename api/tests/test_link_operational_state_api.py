from __future__ import annotations

from app import models
from app.services.link_operational_state import recompute_link_oper_state


def test_recompute_link_oper_state_peer_host_offline_reason(
    test_db,
    sample_lab,
    monkeypatch,
) -> None:
    from app import agent_client

    # Keep this test deterministic even if earlier tests patched global agent_client helpers.
    monkeypatch.setattr(
        agent_client,
        "is_agent_online",
        lambda host: bool(host and host.status == "online" and host.last_heartbeat),
    )

    source_host = models.Host(
        id="h-source",
        name="source",
        address="10.0.0.1:8000",
        status="online",
    )
    target_host = models.Host(
        id="h-target",
        name="target",
        address="10.0.0.2:8000",
        status="offline",
    )
    test_db.add_all([source_host, target_host])
    test_db.add_all(
        [
            models.NodeState(
                lab_id=sample_lab.id,
                node_id="n1",
                node_name="r1",
                desired_state="running",
                actual_state="running",
            ),
            models.NodeState(
                lab_id=sample_lab.id,
                node_id="n2",
                node_name="r2",
                desired_state="running",
                actual_state="running",
            ),
        ]
    )
    link = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        source_host_id=source_host.id,
        target_host_id=target_host.id,
        desired_state="up",
        actual_state="up",
        source_carrier_state="on",
        target_carrier_state="on",
    )
    test_db.add(link)
    test_db.commit()

    changed = recompute_link_oper_state(test_db, link)
    assert changed is True
    assert link.source_oper_state == "down"
    assert link.source_oper_reason == "peer_host_offline"


def test_set_link_state_recomputes_oper_fields(
    test_client,
    test_db,
    auth_headers,
    sample_lab,
) -> None:
    link = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1-eth1-r2-eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="up",
        actual_state="unknown",
    )
    test_db.add(link)
    test_db.commit()

    response = test_client.put(
        f"/labs/{sample_lab.id}/links/{link.link_name}/state",
        headers=auth_headers,
        json={"state": "down"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["desired_state"] == "down"
    assert data["source_oper_state"] == "down"
    assert data["target_oper_state"] == "down"
    assert data["source_oper_reason"] == "admin_down"
    assert data["target_oper_reason"] == "admin_down"
    assert data["oper_epoch"] >= 1


def test_set_all_links_recomputes_oper_fields(
    test_client,
    test_db,
    auth_headers,
    sample_lab,
) -> None:
    links = [
        models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1-eth1-r2-eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        ),
        models.LinkState(
            lab_id=sample_lab.id,
            link_name="r2-eth2-r3-eth2",
            source_node="r2",
            source_interface="eth2",
            target_node="r3",
            target_interface="eth2",
            desired_state="up",
            actual_state="unknown",
        ),
    ]
    test_db.add_all(links)
    test_db.commit()

    response = test_client.put(
        f"/labs/{sample_lab.id}/links/desired-state",
        headers=auth_headers,
        json={"state": "down"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["links"]) == 2

    for item in payload["links"]:
        assert item["desired_state"] == "down"
        assert item["source_oper_state"] == "down"
        assert item["target_oper_state"] == "down"
        assert item["source_oper_reason"] == "admin_down"
        assert item["target_oper_reason"] == "admin_down"
        assert item["oper_epoch"] >= 1
