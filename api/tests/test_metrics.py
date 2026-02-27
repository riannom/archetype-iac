"""Tests for Prometheus metrics module."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


class TestMetricDefinitions:
    """Tests that metrics exist and have correct names/types."""

    def test_prometheus_available_flag(self):
        """PROMETHEUS_AVAILABLE flag indicates whether prometheus_client is installed."""
        from app.metrics import PROMETHEUS_AVAILABLE
        assert isinstance(PROMETHEUS_AVAILABLE, bool)

    def test_nodes_total_exists(self):
        """archetype_nodes_total metric is defined."""
        from app.metrics import nodes_total
        assert nodes_total is not None

    def test_nodes_ready_exists(self):
        """archetype_nodes_ready metric is defined."""
        from app.metrics import nodes_ready
        assert nodes_ready is not None

    def test_jobs_total_exists(self):
        """archetype_jobs_total metric is defined."""
        from app.metrics import jobs_total
        assert jobs_total is not None

    def test_job_duration_exists(self):
        """archetype_job_duration_seconds metric is defined."""
        from app.metrics import job_duration
        assert job_duration is not None

    def test_agents_online_exists(self):
        """archetype_agents_online metric is defined."""
        from app.metrics import agents_online
        assert agents_online is not None

    def test_agents_total_exists(self):
        """archetype_agents_total metric is defined."""
        from app.metrics import agents_total
        assert agents_total is not None

    def test_labs_total_exists(self):
        """archetype_labs_total metric is defined."""
        from app.metrics import labs_total
        assert labs_total is not None

    def test_enforcement_actions_exists(self):
        """archetype_enforcement_total metric is defined."""
        from app.metrics import enforcement_actions
        assert enforcement_actions is not None

    def test_enforcement_failures_exists(self):
        """archetype_enforcement_failures_total metric is defined."""
        from app.metrics import enforcement_failures
        assert enforcement_failures is not None

    def test_reconciliation_cycle_duration_exists(self):
        """archetype_reconciliation_cycle_seconds metric is defined."""
        from app.metrics import reconciliation_cycle_duration
        assert reconciliation_cycle_duration is not None

    def test_broadcast_messages_exists(self):
        """archetype_broadcast_messages_total metric is defined."""
        from app.metrics import broadcast_messages
        assert broadcast_messages is not None


class TestNodeMetrics:
    """Tests for node metric recording and update functions."""

    def test_update_node_metrics_empty_db(self, test_db: Session):
        """update_node_metrics succeeds with no nodes in database."""
        from app.metrics import update_node_metrics
        # Should not raise
        update_node_metrics(test_db)

    def test_update_node_metrics_with_nodes(
        self,
        test_db: Session,
        sample_lab_with_nodes: tuple,
    ):
        """update_node_metrics counts nodes by state."""
        from app.metrics import update_node_metrics
        # Should not raise when nodes exist
        update_node_metrics(test_db)

    def test_update_node_metrics_handles_errors(self, monkeypatch):
        """update_node_metrics catches exceptions gracefully."""
        from app.metrics import update_node_metrics

        mock_session = MagicMock()
        mock_session.query.side_effect = Exception("DB error")
        # Should not raise
        update_node_metrics(mock_session)


class TestJobMetrics:
    """Tests for job metric recording functions."""

    def test_record_job_started(self):
        """record_job_started does not raise."""
        from app.metrics import record_job_started
        record_job_started("up", queue_wait_seconds=5.0)

    def test_record_job_started_no_wait(self):
        """record_job_started works without queue_wait_seconds."""
        from app.metrics import record_job_started
        record_job_started("down")

    def test_record_job_completed(self):
        """record_job_completed does not raise."""
        from app.metrics import record_job_completed
        record_job_completed("up", duration_seconds=30.0)

    def test_record_job_failed(self):
        """record_job_failed does not raise."""
        from app.metrics import record_job_failed
        record_job_failed("up", duration_seconds=10.0, failure_message="timeout")

    def test_record_job_failed_with_reason(self):
        """record_job_failed accepts an explicit reason."""
        from app.metrics import record_job_failed
        record_job_failed("up", reason="agent_unavailable")

    def test_update_job_metrics_empty(self, test_db: Session):
        """update_job_metrics succeeds with no jobs."""
        from app.metrics import update_job_metrics
        update_job_metrics(test_db)

    def test_update_job_metrics_with_jobs(
        self, test_db: Session, sample_job: models.Job
    ):
        """update_job_metrics counts active jobs by action."""
        from app.metrics import update_job_metrics
        update_job_metrics(test_db)


class TestLabMetrics:
    """Tests for lab metric recording."""

    def test_update_lab_metrics_empty(self, test_db: Session):
        """update_lab_metrics succeeds with no labs."""
        from app.metrics import update_lab_metrics
        update_lab_metrics(test_db)

    def test_update_lab_metrics_with_labs(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """update_lab_metrics counts labs by state."""
        from app.metrics import update_lab_metrics
        update_lab_metrics(test_db)


class TestAgentMetrics:
    """Tests for agent metric recording."""

    def test_update_agent_metrics_empty(self, test_db: Session):
        """update_agent_metrics succeeds with no agents."""
        from app.metrics import update_agent_metrics
        update_agent_metrics(test_db)

    def test_update_agent_metrics_with_host(
        self, test_db: Session, sample_host: models.Host
    ):
        """update_agent_metrics records per-host resource metrics."""
        from app.metrics import update_agent_metrics
        update_agent_metrics(test_db)

    def test_update_agent_metrics_handles_errors(self, monkeypatch):
        """update_agent_metrics catches exceptions gracefully."""
        from app.metrics import update_agent_metrics

        mock_session = MagicMock()
        mock_session.query.side_effect = Exception("DB error")
        update_agent_metrics(mock_session)


class TestEnforcementMetrics:
    """Tests for enforcement metric recording."""

    def test_record_enforcement_action(self):
        """record_enforcement_action does not raise."""
        from app.metrics import record_enforcement_action
        record_enforcement_action("success")
        record_enforcement_action("failed")
        record_enforcement_action("skipped")

    def test_record_enforcement_exhausted(self):
        """record_enforcement_exhausted does not raise."""
        from app.metrics import record_enforcement_exhausted
        record_enforcement_exhausted()

    def test_record_enforcement_skip(self):
        """record_enforcement_skip does not raise."""
        from app.metrics import record_enforcement_skip
        record_enforcement_skip("lab_stopped")

    def test_update_enforcement_metrics(self, test_db: Session):
        """update_enforcement_metrics counts pending enforcement nodes."""
        from app.metrics import update_enforcement_metrics
        update_enforcement_metrics(test_db)

    def test_record_enforcement_duration(self):
        """record_enforcement_duration does not raise."""
        from app.metrics import record_enforcement_duration
        record_enforcement_duration(1.5)


class TestMetricLabels:
    """Tests for correct label values in metrics."""

    def test_normalize_action_label_empty(self):
        """Empty action normalized to 'unknown'."""
        from app.metrics import _normalize_action_label
        assert _normalize_action_label("") == "unknown"
        assert _normalize_action_label(None) == "unknown"

    def test_normalize_action_label_sync(self):
        """sync: prefix normalized to 'sync'."""
        from app.metrics import _normalize_action_label
        assert _normalize_action_label("sync:node:r1") == "sync"

    def test_normalize_action_label_node(self):
        """node: prefix normalized to 'node'."""
        from app.metrics import _normalize_action_label
        assert _normalize_action_label("node:start:r1") == "node"

    def test_normalize_action_label_links(self):
        """links: prefix normalized to 'links'."""
        from app.metrics import _normalize_action_label
        assert _normalize_action_label("links:create") == "links"

    def test_normalize_action_label_simple(self):
        """Simple actions pass through unchanged."""
        from app.metrics import _normalize_action_label
        assert _normalize_action_label("up") == "up"
        assert _normalize_action_label("down") == "down"

    def test_normalize_reason_label(self):
        """Reason labels are lowered and cleaned."""
        from app.metrics import _normalize_reason_label
        result = _normalize_reason_label("Agent Unavailable")
        assert result == "agent_unavailable"

    def test_normalize_reason_label_empty(self):
        """Empty reason label returns 'unknown'."""
        from app.metrics import _normalize_reason_label
        assert _normalize_reason_label("") == "unknown"

    def test_normalize_reason_label_long(self):
        """Very long reason labels are truncated."""
        from app.metrics import _normalize_reason_label
        long_reason = "a" * 200
        result = _normalize_reason_label(long_reason)
        assert len(result) <= 64


class TestInferJobFailureReason:
    """Tests for failure reason inference from error messages."""

    def test_infer_timeout(self):
        """Timeout messages infer timeout reason."""
        from app.metrics import infer_job_failure_reason
        assert infer_job_failure_reason("timed out after 1200s") == "timeout_1200s"
        assert infer_job_failure_reason("timed out after 300s") == "timeout_300s"

    def test_infer_no_healthy_agent(self):
        """No agent messages infer no_healthy_agent reason."""
        from app.metrics import infer_job_failure_reason
        assert infer_job_failure_reason("No healthy agent available") == "no_healthy_agent"

    def test_infer_missing_image(self):
        """Missing image messages infer missing_image reason."""
        from app.metrics import infer_job_failure_reason
        assert infer_job_failure_reason("No image found for device") == "missing_image"

    def test_infer_unknown(self):
        """Unknown messages return 'unknown'."""
        from app.metrics import infer_job_failure_reason
        assert infer_job_failure_reason("Something completely unexpected") == "unknown"

    def test_infer_none(self):
        """None message returns 'unknown'."""
        from app.metrics import infer_job_failure_reason
        assert infer_job_failure_reason(None) == "unknown"

    def test_infer_preflight_connectivity(self):
        """Preflight connectivity failure inferred."""
        from app.metrics import infer_job_failure_reason
        result = infer_job_failure_reason("Preflight connectivity check failed")
        assert result == "preflight_connectivity_failed"

    def test_infer_agent_connection_refused(self):
        """Connection refused inferred."""
        from app.metrics import infer_job_failure_reason
        result = infer_job_failure_reason("Connection refused")
        assert result == "agent_connection_refused"


class TestReconciliationMetrics:
    """Tests for reconciliation cycle metrics."""

    def test_record_reconciliation_cycle(self):
        """record_reconciliation_cycle does not raise."""
        from app.metrics import record_reconciliation_cycle
        record_reconciliation_cycle(duration=2.5, labs_checked=3, state_changes=1)

    def test_record_reconciliation_cycle_no_changes(self):
        """record_reconciliation_cycle works with zero state changes."""
        from app.metrics import record_reconciliation_cycle
        record_reconciliation_cycle(duration=1.0, labs_checked=0, state_changes=0)


class TestBroadcastMetrics:
    """Tests for broadcast metric recording."""

    def test_record_broadcast_success(self):
        """record_broadcast with success=True counts messages."""
        from app.metrics import record_broadcast
        record_broadcast("node_state", success=True)

    def test_record_broadcast_failure(self):
        """record_broadcast with success=False counts failures."""
        from app.metrics import record_broadcast
        record_broadcast("node_state", success=False)


class TestNodeStateTransition:
    """Tests for node state transition metrics."""

    def test_record_node_state_transition(self):
        """record_node_state_transition does not raise."""
        from app.metrics import record_node_state_transition
        record_node_state_transition("running_to_stopped")

    def test_record_node_state_transition_various(self):
        """record_node_state_transition handles various types."""
        from app.metrics import record_node_state_transition
        record_node_state_transition("stopped_to_running")
        record_node_state_transition("error_to_stopped")
        record_node_state_transition("running_to_error")


class TestGetMetrics:
    """Tests for the get_metrics output function."""

    def test_get_metrics_returns_tuple(self):
        """get_metrics returns (bytes, content_type) tuple."""
        from app.metrics import get_metrics
        data, content_type = get_metrics()
        assert isinstance(data, bytes)
        assert isinstance(content_type, str)

    def test_get_metrics_content_type(self):
        """get_metrics content type is text-based."""
        from app.metrics import get_metrics
        _, content_type = get_metrics()
        assert "text" in content_type


class TestUpdateAllMetrics:
    """Tests for the combined update_all_metrics function."""

    def test_update_all_metrics(self, test_db: Session):
        """update_all_metrics runs all metric update functions."""
        from app.metrics import update_all_metrics
        # Should not raise
        update_all_metrics(test_db)


class TestLinkOperTransition:
    """Tests for link operational state transition metrics."""

    def test_record_link_oper_transition(self):
        """record_link_oper_transition does not raise."""
        from app.metrics import record_link_oper_transition
        record_link_oper_transition(
            endpoint="source",
            old_state="unknown",
            new_state="up",
            reason="initial_creation",
            is_cross_host=False,
        )

    def test_record_link_oper_transition_cross_host(self):
        """record_link_oper_transition handles cross-host links."""
        from app.metrics import record_link_oper_transition
        record_link_oper_transition(
            endpoint="target",
            old_state="up",
            new_state="down",
            reason="agent_offline",
            is_cross_host=True,
        )


class TestLinkReservationMetrics:
    """Tests for link endpoint reservation metrics."""

    def test_set_link_endpoint_reservation_metrics(self):
        """set_link_endpoint_reservation_metrics does not raise."""
        from app.metrics import set_link_endpoint_reservation_metrics
        set_link_endpoint_reservation_metrics(
            total=10, missing=1, orphaned=2, conflicts=0
        )
