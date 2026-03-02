from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app import models
import app.tasks.job_health as job_health


class _DummyResponse:
    def __init__(self, *, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        result = self._handler(url)
        if isinstance(result, Exception):
            raise result
        return result


def _fake_get_session(session):
    @contextmanager
    def _get_session():
        yield session

    return _get_session


def _mk_host(test_db, *, host_id: str, status: str = "online", address: str = "127.0.0.1:8080") -> models.Host:
    host = models.Host(
        id=host_id,
        name=host_id,
        address=address,
        status=status,
        capabilities="{}",
        resource_usage="{}",
        version="1.0",
        last_heartbeat=datetime.now(timezone.utc),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _mk_lab(test_db, *, lab_id: str = "lab-a") -> models.Lab:
    lab = models.Lab(
        id=lab_id,
        name=f"Lab {lab_id}",
        owner_id="owner",
        provider="docker",
        state="running",
        workspace_path=f"/tmp/{lab_id}",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _mk_job(
    test_db,
    *,
    lab_id: str | None,
    action: str,
    status: str = "running",
    agent_id: str | None = None,
    retry_count: int = 0,
    log_path: str | None = None,
) -> models.Job:
    job = models.Job(
        lab_id=lab_id,
        user_id=None,
        action=action,
        status=status,
        agent_id=agent_id,
        retry_count=retry_count,
        log_path=log_path,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def test_file_path_and_log_read_helpers(monkeypatch, tmp_path) -> None:
    log_file = tmp_path / "job.log"
    content = "A" * 13050
    log_file.write_text(content)

    assert job_health._is_file_path(None) is False
    assert job_health._is_file_path("line1\nline2") is False
    assert job_health._is_file_path("relative/path.log") is False
    assert job_health._is_file_path("/" + "x" * 4100) is False
    assert job_health._is_file_path(str(log_file)) is True

    assert job_health._read_log_for_classification("inline text") == "inline text"
    tail = job_health._read_log_for_classification(str(log_file))
    assert tail is not None
    assert len(tail) == 12000

    monkeypatch.setattr(Path, "is_file", lambda _self: (_ for _ in ()).throw(OSError("boom")))
    assert job_health._is_file_path(str(log_file)) is False

    monkeypatch.setattr(Path, "is_file", lambda _self: True)
    monkeypatch.setattr(Path, "read_text", lambda _self, errors=None: (_ for _ in ()).throw(RuntimeError("read fail")))
    assert job_health._read_log_for_classification(str(log_file)) is None


@pytest.mark.asyncio
async def test_check_stuck_jobs_handles_inner_and_outer_exceptions(test_db, monkeypatch) -> None:
    lab = _mk_lab(test_db, lab_id="lab-inner")
    _mk_job(test_db, lab_id=lab.id, action="up", status="running")

    monkeypatch.setattr(job_health, "get_session", _fake_get_session(test_db))
    monkeypatch.setattr(job_health, "_check_single_job", AsyncMock(side_effect=RuntimeError("per-job fail")))

    await job_health.check_stuck_jobs()

    # Outer exception path
    monkeypatch.setattr(job_health, "utcnow", lambda: (_ for _ in ()).throw(RuntimeError("clock fail")))
    await job_health.check_stuck_jobs()


@pytest.mark.asyncio
async def test_check_single_job_non_retryable_and_offline_retry_path(test_db, monkeypatch) -> None:
    lab = _mk_lab(test_db, lab_id="lab-single")
    offline_host = _mk_host(test_db, host_id="offline-h", status="offline")

    # Non-retryable branch.
    job = _mk_job(
        test_db,
        lab_id=lab.id,
        action="up",
        status="running",
        agent_id=offline_host.id,
        log_path="inline log",
    )

    monkeypatch.setattr(job_health, "is_job_stuck", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_health, "_timed_out_job_is_non_retryable", lambda _a, _b: (True, "missing_image"))
    fail = AsyncMock(return_value=None)
    monkeypatch.setattr(job_health, "_fail_job", fail)

    await job_health._check_single_job(test_db, job, datetime.now(timezone.utc))

    fail.assert_awaited_once()
    assert "missing_image" in fail.await_args.kwargs["reason"]

    # Retry branch with offline-agent exclusion.
    retry_job = _mk_job(
        test_db,
        lab_id=lab.id,
        action="up",
        status="running",
        agent_id=offline_host.id,
    )
    monkeypatch.setattr(job_health, "_timed_out_job_is_non_retryable", lambda _a, _b: (False, None))
    retry = AsyncMock(return_value=None)
    monkeypatch.setattr(job_health, "_retry_job", retry)

    await job_health._check_single_job(test_db, retry_job, datetime.now(timezone.utc))

    retry.assert_awaited_once()
    assert retry.await_args.kwargs["exclude_agent"] == offline_host.id


@pytest.mark.asyncio
async def test_trigger_job_execution_branch_coverage(test_db, monkeypatch) -> None:
    import app.services.topology as topology_module
    import app.tasks.jobs as jobs_module
    import app.utils.lab as lab_utils

    # Missing lab -> immediate failure.
    missing_lab_job = _mk_job(test_db, lab_id="missing-lab", action="up", status="queued")
    await job_health._trigger_job_execution(test_db, missing_lab_job)
    assert missing_lab_job.status == "failed"
    assert "lab not found" in (missing_lab_job.log_path or "")

    lab = _mk_lab(test_db, lab_id="lab-trigger")

    monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _lab: "docker")

    # No healthy agent branch.
    no_agent_job = _mk_job(test_db, lab_id=lab.id, action="down", status="queued")
    monkeypatch.setattr(job_health.agent_client, "get_healthy_agent", AsyncMock(return_value=None))
    await job_health._trigger_job_execution(test_db, no_agent_job)
    assert no_agent_job.status == "failed"
    assert "no healthy agent" in (no_agent_job.log_path or "")

    healthy = MagicMock()
    healthy.id = "agent-x"
    monkeypatch.setattr(job_health.agent_client, "get_healthy_agent", AsyncMock(return_value=healthy))

    # Action up with no topology branch.
    class NoTopologyService:
        def __init__(self, _session):
            pass

        def has_nodes(self, _lab_id):
            return False

    monkeypatch.setattr(topology_module, "TopologyService", NoTopologyService)
    up_job = _mk_job(test_db, lab_id=lab.id, action="up", status="queued")
    await job_health._trigger_job_execution(test_db, up_job)
    assert up_job.status == "failed"
    assert "no topology" in (up_job.log_path or "")

    # down + sync schedule paths.
    run_agent_job = AsyncMock(return_value=None)
    run_node_reconcile = AsyncMock(return_value=None)
    monkeypatch.setattr(jobs_module, "run_agent_job", run_agent_job)
    monkeypatch.setattr(jobs_module, "run_node_reconcile", run_node_reconcile)

    created_tasks: list[str] = []

    def _safe_create_task(coro, *, name: str):
        created_tasks.append(name)
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(job_health, "safe_create_task", _safe_create_task)

    down_job = _mk_job(test_db, lab_id=lab.id, action="down", status="queued")
    await job_health._trigger_job_execution(test_db, down_job)

    # sync:lab branch pulls node ids from node_states
    test_db.add_all(
        [
            models.NodeState(lab_id=lab.id, node_id="n1", node_name="n1", desired_state="running", actual_state="stopped"),
            models.NodeState(lab_id=lab.id, node_id="n2", node_name="n2", desired_state="running", actual_state="stopped"),
        ]
    )
    test_db.commit()

    sync_job = _mk_job(test_db, lab_id=lab.id, action="sync:lab", status="queued")
    await job_health._trigger_job_execution(test_db, sync_job)

    unknown_job = _mk_job(test_db, lab_id=lab.id, action="weird-action", status="queued")
    await job_health._trigger_job_execution(test_db, unknown_job)

    assert any(name.startswith("retry:destroy:") for name in created_tasks)
    assert any(name.startswith("retry:sync:") for name in created_tasks)
    assert unknown_job.status == "failed"
    assert "unknown action type" in (unknown_job.log_path or "")


@pytest.mark.asyncio
async def test_check_agent_active_transfers_branches(monkeypatch) -> None:
    host = MagicMock()
    host.address = "127.0.0.1:8080"
    host.name = "agent-host"

    monkeypatch.setattr(job_health.agent_client, "_get_agent_auth_headers", lambda: {"Authorization": "Bearer x"})

    # 404 -> False
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(lambda _url: _DummyResponse(status_code=404)),
    )
    assert await job_health._check_agent_active_transfers(host, "job-1") is False

    # 200 + active -> True
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            lambda _url: _DummyResponse(status_code=200, payload={"active_jobs": {"job-2": {}}})
        ),
    )
    assert await job_health._check_agent_active_transfers(host, "job-2") is True

    # Exception -> False
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(lambda _url: RuntimeError("network down")),
    )
    assert await job_health._check_agent_active_transfers(host, "job-3") is False


@pytest.mark.asyncio
async def test_check_stuck_locks_releases_stuck_entries(test_db, monkeypatch) -> None:
    host = _mk_host(test_db, host_id="lock-host", status="online")
    host.last_heartbeat = datetime.now(timezone.utc)
    test_db.commit()

    monkeypatch.setattr(job_health, "get_session", _fake_get_session(test_db))
    monkeypatch.setattr(
        job_health.agent_client,
        "get_agent_lock_status",
        AsyncMock(return_value={"locks": [{"is_stuck": True, "lab_id": "lab-1", "age_seconds": 120.0}]}),
    )
    release = AsyncMock(return_value={"status": "cleared"})
    monkeypatch.setattr(job_health.agent_client, "release_agent_lock", release)

    await job_health.check_stuck_locks()

    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_orphaned_image_sync_status_branches(test_db, sample_lab, monkeypatch) -> None:
    monkeypatch.setattr(job_health, "get_session", _fake_get_session(test_db))

    # Case 1: no node definition/image => clear
    ns_no_node = models.NodeState(
        lab_id=sample_lab.id,
        node_id="n-missing",
        node_name="missing-node",
        desired_state="running",
        actual_state="running",
        image_sync_status="syncing",
        image_sync_message="pending",
    )

    # Case 2: node exists but no placement => clear
    ns_no_placement = models.NodeState(
        lab_id=sample_lab.id,
        node_id="n-no-placement",
        node_name="node-no-placement",
        desired_state="running",
        actual_state="running",
        image_sync_status="checking",
        image_sync_message="pending",
    )

    # Case 3: node+placement with no active sync => clear + broadcast
    ns_orphaned = models.NodeState(
        lab_id=sample_lab.id,
        node_id="n-orphaned",
        node_name="node-orphaned",
        desired_state="running",
        actual_state="running",
        image_sync_status="syncing",
        image_sync_message="pending",
    )

    # Case 4: active sync exists => keep status
    ns_active = models.NodeState(
        lab_id=sample_lab.id,
        node_id="n-active",
        node_name="node-active",
        desired_state="running",
        actual_state="running",
        image_sync_status="syncing",
        image_sync_message="pending",
    )

    test_db.add_all([ns_no_node, ns_no_placement, ns_orphaned, ns_active])

    node_no_placement = models.Node(
        lab_id=sample_lab.id,
        gui_id="g-no-placement",
        display_name="node-no-placement",
        container_name="node-no-placement",
        node_type="device",
        device="ceos",
        image="docker:ceos:1",
    )
    node_orphaned = models.Node(
        lab_id=sample_lab.id,
        gui_id="g-orphaned",
        display_name="node-orphaned",
        container_name="node-orphaned",
        node_type="device",
        device="ceos",
        image="docker:ceos:2",
    )
    node_active = models.Node(
        lab_id=sample_lab.id,
        gui_id="g-active",
        display_name="node-active",
        container_name="node-active",
        node_type="device",
        device="ceos",
        image="docker:ceos:3",
    )
    test_db.add_all([node_no_placement, node_orphaned, node_active])
    test_db.commit()

    host = _mk_host(test_db, host_id="sync-host", status="online")
    test_db.add_all(
        [
            models.NodePlacement(lab_id=sample_lab.id, node_name="node-orphaned", host_id=host.id, status="deployed"),
            models.NodePlacement(lab_id=sample_lab.id, node_name="node-active", host_id=host.id, status="deployed"),
        ]
    )
    test_db.add(
        models.ImageSyncJob(
            image_id="docker:ceos:3",
            host_id=host.id,
            status="transferring",
            started_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    import app.services.broadcaster as broadcaster

    broadcast = AsyncMock(return_value=None)
    monkeypatch.setattr(broadcaster, "broadcast_node_state_change", broadcast)

    await job_health.check_orphaned_image_sync_status()

    test_db.refresh(ns_no_node)
    test_db.refresh(ns_no_placement)
    test_db.refresh(ns_orphaned)
    test_db.refresh(ns_active)

    assert ns_no_node.image_sync_status is None
    assert ns_no_placement.image_sync_status is None
    assert ns_orphaned.image_sync_status is None
    assert ns_active.image_sync_status == "syncing"
    broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_job_health_monitor_error_then_cancel(monkeypatch) -> None:
    monkeypatch.setattr(job_health.settings, "job_health_check_interval", 0)
    monkeypatch.setattr(job_health.settings, "job_max_retries", 3)

    async def _fake_sleep(_interval):
        return None

    # Allow first loop to raise generic error, second to stop via cancellation.
    calls = {"n": 0}

    async def _check_stuck_jobs():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        raise asyncio.CancelledError()

    monkeypatch.setattr(job_health.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(job_health, "check_stuck_jobs", _check_stuck_jobs)
    monkeypatch.setattr(job_health, "check_orphaned_queued_jobs", AsyncMock(return_value=None))
    monkeypatch.setattr(job_health, "check_jobs_on_offline_agents", AsyncMock(return_value=None))
    monkeypatch.setattr(job_health, "check_stuck_image_sync_jobs", AsyncMock(return_value=None))
    monkeypatch.setattr(job_health, "check_stuck_locks", AsyncMock(return_value=None))
    monkeypatch.setattr(job_health, "check_orphaned_image_sync_status", AsyncMock(return_value=None))
    monkeypatch.setattr(job_health.asyncio, "to_thread", AsyncMock(return_value=None))

    await job_health.job_health_monitor()

    assert calls["n"] == 2
