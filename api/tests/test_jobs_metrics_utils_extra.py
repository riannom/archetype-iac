from __future__ import annotations


from unittest.mock import MagicMock

import pytest

import app.jobs as jobs_module
import app.metrics as metrics_module

from app import models


def test_has_conflicting_job(test_db, monkeypatch) -> None:
    job = models.Job(
        lab_id="lab",
        user_id=None,
        action="up",
        status="running",
    )
    test_db.add(job)
    test_db.commit()

    # Pass session directly (has_conflicting_job accepts optional session param)
    has_conflict, action = jobs_module.has_conflicting_job("lab", "down", session=test_db)
    assert has_conflict
    assert action == "up"


def test_build_command_invalid_node(monkeypatch) -> None:
    class FakeError(Exception):
        pass

    def fake_node_action_command(provider, lab_id, subaction, node):
        raise jobs_module.ProviderActionError("bad")

    monkeypatch.setattr(jobs_module, "node_action_command", fake_node_action_command)

    with pytest.raises(ValueError):
        jobs_module._build_command("lab", "node:start:node1")


def test_metrics_dummy_when_no_prometheus(monkeypatch) -> None:
    monkeypatch.setattr(metrics_module, "PROMETHEUS_AVAILABLE", False)

    # Should not raise
    metrics_module.record_job_started("up")
    metrics_module.record_job_completed("up", 1.2)
    metrics_module.record_job_failed("up", 1.2)
    metrics_module.record_enforcement_action("success")
    metrics_module.record_enforcement_exhausted()

    data, content_type = metrics_module.get_metrics()
    assert b"Prometheus client" in data
    assert content_type == "text/plain"


def test_infer_job_failure_reason() -> None:
    assert metrics_module.infer_job_failure_reason("Job timed out after 300s") == "timeout_300s"
    assert metrics_module.infer_job_failure_reason("No healthy agent available") == "no_healthy_agent"
    assert metrics_module.infer_job_failure_reason("No image found for node") == "missing_image"
    assert metrics_module.infer_job_failure_reason("Per-link tunnel creation failed on host") == "link_tunnel_creation_failed"
    assert metrics_module.infer_job_failure_reason("Could not find OVS port for ceos_1:eth1") == "ovs_port_missing"
    assert metrics_module.infer_job_failure_reason("Libvirt error: Domain not found") == "libvirt_domain_not_found"
    assert metrics_module.infer_job_failure_reason("unsupported configuration: IDE controllers are unsupported") == "libvirt_unsupported_configuration"
    assert metrics_module.infer_job_failure_reason("ERROR: Preflight connectivity check failed") == "preflight_connectivity_failed"
    assert metrics_module.infer_job_failure_reason("ERROR: Job execution failed on agent") == "agent_job_error"
    assert metrics_module.infer_job_failure_reason("random error") == "unknown"


def test_record_link_oper_transition_normalizes_labels(monkeypatch) -> None:
    metric = MagicMock()
    labeled = MagicMock()
    metric.labels.return_value = labeled

    monkeypatch.setattr(metrics_module, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(metrics_module, "link_oper_transitions", metric)

    metrics_module.record_link_oper_transition(
        endpoint="source",
        old_state="DOWN-ish",
        new_state="UP!",
        reason="Peer Host Offline",
        is_cross_host=True,
    )

    metric.labels.assert_called_once_with(
        endpoint="source",
        old_state="down_ish",
        new_state="up",
        reason="peer_host_offline",
        is_cross_host="true",
    )
    labeled.inc.assert_called_once()
