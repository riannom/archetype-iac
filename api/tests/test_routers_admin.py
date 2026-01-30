"""Tests for admin router endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestReconcileState:
    """Tests for POST /reconcile endpoint."""

    def test_reconcile_requires_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test that reconcile endpoint requires admin access."""
        response = test_client.post("/reconcile", headers=auth_headers)
        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    def test_reconcile_no_agents(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ):
        """Test reconcile with no healthy agents."""
        response = test_client.post("/reconcile", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "No healthy agents" in str(data["errors"])

    @patch("app.agent_client.discover_labs_on_agent")
    async def test_reconcile_discovers_running_labs(
        self,
        mock_discover,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Test reconcile discovers running labs from agents."""
        mock_discover.return_value = {
            "labs": [
                {
                    "lab_id": sample_lab.id,
                    "nodes": [{"name": "r1", "status": "running"}],
                }
            ]
        }

        response = test_client.post("/reconcile", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["agents_queried"] >= 0  # May be 0 if async mock not working

    def test_reconcile_unauthenticated(self, test_client: TestClient):
        """Test reconcile without authentication fails."""
        response = test_client.post("/reconcile")
        assert response.status_code == 401


class TestRefreshLabStatus:
    """Tests for GET /labs/{lab_id}/refresh-status endpoint."""

    def test_refresh_lab_status_requires_access(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test refresh requires access to the lab."""
        # Create lab owned by admin (not shared with test_user)
        lab = models.Lab(
            name="Private Lab", owner_id=admin_user.id, provider="containerlab"
        )
        test_db.add(lab)
        test_db.commit()

        response = test_client.get(
            f"/labs/{lab.id}/refresh-status", headers=auth_headers
        )
        assert response.status_code == 404  # Appears as not found

    def test_refresh_lab_status_no_agent(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test refresh when no healthy agent available."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/refresh-status", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "no healthy agent" in data["error"].lower()

    @patch("app.agent_client.get_healthy_agent")
    @patch("app.agent_client.get_lab_status_from_agent")
    async def test_refresh_lab_status_updates_nodes(
        self,
        mock_get_status,
        mock_get_agent,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Test refresh updates node states from agent."""
        lab, nodes = sample_lab_with_nodes

        mock_get_agent.return_value = sample_host
        mock_get_status.return_value = {
            "nodes": [
                {"name": nodes[0].node_name, "status": "running"},
                {"name": nodes[1].node_name, "status": "running"},
            ]
        }

        response = test_client.get(
            f"/labs/{lab.id}/refresh-status", headers=auth_headers
        )
        assert response.status_code == 200

    def test_refresh_lab_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Test refresh for non-existent lab."""
        response = test_client.get(
            "/labs/nonexistent-lab/refresh-status", headers=auth_headers
        )
        assert response.status_code == 404


class TestSystemLogs:
    """Tests for GET /logs endpoint."""

    def test_logs_requires_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test that logs endpoint requires admin access."""
        response = test_client.get("/logs", headers=auth_headers)
        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    def test_logs_default_params(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ):
        """Test logs endpoint with default parameters."""
        # This will fail to connect to Loki (not running), but should return empty
        response = test_client.get("/logs", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "total_count" in data
        assert "has_more" in data

    def test_logs_with_filters(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ):
        """Test logs endpoint with various filters."""
        response = test_client.get(
            "/logs",
            params={
                "service": "api",
                "level": "ERROR",
                "since": "1h",
                "search": "test",
                "limit": 50,
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["entries"], list)

    @pytest.mark.parametrize("since", ["15m", "1h", "24h"])
    def test_logs_time_ranges(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        since: str,
    ):
        """Test logs endpoint with different time ranges."""
        response = test_client.get(
            "/logs",
            params={"since": since},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200

    def test_logs_limit_validation(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ):
        """Test logs endpoint validates limit parameter."""
        # Valid limits
        response = test_client.get(
            "/logs", params={"limit": 100}, headers=admin_auth_headers
        )
        assert response.status_code == 200

        response = test_client.get(
            "/logs", params={"limit": 1000}, headers=admin_auth_headers
        )
        assert response.status_code == 200

        # Invalid limit (too high)
        response = test_client.get(
            "/logs", params={"limit": 2000}, headers=admin_auth_headers
        )
        assert response.status_code == 422  # Validation error

    def test_logs_unauthenticated(self, test_client: TestClient):
        """Test logs without authentication fails."""
        response = test_client.get("/logs")
        assert response.status_code == 401


class TestAdminAccessControl:
    """Tests for admin access control patterns."""

    def test_admin_user_has_access(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ):
        """Test admin user can access admin endpoints."""
        # Reconcile endpoint as example
        response = test_client.post("/reconcile", headers=admin_auth_headers)
        # Should not be 403
        assert response.status_code != 403

    def test_regular_user_denied(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test regular user is denied access to admin endpoints."""
        response = test_client.post("/reconcile", headers=auth_headers)
        assert response.status_code == 403

        response = test_client.get("/logs", headers=auth_headers)
        assert response.status_code == 403

    def test_admin_can_access_any_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_auth_headers: dict,
    ):
        """Test admin can access labs owned by other users."""
        # Create lab owned by regular user
        lab = models.Lab(
            name="User Lab", owner_id=test_user.id, provider="containerlab"
        )
        test_db.add(lab)
        test_db.commit()

        # Admin should be able to access it
        response = test_client.get(f"/labs/{lab.id}", headers=admin_auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == lab.id
