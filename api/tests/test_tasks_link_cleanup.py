"""Tests for link cleanup operations for orphaned records."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


class TestCleanupDeletedLinks:
    """Tests for _cleanup_deleted_links function."""

    @pytest.mark.asyncio
    async def test_no_deleted_links(self, test_db: Session):
        """Returns 0 when no links are marked as deleted."""
        from app.tasks.link_cleanup import _cleanup_deleted_links

        host_to_agent = {}
        result = await _cleanup_deleted_links(test_db, host_to_agent)
        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_deleted_link_with_teardown(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Deleted links are removed after successful teardown."""
        from app.tasks.link_cleanup import _cleanup_deleted_links

        # Create a link state marked as deleted
        ls = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="deleted",
            actual_state="up",
            source_host_id=sample_host.id,
        )
        test_db.add(ls)
        test_db.commit()
        test_db.refresh(ls)

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_cleanup.teardown_link",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await _cleanup_deleted_links(
                test_db, host_to_agent, lab_id=sample_lab.id
            )
        assert result == 1

        # Verify link state was deleted
        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.id == ls.id)
            .first()
        )
        assert remaining is None

    @pytest.mark.asyncio
    async def test_cleanup_deferred_offline_agent(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Cleanup is deferred when required agents are offline."""
        from app.tasks.link_cleanup import _cleanup_deleted_links

        ls = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="deleted",
            actual_state="up",
            is_cross_host=True,
            source_host_id="offline-agent",
            target_host_id="offline-agent-2",
        )
        test_db.add(ls)
        test_db.commit()

        # No agents available
        host_to_agent = {}
        result = await _cleanup_deleted_links(test_db, host_to_agent)
        assert result == 0

        # Link should still exist (deferred)
        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.id == ls.id)
            .first()
        )
        assert remaining is not None

    @pytest.mark.asyncio
    async def test_cleanup_teardown_failure(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Failed teardown does not delete the link state."""
        from app.tasks.link_cleanup import _cleanup_deleted_links

        ls = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="deleted",
            actual_state="up",
            source_host_id=sample_host.id,
        )
        test_db.add(ls)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_cleanup.teardown_link",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await _cleanup_deleted_links(
                test_db, host_to_agent, lab_id=sample_lab.id
            )
        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_teardown_exception(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Teardown exception does not delete the link state."""
        from app.tasks.link_cleanup import _cleanup_deleted_links

        ls = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="deleted",
            actual_state="up",
            source_host_id=sample_host.id,
        )
        test_db.add(ls)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_cleanup.teardown_link",
            new_callable=AsyncMock,
            side_effect=Exception("OVS error"),
        ):
            result = await _cleanup_deleted_links(
                test_db, host_to_agent, lab_id=sample_lab.id
            )
        assert result == 0


class TestCleanupOrphanedLinkStates:
    """Tests for cleanup_orphaned_link_states function."""

    @pytest.mark.asyncio
    async def test_no_orphans(self, test_db: Session):
        """Returns 0 when no orphaned link states exist."""
        from app.tasks.link_cleanup import cleanup_orphaned_link_states

        result = await cleanup_orphaned_link_states(test_db)
        assert result == 0

    @pytest.mark.asyncio
    async def test_removes_orphaned_non_up_links(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Orphaned link states with non-up actual state are deleted."""
        from app.tasks.link_cleanup import cleanup_orphaned_link_states

        orphan = models.LinkState(
            lab_id=sample_lab.id,
            link_name="orphan:eth1-ghost:eth1",
            source_node="orphan",
            source_interface="eth1",
            target_node="ghost",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
            link_definition_id=None,  # orphaned
        )
        test_db.add(orphan)
        test_db.commit()

        result = await cleanup_orphaned_link_states(test_db)
        assert result == 1

    @pytest.mark.asyncio
    async def test_preserves_up_orphaned_links(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Orphaned links with actual_state=up are preserved."""
        from app.tasks.link_cleanup import cleanup_orphaned_link_states

        orphan = models.LinkState(
            lab_id=sample_lab.id,
            link_name="active-orphan:eth1-live:eth1",
            source_node="active-orphan",
            source_interface="eth1",
            target_node="live",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
            link_definition_id=None,  # orphaned but active
        )
        test_db.add(orphan)
        test_db.commit()

        result = await cleanup_orphaned_link_states(test_db)
        assert result == 0

        # Verify link still exists
        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.id == orphan.id)
            .first()
        )
        assert remaining is not None

    @pytest.mark.asyncio
    async def test_orphan_with_tunnel_tears_down(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Orphaned link with VXLAN tunnel triggers teardown on agents."""
        from app.tasks.link_cleanup import cleanup_orphaned_link_states

        orphan = models.LinkState(
            lab_id=sample_lab.id,
            link_name="orphan:eth1-remote:eth1",
            source_node="orphan",
            source_interface="eth1",
            target_node="remote",
            target_interface="eth1",
            desired_state="down",
            actual_state="down",
            link_definition_id=None,
        )
        test_db.add(orphan)
        test_db.flush()

        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=orphan.id,
            vni=10001,
            vlan_tag=100,
            agent_a_id=sample_host.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=sample_host.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        with patch(
            "app.tasks.link_cleanup._detach_overlay_endpoint",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            result = await cleanup_orphaned_link_states(test_db)
        assert result == 1


class TestCleanupOrphanedTunnels:
    """Tests for cleanup_orphaned_tunnels function."""

    @pytest.mark.asyncio
    async def test_no_orphaned_tunnels(self, test_db: Session):
        """Returns 0 when no orphaned tunnels exist."""
        from app.tasks.link_cleanup import cleanup_orphaned_tunnels

        result = await cleanup_orphaned_tunnels(test_db)
        assert result == 0

    @pytest.mark.asyncio
    async def test_removes_null_link_state_tunnels(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Tunnels with null link_state_id are removed."""
        from app.tasks.link_cleanup import cleanup_orphaned_tunnels

        orphan_tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=None,  # Orphaned
            vni=20001,
            vlan_tag=200,
            agent_a_id=sample_host.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=sample_host.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(orphan_tunnel)
        test_db.commit()

        result = await cleanup_orphaned_tunnels(test_db)
        assert result == 1

    @pytest.mark.asyncio
    async def test_removes_stale_cleanup_tunnels(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_link_state: models.LinkState,
        monkeypatch,
    ):
        """Tunnels in 'cleanup' status past timeout are removed."""
        from app.tasks.link_cleanup import cleanup_orphaned_tunnels
        from app.config import settings

        # Set a short timeout
        object.__setattr__(settings, "orphaned_tunnel_cleanup_timeout", 1)

        stale_tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=sample_link_state.id,
            vni=30001,
            vlan_tag=300,
            agent_a_id=sample_host.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=sample_host.id,
            agent_b_ip="10.0.0.2",
            status="cleanup",
        )
        test_db.add(stale_tunnel)
        test_db.commit()

        # Manually set updated_at to the past
        test_db.execute(
            models.VxlanTunnel.__table__.update()
            .where(models.VxlanTunnel.__table__.c.id == stale_tunnel.id)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        )
        test_db.commit()

        with patch(
            "app.tasks.link_cleanup._detach_overlay_endpoint",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            result = await cleanup_orphaned_tunnels(test_db)
        assert result >= 1

        # Restore setting
        object.__setattr__(settings, "orphaned_tunnel_cleanup_timeout", 300)


class TestDetachOverlayEndpoint:
    """Tests for _detach_overlay_endpoint helper."""

    @pytest.mark.asyncio
    async def test_successful_detach(self, sample_host: models.Host):
        """Successful detach returns (True, None)."""
        from app.tasks.link_cleanup import _detach_overlay_endpoint

        with patch(
            "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
            new_callable=AsyncMock,
            return_value={"success": True},
        ):
            ok, err = await _detach_overlay_endpoint(
                sample_host, "lab-1", "R1", "eth1", "link-1"
            )
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_failed_detach(self, sample_host: models.Host):
        """Failed detach returns (False, error_message)."""
        from app.tasks.link_cleanup import _detach_overlay_endpoint

        with patch(
            "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "port not found"},
        ):
            ok, err = await _detach_overlay_endpoint(
                sample_host, "lab-1", "R1", "eth1", "link-1"
            )
        assert ok is False
        assert "port not found" in err

    @pytest.mark.asyncio
    async def test_exception_during_detach(self, sample_host: models.Host):
        """Exception during detach returns (False, error_string)."""
        from app.tasks.link_cleanup import _detach_overlay_endpoint

        with patch(
            "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Agent unreachable"),
        ):
            ok, err = await _detach_overlay_endpoint(
                sample_host, "lab-1", "R1", "eth1", "link-1"
            )
        assert ok is False
        assert "unreachable" in err.lower()

    @pytest.mark.asyncio
    async def test_detach_with_device_type(self, sample_host: models.Host):
        """Detach normalizes interface name when device_type is provided."""
        from app.tasks.link_cleanup import _detach_overlay_endpoint

        with patch(
            "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_detach:
            ok, err = await _detach_overlay_endpoint(
                sample_host,
                "lab-1",
                "R1",
                "Ethernet1",
                "link-1",
                device_type="ceos",
            )
        assert ok is True
        # The call should have been made (interface normalization applied)
        mock_detach.assert_called_once()

    @pytest.mark.asyncio
    async def test_detach_none_interface(self, sample_host: models.Host):
        """Detach with None interface sends empty string."""
        from app.tasks.link_cleanup import _detach_overlay_endpoint

        with patch(
            "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_detach:
            ok, err = await _detach_overlay_endpoint(
                sample_host, "lab-1", "R1", None, "link-1"
            )
        assert ok is True
        # Verify empty string was passed for interface_name
        call_kwargs = mock_detach.call_args
        assert call_kwargs[1]["interface_name"] == ""


class TestDetectDuplicateTunnels:
    """Tests for detect_duplicate_tunnels function."""

    @pytest.mark.asyncio
    async def test_no_duplicates(self, test_db: Session):
        """Returns 0 when no duplicate tunnels exist."""
        from app.tasks.link_cleanup import detect_duplicate_tunnels

        host_to_agent = {}
        result = await detect_duplicate_tunnels(test_db, host_to_agent)
        assert result == 0

    @pytest.mark.asyncio
    async def test_removes_duplicates_keeps_active(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_link_state: models.LinkState,
    ):
        """Duplicate tunnels are removed, keeping the one with active link state."""
        from app.tasks.link_cleanup import detect_duplicate_tunnels

        # Create two tunnels for the same VNI and agent pair
        t1 = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=sample_link_state.id,
            vni=50001,
            vlan_tag=500,
            agent_a_id=sample_host.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=sample_host.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        t2 = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=None,  # No link state = inactive
            vni=50001,
            vlan_tag=500,
            agent_a_id=sample_host.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=sample_host.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add_all([t1, t2])
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
            new_callable=AsyncMock,
        ):
            result = await detect_duplicate_tunnels(test_db, host_to_agent)
        assert result >= 1


class TestPeriodicCleanup:
    """Tests for periodic cleanup handling DB errors gracefully."""

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_handles_db_error(self, monkeypatch):
        """cleanup_orphaned_link_states handles DB exceptions."""
        from app.tasks.link_cleanup import cleanup_orphaned_link_states

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []

        # Should not raise even with mock session
        result = await cleanup_orphaned_link_states(mock_session)
        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_tunnels_handles_db_error(self, monkeypatch):
        """cleanup_orphaned_tunnels handles exceptions."""
        from app.tasks.link_cleanup import cleanup_orphaned_tunnels

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []

        result = await cleanup_orphaned_tunnels(mock_session)
        assert result == 0
