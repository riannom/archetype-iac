"""Tests for private helpers in app/routers/callbacks.py and the heartbeat endpoint."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from tests.factories import make_host, make_lab, make_link_state, make_node_state


# ── Helpers ─────────────────────────────────────────────────────────────


# ── _auto_connect_pending_links ─────────────────────────────────────────


@pytest.mark.asyncio
class TestAutoConnectPendingLinks:
    """Tests for _auto_connect_pending_links()."""

    async def test_no_host_map_returns_early(self, test_db: Session, test_user: models.User):
        """When _build_host_to_agent_map returns empty, function exits."""
        from app.routers.callbacks import _auto_connect_pending_links

        lab = make_lab(test_db, test_user.id)

        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={},
        ):
            await _auto_connect_pending_links(test_db, lab.id, ["R1"])

    async def test_connects_pending_links_for_ready_nodes(
        self, test_db: Session, test_user: models.User,
    ):
        """Pending links involving newly-ready nodes are connected."""
        from app.routers.callbacks import _auto_connect_pending_links

        host = make_host(test_db)
        lab = make_lab(test_db, test_user.id)
        make_link_state(
            test_db, lab.id,
            source_node="R1", target_node="R2",
            desired_state="up", actual_state="pending",
        )

        mock_create = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={host.id: host},
        ):
            with patch(
                "app.routers.callbacks.create_link_if_ready",
                mock_create,
            ):
                await _auto_connect_pending_links(test_db, lab.id, ["R1"])

        mock_create.assert_awaited_once()
        # Verify it was called with the right link state
        call_args = mock_create.call_args
        assert call_args[0][1] == lab.id  # lab_id

    async def test_skips_links_not_involving_ready_nodes(
        self, test_db: Session, test_user: models.User,
    ):
        """Links not involving any newly-ready node are skipped."""
        from app.routers.callbacks import _auto_connect_pending_links

        host = make_host(test_db)
        lab = make_lab(test_db, test_user.id)
        make_link_state(
            test_db, lab.id,
            source_node="R3", target_node="R4",
            desired_state="up", actual_state="pending",
        )

        mock_create = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={host.id: host},
        ):
            with patch(
                "app.routers.callbacks.create_link_if_ready",
                mock_create,
            ):
                await _auto_connect_pending_links(test_db, lab.id, ["R1"])

        mock_create.assert_not_awaited()

    async def test_skips_links_with_desired_down(
        self, test_db: Session, test_user: models.User,
    ):
        """Links with desired_state=down are not auto-connected."""
        from app.routers.callbacks import _auto_connect_pending_links

        host = make_host(test_db)
        lab = make_lab(test_db, test_user.id)
        make_link_state(
            test_db, lab.id,
            source_node="R1", target_node="R2",
            desired_state="down", actual_state="pending",
        )

        mock_create = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={host.id: host},
        ):
            with patch(
                "app.routers.callbacks.create_link_if_ready",
                mock_create,
            ):
                await _auto_connect_pending_links(test_db, lab.id, ["R1"])

        mock_create.assert_not_awaited()

    async def test_handles_multiple_pending_states(
        self, test_db: Session, test_user: models.User,
    ):
        """Links in any of pending/unknown/down/error states are candidates."""
        from app.routers.callbacks import _auto_connect_pending_links

        host = make_host(test_db)
        lab = make_lab(test_db, test_user.id)

        for actual in ["pending", "unknown", "down", "error"]:
            make_link_state(
                test_db, lab.id,
                source_node="R1",
                source_interface=f"eth{actual}",
                target_node="R2",
                target_interface=f"eth{actual}",
                desired_state="up",
                actual_state=actual,
            )

        mock_create = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={host.id: host},
        ):
            with patch(
                "app.routers.callbacks.create_link_if_ready",
                mock_create,
            ):
                await _auto_connect_pending_links(test_db, lab.id, ["R1"])

        assert mock_create.await_count == 4


# ── _auto_reattach_overlay_endpoints ────────────────────────────────────


@pytest.mark.asyncio
class TestAutoReattachOverlayEndpoints:
    """Tests for _auto_reattach_overlay_endpoints()."""

    async def test_no_host_map_returns_early(
        self, test_db: Session, test_user: models.User,
    ):
        """Empty host map means no work to do."""
        from app.routers.callbacks import _auto_reattach_overlay_endpoints

        lab = make_lab(test_db, test_user.id)

        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={},
        ):
            await _auto_reattach_overlay_endpoints(test_db, lab.id, ["R1"])

    async def test_no_cross_host_links_is_noop(
        self, test_db: Session, test_user: models.User,
    ):
        """No cross-host links means nothing to reattach."""
        from app.routers.callbacks import _auto_reattach_overlay_endpoints

        host = make_host(test_db)
        lab = make_lab(test_db, test_user.id)
        # Only same-host links
        make_link_state(
            test_db, lab.id,
            source_node="R1", target_node="R2",
            is_cross_host=False,
        )

        mock_attach = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={host.id: host},
        ):
            with patch("app.routers.callbacks.agent_client") as mock_ac:
                mock_ac.resolve_data_plane_ip = AsyncMock(return_value="10.0.0.1")
                mock_ac.attach_overlay_interface_on_agent = mock_attach
                with patch("app.routers.infrastructure.get_or_create_settings") as mock_infra:
                    mock_infra.return_value = MagicMock(overlay_mtu=0)
                    await _auto_reattach_overlay_endpoints(test_db, lab.id, ["R1"])

        mock_attach.assert_not_awaited()

    async def test_reattaches_source_endpoint(
        self, test_db: Session, test_user: models.User,
    ):
        """Source endpoint of a cross-host link is reattached when node becomes ready."""
        from app.routers.callbacks import _auto_reattach_overlay_endpoints

        host_a = make_host(test_db, host_id="host-a")
        host_b = make_host(test_db, host_id="host-b")
        lab = make_lab(test_db, test_user.id)
        make_link_state(
            test_db, lab.id,
            source_node="R1", target_node="R2",
            is_cross_host=True,
            source_host_id="host-a",
            target_host_id="host-b",
            vni=5000,
        )

        mock_attach = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={"host-a": host_a, "host-b": host_b},
        ):
            with patch("app.routers.callbacks.agent_client") as mock_ac:
                mock_ac.resolve_data_plane_ip = AsyncMock(return_value="10.0.0.1")
                mock_ac.attach_overlay_interface_on_agent = mock_attach
                with patch("app.routers.infrastructure.get_or_create_settings") as mock_infra:
                    mock_infra.return_value = MagicMock(overlay_mtu=9000)
                    with patch("app.services.link_manager.allocate_vni", return_value=5000):
                        await _auto_reattach_overlay_endpoints(
                            test_db, lab.id, ["R1"],
                        )

        # Should call attach for R1 (source node)
        assert mock_attach.await_count >= 1

    async def test_skips_when_host_not_in_map(
        self, test_db: Session, test_user: models.User,
    ):
        """Cross-host link is skipped when one host is not in the agent map."""
        from app.routers.callbacks import _auto_reattach_overlay_endpoints

        host_a = make_host(test_db, host_id="host-a")
        lab = make_lab(test_db, test_user.id)
        make_link_state(
            test_db, lab.id,
            source_node="R1", target_node="R2",
            is_cross_host=True,
            source_host_id="host-a",
            target_host_id="host-missing",
        )

        mock_attach = AsyncMock()
        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={"host-a": host_a},
        ):
            with patch("app.routers.callbacks.agent_client") as mock_ac:
                mock_ac.attach_overlay_interface_on_agent = mock_attach
                with patch("app.routers.infrastructure.get_or_create_settings") as mock_infra:
                    mock_infra.return_value = MagicMock(overlay_mtu=0)
                    await _auto_reattach_overlay_endpoints(
                        test_db, lab.id, ["R1"],
                    )

        mock_attach.assert_not_awaited()

    async def test_allocates_vni_if_missing(
        self, test_db: Session, test_user: models.User,
    ):
        """VNI is allocated and stored when link has no VNI."""
        from app.routers.callbacks import _auto_reattach_overlay_endpoints

        host_a = make_host(test_db, host_id="host-a")
        host_b = make_host(test_db, host_id="host-b")
        lab = make_lab(test_db, test_user.id)
        ls = make_link_state(
            test_db, lab.id,
            source_node="R1", target_node="R2",
            is_cross_host=True,
            source_host_id="host-a",
            target_host_id="host-b",
            vni=None,  # No VNI yet
        )

        with patch(
            "app.routers.callbacks._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={"host-a": host_a, "host-b": host_b},
        ):
            with patch("app.routers.callbacks.agent_client") as mock_ac:
                mock_ac.resolve_data_plane_ip = AsyncMock(return_value="10.0.0.1")
                mock_ac.attach_overlay_interface_on_agent = AsyncMock()
                with patch("app.routers.infrastructure.get_or_create_settings") as mock_infra:
                    mock_infra.return_value = MagicMock(overlay_mtu=0)
                    with patch("app.services.link_manager.allocate_vni", return_value=7777):
                        await _auto_reattach_overlay_endpoints(
                            test_db, lab.id, ["R1"],
                        )

        test_db.refresh(ls)
        assert ls.vni == 7777


# ── _update_node_states ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestUpdateNodeStates:
    """Tests for _update_node_states()."""

    async def test_updates_node_actual_state(
        self, test_db: Session, test_user: models.User,
    ):
        """Node state is updated from callback payload."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)
        ns = make_node_state(test_db, lab.id, "R1", actual_state="starting")

        with patch(
            "app.routers.callbacks._auto_connect_pending_links",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.routers.callbacks._auto_reattach_overlay_endpoints",
                new_callable=AsyncMock,
            ):
                await _update_node_states(test_db, lab.id, {"R1": "running"})

        test_db.flush()
        test_db.refresh(ns)
        assert ns.actual_state == "running"

    async def test_clears_error_on_running(
        self, test_db: Session, test_user: models.User,
    ):
        """Error message is cleared when state transitions to running."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)
        ns = make_node_state(test_db, lab.id, "R1", actual_state="error")
        ns.error_message = "Previous error"
        test_db.commit()

        with patch(
            "app.routers.callbacks._auto_connect_pending_links",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.routers.callbacks._auto_reattach_overlay_endpoints",
                new_callable=AsyncMock,
            ):
                await _update_node_states(test_db, lab.id, {"R1": "running"})

        test_db.flush()
        test_db.refresh(ns)
        assert ns.error_message is None

    async def test_clears_error_on_stopped(
        self, test_db: Session, test_user: models.User,
    ):
        """Error message is cleared when state transitions to stopped."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)
        ns = make_node_state(test_db, lab.id, "R1", actual_state="error")
        ns.error_message = "Previous error"
        test_db.commit()

        with patch(
            "app.routers.callbacks._auto_connect_pending_links",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.routers.callbacks._auto_reattach_overlay_endpoints",
                new_callable=AsyncMock,
            ):
                await _update_node_states(test_db, lab.id, {"R1": "stopped"})

        test_db.flush()
        test_db.refresh(ns)
        assert ns.error_message is None

    async def test_triggers_auto_connect_on_state_change_to_running(
        self, test_db: Session, test_user: models.User,
    ):
        """Auto-connect is triggered when a node transitions to running."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)
        make_node_state(test_db, lab.id, "R1", actual_state="starting")

        mock_connect = AsyncMock()
        mock_reattach = AsyncMock()
        with patch(
            "app.routers.callbacks._auto_connect_pending_links", mock_connect,
        ):
            with patch(
                "app.routers.callbacks._auto_reattach_overlay_endpoints",
                mock_reattach,
            ):
                await _update_node_states(test_db, lab.id, {"R1": "running"})

        mock_connect.assert_awaited_once()
        mock_reattach.assert_awaited_once()
        # Verify R1 is in the node_names list
        assert "R1" in mock_connect.call_args[0][2]

    async def test_no_auto_connect_when_state_unchanged(
        self, test_db: Session, test_user: models.User,
    ):
        """Auto-connect is NOT triggered when state doesn't change."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)
        make_node_state(test_db, lab.id, "R1", actual_state="running")

        mock_connect = AsyncMock()
        mock_reattach = AsyncMock()
        with patch(
            "app.routers.callbacks._auto_connect_pending_links", mock_connect,
        ):
            with patch(
                "app.routers.callbacks._auto_reattach_overlay_endpoints",
                mock_reattach,
            ):
                await _update_node_states(test_db, lab.id, {"R1": "running"})

        mock_connect.assert_not_awaited()

    async def test_unknown_node_name_ignored(
        self, test_db: Session, test_user: models.User,
    ):
        """Node names not in the database are silently ignored."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)

        # No node state for "R99" in DB — should not raise
        await _update_node_states(test_db, lab.id, {"R99": "running"})

    async def test_auto_connect_failure_is_caught(
        self, test_db: Session, test_user: models.User,
    ):
        """Failure in auto_connect_pending_links is caught and logged."""
        from app.routers.callbacks import _update_node_states

        lab = make_lab(test_db, test_user.id)
        make_node_state(test_db, lab.id, "R1", actual_state="starting")

        with patch(
            "app.routers.callbacks._auto_connect_pending_links",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Agent unreachable"),
        ):
            with patch(
                "app.routers.callbacks._auto_reattach_overlay_endpoints",
                new_callable=AsyncMock,
            ):
                # Should not raise
                await _update_node_states(test_db, lab.id, {"R1": "running"})


# ── job_heartbeat endpoint ──────────────────────────────────────────────


class TestJobHeartbeatEndpoint:
    """Tests for POST /callbacks/job/{job_id}/heartbeat."""

    def test_heartbeat_records_timestamp(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Heartbeat updates last_heartbeat on the job."""
        job = models.Job(
            id="heartbeat-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.post("/callbacks/job/heartbeat-job/heartbeat")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Heartbeat recorded"

        test_db.refresh(job)
        assert job.last_heartbeat is not None

    def test_heartbeat_unknown_job(self, test_client: TestClient):
        """Heartbeat for unknown job returns failure."""
        response = test_client.post(
            "/callbacks/job/nonexistent-job/heartbeat",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["message"].lower()

    def test_heartbeat_completed_job_noop(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Heartbeat on completed job returns success but doesn't update."""
        job = models.Job(
            id="completed-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.post("/callbacks/job/completed-job/heartbeat")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "already" in data["message"].lower()

    def test_heartbeat_queued_job_accepted(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Heartbeat on queued job is accepted (queued is an active status)."""
        job = models.Job(
            id="queued-job-hb",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.post("/callbacks/job/queued-job-hb/heartbeat")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Heartbeat recorded"

    def test_heartbeat_failed_job_noop(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Heartbeat on failed job returns success but doesn't update."""
        job = models.Job(
            id="failed-job-hb",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="failed",
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.post("/callbacks/job/failed-job-hb/heartbeat")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "already" in data["message"].lower()