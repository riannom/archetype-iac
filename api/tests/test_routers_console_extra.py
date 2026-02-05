from __future__ import annotations

import app.routers.console as console_router  # noqa: F401
import pytest

from app import models


def test_console_ws_lab_not_found(test_client):
    with pytest.raises(Exception):
        with test_client.websocket_connect("/labs/missing/nodes/node1/console") as ws:
            message = ws.receive_text()
            assert "Lab not found" in message


def test_console_ws_no_agent_available(test_client, test_db):
    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    # No agents in db, so should return "No healthy agent available"
    with pytest.raises(Exception):
        with test_client.websocket_connect(f"/labs/{lab.id}/nodes/node1/console") as ws:
            message = ws.receive_text()
            assert "No healthy agent available" in message
