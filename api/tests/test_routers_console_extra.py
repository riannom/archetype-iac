from __future__ import annotations

import app.routers.console as console_router  # noqa: F401
import pytest
from sqlalchemy.orm import Session

from app import models


class _FakeSessionLocal:
    """Wraps a test DB session so that .close() is a no-op."""

    def __init__(self, session: Session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def test_console_ws_lab_not_found(test_client, test_db, monkeypatch):
    monkeypatch.setattr("app.routers.console.SessionLocal", lambda: _FakeSessionLocal(test_db))
    with test_client.websocket_connect("/labs/missing/nodes/node1/console") as ws:
        message = ws.receive_text()
        assert "not found" in message.lower()


def test_console_ws_no_agent_available(test_client, test_db, monkeypatch):
    monkeypatch.setattr("app.routers.console.SessionLocal", lambda: _FakeSessionLocal(test_db))
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
    with test_client.websocket_connect(f"/labs/{lab.id}/nodes/node1/console") as ws:
        message = ws.receive_text()
        assert "no healthy agent" in message.lower()
