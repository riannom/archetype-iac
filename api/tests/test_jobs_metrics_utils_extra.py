from __future__ import annotations


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
