"""Tests for permissions router endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.auth import hash_password


class TestListPermissions:
    """Tests for GET /labs/{lab_id}/permissions."""

    def test_list_permissions_empty(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test listing permissions when lab has none."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/permissions", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "permissions" in data
        assert data["permissions"] == []

    def test_list_permissions_with_shared_users(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test listing permissions returns shared users."""
        # Add a permission for admin user
        permission = models.Permission(
            lab_id=sample_lab.id, user_id=admin_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/permissions", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["permissions"]) == 1
        assert data["permissions"][0]["user_id"] == admin_user.id
        assert data["permissions"][0]["role"] == "viewer"
        assert data["permissions"][0]["user_email"] == admin_user.email

    def test_list_permissions_lab_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Test listing permissions for non-existent lab returns 404."""
        response = test_client.get(
            "/labs/nonexistent-lab-id/permissions", headers=auth_headers
        )
        assert response.status_code == 404

    def test_list_permissions_unauthenticated(
        self, test_client: TestClient, sample_lab: models.Lab
    ):
        """Test listing permissions without auth fails."""
        response = test_client.get(f"/labs/{sample_lab.id}/permissions")
        assert response.status_code == 401

    def test_list_permissions_as_viewer(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        test_user: models.User,
        auth_headers: dict,
        monkeypatch,
    ):
        """Test viewer can list permissions for labs they have access to."""
        from app.config import settings
        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")

        # Create lab owned by admin
        lab = models.Lab(
            name="Admin Lab", owner_id=admin_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.flush()

        # Share with test user as viewer
        permission = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        # Test user should be able to list permissions
        response = test_client.get(f"/labs/{lab.id}/permissions", headers=auth_headers)
        assert response.status_code == 200


class TestAddPermission:
    """Tests for POST /labs/{lab_id}/permissions."""

    def test_add_permission_as_owner(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test owner can add permissions."""
        response = test_client.post(
            f"/labs/{sample_lab.id}/permissions",
            json={"user_identifier": admin_user.email, "role": "editor"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == admin_user.id
        assert data["role"] == "editor"
        assert data["lab_id"] == sample_lab.id
        assert data["user_email"] == admin_user.email

    def test_add_permission_as_admin(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        admin_auth_headers: dict,
    ):
        """Test admin can add permissions to any lab."""
        # Create lab owned by regular user
        lab = models.Lab(
            name="User Lab", owner_id=test_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.commit()

        # Create another user to share with
        other_user = models.User(
            username="otheruser",
            email="other@example.com",
            hashed_password=hash_password("password123"),
            is_active=True,
            global_role="operator",
        )
        test_db.add(other_user)
        test_db.commit()

        response = test_client.post(
            f"/labs/{lab.id}/permissions",
            json={"user_identifier": other_user.email, "role": "viewer"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200

    def test_add_permission_forbidden_for_viewer(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test viewer cannot add permissions."""
        # Create lab owned by admin
        lab = models.Lab(
            name="Admin Lab", owner_id=admin_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.flush()

        # Share with test user as viewer
        permission = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        # Create another user
        other_user = models.User(
            username="otheruser",
            email="other@example.com",
            hashed_password=hash_password("password123"),
            is_active=True,
        )
        test_db.add(other_user)
        test_db.commit()

        response = test_client.post(
            f"/labs/{lab.id}/permissions",
            json={"user_identifier": other_user.email, "role": "viewer"},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_add_permission_user_not_found(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test adding permission for non-existent user fails."""
        response = test_client.post(
            f"/labs/{sample_lab.id}/permissions",
            json={"user_identifier": "nonexistent@example.com", "role": "viewer"},
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert "User not found" in response.json()["detail"]

    def test_add_permission_lab_not_found(
        self, test_client: TestClient, admin_user: models.User, auth_headers: dict
    ):
        """Test adding permission to non-existent lab fails."""
        response = test_client.post(
            "/labs/nonexistent-lab-id/permissions",
            json={"user_identifier": admin_user.email, "role": "viewer"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_add_permission_different_roles(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test adding permissions with different roles."""
        for role in ["viewer", "editor", "admin"]:
            user = models.User(
                username=f"{role}user",
                email=f"{role}user@example.com",
                hashed_password=hash_password("password123"),
                is_active=True,
            )
            test_db.add(user)
            test_db.commit()

            response = test_client.post(
                f"/labs/{sample_lab.id}/permissions",
                json={"user_identifier": user.email, "role": role},
                headers=auth_headers,
            )
            assert response.status_code == 200
            assert response.json()["role"] == role


class TestDeletePermission:
    """Tests for DELETE /labs/{lab_id}/permissions/{permission_id}."""

    def test_delete_permission_as_owner(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test owner can delete permissions."""
        # Add a permission
        permission = models.Permission(
            lab_id=sample_lab.id, user_id=admin_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()
        test_db.refresh(permission)

        response = test_client.delete(
            f"/labs/{sample_lab.id}/permissions/{permission.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # Verify permission is deleted
        deleted = test_db.get(models.Permission, permission.id)
        assert deleted is None

    def test_delete_permission_as_admin(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        admin_auth_headers: dict,
    ):
        """Test admin can delete permissions from any lab."""
        # Create lab owned by regular user
        lab = models.Lab(
            name="User Lab", owner_id=test_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.flush()

        # Add permission
        other_user = models.User(
            username="otheruser",
            email="other@example.com",
            hashed_password=hash_password("password123"),
            is_active=True,
        )
        test_db.add(other_user)
        test_db.flush()

        permission = models.Permission(
            lab_id=lab.id, user_id=other_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()
        test_db.refresh(permission)

        response = test_client.delete(
            f"/labs/{lab.id}/permissions/{permission.id}",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200

    def test_delete_permission_forbidden_for_viewer(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test viewer cannot delete permissions."""
        # Create lab owned by admin
        lab = models.Lab(
            name="Admin Lab", owner_id=admin_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.flush()

        # Share with test user as viewer
        viewer_perm = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(viewer_perm)
        test_db.flush()

        # Create another permission to try to delete
        other_user = models.User(
            username="otheruser2",
            email="other@example.com",
            hashed_password=hash_password("password123"),
            is_active=True,
        )
        test_db.add(other_user)
        test_db.flush()

        other_perm = models.Permission(
            lab_id=lab.id, user_id=other_user.id, role="editor"
        )
        test_db.add(other_perm)
        test_db.commit()
        test_db.refresh(other_perm)

        response = test_client.delete(
            f"/labs/{lab.id}/permissions/{other_perm.id}",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_delete_permission_not_found(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test deleting non-existent permission fails."""
        response = test_client.delete(
            f"/labs/{sample_lab.id}/permissions/nonexistent-id",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_permission_wrong_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test deleting permission with wrong lab ID fails."""
        # Create another lab
        other_lab = models.Lab(
            name="Other Lab", owner_id=test_user.id, provider="docker"
        )
        test_db.add(other_lab)
        test_db.flush()

        # Add permission to other lab
        permission = models.Permission(
            lab_id=other_lab.id, user_id=admin_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()
        test_db.refresh(permission)

        # Try to delete using sample_lab's endpoint
        response = test_client.delete(
            f"/labs/{sample_lab.id}/permissions/{permission.id}",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestPermissionAccessControl:
    """Tests for access control based on permissions."""

    def test_viewer_can_access_shared_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test that a viewer can access a lab shared with them."""
        # Create lab owned by admin
        lab = models.Lab(
            name="Shared Lab", owner_id=admin_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.flush()

        # Share with test user
        permission = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        # Test user should be able to access the lab
        response = test_client.get(f"/labs/{lab.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == lab.id

    def test_non_shared_user_cannot_access_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test that a user without permission cannot access a private lab."""
        # Create lab owned by admin (not shared with test_user)
        lab = models.Lab(
            name="Private Lab", owner_id=admin_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.commit()

        # Test user should NOT be able to access the lab
        response = test_client.get(f"/labs/{lab.id}", headers=auth_headers)
        assert response.status_code == 403  # Access denied

    def test_shared_lab_appears_in_list(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test that shared labs appear in user's lab list."""
        # Create lab owned by admin
        lab = models.Lab(
            name="Shared Lab", owner_id=admin_user.id, provider="docker"
        )
        test_db.add(lab)
        test_db.flush()

        # Share with test user
        permission = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        # Lab should appear in test user's list
        response = test_client.get("/labs", headers=auth_headers)
        assert response.status_code == 200
        lab_ids = [lab_item["id"] for lab_item in response.json()["labs"]]
        assert lab.id in lab_ids
