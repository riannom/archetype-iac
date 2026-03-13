"""Extended tests for StopMixin in node_lifecycle_stop.py.

Covers additional scenarios beyond the base test file:
- _auto_extract_before_stop: multi-agent grouping, offline agent fallback,
  stopping-state extraction, config filtering by stop node names, empty
  content/node_name skipped, dedup (save returns None), general exception
  handling, no-placement defaults to self.agent, extraction exception in gather
- _stop_nodes: multi-agent parallel batches, "not found" on default agent does
  NOT trigger fallback, fallback batch with multiple nodes, mixed success/failure
  in single batch, session.commit at end, agent grouping with no placement
- _apply_stop_result: success clears all fields & broadcasts, failure defaults
  to "Stop failed", old_state tracked in log, failure broadcasts with "error"
- _converge_stopped_desired_error_states: reset_enforcement called, mixed states
  (some match, some don't), zero matches returns 0, is_ready set to False,
  image_sync fields cleared, broadcast per normalized node
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_client import AgentUnavailableError
from app.state import NodeActualState
from app.tasks.node_lifecycle import NodeLifecycleManager, _get_container_name
from tests.factories import make_host, make_job, make_lab, make_node, make_node_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_manager(test_db, lab, job, host, node_states):
    """Create a NodeLifecycleManager with standard mocking via __new__()."""
    manager = NodeLifecycleManager.__new__(NodeLifecycleManager)
    manager.session = test_db
    manager.lab = lab
    manager.job = job
    manager.agent = host
    manager.node_states = node_states
    manager.log_parts = []
    manager.placements_map = {}
    manager._broadcast_state = MagicMock()
    manager._release_db_transaction_for_io = MagicMock()
    return manager


# ---------------------------------------------------------------------------
# Tests: _auto_extract_before_stop - stopping-state nodes
# ---------------------------------------------------------------------------


class TestAutoExtractStoppingState:
    """Tests for extraction from nodes in stopping state."""

    @pytest.mark.asyncio
    async def test_stopping_nodes_are_extractable(self, test_db, test_user):
        """Nodes in 'stopping' state should be included for extraction."""
        host = make_host(test_db, "agent-stop-1")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        ns = make_node_state(test_db, lab.id, "R1", actual="stopping")

        manager = _create_manager(test_db, lab, job, host, [ns])

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": "R1", "content": "hostname R1\n"}],
            })
            with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                mock_cs = MagicMock()
                mock_cs.save_extracted_config.return_value = MagicMock()
                mock_cs_cls.return_value = mock_cs

                await manager._auto_extract_before_stop([ns])

                mock_ac.extract_configs_on_agent.assert_awaited_once()
                mock_cs.save_extracted_config.assert_called_once()


class TestAutoExtractAgentFallback:
    """Tests for agent fallback during extraction."""

    @pytest.mark.asyncio
    async def test_offline_placement_agent_falls_back_to_default(self, test_db, test_user):
        """When placement agent is offline, extraction should use default agent."""
        host_default = make_host(test_db, "agent-ext-def", "Default")
        host_offline = make_host(test_db, "agent-ext-off", "Offline", status="offline")
        lab = make_lab(test_db, test_user, agent_id=host_default.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host_default, [ns])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_offline.id),
        }

        extract_agents = []

        async def capture_extract(agent, lab_id):
            extract_agents.append(agent.id)
            return {"success": True, "configs": []}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = False
            mock_ac.extract_configs_on_agent = AsyncMock(side_effect=capture_extract)

            await manager._auto_extract_before_stop([ns])

        # Should use default agent since offline agent is not online
        assert extract_agents == [host_default.id]

    @pytest.mark.asyncio
    async def test_partial_timeout_keeps_successful_agent_results(self, test_db, test_user):
        """One slow agent should not discard configs extracted from another."""
        host_a = make_host(test_db, "agent-ext-a", "Agent A")
        host_b = make_host(test_db, "agent-ext-b", "Agent B")
        lab = make_lab(test_db, test_user, agent_id=host_a.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        make_node(test_db, lab.id, "R2")
        ns1 = make_node_state(test_db, lab.id, "R1", actual="running")
        ns2 = make_node_state(test_db, lab.id, "R2", actual="running")

        manager = _create_manager(test_db, lab, job, host_a, [ns1, ns2])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_a.id),
            "R2": MagicMock(host_id=host_b.id),
        }

        async def fake_extract(agent, lab_id):  # noqa: ARG001
            if agent.id == host_b.id:
                await asyncio.sleep(0.2)
                node_name = "R2"
            else:
                node_name = "R1"
            return {
                "success": True,
                "configs": [{"node_name": node_name, "content": f"hostname {node_name}\n"}],
            }

        with patch("app.tasks.node_lifecycle_stop.settings") as mock_settings:
            mock_settings.auto_extract_on_stop_timeout_seconds = 0.01
            with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
                mock_ac.is_agent_online.return_value = True
                mock_ac.extract_configs_on_agent = AsyncMock(side_effect=fake_extract)
                with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                    mock_cs = MagicMock()
                    mock_cs.save_extracted_config.return_value = MagicMock()
                    mock_cs_cls.return_value = mock_cs

                    await manager._auto_extract_before_stop([ns1, ns2])

        mock_cs.save_extracted_config.assert_called_once()
        assert any("timed out on Agent B".lower() in p.lower() for p in manager.log_parts)

    @pytest.mark.asyncio
    async def test_no_placement_defaults_to_self_agent(self, test_db, test_user):
        """Nodes without placement should use self.agent for extraction."""
        host = make_host(test_db, "agent-ext-noplace")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])
        manager.placements_map = {}  # No placement

        extract_agents = []

        async def capture_extract(agent, lab_id):
            extract_agents.append(agent.id)
            return {"success": True, "configs": []}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.extract_configs_on_agent = AsyncMock(side_effect=capture_extract)

            await manager._auto_extract_before_stop([ns])

        assert extract_agents == [host.id]


class TestAutoExtractConfigFiltering:
    """Tests for config filtering during auto-extract."""

    @pytest.mark.asyncio
    async def test_configs_filtered_to_stop_node_names(self, test_db, test_user):
        """Only configs for nodes being stopped should be saved."""
        host = make_host(test_db, "agent-ext-filt")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        make_node(test_db, lab.id, "R2")
        ns1 = make_node_state(test_db, lab.id, "R1", actual="running")
        # R2 is not being stopped but its config comes back from agent

        manager = _create_manager(test_db, lab, job, host, [ns1])

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [
                    {"node_name": "R1", "content": "hostname R1\n"},
                    {"node_name": "R2", "content": "hostname R2\n"},  # not being stopped
                ],
            })
            with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                mock_cs = MagicMock()
                mock_cs.save_extracted_config.return_value = MagicMock()
                mock_cs_cls.return_value = mock_cs

                # Only pass ns1 (R1) as nodes being stopped
                await manager._auto_extract_before_stop([ns1])

                # Only R1's config should be saved
                assert mock_cs.save_extracted_config.call_count == 1
                saved_node = mock_cs.save_extracted_config.call_args.kwargs.get("node_name")
                assert saved_node == "R1"

    @pytest.mark.asyncio
    async def test_empty_node_name_or_content_skipped(self, test_db, test_user):
        """Configs with empty node_name or content should be skipped."""
        host = make_host(test_db, "agent-ext-empty")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [
                    {"node_name": "", "content": "some content"},   # empty name
                    {"node_name": "R1", "content": ""},             # empty content
                    {"node_name": None, "content": "hostname R1"},  # None name
                ],
            })
            with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                mock_cs = MagicMock()
                mock_cs_cls.return_value = mock_cs

                await manager._auto_extract_before_stop([ns])

                # None of these should trigger save_extracted_config
                mock_cs.save_extracted_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_snapshot_not_counted(self, test_db, test_user):
        """When save_extracted_config returns None (dedup), it should not count as created."""
        host = make_host(test_db, "agent-ext-dedup")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": "R1", "content": "hostname R1\n"}],
            })
            with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                mock_cs = MagicMock()
                mock_cs.save_extracted_config.return_value = None  # dedup
                mock_cs_cls.return_value = mock_cs

                await manager._auto_extract_before_stop([ns])

                mock_cs.save_extracted_config.assert_called_once()
                # Log should say 0 snapshots created
                log = " ".join(manager.log_parts)
                assert "0 autosave" in log


class TestAutoExtractExceptionHandling:
    """Tests for general exception handling in auto-extract."""

    @pytest.mark.asyncio
    async def test_general_exception_caught_and_logged(self, test_db, test_user, caplog):
        """A general exception during auto-extract should be caught and not propagated."""
        host = make_host(test_db, "agent-ext-exc")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.extract_configs_on_agent = AsyncMock(
                side_effect=RuntimeError("unexpected")
            )

            # Should NOT raise
            await manager._auto_extract_before_stop([ns])

        log = " ".join(manager.log_parts)
        assert "no configs extracted" in log.lower()
        assert "Auto-extract failed on agent" in caplog.text
        assert "unexpected" in caplog.text

    @pytest.mark.asyncio
    async def test_gather_exception_from_one_agent_does_not_block(self, test_db, test_user):
        """If one agent raises exception in gather, configs from other agents still processed."""
        host_a = make_host(test_db, "agent-ext-ga", "Agent A")
        host_b = make_host(test_db, "agent-ext-gb", "Agent B")
        lab = make_lab(test_db, test_user, agent_id=host_a.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1")
        make_node(test_db, lab.id, "R2")
        ns1 = make_node_state(test_db, lab.id, "R1", actual="running")
        ns2 = make_node_state(test_db, lab.id, "R2", actual="running", node_id="r2")

        manager = _create_manager(test_db, lab, job, host_a, [ns1, ns2])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_a.id),
            "R2": MagicMock(host_id=host_b.id),
        }

        async def fake_extract(agent, lab_id):
            if agent.id == host_b.id:
                raise ConnectionError("agent B down")
            return {
                "success": True,
                "configs": [{"node_name": "R1", "content": "hostname R1\n"}],
            }

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(side_effect=fake_extract)
            with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                mock_cs = MagicMock()
                mock_cs.save_extracted_config.return_value = MagicMock()
                mock_cs_cls.return_value = mock_cs

                await manager._auto_extract_before_stop([ns1, ns2])

                # R1's config from agent A should still be saved
                mock_cs.save_extracted_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_configs_extracted_logs_message(self, test_db, test_user):
        """When extraction returns no configs, a log message is appended."""
        host = make_host(test_db, "agent-ext-noconf")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [],  # empty
            })

            await manager._auto_extract_before_stop([ns])

        log = " ".join(manager.log_parts)
        assert "no configs" in log.lower()

    @pytest.mark.asyncio
    async def test_extraction_builds_node_device_map(self, test_db, test_user):
        """The node_device_map should map container_name to device."""
        host = make_host(test_db, "agent-ext-devmap")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        make_node(test_db, lab.id, "R1", device="ceos")
        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        saved_kwargs = []

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": "R1", "content": "hostname R1\n"}],
            })
            with patch("app.services.config_service.ConfigService") as mock_cs_cls:
                mock_cs = MagicMock()

                def capture_save(**kwargs):
                    saved_kwargs.append(kwargs)
                    return MagicMock()

                mock_cs.save_extracted_config.side_effect = capture_save
                mock_cs_cls.return_value = mock_cs

                await manager._auto_extract_before_stop([ns])

        assert len(saved_kwargs) == 1
        assert saved_kwargs[0]["device_kind"] == "ceos"


# ---------------------------------------------------------------------------
# Tests: _stop_nodes - multi-agent parallel batches
# ---------------------------------------------------------------------------


class TestStopNodesMultiAgent:
    """Tests for parallel multi-agent stop batches."""

    @pytest.mark.asyncio
    async def test_three_agents_called_in_parallel(self, test_db, test_user):
        """Nodes on 3 different agents should produce 3 parallel reconcile calls."""
        host_a = make_host(test_db, "agent-ma-a", "Agent A")
        host_b = make_host(test_db, "agent-ma-b", "Agent B")
        host_c = make_host(test_db, "agent-ma-c", "Agent C")
        lab = make_lab(test_db, test_user, agent_id=host_a.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns1 = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")
        ns2 = make_node_state(test_db, lab.id, "R2", desired="stopped", actual="running", node_id="r2")
        ns3 = make_node_state(test_db, lab.id, "R3", desired="stopped", actual="running", node_id="r3")

        manager = _create_manager(test_db, lab, job, host_a, [ns1, ns2, ns3])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_a.id),
            "R2": MagicMock(host_id=host_b.id),
            "R3": MagicMock(host_id=host_c.id),
        }

        called_agents = []

        async def fake_reconcile(agent, lab_id, batch):
            called_agents.append(agent.id)
            results = []
            for item in batch:
                results.append({"container_name": item["container_name"], "success": True})
            return {"results": results}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns1, ns2, ns3])

        assert set(called_agents) == {host_a.id, host_b.id, host_c.id}
        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value
        assert ns3.actual_state == NodeActualState.STOPPED.value

    @pytest.mark.asyncio
    async def test_not_found_on_default_agent_no_fallback(self, test_db, test_user):
        """'not found' on default agent should NOT trigger fallback (only non-default)."""
        host = make_host(test_db, "agent-ma-nofall")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])
        manager.placements_map = {}

        cn = _get_container_name(lab.id, "R1")

        async def fake_reconcile(agent, lab_id, batch):
            return {"results": [{"container_name": cn, "success": False, "error": "not found"}]}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns])

        # Should be called only once (no fallback)
        assert mock_ac.reconcile_nodes_on_agent.await_count == 1
        # "not found" on default agent is treated as failure, not fallback
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_fallback_batch_with_multiple_nodes(self, test_db, test_user):
        """Multiple nodes not found on non-default agent should all fallback together."""
        host_default = make_host(test_db, "agent-ma-def2", "Default")
        host_other = make_host(test_db, "agent-ma-oth2", "Other")
        lab = make_lab(test_db, test_user, agent_id=host_default.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns1 = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")
        ns2 = make_node_state(test_db, lab.id, "R2", desired="stopped", actual="running", node_id="r2")

        manager = _create_manager(test_db, lab, job, host_default, [ns1, ns2])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_other.id),
            "R2": MagicMock(host_id=host_other.id),
        }

        cn1 = _get_container_name(lab.id, "R1")
        cn2 = _get_container_name(lab.id, "R2")
        fallback_batches = []

        async def fake_reconcile(agent, lab_id, batch):
            if agent.id == host_other.id:
                return {"results": [
                    {"container_name": cn1, "success": False, "error": "not found"},
                    {"container_name": cn2, "success": False, "error": "not found"},
                ]}
            else:
                fallback_batches.append(batch)
                return {"results": [
                    {"container_name": cn1, "success": True},
                    {"container_name": cn2, "success": True},
                ]}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns1, ns2])

        # Fallback should contain both nodes
        assert len(fallback_batches) == 1
        assert len(fallback_batches[0]) == 2
        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value


class TestStopNodesMixedResults:
    """Tests for mixed success/failure results in stop batches."""

    @pytest.mark.asyncio
    async def test_mixed_success_failure_in_single_batch(self, test_db, test_user):
        """Some nodes succeed and some fail in a single batch."""
        host = make_host(test_db, "agent-mix")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns1 = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")
        ns2 = make_node_state(test_db, lab.id, "R2", desired="stopped", actual="running", node_id="r2")

        manager = _create_manager(test_db, lab, job, host, [ns1, ns2])
        manager.placements_map = {}

        cn1 = _get_container_name(lab.id, "R1")
        cn2 = _get_container_name(lab.id, "R2")

        async def fake_reconcile(agent, lab_id, batch):
            return {"results": [
                {"container_name": cn1, "success": True},
                {"container_name": cn2, "success": False, "error": "permission denied"},
            ]}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns1, ns2])

        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.ERROR.value
        assert "permission denied" in ns2.error_message

    @pytest.mark.asyncio
    async def test_no_placement_defaults_to_self_agent(self, test_db, test_user):
        """Nodes without placement should be sent to self.agent."""
        host = make_host(test_db, "agent-noplace")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])
        manager.placements_map = {}

        called_agents = []

        async def fake_reconcile(agent, lab_id, batch):
            called_agents.append(agent.id)
            cn = _get_container_name(lab.id, "R1")
            return {"results": [{"container_name": cn, "success": True}]}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns])

        assert called_agents == [host.id]

    @pytest.mark.asyncio
    async def test_fallback_agent_unavailable_sets_transient(self, test_db, test_user):
        """AgentUnavailableError during fallback sets transient error."""
        host_default = make_host(test_db, "agent-fb-unav-d", "Default")
        host_other = make_host(test_db, "agent-fb-unav-o", "Other")
        lab = make_lab(test_db, test_user, agent_id=host_default.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")

        manager = _create_manager(test_db, lab, job, host_default, [ns])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_other.id),
        }

        cn = _get_container_name(lab.id, "R1")
        call_count = {"n": 0}

        async def fake_reconcile(agent, lab_id, batch):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"results": [{"container_name": cn, "success": False, "error": "not found"}]}
            raise AgentUnavailableError("default agent down")

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns])

        assert "transient" in ns.error_message.lower()

    @pytest.mark.asyncio
    async def test_fallback_exception_sets_error_state(self, test_db, test_user):
        """Generic exception during fallback should set error state."""
        host_default = make_host(test_db, "agent-fb-exc-d", "Default")
        host_other = make_host(test_db, "agent-fb-exc-o", "Other")
        lab = make_lab(test_db, test_user, agent_id=host_default.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="running")

        manager = _create_manager(test_db, lab, job, host_default, [ns])
        manager.placements_map = {
            "R1": MagicMock(host_id=host_other.id),
        }

        cn = _get_container_name(lab.id, "R1")
        call_count = {"n": 0}

        async def fake_reconcile(agent, lab_id, batch):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"results": [{"container_name": cn, "success": False, "error": "not found"}]}
            raise RuntimeError("socket timeout")

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.reconcile_nodes_on_agent = AsyncMock(side_effect=fake_reconcile)

            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "socket timeout" in ns.error_message
        assert ns.boot_started_at is None
        assert ns.is_ready is False


# ---------------------------------------------------------------------------
# Tests: _apply_stop_result - detailed behavior
# ---------------------------------------------------------------------------


class TestApplyStopResultDetailed:
    """Detailed tests for _apply_stop_result."""

    def test_success_clears_all_fields_and_broadcasts(self, test_db, test_user):
        """Successful stop should clear all transitional fields and broadcast."""
        host = make_host(test_db, "agent-asr-1")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="stopping")
        ns.stopping_started_at = datetime.now(timezone.utc)
        ns.boot_started_at = datetime.now(timezone.utc)
        ns.error_message = "stale error"
        ns.is_ready = True
        test_db.commit()

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._apply_stop_result(ns, {"success": True}, host)

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.stopping_started_at is not None  # Kept for graceful shutdown guard
        assert ns.boot_started_at is None
        assert ns.error_message is None
        assert ns.is_ready is False
        manager._broadcast_state.assert_called_once_with(ns, name_suffix="stopped")

    def test_failure_defaults_to_stop_failed(self, test_db, test_user):
        """Failure without 'error' key should default to 'Stop failed'."""
        host = make_host(test_db, "agent-asr-2")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._apply_stop_result(ns, {"success": False}, host)

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Stop failed"
        manager._broadcast_state.assert_called_once_with(ns, name_suffix="error")

    def test_failure_preserves_custom_error(self, test_db, test_user):
        """Failure with custom error message should use it."""
        host = make_host(test_db, "agent-asr-3")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._apply_stop_result(
            ns, {"success": False, "error": "container locked by another process"}, host
        )

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "container locked by another process"

    def test_success_tracks_old_state_in_log(self, test_db, test_user):
        """On success, the log message should include the node name."""
        host = make_host(test_db, "agent-asr-4")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._apply_stop_result(ns, {"success": True}, host)

        log = " ".join(manager.log_parts)
        assert "R1" in log
        assert "stopped" in log

    def test_failure_clears_timestamps(self, test_db, test_user):
        """Failed stop should also clear timestamps."""
        host = make_host(test_db, "agent-asr-5")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", actual="stopping")
        ns.stopping_started_at = datetime.now(timezone.utc)
        ns.boot_started_at = datetime.now(timezone.utc)
        test_db.commit()

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._apply_stop_result(ns, {"success": False, "error": "timeout"}, host)

        assert ns.stopping_started_at is None
        assert ns.boot_started_at is None
        assert ns.is_ready is False


# ---------------------------------------------------------------------------
# Tests: _converge_stopped_desired_error_states - comprehensive
# ---------------------------------------------------------------------------


class TestConvergeStoppedDesiredErrorComprehensive:
    """Comprehensive tests for _converge_stopped_desired_error_states."""

    def test_reset_enforcement_called(self, test_db, test_user):
        """Convergence should call reset_enforcement() on each normalized node."""
        host = make_host(test_db, "agent-conv-1")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="error")
        ns.enforcement_attempts = 3
        test_db.commit()

        manager = _create_manager(test_db, lab, job, host, [ns])

        count = manager._converge_stopped_desired_error_states()

        assert count == 1
        assert ns.enforcement_attempts == 0

    def test_mixed_states_only_matching_normalized(self, test_db, test_user):
        """Only desired=stopped + actual=error nodes should be normalized."""
        host = make_host(test_db, "agent-conv-2")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns_match = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="error")
        ns_running = make_node_state(test_db, lab.id, "R2", desired="running", actual="error", node_id="r2")
        ns_stopped = make_node_state(test_db, lab.id, "R3", desired="stopped", actual="stopped", node_id="r3")

        manager = _create_manager(test_db, lab, job, host, [ns_match, ns_running, ns_stopped])

        count = manager._converge_stopped_desired_error_states()

        assert count == 1
        assert ns_match.actual_state == NodeActualState.STOPPED.value
        assert ns_running.actual_state == NodeActualState.ERROR.value  # unchanged
        assert ns_stopped.actual_state == NodeActualState.STOPPED.value  # was already stopped

    def test_zero_matches_returns_zero(self, test_db, test_user):
        """When no nodes match, should return 0."""
        host = make_host(test_db, "agent-conv-3")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="running", actual="running")

        manager = _create_manager(test_db, lab, job, host, [ns])

        count = manager._converge_stopped_desired_error_states()
        assert count == 0

    def test_is_ready_set_to_false(self, test_db, test_user):
        """Normalized nodes should have is_ready=False."""
        host = make_host(test_db, "agent-conv-4")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="error")
        ns.is_ready = True
        test_db.commit()

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._converge_stopped_desired_error_states()

        assert ns.is_ready is False

    def test_clears_image_sync_and_timestamps(self, test_db, test_user):
        """Convergence should clear all sync and timestamp fields."""
        host = make_host(test_db, "agent-conv-5")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="error")
        ns.image_sync_status = "failed"
        ns.image_sync_message = "disk full"
        ns.stopping_started_at = datetime.now(timezone.utc)
        ns.starting_started_at = datetime.now(timezone.utc)
        ns.boot_started_at = datetime.now(timezone.utc)
        test_db.commit()

        manager = _create_manager(test_db, lab, job, host, [ns])

        count = manager._converge_stopped_desired_error_states()

        assert count == 1
        assert ns.image_sync_status is None
        assert ns.image_sync_message is None
        assert ns.stopping_started_at is None
        assert ns.starting_started_at is None
        assert ns.boot_started_at is None

    def test_broadcasts_stopped_for_each_normalized(self, test_db, test_user):
        """Each normalized node should trigger a broadcast with name_suffix='stopped'."""
        host = make_host(test_db, "agent-conv-6")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns1 = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="error")
        ns2 = make_node_state(test_db, lab.id, "R2", desired="stopped", actual="error", node_id="r2")
        ns3 = make_node_state(test_db, lab.id, "R3", desired="running", actual="error", node_id="r3")

        manager = _create_manager(test_db, lab, job, host, [ns1, ns2, ns3])

        count = manager._converge_stopped_desired_error_states()

        assert count == 2
        assert manager._broadcast_state.call_count == 2
        # Verify correct name_suffix
        for c in manager._broadcast_state.call_args_list:
            assert c.kwargs.get("name_suffix") == "stopped" or c[1].get("name_suffix") == "stopped"

    def test_error_message_cleared(self, test_db, test_user):
        """Error message should be cleared after normalization."""
        host = make_host(test_db, "agent-conv-7")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        ns = make_node_state(test_db, lab.id, "R1", desired="stopped", actual="error")
        ns.error_message = "Docker daemon crashed"
        test_db.commit()

        manager = _create_manager(test_db, lab, job, host, [ns])

        manager._converge_stopped_desired_error_states()

        assert ns.error_message is None
        assert ns.actual_state == NodeActualState.STOPPED.value

    def test_empty_node_states_returns_zero(self, test_db, test_user):
        """Empty node_states list should return 0 without errors."""
        host = make_host(test_db, "agent-conv-8")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab.id, test_user.id)

        manager = _create_manager(test_db, lab, job, host, [])

        count = manager._converge_stopped_desired_error_states()
        assert count == 0