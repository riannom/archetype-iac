from __future__ import annotations

from datetime import datetime, timezone

import app.routers.events as events_router  # noqa: F401
import app.routers.callbacks as callbacks_router  # noqa: F401

from app import models


def test_event_node_update(test_client, test_db) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    node_state = models.NodeState(
        lab_id=lab.id,
        node_id="n1",
        node_name="node1",
        desired_state="running",
        actual_state="stopped",
    )
    test_db.add(node_state)
    test_db.commit()

    resp = test_client.post(
        "/events/node",
        json={
            "agent_id": "test-agent",
            "lab_id": lab.id,
            "node_name": "node1",
            "event_type": "started",
            "status": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert resp.status_code == 200
    test_db.refresh(node_state)
    assert node_state.actual_state == "running"


def test_batch_events_ignores_missing(test_client, test_db) -> None:
    resp = test_client.post("/events/batch", json=[])
    assert resp.status_code == 200

    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    node_state = models.NodeState(
        lab_id=lab.id,
        node_id="n1",
        node_name="node1",
        desired_state="running",
        actual_state="running",
    )
    test_db.add(node_state)
    test_db.commit()

    resp = test_client.post(
        "/events/batch",
        json=[
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": "node1",
                "event_type": "died",
                "status": "code 137",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ],
    )
    assert resp.status_code == 200
    test_db.refresh(node_state)
    assert node_state.actual_state == "stopped"


def test_callbacks_job_and_dead_letter(test_client, test_db) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    job = models.Job(
        lab_id=lab.id,
        user_id=None,
        action="up",
        status="running",
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    payload = {
        "job_id": job.id,
        "agent_id": "test-agent",
        "status": "completed",
        "stdout": "ok",
        "stderr": "",
    }

    resp = test_client.post(f"/callbacks/job/{job.id}", json=payload)
    assert resp.status_code == 200
    test_db.refresh(job)
    assert job.status == "completed"

    dead = test_client.post(f"/callbacks/dead-letter/{job.id}", json=payload)
    assert dead.status_code == 200


def test_callbacks_heartbeat(test_client, test_db) -> None:
    job = models.Job(
        lab_id="lab",
        user_id=None,
        action="up",
        status="running",
    )
    test_db.add(job)
    test_db.commit()

    resp = test_client.post(f"/callbacks/job/{job.id}/heartbeat")
    assert resp.status_code == 200
