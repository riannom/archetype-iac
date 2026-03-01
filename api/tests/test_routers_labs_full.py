"""Tests for lab CRUD, deploy/destroy, topology import/export endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestCreateLab:
    """Tests for POST /labs endpoint."""

    def test_create_lab_with_name(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Create a lab with a valid name returns 200."""
        resp = test_client.post(
            "/labs", json={"name": "My Lab"}, headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My Lab"
        assert data["state"] == "stopped"
        assert data["provider"] == "docker"
        assert "id" in data

    def test_create_lab_with_provider(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Create a lab with an explicit provider."""
        resp = test_client.post(
            "/labs",
            json={"name": "Libvirt Lab", "provider": "libvirt"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["provider"] == "libvirt"

    def test_create_lab_requires_name(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Creating a lab without a name returns 422 validation error."""
        resp = test_client.post("/labs", json={}, headers=auth_headers)
        assert resp.status_code == 422

    def test_create_lab_requires_auth(self, test_client: TestClient):
        """Creating a lab without auth returns 401."""
        resp = test_client.post("/labs", json={"name": "No Auth"})
        assert resp.status_code == 401

    def test_create_lab_viewer_forbidden(
        self, test_client: TestClient, test_db: Session, monkeypatch
    ):
        """Viewer role cannot create labs."""
        from app.auth import create_access_token, hash_password

        viewer = models.User(
            username="viewer1",
            email="viewer@example.com",
            hashed_password=hash_password("pw123456"),
            is_active=True,
            global_role="viewer",
        )
        test_db.add(viewer)
        test_db.commit()
        test_db.refresh(viewer)
        token = create_access_token(viewer.id)
        headers = {"Authorization": f"Bearer {token}"}

        resp = test_client.post(
            "/labs", json={"name": "Blocked"}, headers=headers
        )
        assert resp.status_code == 403


class TestGetLab:
    """Tests for GET /labs/{id} endpoint."""

    def test_get_lab_returns_detail(
        self, test_client: TestClient, auth_headers: dict, sample_lab: models.Lab
    ):
        """Get lab by ID returns full detail."""
        resp = test_client.get(f"/labs/{sample_lab.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sample_lab.id
        assert data["name"] == "Test Lab"
        assert "node_count" in data
        assert "running_count" in data

    def test_get_lab_404_missing(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Get non-existent lab returns 403 or 404."""
        resp = test_client.get("/labs/nonexistent-id", headers=auth_headers)
        assert resp.status_code in (403, 404)

    def test_get_lab_includes_counts(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab_with_nodes: tuple,
    ):
        """Get lab includes node_count and running_count fields."""
        lab, nodes = sample_lab_with_nodes
        with patch("app.routers.labs.get_config_by_device", return_value=None):
            resp = test_client.get(f"/labs/{lab.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "node_count" in data
        assert "running_count" in data
        assert "container_count" in data
        assert "vm_count" in data


class TestListLabs:
    """Tests for GET /labs endpoint."""

    def test_list_labs_returns_all(
        self, test_client: TestClient, auth_headers: dict, sample_lab: models.Lab
    ):
        """List labs returns at least the sample lab."""
        resp = test_client.get("/labs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "labs" in data
        lab_ids = [lab["id"] for lab in data["labs"]]
        assert sample_lab.id in lab_ids

    def test_list_labs_empty(
        self, test_client: TestClient, auth_headers: dict
    ):
        """List labs with no labs returns empty list."""
        resp = test_client.get("/labs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "labs" in data

    def test_list_labs_pagination(
        self, test_client: TestClient, auth_headers: dict, test_db: Session, test_user: models.User
    ):
        """List labs respects skip and limit parameters."""
        for i in range(5):
            lab = models.Lab(
                name=f"Lab {i}", owner_id=test_user.id, provider="docker"
            )
            test_db.add(lab)
        test_db.commit()

        resp = test_client.get("/labs?skip=0&limit=2", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["labs"]) <= 2

    def test_list_labs_requires_auth(self, test_client: TestClient):
        """List labs without auth returns 401."""
        resp = test_client.get("/labs")
        assert resp.status_code == 401


class TestUpdateTopology:
    """Tests for POST /labs/{lab_id}/update-topology endpoint."""

    def test_update_topology_syncs_nodes(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Updating topology from graph JSON syncs nodes and links."""
        payload = {
            "nodes": [
                {"id": "n1", "name": "R1", "device": "linux"},
                {"id": "n2", "name": "R2", "device": "linux"},
            ],
            "links": [
                {
                    "endpoints": [
                        {"node": "R1", "ifname": "eth1"},
                        {"node": "R2", "ifname": "eth1"},
                    ]
                }
            ],
        }
        with patch("app.routers.labs.process_link_changes", new_callable=AsyncMock):
            with patch("app.routers.labs.process_node_changes", new_callable=AsyncMock):
                resp = test_client.post(
                    f"/labs/{sample_lab.id}/update-topology",
                    json=payload,
                    headers=auth_headers,
                )
        assert resp.status_code == 200

    def test_update_topology_lab_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Update topology for nonexistent lab returns error."""
        payload = {"nodes": [], "links": []}
        resp = test_client.post(
            "/labs/nonexistent/update-topology",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code in (403, 404)


class TestDeleteLab:
    """Tests for DELETE /labs/{id} endpoint."""

    def test_delete_lab_success(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Delete a stopped lab succeeds."""
        resp = test_client.delete(
            f"/labs/{sample_lab.id}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_lab_running_destroys_first(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Deleting a running lab triggers destroy before deletion."""
        sample_lab.state = "running"
        test_db.commit()

        with patch(
            "app.routers.labs.run_agent_job",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.routers.labs.get_online_agent_for_lab",
                new_callable=AsyncMock,
                return_value=None,
            ):
                resp = test_client.delete(
                    f"/labs/{sample_lab.id}", headers=auth_headers
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_lab_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Delete nonexistent lab returns error."""
        resp = test_client.delete("/labs/nonexistent", headers=auth_headers)
        assert resp.status_code in (403, 404)


class TestLabUp:
    """Tests for POST /labs/{lab_id}/up endpoint."""

    def test_lab_up_enqueues_job(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_node_definitions: list,
    ):
        """Deploy creates a queued job and returns JobOut."""
        with patch(
            "app.routers.jobs.has_conflicting_job", return_value=(False, None)
        ), patch(
            "app.routers.jobs.run_agent_job", new_callable=AsyncMock
        ), patch(
            "app.routers.jobs.get_agent_providers", return_value=["docker"]
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/up", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "up"
        assert data["status"] == "queued"

    def test_lab_up_409_conflicting_job(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_node_definitions: list,
    ):
        """Deploy returns 409 when a conflicting job exists."""
        with patch(
            "app.routers.jobs.has_conflicting_job",
            return_value=(True, "up"),
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/up", headers=auth_headers
            )
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"]

    def test_lab_up_no_topology(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Deploy returns 400 if no topology is defined."""
        with patch(
            "app.routers.jobs.has_conflicting_job", return_value=(False, None)
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/up", headers=auth_headers
            )
        assert resp.status_code == 400
        assert "topology" in resp.json()["detail"].lower()

    def test_lab_up_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab
    ):
        """Deploy without auth returns 401."""
        resp = test_client.post(f"/labs/{sample_lab.id}/up")
        assert resp.status_code == 401


class TestLabDown:
    """Tests for POST /labs/{lab_id}/down endpoint."""

    def test_lab_down_enqueues_job(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Destroy creates a queued job."""
        with patch(
            "app.routers.jobs.has_conflicting_job", return_value=(False, None)
        ), patch(
            "app.routers.jobs.run_agent_job", new_callable=AsyncMock
        ), patch(
            "app.routers.jobs.get_agent_providers", return_value=["docker"]
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/down", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "down"
        assert data["status"] == "queued"

    def test_lab_down_409_conflicting(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Destroy returns 409 when conflicting job exists."""
        with patch(
            "app.routers.jobs.has_conflicting_job",
            return_value=(True, "up"),
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/down", headers=auth_headers
            )
        assert resp.status_code == 409


class TestLabRestart:
    """Tests for POST /labs/{lab_id}/restart endpoint."""

    def test_lab_restart_enqueues_jobs(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_node_definitions: list,
    ):
        """Restart creates down+up jobs."""
        with patch(
            "app.routers.jobs.has_conflicting_job", return_value=(False, None)
        ), patch(
            "app.routers.jobs.run_agent_job", new_callable=AsyncMock
        ), patch(
            "app.routers.jobs.get_agent_providers", return_value=["docker"]
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/restart", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "down"
        assert data["status"] == "queued"


class TestExportTopology:
    """Tests for GET /labs/{lab_id}/export-yaml endpoint."""

    def test_export_yaml_success(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_node_definitions: list,
    ):
        """Export YAML returns topology content."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/export-yaml", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data

    def test_export_yaml_no_topology(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Export YAML with no topology returns 404."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/export-yaml", headers=auth_headers
        )
        assert resp.status_code == 404


class TestImportTopology:
    """Tests for POST /labs/{lab_id}/update-topology-from-yaml endpoint."""

    def test_import_from_yaml_success(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Import topology from YAML updates the lab."""
        yaml_content = "nodes:\n  r1:\n    device: linux\n  r2:\n    device: linux\nlinks:\n  - r1: {}\n    r2: {}\n"
        with patch(
            "app.routers.labs.process_link_changes", new_callable=AsyncMock
        ), patch(
            "app.routers.labs.process_node_changes", new_callable=AsyncMock
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/update-topology-from-yaml",
                json={"content": yaml_content},
                headers=auth_headers,
            )
        assert resp.status_code == 200

    def test_import_from_yaml_invalid(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Import invalid YAML returns error."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/update-topology-from-yaml",
            json={"content": "not: valid: yaml: ["},
            headers=auth_headers,
        )
        assert resp.status_code in (400, 422, 500)


class TestCloneLab:
    """Tests for POST /labs/{lab_id}/clone endpoint."""

    def test_clone_lab_success(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Clone creates a new lab with (copy) suffix."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/clone", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "(copy)" in data["name"]
        assert data["id"] != sample_lab.id
