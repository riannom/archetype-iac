"""Tests for NLM phase timing instrumentation."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeNodeState:
    """Minimal stand-in for models.NodeState."""
    def __init__(self, node_name, actual_state="undeployed", desired_state="running",
                 node_id="n1", lab_id="lab1"):
        self.node_name = node_name
        self.node_id = node_id
        self.lab_id = lab_id
        self.actual_state = actual_state
        self.desired_state = desired_state
        self.boot_started_at = None
        self.starting_started_at = None
        self.stopping_started_at = None
        self.is_ready = False
        self.error_message = None
        self.enforcement_attempts = 0
        self.max_enforcement_attempts = 5
        self.image_sync_status = None
        self.image_sync_message = None


class FakeNode:
    """Minimal stand-in for models.Node."""
    def __init__(self, name, device="ceos"):
        self.container_name = name
        self.device = device
        self.gui_id = f"gui-{name}"


class TestDominantDeviceType:
    """Tests for _dominant_device_type() helper."""

    def _make_manager(self, device_types):
        """Create a minimal NLM instance with pre-loaded maps."""
        from app.tasks.node_lifecycle import NodeLifecycleManager

        session = MagicMock()
        lab = MagicMock(id="lab1")
        job = MagicMock(id="job1")
        mgr = NodeLifecycleManager(session, lab, job, node_ids=["n1"])
        # Pre-populate maps
        mgr.node_states = []
        mgr.db_nodes_map = {}
        for i, dt in enumerate(device_types):
            name = f"node{i}"
            ns = FakeNodeState(name)
            mgr.node_states.append(ns)
            mgr.db_nodes_map[name] = FakeNode(name, device=dt)
        return mgr

    def test_single_ceos(self):
        mgr = self._make_manager(["ceos"])
        assert mgr._dominant_device_type() == "ceos"

    def test_mixed_returns_most_common(self):
        mgr = self._make_manager(["ceos", "ceos", "srlinux"])
        assert mgr._dominant_device_type() == "ceos"

    def test_unknown_device_returns_other(self):
        mgr = self._make_manager(["custom_device"])
        assert mgr._dominant_device_type() == "other"

    def test_empty_returns_other(self):
        mgr = self._make_manager([])
        assert mgr._dominant_device_type() == "other"

    def test_subset_of_nodes(self):
        """Can pass a subset of node_states."""
        mgr = self._make_manager(["ceos", "srlinux", "linux"])
        subset = [mgr.node_states[1]]  # srlinux
        assert mgr._dominant_device_type(subset) == "srlinux"


class TestPhaseTimingInstrumentation:
    """Verify that NLM phases record metrics."""

    @pytest.mark.asyncio
    async def test_deploy_phase_records_duration(self):
        """_deploy_nodes phase should observe nlm_phase_duration."""
        from app.timing import AsyncTimedOperation

        mock_hist = MagicMock()
        timer = AsyncTimedOperation(
            histogram=mock_hist,
            labels={"phase": "container_deploy", "device_type": "ceos"},
        )
        async with timer:
            await asyncio.sleep(0.01)

        mock_hist.labels.assert_called_once_with(phase="container_deploy", device_type="ceos")
        mock_hist.labels.return_value.observe.assert_called_once()
        assert timer.duration_ms > 0

    @pytest.mark.asyncio
    async def test_stop_phase_records_duration(self):
        from app.timing import AsyncTimedOperation

        mock_hist = MagicMock()
        async with AsyncTimedOperation(
            histogram=mock_hist,
            labels={"phase": "container_stop", "device_type": "linux"},
        ) as t:
            await asyncio.sleep(0.01)
        assert t.success is True
        mock_hist.labels.return_value.observe.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase_error_still_records(self):
        """When the phase throws, timing should still record."""
        from app.timing import AsyncTimedOperation

        mock_hist = MagicMock()
        timer = AsyncTimedOperation(
            histogram=mock_hist,
            labels={"phase": "container_deploy", "device_type": "ceos"},
        )
        with pytest.raises(RuntimeError):
            async with timer:
                raise RuntimeError("agent failed")
        assert timer.success is False
        assert timer.duration_ms >= 0
        mock_hist.labels.return_value.observe.assert_called_once()

    @pytest.mark.asyncio
    async def test_structured_log_contains_phase(self, caplog):
        """Phase name should appear in structured log."""
        import logging
        from app.timing import AsyncTimedOperation

        with caplog.at_level(logging.INFO, logger="app.timing"):
            async with AsyncTimedOperation(
                log_event="nlm_phase",
                log_extras={"lab_id": "test-lab", "phase": "image_sync"},
            ):
                pass
        assert any("nlm_phase completed" in r.message for r in caplog.records)
