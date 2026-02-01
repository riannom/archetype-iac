"""Tests for lab utility functions (utils/lab.py).

This module tests:
- Lab provider retrieval (get_lab_provider)
- Lab access control (get_lab_or_404)
- Lab state updates (update_lab_state)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.utils.lab import get_lab_or_404, get_lab_provider, update_lab_state


class TestGetLabProvider:
    """Tests for get_lab_provider function."""

    def test_returns_lab_provider_when_set(self):
        """Returns the lab's configured provider."""
        lab = MagicMock(spec=models.Lab)
        lab.provider = "docker"
        assert get_lab_provider(lab) == "docker"

    def test_returns_docker_when_provider_is_none(self):
        """Returns 'docker' when provider is None."""
        lab = MagicMock(spec=models.Lab)
        lab.provider = None
        assert get_lab_provider(lab) == "docker"

    def test_returns_docker_when_provider_is_empty(self):
        """Returns 'docker' when provider is empty string."""
        lab = MagicMock(spec=models.Lab)
        lab.provider = ""
        assert get_lab_provider(lab) == "docker"

    def test_returns_custom_provider(self):
        """Returns custom provider values."""
        lab = MagicMock(spec=models.Lab)
        lab.provider = "libvirt"
        assert get_lab_provider(lab) == "libvirt"


class TestGetLabOr404:
    """Tests for get_lab_or_404 function."""

    def test_raises_404_when_lab_not_found(self, test_db: Session, test_user: models.User):
        """Raises HTTPException 404 when lab doesn't exist."""
        with pytest.raises(HTTPException) as exc_info:
            get_lab_or_404("nonexistent-lab-id", test_db, test_user)

        assert exc_info.value.status_code == 404
        assert "Lab not found" in exc_info.value.detail

    def test_owner_can_access_lab(
        self, test_db: Session, test_user: models.User, sample_lab: models.Lab
    ):
        """Lab owner can access their lab."""
        result = get_lab_or_404(sample_lab.id, test_db, test_user)
        assert result.id == sample_lab.id

    def test_admin_can_access_any_lab(
        self, test_db: Session, sample_lab: models.Lab, admin_user: models.User
    ):
        """Admin can access any lab."""
        result = get_lab_or_404(sample_lab.id, test_db, admin_user)
        assert result.id == sample_lab.id

    def test_non_owner_without_permission_denied(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Non-owner without permission gets 403."""
        # Create a different user
        other_user = models.User(
            email="other@example.com",
            hashed_password="hashed",
            is_active=True,
            is_admin=False,
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        with pytest.raises(HTTPException) as exc_info:
            get_lab_or_404(sample_lab.id, test_db, other_user)

        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail

    def test_user_with_permission_can_access(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """User with permission grant can access lab."""
        # Create a different user
        other_user = models.User(
            email="permitted@example.com",
            hashed_password="hashed",
            is_active=True,
            is_admin=False,
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        # Grant permission
        permission = models.Permission(
            lab_id=sample_lab.id,
            user_id=other_user.id,
            role="viewer",
        )
        test_db.add(permission)
        test_db.commit()

        result = get_lab_or_404(sample_lab.id, test_db, other_user)
        assert result.id == sample_lab.id

    def test_permission_for_different_lab_denied(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Permission for different lab doesn't grant access."""
        # Create another lab owned by test_user
        other_lab = models.Lab(
            name="Other Lab",
            owner_id=test_user.id,
            provider="docker",
        )
        test_db.add(other_lab)
        test_db.commit()
        test_db.refresh(other_lab)

        # Create a different user
        other_user = models.User(
            email="other2@example.com",
            hashed_password="hashed",
            is_active=True,
            is_admin=False,
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        # Grant permission to other_lab, not sample_lab
        permission = models.Permission(
            lab_id=other_lab.id,
            user_id=other_user.id,
            role="viewer",
        )
        test_db.add(permission)
        test_db.commit()

        # Should not be able to access sample_lab
        with pytest.raises(HTTPException) as exc_info:
            get_lab_or_404(sample_lab.id, test_db, other_user)

        assert exc_info.value.status_code == 403

    def test_inactive_user_with_ownership(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Inactive user can still access owned lab (auth handled elsewhere)."""
        test_user.is_active = False
        test_db.commit()

        # The function doesn't check is_active, that's handled by auth middleware
        result = get_lab_or_404(sample_lab.id, test_db, test_user)
        assert result.id == sample_lab.id


class TestGetLabOr404EdgeCases:
    """Edge case tests for get_lab_or_404."""

    def test_empty_lab_id(self, test_db: Session, test_user: models.User):
        """Empty lab ID returns 404."""
        with pytest.raises(HTTPException) as exc_info:
            get_lab_or_404("", test_db, test_user)

        assert exc_info.value.status_code == 404

    def test_none_lab_id_behavior(self, test_db: Session, test_user: models.User):
        """None lab ID is handled (returns 404)."""
        # SQLAlchemy will handle None appropriately
        with pytest.raises(HTTPException) as exc_info:
            get_lab_or_404(None, test_db, test_user)  # type: ignore

        assert exc_info.value.status_code == 404

    def test_multiple_permissions_for_same_lab(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Multiple permission records for same lab/user still grants access."""
        other_user = models.User(
            email="multi@example.com",
            hashed_password="hashed",
            is_active=True,
            is_admin=False,
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        # Add multiple permissions (shouldn't happen, but handle gracefully)
        perm1 = models.Permission(lab_id=sample_lab.id, user_id=other_user.id, role="viewer")
        perm2 = models.Permission(lab_id=sample_lab.id, user_id=other_user.id, role="editor")
        test_db.add(perm1)
        test_db.add(perm2)
        test_db.commit()

        result = get_lab_or_404(sample_lab.id, test_db, other_user)
        assert result.id == sample_lab.id

    def test_lab_without_owner(self, test_db: Session, admin_user: models.User):
        """Lab without owner can be accessed by admin."""
        orphan_lab = models.Lab(
            name="Orphan Lab",
            owner_id=None,
            provider="docker",
        )
        test_db.add(orphan_lab)
        test_db.commit()
        test_db.refresh(orphan_lab)

        result = get_lab_or_404(orphan_lab.id, test_db, admin_user)
        assert result.id == orphan_lab.id

    def test_lab_without_owner_non_admin_denied(self, test_db: Session, test_user: models.User):
        """Lab without owner cannot be accessed by non-admin without permission."""
        orphan_lab = models.Lab(
            name="Orphan Lab 2",
            owner_id=None,
            provider="docker",
        )
        test_db.add(orphan_lab)
        test_db.commit()
        test_db.refresh(orphan_lab)

        with pytest.raises(HTTPException) as exc_info:
            get_lab_or_404(orphan_lab.id, test_db, test_user)

        assert exc_info.value.status_code == 403


class TestUpdateLabState:
    """Tests for update_lab_state function."""

    def test_updates_lab_state(self, test_db: Session, sample_lab: models.Lab):
        """Updates lab state correctly."""
        update_lab_state(test_db, sample_lab.id, "running")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "running"

    def test_updates_state_updated_at(self, test_db: Session, sample_lab: models.Lab):
        """Updates state_updated_at timestamp."""
        before = datetime.now(timezone.utc)
        update_lab_state(test_db, sample_lab.id, "running")

        test_db.refresh(sample_lab)
        assert sample_lab.state_updated_at is not None
        assert sample_lab.state_updated_at >= before

    def test_updates_agent_id(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Updates agent_id when provided."""
        update_lab_state(test_db, sample_lab.id, "running", agent_id=sample_host.id)

        test_db.refresh(sample_lab)
        assert sample_lab.agent_id == sample_host.id

    def test_does_not_update_agent_id_when_none(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Preserves existing agent_id when not provided."""
        # Set initial agent
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "stopping")

        test_db.refresh(sample_lab)
        assert sample_lab.agent_id == sample_host.id  # Preserved

    def test_updates_error_message(self, test_db: Session, sample_lab: models.Lab):
        """Sets error message when provided."""
        update_lab_state(test_db, sample_lab.id, "error", error="Deployment failed")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "error"
        assert sample_lab.state_error == "Deployment failed"

    def test_clears_error_on_success_state(self, test_db: Session, sample_lab: models.Lab):
        """Clears state_error on non-error states."""
        # Set initial error
        sample_lab.state = "error"
        sample_lab.state_error = "Previous error"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "running")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "running"
        assert sample_lab.state_error is None

    def test_preserves_error_on_error_state(self, test_db: Session, sample_lab: models.Lab):
        """Preserves error message when state is 'error'."""
        sample_lab.state_error = "Existing error"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "error")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "error"
        assert sample_lab.state_error == "Existing error"

    def test_preserves_error_on_unknown_state(self, test_db: Session, sample_lab: models.Lab):
        """Preserves error message when state is 'unknown'."""
        sample_lab.state_error = "Unknown state error"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "unknown")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "unknown"
        assert sample_lab.state_error == "Unknown state error"

    def test_replaces_error_when_explicit_error_provided(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Explicit error parameter replaces existing error."""
        sample_lab.state_error = "Old error"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "error", error="New error")

        test_db.refresh(sample_lab)
        assert sample_lab.state_error == "New error"

    def test_no_op_for_nonexistent_lab(self, test_db: Session):
        """Does nothing for nonexistent lab (no error raised)."""
        # Should not raise an exception
        update_lab_state(test_db, "nonexistent-lab-id", "running")


class TestUpdateLabStateTransitions:
    """Tests for state transition scenarios."""

    def test_stopped_to_starting(self, test_db: Session, sample_lab: models.Lab):
        """Transition from stopped to starting."""
        sample_lab.state = "stopped"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "starting")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "starting"
        assert sample_lab.state_error is None

    def test_starting_to_running(self, test_db: Session, sample_lab: models.Lab):
        """Transition from starting to running."""
        sample_lab.state = "starting"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "running")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "running"

    def test_running_to_stopping(self, test_db: Session, sample_lab: models.Lab):
        """Transition from running to stopping."""
        sample_lab.state = "running"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "stopping")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "stopping"

    def test_stopping_to_stopped(self, test_db: Session, sample_lab: models.Lab):
        """Transition from stopping to stopped."""
        sample_lab.state = "stopping"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "stopped")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "stopped"

    def test_any_state_to_error(self, test_db: Session, sample_lab: models.Lab):
        """Any state can transition to error."""
        states = ["stopped", "starting", "running", "stopping", "unknown"]

        for initial_state in states:
            sample_lab.state = initial_state
            sample_lab.state_error = None
            test_db.commit()

            update_lab_state(test_db, sample_lab.id, "error", error=f"Error from {initial_state}")

            test_db.refresh(sample_lab)
            assert sample_lab.state == "error"
            assert f"Error from {initial_state}" in sample_lab.state_error

    def test_error_to_stopped(self, test_db: Session, sample_lab: models.Lab):
        """Error state can transition to stopped (recovery)."""
        sample_lab.state = "error"
        sample_lab.state_error = "Previous failure"
        test_db.commit()

        update_lab_state(test_db, sample_lab.id, "stopped")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "stopped"
        assert sample_lab.state_error is None  # Cleared


class TestUpdateLabStateEdgeCases:
    """Edge case tests for update_lab_state."""

    def test_empty_state_string(self, test_db: Session, sample_lab: models.Lab):
        """Empty state string is set (caller's responsibility to validate)."""
        update_lab_state(test_db, sample_lab.id, "")

        test_db.refresh(sample_lab)
        assert sample_lab.state == ""

    def test_empty_error_string(self, test_db: Session, sample_lab: models.Lab):
        """Empty error string is set."""
        update_lab_state(test_db, sample_lab.id, "error", error="")

        test_db.refresh(sample_lab)
        assert sample_lab.state_error == ""

    def test_long_error_message(self, test_db: Session, sample_lab: models.Lab):
        """Long error messages are stored (within Text field limits)."""
        long_error = "Error: " + "x" * 10000
        update_lab_state(test_db, sample_lab.id, "error", error=long_error)

        test_db.refresh(sample_lab)
        assert sample_lab.state_error == long_error

    def test_unicode_error_message(self, test_db: Session, sample_lab: models.Lab):
        """Unicode characters in error message are handled."""
        unicode_error = "Error: Connection failed \u2013 timeout \u2014 retry"
        update_lab_state(test_db, sample_lab.id, "error", error=unicode_error)

        test_db.refresh(sample_lab)
        assert sample_lab.state_error == unicode_error

    def test_special_characters_in_state(self, test_db: Session, sample_lab: models.Lab):
        """Special characters in state string are handled."""
        update_lab_state(test_db, sample_lab.id, "custom_state-v2.1")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "custom_state-v2.1"

    def test_concurrent_updates(self, test_db: Session, sample_lab: models.Lab):
        """Sequential updates work correctly (simulated concurrency)."""
        update_lab_state(test_db, sample_lab.id, "starting")
        test_db.refresh(sample_lab)
        assert sample_lab.state == "starting"

        update_lab_state(test_db, sample_lab.id, "running")
        test_db.refresh(sample_lab)
        assert sample_lab.state == "running"

        update_lab_state(test_db, sample_lab.id, "stopping")
        test_db.refresh(sample_lab)
        assert sample_lab.state == "stopping"

    def test_all_parameters_together(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """All parameters can be updated together."""
        update_lab_state(
            test_db,
            sample_lab.id,
            "error",
            agent_id=sample_host.id,
            error="Complete failure with all params"
        )

        test_db.refresh(sample_lab)
        assert sample_lab.state == "error"
        assert sample_lab.agent_id == sample_host.id
        assert sample_lab.state_error == "Complete failure with all params"
        assert sample_lab.state_updated_at is not None


class TestUpdateLabStateWithMultipleLabs:
    """Tests for update_lab_state with multiple labs."""

    def test_updates_only_target_lab(
        self, test_db: Session, test_user: models.User, sample_lab: models.Lab
    ):
        """Update only affects the target lab."""
        # Create another lab
        other_lab = models.Lab(
            name="Other Lab",
            owner_id=test_user.id,
            state="stopped",
            provider="docker",
        )
        test_db.add(other_lab)
        test_db.commit()
        test_db.refresh(other_lab)

        # Update only sample_lab
        update_lab_state(test_db, sample_lab.id, "running")

        test_db.refresh(sample_lab)
        test_db.refresh(other_lab)

        assert sample_lab.state == "running"
        assert other_lab.state == "stopped"  # Unchanged

    def test_multiple_labs_independent_updates(
        self, test_db: Session, test_user: models.User, sample_lab: models.Lab
    ):
        """Multiple labs can be updated independently."""
        labs = [sample_lab]
        for i in range(3):
            lab = models.Lab(
                name=f"Lab {i}",
                owner_id=test_user.id,
                state="stopped",
                provider="docker",
            )
            test_db.add(lab)
            labs.append(lab)
        test_db.commit()
        for lab in labs:
            test_db.refresh(lab)

        # Update each lab to different states
        states = ["running", "starting", "error", "stopping"]
        for lab, state in zip(labs, states):
            update_lab_state(test_db, lab.id, state)

        for lab, expected_state in zip(labs, states):
            test_db.refresh(lab)
            assert lab.state == expected_state
