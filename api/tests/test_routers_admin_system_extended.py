"""Tests for admin, system, and infrastructure router endpoints.

Covers:
- admin.py: reconcile, runtime-drift, refresh-state, logs, reconcile-images,
            cleanup-stuck-jobs, audit-logs
- system.py: version, updates, login-defaults, link-reservations/health,
             alerts, diagnostics
- infrastructure.py: settings GET/PATCH, mesh GET, mesh/test-mtu POST,
                     mesh/test-all POST
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from tests.factories import make_host, make_job, make_lab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audit_log(
    test_db: Session,
    event_type: str = "login",
    user_id: str | None = None,
    target_user_id: str | None = None,
    details: dict | None = None,
) -> models.AuditLog:
    """Persist and return an AuditLog record."""
    entry = models.AuditLog(
        event_type=event_type,
        user_id=user_id,
        target_user_id=target_user_id,
        ip_address="127.0.0.1",
        details_json=json.dumps(details) if details else None,
    )
    test_db.add(entry)
    test_db.commit()
    test_db.refresh(entry)
    return entry


# ===========================================================================
# admin.py tests
# ===========================================================================


class TestReconcileEndpoint:
    """POST /reconcile — admin-only endpoint."""

    def test_reconcile_requires_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ) -> None:
        resp = test_client.post("/reconcile", headers=auth_headers)
        assert resp.status_code == 403

    def test_reconcile_requires_auth(self, test_client: TestClient) -> None:
        resp = test_client.post("/reconcile")
        assert resp.status_code == 401

    def test_reconcile_no_agents_returns_error(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ) -> None:
        resp = test_client.post("/reconcile", headers=admin_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["agents_queried"] == 0
        assert "No healthy agents available" in body["errors"]

    def test_reconcile_with_online_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        make_host(test_db, status="online")
        make_lab(test_db, owner_id=admin_user.id, state="running")

        with patch(
            "app.routers.admin.agent_client.discover_labs_on_agent",
            new_callable=AsyncMock,
            return_value={"labs": []},
        ):
            resp = test_client.post("/reconcile", headers=admin_auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["agents_queried"] == 1
        # Lab had no containers — should have been marked stopped
        assert body["labs_updated"] == 1

    def test_reconcile_cleanup_orphans(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
    ) -> None:
        make_host(test_db, status="online")

        discover_result = {"labs": [{"lab_id": "orphan-lab", "nodes": [{"status": "running"}]}]}
        cleanup_result = {"removed_containers": ["orphan-lab-router1"]}

        with (
            patch(
                "app.routers.admin.agent_client.discover_labs_on_agent",
                new_callable=AsyncMock,
                return_value=discover_result,
            ),
            patch(
                "app.routers.admin.agent_client.cleanup_orphans_on_agent",
                new_callable=AsyncMock,
                return_value=cleanup_result,
            ),
        ):
            resp = test_client.post(
                "/reconcile?cleanup_orphans=true",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "orphan-lab-router1" in body["orphans_cleaned"]


class TestAuditLabRuntimeDrift:
    """GET /labs/{lab_id}/runtime-drift — admin-only."""

    def test_runtime_drift_requires_admin(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        test_user: models.User,
    ) -> None:
        lab = make_lab(test_db, owner_id=test_user.id)
        resp = test_client.get(
            f"/labs/{lab.id}/runtime-drift", headers=auth_headers
        )
        assert resp.status_code == 403

    def test_runtime_drift_404_for_unknown_lab(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ) -> None:
        resp = test_client.get(
            "/labs/does-not-exist/runtime-drift", headers=admin_auth_headers
        )
        assert resp.status_code == 404

    def test_runtime_drift_no_device_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        lab = make_lab(test_db, owner_id=admin_user.id)
        resp = test_client.get(
            f"/labs/{lab.id}/runtime-drift", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["lab_id"] == lab.id
        assert body["summary"]["scanned_nodes"] == 0
        assert body["nodes"] == []


class TestRefreshLabState:
    """POST /labs/{lab_id}/refresh-state — admin-only."""

    def test_refresh_requires_admin(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        test_user: models.User,
    ) -> None:
        lab = make_lab(test_db, owner_id=test_user.id)
        resp = test_client.post(
            f"/labs/{lab.id}/refresh-state", headers=auth_headers
        )
        assert resp.status_code == 403

    def test_refresh_404_for_unknown_lab(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ) -> None:
        resp = test_client.post(
            "/labs/no-such-lab/refresh-state", headers=admin_auth_headers
        )
        assert resp.status_code == 404

    def test_refresh_no_agent_returns_error(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        lab = make_lab(test_db, owner_id=admin_user.id)

        with patch(
            "app.routers.admin.agent_client.get_healthy_agent",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = test_client.post(
                f"/labs/{lab.id}/refresh-state", headers=admin_auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body

    def test_refresh_with_running_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        host = make_host(test_db, status="online")
        lab = make_lab(test_db, owner_id=admin_user.id, state="stopped")
        lab.agent_id = host.id
        test_db.commit()

        node_state = models.NodeState(
            lab_id=lab.id,
            node_id="r1",
            node_name="router1",
            desired_state="running",
            actual_state="stopped",
        )
        test_db.add(node_state)
        test_db.commit()

        agent_result = {"nodes": [{"name": "router1", "status": "running"}]}

        with (
            patch(
                "app.routers.admin.agent_client.is_agent_online",
                return_value=True,
            ),
            patch(
                "app.routers.admin.agent_client.get_lab_status_from_agent",
                new_callable=AsyncMock,
                return_value=agent_result,
            ),
        ):
            resp = test_client.post(
                f"/labs/{lab.id}/refresh-state", headers=admin_auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "running"
        assert len(body["nodes"]) == 1


class TestGetSystemLogs:
    """GET /logs — admin-only, queries Loki."""

    def test_logs_requires_admin(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.get("/logs", headers=auth_headers)
        assert resp.status_code == 403

    def test_logs_loki_unavailable_returns_empty(
        self, test_client: TestClient, admin_auth_headers: dict
    ) -> None:
        import httpx

        with patch(
            "app.routers.admin.httpx.AsyncClient",
            side_effect=httpx.ConnectError("refused"),
        ):
            resp = test_client.get("/logs", headers=admin_auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == []
        assert body["total_count"] == 0

    def test_logs_invalid_service_returns_400(
        self, test_client: TestClient, admin_auth_headers: dict
    ) -> None:
        resp = test_client.get(
            "/logs?service=../../etc/passwd", headers=admin_auth_headers
        )
        assert resp.status_code == 400

    def test_logs_invalid_level_returns_400(
        self, test_client: TestClient, admin_auth_headers: dict
    ) -> None:
        resp = test_client.get(
            "/logs?level=DROP+TABLE", headers=admin_auth_headers
        )
        assert resp.status_code == 400


class TestReconcileImages:
    """POST /reconcile-images — admin-only."""

    def test_reconcile_images_requires_admin(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.post("/reconcile-images", headers=auth_headers)
        assert resp.status_code == 403

    def test_reconcile_images_basic(
        self, test_client: TestClient, admin_auth_headers: dict
    ) -> None:
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"synced": 0, "added": 0, "removed": 0}

        with patch(
            "app.tasks.image_reconciliation.reconcile_image_hosts",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            resp = test_client.post("/reconcile-images", headers=admin_auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert "synced" in body

    def test_reconcile_images_verify_agents(
        self, test_client: TestClient, admin_auth_headers: dict
    ) -> None:
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"synced": 0, "added": 0, "removed": 0}

        with patch(
            "app.tasks.image_reconciliation.full_image_reconciliation",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            resp = test_client.post(
                "/reconcile-images?verify_agents=true", headers=admin_auth_headers
            )

        assert resp.status_code == 200


class TestCleanupStuckJobs:
    """POST /cleanup-stuck-jobs — admin-only."""

    def test_cleanup_requires_admin(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.post("/cleanup-stuck-jobs", headers=auth_headers)
        assert resp.status_code == 403

    def test_cleanup_no_stuck_jobs(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ) -> None:
        resp = test_client.post("/cleanup-stuck-jobs", headers=admin_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["cleaned_count"] == 0
        assert body["cleaned_jobs"] == []

    def test_cleanup_marks_old_running_job_failed(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        from datetime import timedelta

        lab = make_lab(test_db, owner_id=admin_user.id)
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        make_job(test_db, lab_id=lab.id, status="running", created_at=old_time, action="deploy")

        resp = test_client.post(
            "/cleanup-stuck-jobs?max_age_minutes=5", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cleaned_count"] == 1
        assert body["cleaned_jobs"][0]["action"] == "deploy"

    def test_cleanup_leaves_recent_jobs_alone(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        lab = make_lab(test_db, owner_id=admin_user.id)
        make_job(test_db, lab_id=lab.id, status="running")  # recent

        resp = test_client.post(
            "/cleanup-stuck-jobs?max_age_minutes=5", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cleaned_count"] == 0


class TestGetAuditLogs:
    """GET /audit-logs — super_admin only."""

    def test_audit_logs_requires_auth(self, test_client: TestClient) -> None:
        resp = test_client.get("/audit-logs")
        assert resp.status_code == 401

    def test_audit_logs_requires_super_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ) -> None:
        # Regular operator is not super_admin
        resp = test_client.get("/audit-logs", headers=auth_headers)
        assert resp.status_code == 403

    def test_audit_logs_empty(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        # Elevate admin user to super_admin
        admin_user.global_role = "super_admin"
        test_db.commit()

        resp = test_client.get("/audit-logs", headers=admin_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    def test_audit_logs_with_entries(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        admin_user.global_role = "super_admin"
        test_db.commit()

        _make_audit_log(test_db, event_type="login", user_id=admin_user.id)
        _make_audit_log(test_db, event_type="user_created", user_id=admin_user.id)

        resp = test_client.get("/audit-logs", headers=admin_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["entries"]) == 2

    def test_audit_logs_filter_by_event_type(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        admin_user: models.User,
    ) -> None:
        admin_user.global_role = "super_admin"
        test_db.commit()

        _make_audit_log(test_db, event_type="login")
        _make_audit_log(test_db, event_type="logout")

        resp = test_client.get(
            "/audit-logs?event_type=login", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["entries"][0]["event_type"] == "login"


# ===========================================================================
# system.py tests
# ===========================================================================


class TestSystemVersion:
    """GET /system/version — unauthenticated."""

    def test_version_no_auth_required(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/version")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body

    def test_version_returns_string(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/version")
        body = resp.json()
        assert isinstance(body["version"], str)


class TestSystemUpdates:
    """GET /system/updates — unauthenticated, calls GitHub."""

    def test_updates_github_connect_error(self, test_client: TestClient) -> None:
        import httpx

        # Reset cache to force a fresh check
        from app.routers.system import _update_cache
        _update_cache["data"] = None
        _update_cache["timestamp"] = 0

        with patch(
            "app.routers.system.httpx.AsyncClient",
            side_effect=httpx.ConnectError("no route"),
        ):
            resp = test_client.get("/system/updates")

        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is not None

    def test_updates_github_404_means_no_releases(
        self, test_client: TestClient
    ) -> None:
        from app.routers.system import _update_cache
        _update_cache["data"] = None
        _update_cache["timestamp"] = 0

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.routers.system.httpx.AsyncClient", return_value=mock_client):
            resp = test_client.get("/system/updates")

        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is False

    def test_updates_returns_cached_result(self, test_client: TestClient) -> None:
        import time
        from app.routers.system import _update_cache

        _update_cache["data"] = {
            "current_version": "1.0.0",
            "latest_version": "2.0.0",
            "update_available": True,
            "release_url": "https://example.com",
            "release_notes": "New stuff",
            "published_at": "2024-01-01",
            "error": None,
        }
        _update_cache["timestamp"] = time.time()

        resp = test_client.get("/system/updates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is True


class TestSystemLoginDefaults:
    """GET /system/login-defaults — unauthenticated."""

    def test_login_defaults_no_auth(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/login-defaults")
        assert resp.status_code == 200
        body = resp.json()
        assert "dark_theme_id" in body
        assert "light_theme_id" in body

    def test_login_defaults_returns_defaults_when_no_settings(
        self, test_client: TestClient
    ) -> None:
        resp = test_client.get("/system/login-defaults")
        body = resp.json()
        # Default values from InfraSettings model
        assert body["dark_theme_id"] == "midnight"


class TestSystemAlerts:
    """GET /system/alerts — unauthenticated."""

    def test_alerts_no_auth_required(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/alerts")
        assert resp.status_code == 200

    def test_alerts_empty_when_no_agents(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/alerts")
        body = resp.json()
        assert body["agent_error_count"] == 0
        assert body["alerts"] == []

    def test_alerts_shows_agents_with_errors(
        self, test_client: TestClient, test_db: Session
    ) -> None:
        make_host(
            test_db,
            host_id="broken-agent",
            status="degraded",
            last_error="Docker daemon crashed",
        )

        resp = test_client.get("/system/alerts")
        body = resp.json()
        assert body["agent_error_count"] == 1
        assert body["alerts"][0]["error_message"] == "Docker daemon crashed"

    def test_alerts_excludes_healthy_agents(
        self, test_client: TestClient, test_db: Session
    ) -> None:
        make_host(test_db, host_id="healthy", status="online", last_error=None)

        resp = test_client.get("/system/alerts")
        body = resp.json()
        assert body["agent_error_count"] == 0


class TestSystemDiagnostics:
    """GET /system/diagnostics — unauthenticated."""

    def test_diagnostics_returns_200(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/diagnostics")
        assert resp.status_code == 200

    def test_diagnostics_structure(self, test_client: TestClient) -> None:
        resp = test_client.get("/system/diagnostics")
        body = resp.json()
        assert "timestamp" in body
        assert "python_version" in body
        assert "asyncio_tasks" in body
        assert "task_count" in body
        assert "recent_failed_jobs" in body
        assert "recent_jobs" in body
        assert "event_loop_running" in body

    def test_diagnostics_includes_failed_jobs(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
    ) -> None:
        lab = make_lab(test_db, owner_id=admin_user.id)
        make_job(test_db, lab_id=lab.id, status="failed", action="deploy")

        resp = test_client.get("/system/diagnostics")
        body = resp.json()
        assert len(body["recent_failed_jobs"]) == 1
        assert body["recent_failed_jobs"][0]["action"] == "deploy"


class TestSystemLinkReservationsHealth:
    """GET /system/link-reservations/health — admin-only."""

    def test_link_reservations_health_requires_admin(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.get(
            "/system/link-reservations/health", headers=auth_headers
        )
        assert resp.status_code == 403

    def test_link_reservations_health_returns_snapshot(
        self, test_client: TestClient, admin_auth_headers: dict
    ) -> None:
        with patch(
            "app.routers.system.get_link_endpoint_reservation_health_snapshot",
            return_value={"healthy": True, "drift_count": 0},
        ):
            resp = test_client.get(
                "/system/link-reservations/health", headers=admin_auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["healthy"] is True


# ===========================================================================
# infrastructure.py tests
# ===========================================================================


class TestInfrastructureSettings:
    """GET/PATCH /infrastructure/settings."""

    def test_get_settings_requires_auth(self, test_client: TestClient) -> None:
        resp = test_client.get("/infrastructure/settings")
        assert resp.status_code == 401

    def test_get_settings_operator_allowed(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.get("/infrastructure/settings", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "overlay_mtu" in body
        assert "mtu_verification_enabled" in body

    def test_get_settings_creates_default_row(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.get("/infrastructure/settings", headers=auth_headers)
        body = resp.json()
        assert body["overlay_mtu"] == 1450  # default

    def test_patch_settings_requires_admin(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.patch(
            "/infrastructure/settings",
            json={"overlay_mtu": 9000},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_patch_settings_updates_mtu(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ) -> None:
        resp = test_client.patch(
            "/infrastructure/settings",
            json={"overlay_mtu": 1400},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["overlay_mtu"] == 1400

    def test_patch_settings_login_theme(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ) -> None:
        resp = test_client.patch(
            "/infrastructure/settings",
            json={"login_dark_theme_id": "dracula"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["login_dark_theme_id"] == "dracula"


class TestInfrastructureMesh:
    """GET /infrastructure/mesh."""

    def test_get_mesh_requires_auth(self, test_client: TestClient) -> None:
        resp = test_client.get("/infrastructure/mesh")
        assert resp.status_code == 401

    def test_get_mesh_empty(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.get("/infrastructure/mesh", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["agents"] == []
        assert body["links"] == []
        assert "settings" in body

    def test_get_mesh_with_single_agent(
        self, test_client: TestClient, test_db: Session, auth_headers: dict
    ) -> None:
        make_host(test_db, host_id="solo-agent", name="Solo")

        resp = test_client.get("/infrastructure/mesh", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["agents"]) == 1
        assert body["agents"][0]["name"] == "Solo"
        # No pairs possible with one agent → no links created
        assert body["links"] == []

    def test_get_mesh_two_agents_creates_links(
        self, test_client: TestClient, test_db: Session, auth_headers: dict
    ) -> None:
        make_host(test_db, host_id="alpha", name="Alpha")
        make_host(test_db, host_id="beta", name="Beta")

        resp = test_client.get("/infrastructure/mesh", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["agents"]) == 2
        # Two management links (alpha->beta and beta->alpha)
        assert len(body["links"]) == 2


class TestMeshTestMtu:
    """POST /infrastructure/mesh/test-mtu."""

    def test_test_mtu_requires_auth(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/infrastructure/mesh/test-mtu",
            json={"source_agent_id": "a", "target_agent_id": "b"},
        )
        assert resp.status_code == 401

    def test_test_mtu_source_not_found(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        resp = test_client.post(
            "/infrastructure/mesh/test-mtu",
            json={"source_agent_id": "missing-src", "target_agent_id": "missing-tgt"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_test_mtu_source_offline(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
    ) -> None:
        src = make_host(test_db, host_id="src", status="offline")
        tgt = make_host(test_db, host_id="tgt", status="online")

        with patch(
            "app.routers.infrastructure.agent_client.is_agent_online",
            return_value=False,
        ):
            resp = test_client.post(
                "/infrastructure/mesh/test-mtu",
                json={
                    "source_agent_id": src.id,
                    "target_agent_id": tgt.id,
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "offline" in body["error"].lower()

    def test_test_mtu_success(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
    ) -> None:
        src = make_host(test_db, host_id="src2", status="online")
        tgt = make_host(test_db, host_id="tgt2", status="online")

        mtu_result = {
            "success": True,
            "tested_mtu": 1500,
            "link_type": "direct",
            "latency_ms": 0.5,
        }

        with (
            patch(
                "app.routers.infrastructure.agent_client.is_agent_online",
                return_value=True,
            ),
            patch(
                "app.routers.infrastructure.agent_client.test_mtu_on_agent",
                new_callable=AsyncMock,
                return_value=mtu_result,
            ),
            patch(
                "app.agent_client.selection.resolve_agent_ip",
                new_callable=AsyncMock,
                return_value="10.0.0.2",
            ),
        ):
            resp = test_client.post(
                "/infrastructure/mesh/test-mtu",
                json={
                    "source_agent_id": src.id,
                    "target_agent_id": tgt.id,
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["tested_mtu"] == 1500


class TestMeshTestAll:
    """POST /infrastructure/mesh/test-all."""

    def test_test_all_requires_auth(self, test_client: TestClient) -> None:
        resp = test_client.post("/infrastructure/mesh/test-all")
        assert resp.status_code == 401

    def test_test_all_fewer_than_two_online_agents(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
    ) -> None:
        make_host(test_db, host_id="lone-agent", status="online")

        with patch(
            "app.routers.infrastructure.agent_client.is_agent_online",
            return_value=True,
        ):
            resp = test_client.post(
                "/infrastructure/mesh/test-all", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_pairs"] == 0
        assert body["successful"] == 0
        assert body["failed"] == 0