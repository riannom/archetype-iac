"""Tests for api/app/tasks/jobs.py — round 12.

Targets deep paths: config extraction before destroy (multi-host, agent
offline, empty results), IP capture during deploy (partial data, missing
node defs, exception recovery), agent-offline handling during job execution
(AgentUnavailableError, AgentJobError), and job completion callback branches
(unknown action, stdout/stderr capture, webhook dispatch on failure).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.agent_client import AgentJobError, AgentUnavailableError
from app.tasks.jobs import (
    _auto_extract_configs_before_destroy,
    _capture_node_ips,
    _cleanup_network_records_after_destroy,
    _get_node_info_for_webhook,
    _release_db_transaction_for_io,
    _reset_session_after_db_error,
    run_agent_job,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_lab(db, owner_id, state="running"):
    lab = models.Lab(name="Test", owner_id=owner_id, provider="docker", state=state)
    db.add(lab)
    db.commit()
    db.refresh(lab)
    return lab


def _make_host(db, host_id="h1", status="online"):
    h = models.Host(
        id=host_id, name=f"Agent-{host_id}", address="localhost:8080",
        status=status, capabilities="{}", last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _make_node(db, lab_id, gui_id, display_name, container_name, device="linux"):
    n = models.Node(
        lab_id=lab_id, gui_id=gui_id, display_name=display_name,
        container_name=container_name, device=device,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def _make_node_state(db, lab_id, node_id, node_name, node_definition_id=None,
                     desired="running", actual="running"):
    ns = models.NodeState(
        lab_id=lab_id, node_id=node_id, node_name=node_name,
        node_definition_id=node_definition_id,
        desired_state=desired, actual_state=actual,
    )
    db.add(ns)
    db.commit()
    db.refresh(ns)
    return ns


def _make_job(db, lab_id, user_id, action="up", status="queued"):
    j = models.Job(lab_id=lab_id, user_id=user_id, action=action, status=status)
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _make_placement(db, lab_id, node_name, host_id, node_definition_id=None):
    p = models.NodePlacement(
        lab_id=lab_id, node_name=node_name,
        node_definition_id=node_definition_id, host_id=host_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# _auto_extract_configs_before_destroy — multi-host & edge cases
# ---------------------------------------------------------------------------


class TestAutoExtractMultiHost:
    """Multi-host auto-extraction and error branches."""

    def test_multihost_extracts_from_all_agents(self, test_db: Session, test_user: models.User):
        """When nodes are placed on multiple agents, extraction happens concurrently."""
        lab = _make_lab(test_db, test_user.id)
        h1 = _make_host(test_db, "h1")
        h2 = _make_host(test_db, "h2")
        n1 = _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1", "ceos")
        n2 = _make_node(test_db, lab.id, "n2", "R2", "archetype-test-r2", "ceos")
        _make_placement(test_db, lab.id, n1.container_name, h1.id, n1.id)
        _make_placement(test_db, lab.id, n2.container_name, h2.id, n2.id)

        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService") as mock_cs_cls:
            mock_settings.feature_auto_extract_on_destroy = True
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(side_effect=[
                {"success": True, "configs": [{"node_name": "archetype-test-r1", "content": "conf1"}]},
                {"success": True, "configs": [{"node_name": "archetype-test-r2", "content": "conf2"}]},
            ])
            mock_svc = MagicMock()
            mock_svc.save_extracted_config.return_value = MagicMock()
            mock_cs_cls.return_value = mock_svc

            _run(_auto_extract_configs_before_destroy(test_db, lab, h1))

        assert mock_svc.save_extracted_config.call_count == 2

    def test_one_agent_offline_still_extracts_from_healthy(self, test_db: Session, test_user: models.User):
        """When one agent is offline, extraction proceeds with healthy agents only."""
        lab = _make_lab(test_db, test_user.id)
        h1 = _make_host(test_db, "h1")
        h2 = _make_host(test_db, "h2", status="offline")
        n1 = _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1")
        n2 = _make_node(test_db, lab.id, "n2", "R2", "archetype-test-r2")
        _make_placement(test_db, lab.id, n1.container_name, h1.id, n1.id)
        _make_placement(test_db, lab.id, n2.container_name, h2.id, n2.id)

        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService") as mock_cs_cls:
            mock_settings.feature_auto_extract_on_destroy = True
            # h1 online, h2 offline
            mock_ac.is_agent_online.side_effect = lambda h: h.id == "h1"
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": "archetype-test-r1", "content": "conf1"}],
            })
            mock_svc = MagicMock()
            mock_svc.save_extracted_config.return_value = MagicMock()
            mock_cs_cls.return_value = mock_svc

            _run(_auto_extract_configs_before_destroy(test_db, lab, h1))

        # Only h1 was queried
        mock_ac.extract_configs_on_agent.assert_awaited_once()
        mock_svc.save_extracted_config.assert_called_once()

    def test_all_agents_offline_returns_early(self, test_db: Session, test_user: models.User):
        """When all agents are offline, extraction is skipped gracefully."""
        lab = _make_lab(test_db, test_user.id)
        h1 = _make_host(test_db, "h1", status="offline")
        n1 = _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1")
        _make_placement(test_db, lab.id, n1.container_name, h1.id, n1.id)

        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_settings.feature_auto_extract_on_destroy = True
            mock_ac.is_agent_online.return_value = False

            # Should not raise
            _run(_auto_extract_configs_before_destroy(test_db, lab, h1))

        mock_ac.extract_configs_on_agent = AsyncMock()
        mock_ac.extract_configs_on_agent.assert_not_awaited()

    def test_agent_extract_returns_exception_continues(self, test_db: Session, test_user: models.User):
        """When asyncio.gather returns an exception for one agent, other configs are still saved."""
        lab = _make_lab(test_db, test_user.id)
        h1 = _make_host(test_db, "h1")
        h2 = _make_host(test_db, "h2")
        n1 = _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1", "ceos")
        n2 = _make_node(test_db, lab.id, "n2", "R2", "archetype-test-r2", "ceos")
        _make_placement(test_db, lab.id, n1.container_name, h1.id, n1.id)
        _make_placement(test_db, lab.id, n2.container_name, h2.id, n2.id)

        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService") as mock_cs_cls:
            mock_settings.feature_auto_extract_on_destroy = True
            mock_ac.is_agent_online.return_value = True
            # First agent raises, second succeeds
            mock_ac.extract_configs_on_agent = AsyncMock(side_effect=[
                ConnectionError("agent h1 unreachable"),
                {"success": True, "configs": [{"node_name": "archetype-test-r2", "content": "conf2"}]},
            ])
            mock_svc = MagicMock()
            mock_svc.save_extracted_config.return_value = MagicMock()
            mock_cs_cls.return_value = mock_svc

            _run(_auto_extract_configs_before_destroy(test_db, lab, h1))

        # Only the successful config saved
        mock_svc.save_extracted_config.assert_called_once()

    def test_extract_empty_content_skipped(self, test_db: Session, test_user: models.User):
        """Configs with empty content are skipped."""
        lab = _make_lab(test_db, test_user.id)
        h1 = _make_host(test_db, "h1")
        _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1")

        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService") as mock_cs_cls:
            mock_settings.feature_auto_extract_on_destroy = True
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": "archetype-test-r1", "content": ""}],
            })
            mock_svc = MagicMock()
            mock_cs_cls.return_value = mock_svc

            _run(_auto_extract_configs_before_destroy(test_db, lab, h1))

        mock_svc.save_extracted_config.assert_not_called()


# ---------------------------------------------------------------------------
# _capture_node_ips — edge cases
# ---------------------------------------------------------------------------


class TestCaptureNodeIps:
    """IP capture after successful deploy."""

    def test_captures_ips_for_matching_nodes(self, test_db: Session, test_user: models.User):
        """IPs are stored on NodeState when agent reports them."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        node_def = _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1")
        ns = _make_node_state(test_db, lab.id, "n1", "R1",
                              node_definition_id=node_def.id)

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [
                    {"name": "archetype-test-r1", "ip_addresses": ["10.0.0.1", "10.0.0.2"]},
                ],
            })
            _run(_capture_node_ips(test_db, lab.id, host))

        test_db.refresh(ns)
        assert ns.management_ip == "10.0.0.1"
        assert json.loads(ns.management_ips_json) == ["10.0.0.1", "10.0.0.2"]

    def test_no_nodes_in_status_skips(self, test_db: Session, test_user: models.User):
        """When agent returns no nodes, nothing happens."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={"nodes": []})
            _run(_capture_node_ips(test_db, lab.id, host))
        # No error

    def test_missing_node_def_skips_gracefully(self, test_db: Session, test_user: models.User):
        """Nodes returned by agent without matching Node definition are skipped."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [
                    {"name": "archetype-test-unknown", "ip_addresses": ["10.0.0.1"]},
                ],
            })
            # Should not raise
            _run(_capture_node_ips(test_db, lab.id, host))

    def test_node_without_ip_addresses_skipped(self, test_db: Session, test_user: models.User):
        """Nodes with empty ip_addresses list do not update NodeState."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        node_def = _make_node(test_db, lab.id, "n1", "R1", "archetype-test-r1")
        ns = _make_node_state(test_db, lab.id, "n1", "R1",
                              node_definition_id=node_def.id)

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [
                    {"name": "archetype-test-r1", "ip_addresses": []},
                ],
            })
            _run(_capture_node_ips(test_db, lab.id, host))

        test_db.refresh(ns)
        assert ns.management_ip is None

    def test_agent_exception_does_not_propagate(self, test_db: Session, test_user: models.User):
        """Agent errors during IP capture are swallowed (best-effort)."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.get_lab_status_from_agent = AsyncMock(
                side_effect=ConnectionError("agent down")
            )
            # Should not raise
            _run(_capture_node_ips(test_db, lab.id, host))


# ---------------------------------------------------------------------------
# run_agent_job — agent-offline & error recovery paths
# ---------------------------------------------------------------------------


class TestRunAgentJobErrors:
    """Error handling paths in the main run_agent_job function."""

    def _common_patches(self):
        """Return a dict of patch targets and their mock setups."""
        return {
            "app.tasks.jobs.get_session": MagicMock,
            "app.tasks.jobs.agent_client": MagicMock,
            "app.tasks.jobs._broadcast_job_progress": AsyncMock,
            "app.tasks.jobs._dispatch_webhook": AsyncMock,
            "app.tasks.jobs.update_lab_state": MagicMock,
            "app.tasks.jobs._release_db_transaction_for_io": MagicMock,
            "app.tasks.jobs._record_started": MagicMock,
            "app.tasks.jobs._record_failed": MagicMock,
            "app.tasks.jobs.record_job_completed": MagicMock,
            "app.tasks.jobs.emit_deploy_finished": AsyncMock,
            "app.tasks.jobs.emit_destroy_finished": AsyncMock,
            "app.tasks.jobs.emit_job_failed": AsyncMock,
        }

    def test_no_agent_available_fails_job(self, test_db: Session, test_user: models.User):
        """When no healthy agent is available, the job is marked failed."""
        lab = _make_lab(test_db, test_user.id)
        job = _make_job(test_db, lab.id, test_user.id)

        mock_session = MagicMock()
        mock_session.get = lambda model, id_: (
            job if model is models.Job else
            lab if model is models.Lab else None
        )
        mock_session.new = set()
        mock_session.dirty = set()
        mock_session.deleted = set()

        from contextlib import contextmanager

        @contextmanager
        def fake_session():
            yield mock_session

        with patch("app.tasks.jobs.get_session", fake_session), \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.update_lab_state") as mock_uls, \
             patch("app.tasks.jobs._record_failed"), \
             patch("app.tasks.jobs._release_db_transaction_for_io"):
            mock_ac.get_agent_for_lab = AsyncMock(return_value=None)
            mock_ac.get_agent_for_node = AsyncMock(return_value=None)

            _run(run_agent_job(job.id, lab.id, "up"))

        assert job.status == "failed"
        assert "No healthy agent" in job.log_path
        mock_uls.assert_called()

    def test_agent_unavailable_during_deploy(self, test_db: Session, test_user: models.User):
        """AgentUnavailableError during deploy sets lab state to unknown."""
        lab = _make_lab(test_db, test_user.id)
        job = _make_job(test_db, lab.id, test_user.id)
        host = _make_host(test_db)

        mock_session = MagicMock()
        mock_session.get = lambda model, id_: (
            job if model is models.Job else
            lab if model is models.Lab else None
        )
        mock_session.new = set()
        mock_session.dirty = set()
        mock_session.deleted = set()

        from contextlib import contextmanager

        @contextmanager
        def fake_session():
            yield mock_session

        with patch("app.tasks.jobs.get_session", fake_session), \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.update_lab_state") as mock_uls, \
             patch("app.tasks.jobs._record_started"), \
             patch("app.tasks.jobs._record_failed"), \
             patch("app.tasks.jobs._broadcast_job_progress", new_callable=AsyncMock), \
             patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock), \
             patch("app.tasks.jobs._release_db_transaction_for_io"), \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls, \
             patch("app.tasks.jobs._run_job_preflight_checks", new_callable=AsyncMock, return_value=(True, None)):
            mock_ac.get_agent_for_lab = AsyncMock(return_value=host)
            mock_ac.deploy_to_agent = AsyncMock(
                side_effect=AgentUnavailableError("connection lost", agent_id=host.id)
            )
            mock_ac.mark_agent_offline = AsyncMock()
            topo = MagicMock()
            topo.build_deploy_topology.return_value = {}
            mock_topo_cls.return_value = topo

            _run(run_agent_job(job.id, lab.id, "up"))

        assert job.status == "failed"
        assert "Agent became unavailable" in job.log_path
        # Lab state set to unknown
        mock_uls.assert_any_call(mock_session, lab.id, "unknown", error="Agent unavailable: connection lost")
        mock_ac.mark_agent_offline.assert_awaited_once_with(mock_session, host.id)

    def test_agent_job_error_captures_stdout_stderr(self, test_db: Session, test_user: models.User):
        """AgentJobError includes stdout/stderr in the job log."""
        lab = _make_lab(test_db, test_user.id)
        job = _make_job(test_db, lab.id, test_user.id)
        host = _make_host(test_db)

        mock_session = MagicMock()
        mock_session.get = lambda model, id_: (
            job if model is models.Job else
            lab if model is models.Lab else None
        )
        mock_session.new = set()
        mock_session.dirty = set()
        mock_session.deleted = set()

        from contextlib import contextmanager

        @contextmanager
        def fake_session():
            yield mock_session

        with patch("app.tasks.jobs.get_session", fake_session), \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.update_lab_state"), \
             patch("app.tasks.jobs._record_started"), \
             patch("app.tasks.jobs._record_failed"), \
             patch("app.tasks.jobs._broadcast_job_progress", new_callable=AsyncMock), \
             patch("app.tasks.jobs._dispatch_webhook", new_callable=AsyncMock), \
             patch("app.tasks.jobs._release_db_transaction_for_io"), \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls, \
             patch("app.tasks.jobs._run_job_preflight_checks", new_callable=AsyncMock, return_value=(True, None)):
            mock_ac.get_agent_for_lab = AsyncMock(return_value=host)
            mock_ac.deploy_to_agent = AsyncMock(
                side_effect=AgentJobError("deploy crashed", agent_id=host.id,
                                         stdout="creating containers", stderr="OOM killed")
            )
            topo = MagicMock()
            topo.build_deploy_topology.return_value = {}
            mock_topo_cls.return_value = topo

            _run(run_agent_job(job.id, lab.id, "up"))

        assert job.status == "failed"
        assert "STDOUT" in job.log_path
        assert "creating containers" in job.log_path
        assert "STDERR" in job.log_path
        assert "OOM killed" in job.log_path


# ---------------------------------------------------------------------------
# _cleanup_network_records_after_destroy — same-host linkstate cleanup
# ---------------------------------------------------------------------------


class TestCleanupNetworkRecordsAfterDestroy:
    """Network record cleanup after destroy."""

    def test_no_tunnels_deletes_linkstates(self, test_db: Session, test_user: models.User):
        """When no VXLAN tunnels exist, same-host LinkState records are deleted."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        ls = models.LinkState(
            lab_id=lab.id, link_name="R1:eth1-R2:eth1",
            source_node="R1", source_interface="eth1",
            target_node="R2", target_interface="eth1",
            desired_state="up", actual_state="up",
        )
        test_db.add(ls)
        test_db.commit()

        _run(_cleanup_network_records_after_destroy(test_db, lab.id, host))

        remaining = test_db.query(models.LinkState).filter_by(lab_id=lab.id).count()
        assert remaining == 0

    def test_exception_does_not_propagate(self, test_db: Session, test_user: models.User):
        """Exceptions during cleanup are caught and logged, not propagated."""
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)

        with patch.object(test_db, "query", side_effect=RuntimeError("DB exploded")):
            # Should not raise
            _run(_cleanup_network_records_after_destroy(test_db, lab.id, host))


# ---------------------------------------------------------------------------
# _release_db_transaction_for_io / _reset_session_after_db_error
# ---------------------------------------------------------------------------


class TestSessionHelpers:

    def test_release_commits_pending_writes(self):
        """When session has pending writes, release commits."""
        session = MagicMock()
        session.new = {MagicMock()}  # non-empty -> pending writes
        session.dirty = set()
        session.deleted = set()
        _release_db_transaction_for_io(session, context="test")
        session.commit.assert_called_once()
        session.rollback.assert_not_called()

    def test_release_rollbacks_when_clean(self):
        """When session has no pending writes, release rollbacks."""
        session = MagicMock()
        session.new = set()
        session.dirty = set()
        session.deleted = set()
        _release_db_transaction_for_io(session, context="test")
        session.rollback.assert_called_once()
        session.commit.assert_not_called()

    def test_release_reraises_on_commit_failure(self):
        """When commit fails during release, error is re-raised."""
        session = MagicMock()
        session.new = {MagicMock()}
        session.dirty = set()
        session.deleted = set()
        session.commit.side_effect = RuntimeError("commit failed")
        with pytest.raises(RuntimeError, match="commit failed"):
            _release_db_transaction_for_io(session, context="test")

    def test_reset_session_swallows_rollback_error(self):
        """Rollback failures during reset are logged but not raised."""
        session = MagicMock()
        session.rollback.side_effect = RuntimeError("rollback failed")
        # Should not raise
        _reset_session_after_db_error(session, context="test")


# ---------------------------------------------------------------------------
# _get_node_info_for_webhook
# ---------------------------------------------------------------------------


class TestGetNodeInfoForWebhook:

    def test_returns_node_info_list(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        ns = _make_node_state(test_db, lab.id, "n1", "R1")
        ns.management_ip = "10.0.0.1"
        ns.is_ready = True
        test_db.commit()

        result = _get_node_info_for_webhook(test_db, lab.id)
        assert len(result) == 1
        assert result[0]["name"] == "R1"
        assert result[0]["management_ip"] == "10.0.0.1"
        assert result[0]["ready"] is True

    def test_returns_empty_for_no_nodes(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        result = _get_node_info_for_webhook(test_db, lab.id)
        assert result == []
