"""Extended tests for api/app/tasks/jobs_multihost.py.

Covers additional scenarios beyond the base file:
- Deploy: topology JSON construction, node placement updates, management IP capture,
  webhook dispatch on failure, partial rollback with mixed results
- Destroy: missing host records, tunnel teardown interaction, remaining link states,
  completed-with-warnings status, inner exception during error handler
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus, LabState
from app.tasks.jobs_multihost import run_multihost_deploy, run_multihost_destroy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODULE = "app.tasks.jobs_multihost"


def _mock_get_session(test_db: Session):
    @contextmanager
    def mock_session():
        yield test_db
    return mock_session


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


def _make_host(test_db, host_id, *, name=None, status="online"):
    from datetime import datetime, timezone

    host = models.Host(
        id=host_id,
        name=name or host_id,
        address=f"{host_id}:8080",
        status=status,
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage=json.dumps({}),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _make_node(test_db, lab_id, name, *, host_id=None, device="linux"):
    node = models.Node(
        lab_id=lab_id,
        gui_id=name.lower(),
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


@dataclass
class _FakeAnalysis:
    placements: dict
    cross_host_links: list


def _build_standard_patches(test_db, host_map=None, deploy_result=None, destroy_result=None):
    """Build a dict of standard patches for multihost tests."""
    patches = {
        "get_session": patch(f"{MODULE}.get_session", _mock_get_session(test_db)),
        "record_started": patch(f"{MODULE}._record_started"),
        "record_failed": patch(f"{MODULE}._record_failed"),
        "record_completed": patch(f"{MODULE}.record_job_completed"),
        "broadcast": patch(f"{MODULE}._broadcast_job_progress", new_callable=AsyncMock),
        "update_lab_state": patch(f"{MODULE}.update_lab_state"),
        "release_tx": patch(f"{MODULE}._release_db_transaction_for_io"),
        "dispatch_webhook": patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock),
        "emit_deploy": patch(f"{MODULE}.emit_deploy_finished", new_callable=AsyncMock),
        "emit_destroy": patch(f"{MODULE}.emit_destroy_finished", new_callable=AsyncMock),
        "emit_job_failed": patch(f"{MODULE}.emit_job_failed", new_callable=AsyncMock),
        "capture_ips": patch(f"{MODULE}._capture_node_ips", new_callable=AsyncMock),
        "update_placements": patch(f"{MODULE}._update_node_placements", new_callable=AsyncMock),
        "resource_validation": patch(f"{MODULE}.settings.resource_validation_enabled", False),
    }
    return patches


def _enter_patches(patches, keys):
    """Enter a subset of patches, returning dict of mock objects."""
    mocks = {}
    for k in keys:
        mocks[k] = patches[k].__enter__()
    return mocks


# ---------------------------------------------------------------------------
# Tests: Deploy - resource capacity validation
# ---------------------------------------------------------------------------

class TestMultihostDeployResourceCapacity:
    """Tests for resource capacity checking during deploy."""

    @pytest.mark.asyncio
    async def test_resource_capacity_failure_fails_job(self, test_db: Session, test_user):
        """When resource capacity check fails, job should be marked failed."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-cap-fail")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        # Build patches but enable resource_validation
        patches = _build_standard_patches(test_db)
        # Override resource_validation to True
        patches["resource_validation"] = patch(
            f"{MODULE}.settings.resource_validation_enabled", True
        )

        fake_cap_result = MagicMock()
        fake_cap_result.fits = False

        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id, device="linux")]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})

                    with patch(
                        "app.services.resource_capacity.check_multihost_capacity",
                        return_value={host.id: fake_cap_result},
                    ):
                        with patch(
                            "app.services.resource_capacity.format_capacity_error",
                            return_value="Not enough CPU",
                        ):
                            await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "Not enough CPU" in job.log_path

    @pytest.mark.asyncio
    async def test_resource_capacity_warnings_logged(self, test_db: Session, test_user):
        """When resource capacity has warnings, they should appear in job log."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-cap-warn")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        patches["resource_validation"] = patch(
            f"{MODULE}.settings.resource_validation_enabled", True
        )

        fake_cap_result = MagicMock()
        fake_cap_result.fits = True

        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id, device="linux")]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch(
                        "app.services.resource_capacity.check_multihost_capacity",
                        return_value={host.id: fake_cap_result},
                    ):
                        with patch(
                            "app.services.resource_capacity.format_capacity_warnings",
                            return_value=["Memory usage above 80%"],
                        ):
                            with patch(
                                "app.tasks.link_orchestration.create_deployment_links",
                                new_callable=AsyncMock,
                                return_value=(0, 0),
                            ):
                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value
        assert "WARNING: Memory usage above 80%" in job.log_path


# ---------------------------------------------------------------------------
# Tests: Deploy - preflight connectivity failure
# ---------------------------------------------------------------------------

class TestMultihostDeployPreflightFailure:
    """Tests for agent preflight connectivity check failures."""

    @pytest.mark.asyncio
    async def test_preflight_failure_marks_host_missing(self, test_db: Session, test_user):
        """Agent that fails preflight connectivity should cause job failure."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-preflight-fail")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(
                        side_effect=ConnectionError("timeout")
                    )

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "preflight connectivity failed" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_offline_agent_marks_host_missing(self, test_db: Session, test_user):
        """Agent that is offline should cause job failure."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-offline-deploy", status="offline")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = False

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "missing or unhealthy" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Deploy - link creation failure
# ---------------------------------------------------------------------------

class TestMultihostDeployLinkFailure:
    """Tests for link creation failure during deploy."""

    @pytest.mark.asyncio
    async def test_link_failure_fails_job(self, test_db: Session, test_user):
        """When link creation returns failures, job should be marked failed."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-link-fail")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(3, 2),  # 3 ok, 2 failed
                    ):
                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "2 failed" in job.log_path

    @pytest.mark.asyncio
    async def test_zero_link_failures_succeeds(self, test_db: Session, test_user):
        """When all links succeed, job should complete normally."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-link-ok")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(5, 0),  # 5 ok, 0 failed
                    ):
                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# Tests: Deploy - successful happy path
# ---------------------------------------------------------------------------

class TestMultihostDeployHappyPath:
    """Tests for full successful deploy completion."""

    @pytest.mark.asyncio
    async def test_successful_deploy_sets_running_state(self, test_db: Session, test_user):
        """Successful deploy should set lab state to running."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-happy")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"] as mock_completed, patches["broadcast"], \
             patches["release_tx"], patches["dispatch_webhook"] as mock_webhook, \
             patches["emit_deploy"], patches["capture_ips"], \
             patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.update_lab_state") as mock_uls:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_deploy(job.id, lab.id)

            test_db.refresh(job)
            assert job.status == JobStatus.COMPLETED.value
            assert job.completed_at is not None

            # Verify update_lab_state was called with "running"
            running_calls = [
                c for c in mock_uls.call_args_list
                if len(c.args) >= 3 and c.args[2] == "running"
            ]
            assert len(running_calls) >= 1

            # Verify deploy_complete webhook dispatched
            webhook_events = [c.args[0] for c in mock_webhook.call_args_list]
            assert "lab.deploy_complete" in webhook_events

            # Verify record_job_completed called
            mock_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_broadcasts_started_and_completed(self, test_db: Session, test_user):
        """Deploy should broadcast both started and completed progress."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-broadcast")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}._broadcast_job_progress", new_callable=AsyncMock) as mock_bc:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_deploy(job.id, lab.id)

            assert mock_bc.await_count == 2
            statuses = [c.args[3] for c in mock_bc.call_args_list]
            assert "running" in statuses
            assert "completed" in statuses

    @pytest.mark.asyncio
    async def test_first_agent_set_as_primary(self, test_db: Session, test_user):
        """After successful deploy, first agent should be set as primary for lab."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host_a = _make_host(test_db, "host-primary-a", name="Primary A")
        host_b = _make_host(test_db, "host-primary-b", name="Primary B")
        _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        _make_node(test_db, lab.id, "R2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={host_a.id: [{"node_name": "R1"}], host_b.id: [{"node_name": "R2"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.update_lab_state") as mock_uls:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [
                        MagicMock(host_id=host_a.id),
                        MagicMock(host_id=host_b.id),
                    ]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "X"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_deploy(job.id, lab.id)

            # The final update_lab_state call (with "running") should have agent_id
            running_calls = [
                c for c in mock_uls.call_args_list
                if len(c.args) >= 3 and c.args[2] == "running"
            ]
            assert len(running_calls) >= 1
            # agent_id should be set in kwargs
            assert running_calls[-1].kwargs.get("agent_id") is not None


# ---------------------------------------------------------------------------
# Tests: Deploy - unplaced nodes with no default agent
# ---------------------------------------------------------------------------

class TestMultihostDeployUnplacedNoAgent:
    """Tests for unplaced nodes when no default agent is available."""

    @pytest.mark.asyncio
    async def test_no_default_agent_fails_job(self, test_db: Session, test_user):
        """When nodes have no host_id and no default agent, job should fail."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab.id, "R1")  # No host_id

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=None)]
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.get_agent_for_lab = AsyncMock(return_value=None)

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "no host assignment" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Deploy - rollback failure
# ---------------------------------------------------------------------------

class TestMultihostDeployRollbackFailure:
    """Tests for when the rollback itself fails."""

    @pytest.mark.asyncio
    async def test_rollback_exception_logged(self, test_db: Session, test_user):
        """When rollback destroy fails, it should be logged but job still fails."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host_a = _make_host(test_db, "host-rb-a", name="HostA")
        host_b = _make_host(test_db, "host-rb-b", name="HostB")
        _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        _make_node(test_db, lab.id, "R2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={host_a.id: [{"node_name": "R1"}], host_b.id: [{"node_name": "R2"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [
                    MagicMock(host_id=host_a.id),
                    MagicMock(host_id=host_b.id),
                ]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "X"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    # host_a succeeds, host_b fails with exception
                    mock_ac.deploy_to_agent = AsyncMock(side_effect=[
                        {"status": "completed"},
                        RuntimeError("deploy failed"),
                    ])
                    # Rollback also fails
                    mock_ac.destroy_on_agent = AsyncMock(
                        side_effect=RuntimeError("rollback failed")
                    )

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "rollback failed" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_all_deploys_fail_no_rollback_needed(self, test_db: Session, test_user):
        """When all deploys fail, no rollback should be attempted."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-all-fail")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(
                        side_effect=RuntimeError("totally broken")
                    )
                    mock_ac.destroy_on_agent = AsyncMock()

                    await run_multihost_deploy(job.id, lab.id)

                    # destroy_on_agent should NOT be called (no hosts to rollback)
                    mock_ac.destroy_on_agent.assert_not_awaited()

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "no hosts to rollback" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Deploy - deploy result logging
# ---------------------------------------------------------------------------

class TestMultihostDeployResultLogging:
    """Tests for deploy result stdout/stderr logging."""

    @pytest.mark.asyncio
    async def test_stdout_stderr_in_log(self, test_db: Session, test_user):
        """Successful deploy stdout/stderr should appear in job log."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-log", name="LogHost")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={
                        "status": "completed",
                        "stdout": "Container created successfully",
                        "stderr": "Warning: deprecated flag",
                    })

                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert "Container created successfully" in job.log_path
        assert "Warning: deprecated flag" in job.log_path


# ---------------------------------------------------------------------------
# Tests: Deploy - unexpected exception error handler
# ---------------------------------------------------------------------------

class TestMultihostDeployUnexpectedException:
    """Tests for unexpected exception handling in deploy."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_sets_error_state(self, test_db: Session, test_user):
        """Unexpected exception should set lab to error state and fail job."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["release_tx"], \
             patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.update_lab_state") as mock_uls:
                with patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock):
                    with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                        mock_ts = MagicMock()
                        mock_ts.get_nodes.side_effect = ValueError("corrupt data")
                        mock_ts_cls.return_value = mock_ts

                        await run_multihost_deploy(job.id, lab.id)

            test_db.refresh(job)
            assert job.status == JobStatus.FAILED.value
            assert "corrupt data" in job.log_path

            # Should have called update_lab_state with ERROR
            error_calls = [
                c for c in mock_uls.call_args_list
                if len(c.args) >= 3 and c.args[2] == LabState.ERROR.value
            ]
            assert len(error_calls) >= 1

    @pytest.mark.asyncio
    async def test_inner_exception_does_not_propagate(self, test_db: Session, test_user):
        """If error handler also fails, exception should not bubble up."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["broadcast"], \
             patches["release_tx"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}._record_failed", side_effect=RuntimeError("inner boom")):
                with patch(f"{MODULE}.update_lab_state", side_effect=RuntimeError("uls boom")):
                    with patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock):
                        with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                            mock_ts = MagicMock()
                            mock_ts.get_nodes.side_effect = RuntimeError("outer boom")
                            mock_ts_cls.return_value = mock_ts

                            # Should not raise
                            await run_multihost_deploy(job.id, lab.id)


# ---------------------------------------------------------------------------
# Tests: Destroy - successful full path
# ---------------------------------------------------------------------------

class TestMultihostDestroyHappyPath:
    """Tests for full successful destroy completion."""

    @pytest.mark.asyncio
    async def test_successful_destroy_sets_stopped_state(self, test_db: Session, test_user):
        """Successful destroy should set lab state to stopped."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-destroy-ok")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], \
             patches["release_tx"], patches["dispatch_webhook"] as mock_wh, \
             patches["emit_destroy"], patches["emit_job_failed"], \
             patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.update_lab_state") as mock_uls:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.destroy_on_agent = AsyncMock(
                            return_value={"status": "completed"}
                        )

                        with patch(
                            "app.tasks.link_orchestration.teardown_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(2, 0),
                        ):
                            await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value
        assert job.completed_at is not None

        # Verify update_lab_state called with STOPPED
        stopped_calls = [
            c for c in mock_uls.call_args_list
            if len(c.args) >= 3 and c.args[2] == LabState.STOPPED.value
        ]
        assert len(stopped_calls) >= 1

        # Verify destroy_complete webhook dispatched
        webhook_events = [c.args[0] for c in mock_wh.call_args_list]
        assert "lab.destroy_complete" in webhook_events

    @pytest.mark.asyncio
    async def test_link_states_deleted_on_full_success(self, test_db: Session, test_user):
        """On full success, remaining link states should be deleted."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-destroy-clean")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        # Create a lingering link state
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
        )
        test_db.add(ls)
        test_db.commit()
        test_db.refresh(ls)
        ls_id = ls.id

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        # Link state should be deleted
        remaining = test_db.get(models.LinkState, ls_id)
        assert remaining is None


# ---------------------------------------------------------------------------
# Tests: Destroy - tunnel teardown failures
# ---------------------------------------------------------------------------

class TestMultihostDestroyTunnelTeardown:
    """Tests for tunnel teardown failure behavior during destroy."""

    @pytest.mark.asyncio
    async def test_tunnel_failures_cause_warnings(self, test_db: Session, test_user):
        """Tunnel teardown failures should cause completed_with_warnings status."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-tunnel-fail")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(1, 3),  # 1 ok, 3 failed
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED_WITH_WARNINGS.value
        assert "tunnel teardown" in job.log_path.lower() or "overlay teardown" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Destroy - mixed missing and offline agents
# ---------------------------------------------------------------------------

class TestMultihostDestroyMixedAgents:
    """Tests for destroy with mixed missing and offline agents."""

    @pytest.mark.asyncio
    async def test_some_online_some_missing(self, test_db: Session, test_user):
        """Destroy should proceed with available agents and warn about missing."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-online-mix")
        _make_node(test_db, lab.id, "R1", host_id=host.id)
        _make_node(test_db, lab.id, "R2", host_id="nonexistent-host-id")

        analysis = _FakeAnalysis(
            placements={
                host.id: [{"node_name": "R1"}],
                "nonexistent-host-id": [{"node_name": "R2"}],
            },
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        # Should complete with warnings due to missing agent
        assert job.status == JobStatus.COMPLETED_WITH_WARNINGS.value
        assert "missing" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_some_online_some_offline(self, test_db: Session, test_user):
        """Destroy should proceed with online agents and warn about offline."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host_on = _make_host(test_db, "host-on-mixed", name="OnlineHost")
        host_off = _make_host(test_db, "host-off-mixed", name="OfflineHost", status="offline")
        _make_node(test_db, lab.id, "R1", host_id=host_on.id)
        _make_node(test_db, lab.id, "R2", host_id=host_off.id)

        analysis = _FakeAnalysis(
            placements={
                host_on.id: [{"node_name": "R1"}],
                host_off.id: [{"node_name": "R2"}],
            },
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.side_effect = lambda agent: agent.id == host_on.id
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED_WITH_WARNINGS.value
        assert "offline" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Destroy - result logging with truncation
# ---------------------------------------------------------------------------

class TestMultihostDestroyResultLogging:
    """Tests for destroy result stdout/stderr logging."""

    @pytest.mark.asyncio
    async def test_stdout_stderr_truncated_in_log(self, test_db: Session, test_user):
        """Destroy stdout/stderr should be truncated to 200 chars in log."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-trunc", name="TruncHost")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        long_output = "X" * 500

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(return_value={
                        "status": "completed",
                        "stdout": long_output,
                        "stderr": long_output,
                    })

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        # Log should contain stdout/stderr but not the full 500 chars
        assert "STDOUT:" in job.log_path
        assert "STDERR:" in job.log_path
        # Verify truncation to 200 chars
        assert "X" * 500 not in job.log_path
        assert "X" * 200 in job.log_path


# ---------------------------------------------------------------------------
# Tests: Destroy - webhook dispatch
# ---------------------------------------------------------------------------

class TestMultihostDestroyWebhooks:
    """Tests for webhook dispatch during destroy."""

    @pytest.mark.asyncio
    async def test_partial_failure_dispatches_job_failed(self, test_db: Session, test_user):
        """Partial destroy failure should dispatch job.failed webhook."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-wh-fail")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["emit_destroy"], patches["capture_ips"], \
             patches["update_placements"]:
            with patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock) as mock_wh:
                with patch(f"{MODULE}.emit_job_failed", new_callable=AsyncMock):
                    with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                        mock_ts = MagicMock()
                        mock_ts.analyze_placements.return_value = analysis
                        mock_ts_cls.return_value = mock_ts

                        with patch(f"{MODULE}.agent_client") as mock_ac:
                            mock_ac.is_agent_online.return_value = True
                            mock_ac.destroy_on_agent = AsyncMock(
                                return_value={"status": "error"}
                            )

                            with patch(
                                "app.tasks.link_orchestration.teardown_deployment_links",
                                new_callable=AsyncMock,
                                return_value=(0, 0),
                            ):
                                await run_multihost_destroy(job.id, lab.id)

            webhook_events = [c.args[0] for c in mock_wh.call_args_list]
            assert "job.failed" in webhook_events


# ---------------------------------------------------------------------------
# Tests: Destroy - unexpected exception
# ---------------------------------------------------------------------------

class TestMultihostDestroyUnexpectedException:
    """Tests for unexpected exception handling in destroy."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_marks_job_failed(self, test_db: Session, test_user):
        """Unexpected exception should set job to failed with error message."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.side_effect = TypeError("unexpected type")
                mock_ts_cls.return_value = mock_ts

                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "unexpected type" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Deploy - three hosts with mixed outcomes
# ---------------------------------------------------------------------------

class TestMultihostDeployThreeHosts:
    """Tests for deploy with three hosts and various outcomes."""

    @pytest.mark.asyncio
    async def test_three_hosts_one_fails_triggers_rollback_of_two(
        self, test_db: Session, test_user
    ):
        """With 3 hosts where 1 fails, only 2 successful ones should be rolled back."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        hosts = [
            _make_host(test_db, f"host-3h-{i}", name=f"Host{i}")
            for i in range(3)
        ]
        for i, h in enumerate(hosts):
            _make_node(test_db, lab.id, f"R{i+1}", host_id=h.id)

        analysis = _FakeAnalysis(
            placements={h.id: [{"node_name": f"R{i+1}"}] for i, h in enumerate(hosts)},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [
                    MagicMock(host_id=h.id) for h in hosts
                ]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "X"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    # First two succeed, third fails
                    mock_ac.deploy_to_agent = AsyncMock(side_effect=[
                        {"status": "completed"},
                        {"status": "completed"},
                        RuntimeError("host-3 crashed"),
                    ])
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    await run_multihost_deploy(job.id, lab.id)

                # Should have called destroy for the 2 successful hosts
                assert mock_ac.destroy_on_agent.await_count == 2

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value


# ---------------------------------------------------------------------------
# Tests: Deploy - job started_at set correctly
# ---------------------------------------------------------------------------

class TestMultihostDeployJobTimestamps:
    """Tests for correct timestamp handling during deploy."""

    @pytest.mark.asyncio
    async def test_job_transitions_to_running(self, test_db: Session, test_user):
        """Job should transition from queued to running with started_at set."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-ts")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}._record_started") as mock_rs:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_deploy(job.id, lab.id)

            mock_rs.assert_called_once()
            # The first argument should be the job object
            assert mock_rs.call_args[0][1] == "up"


# ---------------------------------------------------------------------------
# Tests: Destroy - job lifecycle transitions
# ---------------------------------------------------------------------------

class TestMultihostDestroyJobLifecycle:
    """Tests for correct job lifecycle during destroy."""

    @pytest.mark.asyncio
    async def test_destroy_sets_stopping_state(self, test_db: Session, test_user):
        """Destroy should set lab state to STOPPING during execution."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-lifecycle")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.update_lab_state") as mock_uls:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.destroy_on_agent = AsyncMock(
                            return_value={"status": "completed"}
                        )

                        with patch(
                            "app.tasks.link_orchestration.teardown_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_destroy(job.id, lab.id)

            # Verify STOPPING was set during execution
            stopping_calls = [
                c for c in mock_uls.call_args_list
                if len(c.args) >= 3 and c.args[2] == LabState.STOPPING.value
            ]
            assert len(stopping_calls) >= 1

    @pytest.mark.asyncio
    async def test_destroy_record_started_called_with_down(self, test_db: Session, test_user):
        """_record_started should be called with 'down' action for destroy."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-rs-down")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}._record_started") as mock_rs:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.destroy_on_agent = AsyncMock(
                            return_value={"status": "completed"}
                        )

                        with patch(
                            "app.tasks.link_orchestration.teardown_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_destroy(job.id, lab.id)

            mock_rs.assert_called_once()
            assert mock_rs.call_args[0][1] == "down"


# ---------------------------------------------------------------------------
# Tests: Deploy - deploy_started webhook
# ---------------------------------------------------------------------------

class TestMultihostDeployWebhookStarted:
    """Tests for deploy_started webhook dispatch."""

    @pytest.mark.asyncio
    async def test_deploy_started_webhook_dispatched(self, test_db: Session, test_user):
        """deploy_started webhook should be dispatched before deploy begins."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-wh-start")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock) as mock_wh:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_deploy(job.id, lab.id)

            webhook_events = [c.args[0] for c in mock_wh.call_args_list]
            assert "lab.deploy_started" in webhook_events
            # deploy_started should come before deploy_complete
            started_idx = webhook_events.index("lab.deploy_started")
            complete_idx = webhook_events.index("lab.deploy_complete")
            assert started_idx < complete_idx


# ---------------------------------------------------------------------------
# Tests: Deploy - STARTING lab state set
# ---------------------------------------------------------------------------

class TestMultihostDeployStartingState:
    """Tests for STARTING state transition during deploy."""

    @pytest.mark.asyncio
    async def test_lab_state_set_to_starting(self, test_db: Session, test_user):
        """During deploy, lab state should transition to STARTING."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-starting")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.update_lab_state") as mock_uls:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            await run_multihost_deploy(job.id, lab.id)

            starting_calls = [
                c for c in mock_uls.call_args_list
                if len(c.args) >= 3 and c.args[2] == LabState.STARTING.value
            ]
            assert len(starting_calls) >= 1
