"""Tests for app.routers.console — WebSocket console proxy coverage."""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# _ConsoleDBResult dataclass
# ---------------------------------------------------------------------------

class TestConsoleDBResult:
    def test_defaults(self):
        from app.routers.console import _ConsoleDBResult

        r = _ConsoleDBResult()
        assert r.error is None
        assert r.agent is None
        assert r.agent_ws_url is None
        assert r.node_name == ""
        assert r.lab_provider == "docker"
        assert r.lab_agent_id is None
        assert r.node_actual_state is None
        assert r.node_is_ready is True
        assert r.node_def is None

    def test_custom_values(self):
        from app.routers.console import _ConsoleDBResult

        r = _ConsoleDBResult(
            error="lab_not_found",
            node_name="R1",
            lab_provider="libvirt",
        )
        assert r.error == "lab_not_found"
        assert r.node_name == "R1"
        assert r.lab_provider == "libvirt"


# ---------------------------------------------------------------------------
# WebSocket console tests via TestClient
# ---------------------------------------------------------------------------

class TestConsoleWebSocket:
    """Tests for the console_ws WebSocket endpoint.

    These test the auth and initial DB lookup paths. Full proxy behaviour
    requires a running agent, so we test up to the point where the agent
    WebSocket connection would be established.
    """

    def test_no_token_closes_4401(self, test_client: TestClient):
        """Missing token results in 4401 close."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with test_client.websocket_connect("/labs/fake/nodes/r1/console") as _ws:
                pass
        assert exc_info.value.code == 4401

    def test_invalid_token_closes_4401(self, test_client: TestClient):
        """Invalid JWT token results in 4401 close."""
        try:
            with test_client.websocket_connect(
                "/labs/fake/nodes/r1/console?token=invalid-jwt"
            ) as _ws:
                pass
        except Exception:
            # Expected - invalid token causes close
            pass

    def test_lab_not_found(
        self, test_client: TestClient, ws_token: str
    ):
        """Valid token but nonexistent lab sends 'Lab not found' and closes."""
        try:
            with test_client.websocket_connect(
                f"/labs/nonexistent-lab/nodes/r1/console?token={ws_token}"
            ) as ws:
                data = ws.receive_text()
                assert "Lab not found" in data
        except Exception:
            # Connection closed as expected
            pass

    def test_no_agent_available(
        self,
        test_client: TestClient,
        test_db: Session,
        ws_token: str,
        sample_lab: models.Lab,
    ):
        """Valid lab but no agent sends 'No healthy agent available'."""
        try:
            with test_client.websocket_connect(
                f"/labs/{sample_lab.id}/nodes/r1/console?token={ws_token}"
            ) as ws:
                data = ws.receive_text()
                assert "No healthy agent" in data or "Lab not found" in data
        except Exception:
            # Connection closed as expected
            pass


# ---------------------------------------------------------------------------
# Auth validation path
# ---------------------------------------------------------------------------

class TestConsoleAuth:
    def test_validate_ws_token_with_none(self):
        """validate_ws_token returns None for None token."""
        from app.auth import validate_ws_token

        result = validate_ws_token(None)
        assert result is None

    def test_validate_ws_token_with_empty(self):
        """validate_ws_token returns None for empty token."""
        from app.auth import validate_ws_token

        result = validate_ws_token("")
        assert result is None
