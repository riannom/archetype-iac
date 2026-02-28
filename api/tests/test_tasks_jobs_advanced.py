"""Advanced tests for helper functions and deeper multihost orchestration in jobs.py.

Covers:
- _capture_node_ips: IP capture from agent status
- _dispatch_webhook: fire-and-forget webhook dispatch
- _get_node_info_for_webhook: node info extraction for webhooks
- _auto_extract_configs_before_destroy: auto config extraction (additional edge cases)
- _create_cross_host_links_if_ready: cross-host link creation logic
- run_multihost_deploy: deeper scenarios (links, capacity, rollback, etc.)
- run_multihost_destroy: deeper scenarios (tunnels, link cleanup, webhooks)
- _record_started / _record_failed: metric label normalization
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs import (
    _capture_node_ips,
    _dispatch_webhook,
    _get_node_info_for_webhook,
    _auto_extract_configs_before_destroy,
    _create_cross_host_links_if_ready,
    _record_started,
    _record_failed,
    run_multihost_deploy,
    run_multihost_destroy,
)


def _mock_get_session(test_db: Session):
    """Create a mock get_session context manager that yields the test database session."""

    @contextmanager
    def mock_session():
        yield test_db

    return mock_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lab(test_db, test_user, *, state="stopped", name="Test Lab"):
    lab = models.Lab(
        name=name,
        owner_id=test_user.id,
        provider="docker",
        state=state,
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _make_job(test_db, lab, test_user, *, action="up", status="queued"):
    job = models.Job(
        lab_id=lab.id,
        user_id=test_user.id,
        action=action,
        status=status,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _make_node(test_db, lab, *, gui_id, name, device="linux", host_id=None):
    node = models.Node(
        lab_id=lab.id,
        gui_id=gui_id,
        display_name=name,
        container_name=name,
        node_type="device",
        device=device,
        host_id=host_id,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _make_node_state(test_db, lab, *, node_id, node_name, actual="running", desired="running"):
    ns = models.NodeState(
        lab_id=lab.id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        is_ready=actual == "running",
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


# ---------------------------------------------------------------------------
# 1. _capture_node_ips
# ---------------------------------------------------------------------------


class TestCaptureNodeIps:
    """Tests for _capture_node_ips function."""

    @pytest.mark.asyncio
    async def test_success_with_ips(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """IPs from agent status are persisted to NodeState records."""
        lab = _make_lab(test_db, test_user)
        ns = _make_node_state(test_db, lab, node_id="n1", node_name="r1")

        agent_status = {
            "nodes": [
                {"name": "r1", "ip_addresses": ["10.0.0.1", "10.0.0.2"]},
            ]
        }

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value=agent_status,
        ):
            await _capture_node_ips(test_db, lab.id, sample_host)

        test_db.refresh(ns)
        assert ns.management_ip == "10.0.0.1"
        assert json.loads(ns.management_ips_json) == ["10.0.0.1", "10.0.0.2"]

    @pytest.mark.asyncio
    async def test_empty_nodes_list(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Empty nodes list from agent does not crash."""
        lab = _make_lab(test_db, test_user)
        _make_node_state(test_db, lab, node_id="n1", node_name="r1")

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value={"nodes": []},
        ):
            await _capture_node_ips(test_db, lab.id, sample_host)

        # No crash, no IPs set
        ns = (
            test_db.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab.id)
            .first()
        )
        assert ns.management_ip is None

    @pytest.mark.asyncio
    async def test_agent_error_suppressed(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Agent communication error is caught and does not propagate."""
        lab = _make_lab(test_db, test_user)

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            # Should NOT raise
            await _capture_node_ips(test_db, lab.id, sample_host)

    @pytest.mark.asyncio
    async def test_partial_ips(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Nodes without IPs are skipped; nodes with IPs are updated."""
        lab = _make_lab(test_db, test_user)
        ns1 = _make_node_state(test_db, lab, node_id="n1", node_name="r1")
        ns2 = _make_node_state(test_db, lab, node_id="n2", node_name="r2")

        agent_status = {
            "nodes": [
                {"name": "r1", "ip_addresses": ["10.0.0.1"]},
                {"name": "r2", "ip_addresses": []},
            ]
        }

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value=agent_status,
        ):
            await _capture_node_ips(test_db, lab.id, sample_host)

        test_db.refresh(ns1)
        test_db.refresh(ns2)
        assert ns1.management_ip == "10.0.0.1"
        assert ns2.management_ip is None

    @pytest.mark.asyncio
    async def test_missing_node_state_record(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Agent reports a node that has no NodeState record; no crash."""
        lab = _make_lab(test_db, test_user)
        # No NodeState for "phantom"

        agent_status = {
            "nodes": [
                {"name": "phantom", "ip_addresses": ["10.0.0.99"]},
            ]
        }

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value=agent_status,
        ):
            # Should not raise
            await _capture_node_ips(test_db, lab.id, sample_host)

    @pytest.mark.asyncio
    async def test_commit_failure_rolls_back_session(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Commit failures trigger rollback in best-effort IP capture."""
        lab = _make_lab(test_db, test_user)
        ns = _make_node_state(test_db, lab, node_id="n1", node_name="r1")

        agent_status = {
            "nodes": [
                {"name": "r1", "ip_addresses": ["10.0.0.1"]},
            ]
        }

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value=agent_status,
        ):
            with patch.object(test_db, "commit", side_effect=RuntimeError("commit failed")):
                with patch.object(test_db, "rollback", wraps=test_db.rollback) as mock_rollback:
                    await _capture_node_ips(test_db, lab.id, sample_host)

        assert mock_rollback.call_count >= 1
        test_db.refresh(ns)
        assert ns.management_ip is None


# ---------------------------------------------------------------------------
# 2. _dispatch_webhook
# ---------------------------------------------------------------------------


class TestDispatchWebhook:
    """Tests for _dispatch_webhook function."""

    @pytest.mark.asyncio
    async def test_success(
        self, test_db: Session, test_user: models.User
    ):
        """Webhook is dispatched with correct payload."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node_state(test_db, lab, node_id="n1", node_name="r1")

        with patch(
            "app.tasks.jobs.webhooks.dispatch_webhook_event",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            await _dispatch_webhook("lab.deploy_complete", lab, job, test_db)

            mock_dispatch.assert_awaited_once()
            call_kwargs = mock_dispatch.call_args.kwargs
            assert call_kwargs["event_type"] == "lab.deploy_complete"
            assert call_kwargs["lab_id"] == lab.id
            assert len(call_kwargs["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_failure_suppressed(
        self, test_db: Session, test_user: models.User
    ):
        """Exception from webhook dispatch is swallowed."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")

        with patch(
            "app.tasks.jobs.webhooks.dispatch_webhook_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("webhook server down"),
        ):
            # Should not raise
            await _dispatch_webhook("lab.deploy_failed", lab, job, test_db)

    @pytest.mark.asyncio
    async def test_various_event_types(
        self, test_db: Session, test_user: models.User
    ):
        """Different event types pass through unchanged."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="down")

        for event_type in ["lab.deploy_started", "lab.destroy_complete", "job.failed"]:
            with patch(
                "app.tasks.jobs.webhooks.dispatch_webhook_event",
                new_callable=AsyncMock,
            ) as mock_dispatch:
                await _dispatch_webhook(event_type, lab, job, test_db)
                assert mock_dispatch.call_args.kwargs["event_type"] == event_type


# ---------------------------------------------------------------------------
# 3. _get_node_info_for_webhook
# ---------------------------------------------------------------------------


class TestGetNodeInfoForWebhook:
    """Tests for _get_node_info_for_webhook function."""

    def test_correct_fields(self, test_db: Session, test_user: models.User):
        """Returned dicts contain the expected keys."""
        lab = _make_lab(test_db, test_user)
        ns = _make_node_state(test_db, lab, node_id="n1", node_name="r1")
        ns.management_ip = "10.0.0.1"
        test_db.commit()

        result = _get_node_info_for_webhook(test_db, lab.id)
        assert len(result) == 1
        assert result[0]["name"] == "r1"
        assert result[0]["state"] == "running"
        assert result[0]["ready"] is True
        assert result[0]["management_ip"] == "10.0.0.1"

    def test_empty_no_nodes(self, test_db: Session, test_user: models.User):
        """Lab with no NodeState records returns empty list."""
        lab = _make_lab(test_db, test_user)
        result = _get_node_info_for_webhook(test_db, lab.id)
        assert result == []

    def test_multiple_nodes(self, test_db: Session, test_user: models.User):
        """Multiple nodes are all returned."""
        lab = _make_lab(test_db, test_user)
        _make_node_state(test_db, lab, node_id="n1", node_name="r1")
        _make_node_state(test_db, lab, node_id="n2", node_name="r2")
        _make_node_state(test_db, lab, node_id="n3", node_name="r3")

        result = _get_node_info_for_webhook(test_db, lab.id)
        assert len(result) == 3
        names = {r["name"] for r in result}
        assert names == {"r1", "r2", "r3"}


# ---------------------------------------------------------------------------
# 4. _auto_extract_configs_before_destroy (additional edge cases)
# ---------------------------------------------------------------------------


class TestAutoExtractAdditional:
    """Additional edge-case tests for _auto_extract_configs_before_destroy."""

    @pytest.mark.asyncio
    async def test_disabled_via_settings(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Feature flag off skips extraction entirely."""
        lab = _make_lab(test_db, test_user, state="running")

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = False
            with patch("app.tasks.jobs.agent_client") as mock_ac:
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)
                mock_ac.extract_configs_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_healthy_agents_returns(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """When all agents are offline the function returns without error."""
        lab = _make_lab(test_db, test_user, state="running")
        # Create a placement pointing at sample_host
        placement = models.NodePlacement(
            lab_id=lab.id,
            node_name="r1",
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True
            with patch("app.tasks.jobs.agent_client") as mock_ac:
                mock_ac.is_agent_online.return_value = False
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)
                mock_ac.extract_configs_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_single_agent(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Single agent extraction creates snapshots."""
        lab = _make_lab(test_db, test_user, state="running")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=sample_host.id)
        placement = models.NodePlacement(
            lab_id=lab.id,
            node_name="r1",
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        mock_result = {
            "success": True,
            "configs": [{"node_name": "r1", "content": "hostname r1"}],
        }

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True
            with patch("app.tasks.jobs.agent_client") as mock_ac:
                mock_ac.is_agent_online.return_value = True
                mock_ac.extract_configs_on_agent = AsyncMock(return_value=mock_result)
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

        snapshots = (
            test_db.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab.id)
            .all()
        )
        assert len(snapshots) == 1
        assert snapshots[0].snapshot_type == "auto_stop"

    @pytest.mark.asyncio
    async def test_partial_failure_one_agent_errors(
        self, test_db: Session, test_user: models.User, multiple_hosts: list[models.Host]
    ):
        """If one agent's extract raises, configs from the other are still saved."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)
        for h, n in [(host1, "r1"), (host2, "r2")]:
            test_db.add(models.NodePlacement(lab_id=lab.id, node_name=n, host_id=h.id))
        test_db.commit()

        async def mock_extract(agent, lab_id):
            if agent.id == host1.id:
                return {"success": True, "configs": [{"node_name": "r1", "content": "!"}]}
            raise RuntimeError("agent unreachable")

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True
            with patch("app.tasks.jobs.agent_client") as mock_ac:
                mock_ac.is_agent_online.return_value = True
                mock_ac.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)
                await _auto_extract_configs_before_destroy(test_db, lab, host1)

        snapshots = (
            test_db.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab.id)
            .all()
        )
        assert len(snapshots) == 1

    @pytest.mark.asyncio
    async def test_multihost_both_agents_called(
        self, test_db: Session, test_user: models.User, multiple_hosts: list[models.Host]
    ):
        """With two placements, both agents are contacted."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)
        for h, n in [(host1, "r1"), (host2, "r2")]:
            test_db.add(models.NodePlacement(lab_id=lab.id, node_name=n, host_id=h.id))
        test_db.commit()

        mock_result = {
            "success": True,
            "configs": [{"node_name": "dummy", "content": "!"}],
        }

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True
            with patch("app.tasks.jobs.agent_client") as mock_ac:
                mock_ac.is_agent_online.return_value = True
                mock_ac.extract_configs_on_agent = AsyncMock(return_value=mock_result)
                await _auto_extract_configs_before_destroy(test_db, lab, host1)

                assert mock_ac.extract_configs_on_agent.call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_to_provided_agent(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """When no placements exist, the provided agent is used as fallback."""
        lab = _make_lab(test_db, test_user, state="running")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=sample_host.id)
        # No NodePlacement records

        mock_result = {
            "success": True,
            "configs": [{"node_name": "r1", "content": "!"}],
        }

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True
            with patch("app.tasks.jobs.agent_client") as mock_ac:
                mock_ac.is_agent_online.return_value = True
                mock_ac.extract_configs_on_agent = AsyncMock(return_value=mock_result)
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

                assert mock_ac.extract_configs_on_agent.call_count == 1
                call_agent = mock_ac.extract_configs_on_agent.call_args[0][0]
                assert call_agent.id == sample_host.id

    @pytest.mark.asyncio
    async def test_exception_suppressed(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Top-level exception is caught — destroy must not fail due to extraction."""
        lab = _make_lab(test_db, test_user, state="running")

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                side_effect=RuntimeError("boom"),
            ):
                # Outer except catches, function returns silently
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)


# ---------------------------------------------------------------------------
# 5. _create_cross_host_links_if_ready
# ---------------------------------------------------------------------------


class TestCreateCrossHostLinksIfReady:
    """Tests for _create_cross_host_links_if_ready function."""

    @pytest.mark.asyncio
    async def test_no_pending_links_returns_early(
        self, test_db: Session, test_user: models.User
    ):
        """No pending cross-host links means function returns immediately."""
        lab = _make_lab(test_db, test_user, state="running")
        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                new_callable=AsyncMock,
            ) as mock_create:
                await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                mock_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_pending_cross_host_links(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Pending cross-host links trigger create_deployment_links."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")

        # Create a pending cross-host link
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
            source_host_id=host1.id,
            target_host_id=host2.id,
        )
        test_db.add(ls)
        test_db.commit()

        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                ) as mock_lock:
                    # Make the context manager yield True (lock acquired)
                    mock_lock.return_value.__enter__ = MagicMock(return_value=True)
                    mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(1, 0),
                    ) as mock_create:
                        await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncategorized_links_trigger_creation(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Links with no host IDs (uncategorized) trigger link creation."""
        lab = _make_lab(test_db, test_user, state="running")

        # Uncategorized: source_host_id is None
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            source_host_id=None,
        )
        test_db.add(ls)
        test_db.commit()

        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                ) as mock_lock:
                    mock_lock.return_value.__enter__ = MagicMock(return_value=True)
                    mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(1, 0),
                    ) as mock_create:
                        await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_links_trigger_creation(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Links defined in DB but without LinkState records trigger creation."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        n1 = _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        n2 = _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host1.id)

        # A Link definition exists but no LinkState row
        link_def = models.Link(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node_id=n1.id,
            source_interface="eth1",
            target_node_id=n2.id,
            target_interface="eth1",
        )
        test_db.add(link_def)
        test_db.commit()

        log_parts: list[str] = []

        with patch(
            "app.tasks.jobs.agent_client.is_agent_online",
            return_value=True,
        ):
            with patch(
                "app.utils.locks.link_ops_lock",
            ) as mock_lock:
                mock_lock.return_value.__enter__ = MagicMock(return_value=True)
                mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                with patch(
                    "app.tasks.link_orchestration.create_deployment_links",
                    new_callable=AsyncMock,
                    return_value=(1, 0),
                ) as mock_create:
                    await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                    mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_recreate_when_tunnels_missing(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Cross-host links marked up but agent reports no tunnels -> force recreate."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")

        # Cross-host link already "up"
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
            is_cross_host=True,
            source_host_id=host1.id,
            target_host_id=host2.id,
        )
        test_db.add(ls)
        # Add placement so the force-check has hosts to query
        test_db.add(models.NodePlacement(lab_id=lab.id, node_name="r1", host_id=host1.id))
        test_db.commit()

        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch(
                    "app.tasks.jobs.agent_client.get_overlay_status_from_agent",
                    new_callable=AsyncMock,
                    return_value={"tunnels": [], "link_tunnels": []},
                ):
                    with patch(
                        "app.utils.locks.link_ops_lock",
                    ) as mock_lock:
                        mock_lock.return_value.__enter__ = MagicMock(return_value=True)
                        mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(1, 0),
                        ) as mock_create:
                            await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                            mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_online_agents_returns_early(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """If no agents are online, function returns without creating links."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")

        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
            source_host_id=host1.id,
        )
        test_db.add(ls)
        test_db.commit()

        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                return_value=False,
            ):
                with patch(
                    "app.tasks.link_orchestration.create_deployment_links",
                    new_callable=AsyncMock,
                ) as mock_create:
                    await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                    mock_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_held_skips(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """When link_ops_lock is already held, creation is skipped."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")

        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
            source_host_id=host1.id,
            target_host_id=host2.id,
        )
        test_db.add(ls)
        test_db.commit()

        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                ) as mock_lock:
                    # Lock NOT acquired
                    mock_lock.return_value.__enter__ = MagicMock(return_value=False)
                    mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                    ) as mock_create:
                        await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)
                        mock_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_handling(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Exception in create_deployment_links is caught and logged."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")

        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
            source_host_id=host1.id,
            target_host_id=host2.id,
        )
        test_db.add(ls)
        test_db.commit()

        log_parts: list[str] = []

        with patch("app.services.topology.TopologyService") as mock_topo_cls:
            mock_topo = MagicMock()
            mock_topo.get_links.return_value = []
            mock_topo_cls.return_value = mock_topo
            with patch(
                "app.tasks.jobs.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                ) as mock_lock:
                    mock_lock.return_value.__enter__ = MagicMock(return_value=True)
                    mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        side_effect=RuntimeError("link creation boom"),
                    ):
                        # Should NOT raise — error is caught
                        await _create_cross_host_links_if_ready(test_db, lab.id, log_parts)

        # Error message appended to log
        assert any("failed" in p.lower() or "boom" in p.lower() for p in log_parts)


# ---------------------------------------------------------------------------
# 6. Deeper run_multihost_deploy scenarios
# ---------------------------------------------------------------------------


@dataclass
class _FakeAnalysis:
    """Minimal stand-in for TopologyAnalysisResult."""
    placements: dict
    cross_host_links: list
    single_host: bool = False


class TestMultihostDeployAdvanced:
    """Advanced tests for run_multihost_deploy."""

    def _base_patches(self, test_db):
        """Return a dict of common patches for deploy tests."""
        return {
            "app.tasks.jobs.get_session": _mock_get_session(test_db),
            "app.tasks.jobs._dispatch_webhook": AsyncMock(),
            "app.tasks.jobs._capture_node_ips": AsyncMock(),
            "app.tasks.jobs.emit_deploy_finished": AsyncMock(),
        }

    @pytest.mark.asyncio
    async def test_deploy_with_link_creation(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Successful deploy calls create_deployment_links and sets lab to running."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        analysis = _FakeAnalysis(
            placements={host1.id: [MagicMock(node_name="r1")]},
            cross_host_links=[],
        )

        patches = self._base_patches(test_db)
        with patch("app.tasks.jobs.get_session", patches["app.tasks.jobs.get_session"]):
            with patch("app.tasks.jobs._dispatch_webhook", patches["app.tasks.jobs._dispatch_webhook"]):
                with patch("app.tasks.jobs._capture_node_ips", patches["app.tasks.jobs._capture_node_ips"]):
                    with patch("app.tasks.jobs.emit_deploy_finished", patches["app.tasks.jobs.emit_deploy_finished"]):
                        with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                    with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(2, 0)) as mock_links:
                                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert lab.state == "running"
        mock_links.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_capacity_failure_fails_job(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Resource capacity check failure marks job as failed."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        analysis = _FakeAnalysis(
            placements={host1.id: [MagicMock(node_name="r1")]},
            cross_host_links=[],
        )

        # Mock capacity check to fail
        cap_result = MagicMock()
        cap_result.fits = False

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.settings") as mock_settings:
                mock_settings.resource_validation_enabled = True
                mock_settings.image_sync_enabled = False
                with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                    with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                        with patch("app.tasks.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch("app.services.resource_capacity.check_multihost_capacity", return_value={host1.id: cap_result}):
                                with patch("app.services.resource_capacity.format_capacity_error", return_value="Insufficient CPU"):
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Insufficient CPU" in job.log_path

    @pytest.mark.asyncio
    async def test_partial_rollback_on_failure(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """If one host's deploy fails, successful hosts are rolled back."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        analysis = _FakeAnalysis(
            placements={
                host1.id: [MagicMock(node_name="r1")],
                host2.id: [MagicMock(node_name="r2")],
            },
            cross_host_links=[],
        )

        # host1 succeeds, host2 fails
        deploy_results = iter([
            {"status": "completed", "stdout": "OK"},
            RuntimeError("deploy failed"),
        ])

        async def mock_deploy(*args, **kwargs):
            result = next(deploy_results)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                with patch("app.tasks.jobs.emit_deploy_finished", new_callable=AsyncMock):
                    with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                        with patch("app.tasks.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock, side_effect=mock_deploy):
                                with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}) as mock_rollback:
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        # Rollback should be called on the host that succeeded
        assert mock_rollback.await_count >= 1

    @pytest.mark.asyncio
    async def test_unplaced_nodes_assigned_to_default_agent(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Nodes without host_id are assigned to the default agent."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        # Node WITHOUT host_id
        node = _make_node(test_db, lab, gui_id="n1", name="r1", host_id=None)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                with patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock):
                    with patch("app.tasks.jobs.emit_deploy_finished", new_callable=AsyncMock):
                        with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=host1):
                            with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                        with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                            await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(node)
        assert node.host_id == host1.id

        test_db.refresh(job)
        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_no_default_agent_fails(
        self,
        test_db: Session,
        test_user: models.User,
    ):
        """Unplaced nodes with no available default agent causes failure."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=None)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=None):
                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "no host assignment" in job.log_path.lower() or "no default agent" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_missing_hosts_fails(
        self,
        test_db: Session,
        test_user: models.User,
    ):
        """If required hosts are missing or offline, job fails."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        # Node assigned to a nonexistent host
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id="nonexistent-host-id")

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=False):
                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "missing" in job.log_path.lower() or "unhealthy" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_link_failure_fails_job(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Failed link setup marks job as failed even if deploy succeeded."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                with patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock):
                    with patch("app.tasks.jobs.emit_deploy_finished", new_callable=AsyncMock):
                        with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                    with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(1, 2)):
                                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "link" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_capacity_warnings_appended_to_log(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Capacity warnings are logged but don't fail the job."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user, action="up")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        cap_result = MagicMock()
        cap_result.fits = True

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.settings") as mock_settings:
                mock_settings.resource_validation_enabled = True
                mock_settings.image_sync_enabled = False
                with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                    with patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock):
                        with patch("app.tasks.jobs.emit_deploy_finished", new_callable=AsyncMock):
                            with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                        with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                            with patch("app.services.resource_capacity.check_multihost_capacity", return_value={host1.id: cap_result}):
                                                with patch("app.services.resource_capacity.format_capacity_warnings", return_value=["High CPU usage on agent-1"]):
                                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        assert "WARNING" in job.log_path
        assert "High CPU" in job.log_path


# ---------------------------------------------------------------------------
# 7. Deeper run_multihost_destroy scenarios
# ---------------------------------------------------------------------------


class TestMultihostDestroyAdvanced:
    """Advanced tests for run_multihost_destroy."""

    @pytest.mark.asyncio
    async def test_tunnel_teardown_called(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Tunnel teardown is invoked before container destruction."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(1, 0)) as mock_teardown:
                    with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                        with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                            with patch("app.tasks.jobs.emit_destroy_finished", new_callable=AsyncMock):
                                await run_multihost_destroy(job.id, lab.id)

        mock_teardown.assert_awaited_once()
        test_db.refresh(job)
        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_offline_agent_warnings_in_log(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Offline agents produce warnings in the job log."""
        host1, host2, host3 = multiple_hosts[0], multiple_hosts[1], multiple_hosts[2]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host3.id)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.is_agent_online", side_effect=lambda h: h.id == host1.id):
                with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                    with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                        with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                            with patch("app.tasks.jobs.emit_job_failed", new_callable=AsyncMock):
                                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed_with_warnings"
        assert "offline" in job.log_path.lower() or "unreachable" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_link_state_cleanup_on_success(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """On full success, remaining LinkState rows are deleted."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        # Pre-existing LinkState
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
        )
        test_db.add(ls)
        test_db.commit()
        ls_id = ls.id

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                    with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                        with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                            with patch("app.tasks.jobs.emit_destroy_finished", new_callable=AsyncMock):
                                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"

        # LinkState should be deleted
        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.id == ls_id)
            .first()
        )
        assert remaining is None

    @pytest.mark.asyncio
    async def test_link_state_preserved_on_partial(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """On partial failure, LinkState rows are updated (not deleted)."""
        host1, host2, host3 = multiple_hosts[0], multiple_hosts[1], multiple_hosts[2]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host3.id)

        ls = models.LinkState(
            lab_id=lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
        )
        test_db.add(ls)
        test_db.commit()
        ls_id = ls.id

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.is_agent_online", side_effect=lambda h: h.id == host1.id):
                with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                    with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                        with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock):
                            with patch("app.tasks.jobs.emit_job_failed", new_callable=AsyncMock):
                                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed_with_warnings"

        # LinkState should be preserved, desired_state set to "deleted"
        remaining = test_db.get(models.LinkState, ls_id)
        assert remaining is not None
        assert remaining.desired_state == "deleted"
        assert remaining.actual_state == "error"

    @pytest.mark.asyncio
    async def test_webhook_dispatched_on_success(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Webhook is dispatched on successful destroy."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                    with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                        with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock) as mock_wh:
                            with patch("app.tasks.jobs.emit_destroy_finished", new_callable=AsyncMock):
                                await run_multihost_destroy(job.id, lab.id)

        # Webhook should have been called with destroy_complete
        mock_wh.assert_awaited()
        event_types = [c.args[0] for c in mock_wh.call_args_list]
        assert "lab.destroy_complete" in event_types

    @pytest.mark.asyncio
    async def test_webhook_dispatched_on_partial_failure(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """On partial failure, job.failed webhook is dispatched."""
        host1, host2, host3 = multiple_hosts[0], multiple_hosts[1], multiple_hosts[2]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host3.id)

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.is_agent_online", side_effect=lambda h: h.id == host1.id):
                with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                    with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                        with patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock) as mock_wh:
                            with patch("app.tasks.jobs.emit_job_failed", new_callable=AsyncMock):
                                await run_multihost_destroy(job.id, lab.id)

        mock_wh.assert_awaited()
        event_types = [c.args[0] for c in mock_wh.call_args_list]
        assert "job.failed" in event_types


# ---------------------------------------------------------------------------
# 8. _record_started / _record_failed
# ---------------------------------------------------------------------------


class TestRecordMetrics:
    """Tests for _record_started and _record_failed metric helpers."""

    def test_record_started_normalizes_action(self):
        """_record_started calls record_job_started with normalized label."""
        job = MagicMock()
        job.created_at = None
        job.started_at = None

        with patch("app.tasks.jobs.record_job_started") as mock_rec:
            _record_started(job, "sync:r1")
            mock_rec.assert_called_once()
            assert mock_rec.call_args[0][0] == "sync"

    def test_record_failed_normalizes_action(self):
        """_record_failed calls record_job_failed with normalized label."""
        job = MagicMock()
        job.log_path = "some error"

        with patch("app.tasks.jobs.record_job_failed") as mock_rec:
            _record_failed(job, "node:start")
            mock_rec.assert_called_once()
            assert mock_rec.call_args[0][0] == "node"
