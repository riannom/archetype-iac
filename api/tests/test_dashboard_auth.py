"""Tests for dashboard endpoint authentication requirements."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session



DASHBOARD_ENDPOINTS = [
    "/dashboard/metrics",
    "/dashboard/metrics/containers",
    "/dashboard/metrics/resources",
]


class TestDashboardRequiresAuth:
    """Verify dashboard endpoints reject unauthenticated requests."""

    @pytest.mark.parametrize("endpoint", DASHBOARD_ENDPOINTS)
    def test_unauthenticated_request_rejected(
        self,
        test_client: TestClient,
        test_db: Session,
        endpoint: str,
    ):
        resp = test_client.get(endpoint)
        assert resp.status_code == 401

    @pytest.mark.parametrize("endpoint", DASHBOARD_ENDPOINTS)
    def test_authenticated_request_allowed(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict[str, str],
        endpoint: str,
    ):
        resp = test_client.get(endpoint, headers=auth_headers)
        assert resp.status_code == 200
