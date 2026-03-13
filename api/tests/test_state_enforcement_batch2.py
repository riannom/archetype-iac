from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import models
import app.tasks.state_enforcement as state_enforcement


def _mk_ns(
    *,
    lab_id: str = "lab-1",
    node_id: str = "node-1",
    node_name: str = "r1",
    desired: str = "running",
    actual: str = "stopped",
    attempts: int = 0,
    image_sync_status: str | None = None,
) -> models.NodeState:
    return models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        enforcement_attempts=attempts,
        image_sync_status=image_sync_status,
    )


def _mk_node(test_db, *, lab_id: str, name: str, host_id: str | None = None) -> models.Node:
    node = models.Node(
        lab_id=lab_id,
        gui_id=f"gui-{name}",
        display_name=name,
        container_name=name,
        node_type="device",
        device="ceos",
        host_id=host_id,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _mk_host(
    test_db,
    *,
    host_id: str,
    name: str,
    status: str = "online",
    address: str = "127.0.0.1:8080",
) -> models.Host:
    host = models.Host(
        id=host_id,
        name=name,
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


def test_skip_reason_label_mappings() -> None:
    assert state_enforcement._skip_reason_label("max retries reached") == "max_retries"
    assert state_enforcement._skip_reason_label("in crash cooldown (2s remaining)") == "crash_cooldown"
    assert state_enforcement._skip_reason_label("in backoff delay (5s remaining)") == "backoff_delay"
    assert state_enforcement._skip_reason_label("desired_state_changed") == "desired_state_changed"
    assert state_enforcement._skip_reason_label("something else") == "other"


def test_record_skip_records_action_and_reason(monkeypatch) -> None:
    actions: list[str] = []
    reasons: list[str] = []
    monkeypatch.setattr(state_enforcement, "record_enforcement_action", lambda action: actions.append(action))
    monkeypatch.setattr(state_enforcement, "record_enforcement_skip", lambda reason: reasons.append(reason))

    state_enforcement._record_skip("max retries reached")

    assert actions == ["skipped"]
    assert reasons == ["max_retries"]


def test_cooldown_key() -> None:
    assert state_enforcement._cooldown_key("lab", "r1") == "enforcement_cooldown:lab:r1"


@pytest.mark.asyncio
async def test_notify_enforcement_failure_success(monkeypatch) -> None:
    import app.services.broadcaster as broadcaster

    publish = AsyncMock(return_value=1)
    monkeypatch.setattr(broadcaster, "get_broadcaster", lambda: SimpleNamespace(publish_node_state=publish))

    ns = _mk_ns(attempts=4)
    await state_enforcement._notify_enforcement_failure("lab-a", ns)

    publish.assert_awaited_once()
    args, kwargs = publish.await_args
    assert args[0] == "lab-a"
    assert "State enforcement failed after 4 attempts" in args[1]["error_message"]


@pytest.mark.asyncio
async def test_notify_enforcement_failure_exception_path(monkeypatch) -> None:
    import app.services.broadcaster as broadcaster

    publish = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(broadcaster, "get_broadcaster", lambda: SimpleNamespace(publish_node_state=publish))

    warnings: list[str] = []
    monkeypatch.setattr(state_enforcement.logger, "warning", lambda msg: warnings.append(str(msg)))

    await state_enforcement._notify_enforcement_failure("lab-a", _mk_ns())

    assert warnings
    assert "Failed to notify UI of enforcement failure" in warnings[0]


@pytest.mark.asyncio
async def test_cooldown_helpers_redis_error_paths(monkeypatch) -> None:
    class BrokenRedis:
        async def exists(self, _key):
            raise state_enforcement.redis.RedisError("exists failed")

        async def setex(self, _key, _ttl, _value):
            raise state_enforcement.redis.RedisError("setex failed")

        async def delete(self, *_keys):
            raise state_enforcement.redis.RedisError("delete failed")

    monkeypatch.setattr(state_enforcement, "get_async_redis", lambda: BrokenRedis())

    assert await state_enforcement._is_on_cooldown("lab", "r1") is False
    await state_enforcement._set_cooldown("lab", "r1")
    await state_enforcement.clear_cooldowns_for_lab("lab", ["r1"])


@pytest.mark.asyncio
async def test_clear_cooldowns_for_lab_branches(monkeypatch) -> None:
    await state_enforcement.clear_cooldowns_for_lab("lab", [])

    class FakeRedis:
        def __init__(self):
            self.deleted_keys: tuple[str, ...] | None = None

        async def delete(self, *keys):
            self.deleted_keys = keys
            return 2

    fake = FakeRedis()
    infos: list[str] = []
    monkeypatch.setattr(state_enforcement, "get_async_redis", lambda: fake)
    monkeypatch.setattr(state_enforcement.logger, "info", lambda msg: infos.append(str(msg)))

    await state_enforcement.clear_cooldowns_for_lab("lab-x", ["r1", "r2"])

    assert fake.deleted_keys == (
        "enforcement_cooldown:lab-x:r1",
        "enforcement_cooldown:lab-x:r2",
    )
    assert infos


@pytest.mark.asyncio
async def test_get_agent_for_node_prefers_node_host_fk(test_db, sample_lab, monkeypatch) -> None:
    host = _mk_host(test_db, host_id="agent-a", name="agent-a", status="online")
    node = _mk_node(test_db, lab_id=sample_lab.id, name="r1", host_id=host.id)

    ns = _mk_ns(lab_id=sample_lab.id, node_name="r1")
    ns.node_definition_id = node.id

    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda h: h.status == "online")

    result = await state_enforcement._get_agent_for_node(test_db, sample_lab, ns)
    assert result is not None
    assert result.id == host.id


@pytest.mark.asyncio
async def test_get_agent_for_node_uses_placement_via_node_definition_fk(test_db, sample_lab, monkeypatch) -> None:
    node_host = _mk_host(test_db, host_id="agent-node", name="agent-node", status="offline")
    placement_host = _mk_host(test_db, host_id="agent-placement", name="agent-placement", status="online")

    node = _mk_node(test_db, lab_id=sample_lab.id, name="r2", host_id=node_host.id)
    placement = models.NodePlacement(
        lab_id=sample_lab.id,
        node_name="r2",
        node_definition_id=node.id,
        host_id=placement_host.id,
        status="deployed",
    )
    test_db.add(placement)
    test_db.commit()

    ns = _mk_ns(lab_id=sample_lab.id, node_name="r2")
    ns.node_definition_id = node.id

    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda h: h.status == "online")

    result = await state_enforcement._get_agent_for_node(test_db, sample_lab, ns)

    assert result is not None
    assert result.id == placement_host.id


@pytest.mark.asyncio
async def test_get_agent_for_node_ignores_name_only_placement_without_fk(test_db, sample_lab, monkeypatch) -> None:
    placement_host = _mk_host(test_db, host_id="agent-placement-nofk", name="agent-placement-nofk", status="online")
    lab_agent = _mk_host(test_db, host_id="agent-lab-nofk", name="agent-lab-nofk", status="online")
    sample_lab.agent_id = lab_agent.id
    test_db.commit()

    test_db.add(
        models.NodePlacement(
            lab_id=sample_lab.id,
            node_name="r-nofk",
            node_definition_id=None,
            host_id=placement_host.id,
            status="deployed",
        )
    )
    test_db.commit()

    ns = _mk_ns(lab_id=sample_lab.id, node_name="r-nofk")
    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda h: h.status == "online")

    result = await state_enforcement._get_agent_for_node(test_db, sample_lab, ns)

    assert result is not None
    assert result.id == lab_agent.id


@pytest.mark.asyncio
async def test_get_agent_for_node_uses_lab_agent_and_healthy_agent_fallback(test_db, sample_lab, monkeypatch) -> None:
    # Lab agent fallback
    lab_agent = _mk_host(test_db, host_id="agent-lab", name="agent-lab", status="online")
    sample_lab.agent_id = lab_agent.id
    test_db.commit()

    ns = _mk_ns(lab_id=sample_lab.id, node_name="missing-node")
    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda h: h.status == "online")

    result = await state_enforcement._get_agent_for_node(test_db, sample_lab, ns)
    assert result is not None
    assert result.id == lab_agent.id

    # Healthy-agent fallback with provider resolution
    sample_lab.agent_id = None
    test_db.commit()

    fallback_agent = MagicMock()
    fallback_agent.id = "agent-fallback"
    healthy = AsyncMock(return_value=fallback_agent)
    monkeypatch.setattr(state_enforcement.agent_client, "get_healthy_agent", healthy)

    import app.utils.lab as lab_utils

    monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _lab: "docker")
    monkeypatch.setattr(lab_utils, "get_node_provider", lambda _node, _session: "libvirt")

    result = await state_enforcement._get_agent_for_node(test_db, sample_lab, ns)

    assert result.id == "agent-fallback"
    healthy.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_enforceable_skip_paths(monkeypatch, test_db) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", False)

    ns_image_sync = _mk_ns(image_sync_status="syncing")
    assert await state_enforcement._is_enforceable(test_db, ns_image_sync) is False

    ns_error = _mk_ns(actual="error", desired="running")
    assert await state_enforcement._is_enforceable(test_db, ns_error) is False

    ns_no_action = _mk_ns(actual="running", desired="running")
    assert await state_enforcement._is_enforceable(test_db, ns_no_action) is False


@pytest.mark.asyncio
async def test_is_enforceable_max_retry_marks_failed_and_schedules_notification(monkeypatch, test_db) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)
    monkeypatch.setattr(state_enforcement, "_should_skip_enforcement", lambda _ns: (True, "max retries reached"))

    scheduled: list[str] = []

    def _safe_schedule(coro, *, name: str):
        scheduled.append(name)
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(state_enforcement, "safe_create_task", _safe_schedule)

    failed_actions: list[str] = []
    exhausted: list[bool] = []
    monkeypatch.setattr(state_enforcement, "record_enforcement_action", lambda action: failed_actions.append(action))
    monkeypatch.setattr(state_enforcement, "record_enforcement_exhausted", lambda: exhausted.append(True))

    ns = _mk_ns(actual="stopped", desired="running", attempts=3)
    ns.error_message = "last error"

    result = await state_enforcement._is_enforceable(test_db, ns)

    assert result is False
    assert ns.actual_state == "error"
    assert ns.enforcement_failed_at is not None
    assert "State enforcement failed after 3 attempts" in (ns.error_message or "")
    assert scheduled and scheduled[0].startswith("notify:enforcement:")
    assert failed_actions == ["failed"]
    assert exhausted == [True]


@pytest.mark.asyncio
async def test_is_enforceable_active_job_checks(monkeypatch, test_db) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)
    monkeypatch.setattr(state_enforcement, "_should_skip_enforcement", lambda _ns: (False, ""))
    monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))

    ns = _mk_ns(lab_id="lab-z", node_name="r10", node_id="node-z")

    # preloaded by name
    assert await state_enforcement._is_enforceable(
        test_db,
        ns,
        active_job_node_names={("lab-z", "r10")},
    ) is False

    # preloaded by id
    assert await state_enforcement._is_enforceable(
        test_db,
        ns,
        active_job_node_names=set(),
        active_job_node_ids={("lab-z", "node-z")},
    ) is False

    # fallback DB path
    monkeypatch.setattr(state_enforcement, "_has_active_job", lambda *_a, **_kw: True)
    assert await state_enforcement._is_enforceable(test_db, ns) is False

    # no active jobs
    monkeypatch.setattr(state_enforcement, "_has_active_job", lambda *_a, **_kw: False)
    assert await state_enforcement._is_enforceable(test_db, ns) is True


def test_has_lab_wide_active_job_uses_preloaded_or_query(test_db, sample_lab) -> None:
    assert state_enforcement._has_lab_wide_active_job(test_db, sample_lab.id, labs_with_active_jobs={sample_lab.id})

    job = models.Job(
        lab_id=sample_lab.id,
        action="up",
        status="queued",
    )
    test_db.add(job)
    test_db.commit()

    assert state_enforcement._has_lab_wide_active_job(test_db, sample_lab.id)


@pytest.mark.asyncio
async def test_try_extract_configs_branches(test_db, sample_lab, monkeypatch) -> None:
    # No restart candidates
    await state_enforcement._try_extract_configs(
        test_db,
        sample_lab,
        [_mk_ns(lab_id=sample_lab.id, actual="running")],
    )

    # Successful extraction and save path
    host = _mk_host(test_db, host_id="extract-host", name="extract-host", status="online")
    placement = models.NodePlacement(lab_id=sample_lab.id, node_name="r1", host_id=host.id, status="deployed")
    test_db.add(placement)

    _mk_node(test_db, lab_id=sample_lab.id, name="r1", host_id=host.id)
    _mk_node(test_db, lab_id=sample_lab.id, name="r2", host_id=host.id)

    restart_ns = _mk_ns(lab_id=sample_lab.id, node_name="r1", actual="error")
    keep_ns = _mk_ns(lab_id=sample_lab.id, node_name="r2", actual="running")

    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda _h: True)
    monkeypatch.setattr(
        state_enforcement.agent_client,
        "extract_configs_on_agent",
        AsyncMock(
            return_value={
                "success": True,
                "configs": [
                    {"node_name": "r1", "content": "hostname r1"},
                    {"node_name": "r2", "content": "hostname r2"},
                    {"node_name": "r3", "content": "hostname r3"},
                ],
            }
        ),
    )

    saved: list[dict] = []

    class FakeConfigService:
        def __init__(self, _session):
            pass

        def save_extracted_config(self, **kwargs):
            saved.append(kwargs)

    import app.services.config_service as config_service

    monkeypatch.setattr(config_service, "ConfigService", FakeConfigService)

    await state_enforcement._try_extract_configs(
        test_db,
        sample_lab,
        [restart_ns, keep_ns],
        hosts_by_id={host.id: host},
    )

    assert len(saved) == 1
    assert saved[0]["node_name"] == "r1"
    assert saved[0]["snapshot_type"] == "auto_restart"


@pytest.mark.asyncio
async def test_enforce_lab_states_batches_nodes(test_db, monkeypatch) -> None:
    lab = models.Lab(
        name="batch-lab",
        owner_id="owner",
        provider="docker",
        state="running",
        workspace_path="/tmp/batch-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    ns1 = _mk_ns(lab_id=lab.id, node_id="n1", node_name="n1")
    ns2 = _mk_ns(lab_id=lab.id, node_id="n2", node_name="n2")
    test_db.add_all([ns1, ns2])
    test_db.commit()

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(state_enforcement, "get_session", _fake_get_session)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    monkeypatch.setattr(state_enforcement, "_is_enforceable", AsyncMock(return_value=True))
    monkeypatch.setattr(state_enforcement, "_has_lab_wide_active_job", lambda *_a, **_kw: False)
    monkeypatch.setattr(state_enforcement, "_try_extract_configs", AsyncMock(return_value=None))
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))

    captured_tasks: list[str] = []

    def _safe_schedule(coro, *, name: str):
        captured_tasks.append(name)
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(state_enforcement, "safe_create_task", _safe_schedule)
    monkeypatch.setattr(state_enforcement, "record_enforcement_action", lambda *_a: None)

    import app.tasks.jobs as jobs_module
    import app.utils.lab as lab_utils

    monkeypatch.setattr(jobs_module, "run_node_reconcile", AsyncMock(return_value=None))
    monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _lab: "docker")

    await state_enforcement.enforce_lab_states()

    test_db.refresh(ns1)
    test_db.refresh(ns2)
    assert ns1.enforcement_attempts == 1
    assert ns2.enforcement_attempts == 1

    batch_jobs = (
        test_db.query(models.Job)
        .filter(models.Job.lab_id == lab.id, models.Job.action == "sync:batch:2")
        .all()
    )
    assert len(batch_jobs) == 1
    assert captured_tasks and captured_tasks[0].startswith("enforce:batch:")


@pytest.mark.asyncio
async def test_enforce_lab_states_skips_lab_with_active_wide_job(test_db, monkeypatch) -> None:
    lab = models.Lab(
        name="skip-lab",
        owner_id="owner",
        provider="docker",
        state="running",
        workspace_path="/tmp/skip-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    ns = _mk_ns(lab_id=lab.id, node_id="n1", node_name="n1")
    test_db.add(ns)
    test_db.commit()

    # Seed active job to populate labs_with_active_jobs in parser loop.
    test_db.add(models.Job(lab_id=lab.id, action="up", status="running"))
    test_db.commit()

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(state_enforcement, "get_session", _fake_get_session)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    monkeypatch.setattr(state_enforcement, "_is_enforceable", AsyncMock(return_value=True))

    skips: list[str] = []
    monkeypatch.setattr(state_enforcement, "_record_skip", lambda reason: skips.append(reason))

    await state_enforcement.enforce_lab_states()

    assert "lab_wide_active_job" in skips
    assert not test_db.query(models.Job).filter(models.Job.action.like("sync:batch:%")).first()


@pytest.mark.asyncio
async def test_state_enforcement_monitor_handles_error_then_cancel(monkeypatch) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)
    monkeypatch.setattr(type(state_enforcement.settings), "get_interval", lambda self, _name: 0)

    async def _fake_sleep(_interval):
        return None

    calls = {"count": 0}

    async def _fake_enforce() -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient")
        raise asyncio.CancelledError()

    monkeypatch.setattr(state_enforcement.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(state_enforcement, "enforce_lab_states", _fake_enforce)

    await state_enforcement.state_enforcement_monitor()

    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_get_agent_for_node_node_provider_fallback_branch(test_db, sample_lab, monkeypatch) -> None:
    offline = _mk_host(test_db, host_id="agent-offline-provider", name="agent-offline-provider", status="offline")
    node = _mk_node(test_db, lab_id=sample_lab.id, name="r-provider", host_id=offline.id)
    ns = _mk_ns(lab_id=sample_lab.id, node_name="r-provider")
    ns.node_definition_id = node.id

    fallback = MagicMock()
    fallback.id = "provider-fallback"

    import app.utils.lab as lab_utils

    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda _h: False)
    monkeypatch.setattr(lab_utils, "get_node_provider", lambda _node, _session: "libvirt")
    monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _lab: "docker")
    healthy = AsyncMock(return_value=fallback)
    monkeypatch.setattr(state_enforcement.agent_client, "get_healthy_agent", healthy)

    result = await state_enforcement._get_agent_for_node(test_db, sample_lab, ns)
    assert result is fallback
    healthy.assert_awaited_once()
    assert healthy.await_args.kwargs["required_provider"] == "libvirt"


@pytest.mark.asyncio
async def test_enforce_node_state_updates_placement_and_clears_failed_marker(test_db, sample_lab, monkeypatch) -> None:
    old_host = _mk_host(test_db, host_id="old-host", name="old-host", status="online")
    new_host = _mk_host(test_db, host_id="new-host", name="new-host", status="online")
    node = _mk_node(test_db, lab_id=sample_lab.id, name="r-update", host_id=old_host.id)

    placement = models.NodePlacement(
        lab_id=sample_lab.id,
        node_name="r-update",
        node_definition_id=None,
        host_id=old_host.id,
        status="deployed",
    )
    test_db.add(placement)
    test_db.commit()

    ns = _mk_ns(lab_id=sample_lab.id, node_name="r-update", node_id="node-update", actual="stopped", desired="running")
    ns.node_definition_id = node.id
    ns.enforcement_failed_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)

    monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))
    monkeypatch.setattr(state_enforcement, "_has_active_job", lambda *_a, **_kw: False)
    monkeypatch.setattr(state_enforcement, "_get_agent_for_node", AsyncMock(return_value=new_host))
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)

    import app.tasks.jobs as jobs_module
    import app.utils.lab as lab_utils

    monkeypatch.setattr(jobs_module, "run_node_reconcile", AsyncMock(return_value=None))
    monkeypatch.setattr(lab_utils, "get_node_provider", lambda _node, _session: "libvirt")
    monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _lab: "docker")

    scheduled: list[str] = []

    def _safe_schedule(coro, *, name: str):
        scheduled.append(name)
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(state_enforcement, "safe_create_task", _safe_schedule)
    monkeypatch.setattr(state_enforcement, "record_enforcement_action", lambda *_a: None)

    result = await state_enforcement.enforce_node_state(test_db, sample_lab, ns)

    assert result is True
    test_db.refresh(placement)
    test_db.refresh(ns)
    assert placement.host_id == new_host.id
    assert placement.node_definition_id == node.id
    assert ns.enforcement_failed_at is None
    assert scheduled and scheduled[0].startswith("enforce:sync:")


@pytest.mark.asyncio
async def test_enforce_node_state_skips_when_desired_changes_midflight(test_db, sample_lab, monkeypatch) -> None:
    host = _mk_host(test_db, host_id="desired-host", name="desired-host", status="online")
    ns = _mk_ns(lab_id=sample_lab.id, node_name="r-race", node_id="node-race", actual="stopped", desired="running")
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)

    monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))
    monkeypatch.setattr(state_enforcement, "_has_active_job", lambda *_a, **_kw: False)
    monkeypatch.setattr(state_enforcement, "_get_agent_for_node", AsyncMock(return_value=host))
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)

    skip_reasons: list[str] = []
    monkeypatch.setattr(state_enforcement, "_record_skip", lambda reason: skip_reasons.append(reason))

    original_refresh = test_db.refresh

    def _refresh_and_flip(obj, *args, **kwargs):
        original_refresh(obj, *args, **kwargs)
        if obj is ns:
            obj.desired_state = "stopped"

    monkeypatch.setattr(test_db, "refresh", _refresh_and_flip)

    result = await state_enforcement.enforce_node_state(test_db, sample_lab, ns)
    assert result is False
    assert skip_reasons and skip_reasons[-1] == "desired_state_changed"


@pytest.mark.asyncio
async def test_is_enforceable_pending_skip_reason_and_legacy_cooldown(monkeypatch, test_db) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)

    # Pending special-case should force "start" and allow enforcement.
    pending = _mk_ns(actual="pending", desired="running")
    monkeypatch.setattr(state_enforcement, "_should_skip_enforcement", lambda _ns: (False, ""))
    monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))
    monkeypatch.setattr(state_enforcement, "_has_active_job", lambda *_a, **_kw: False)
    assert await state_enforcement._is_enforceable(test_db, pending) is True

    # Generic skip reason branch.
    generic = _mk_ns(actual="stopped", desired="running")
    reasons: list[str] = []
    monkeypatch.setattr(state_enforcement, "_record_skip", lambda reason: reasons.append(reason))
    monkeypatch.setattr(state_enforcement, "_should_skip_enforcement", lambda _ns: (True, "some-transient-reason"))
    assert await state_enforcement._is_enforceable(test_db, generic) is False
    assert reasons and reasons[-1] == "some-transient-reason"

    # Legacy cooldown skip branch.
    monkeypatch.setattr(state_enforcement, "_should_skip_enforcement", lambda _ns: (False, ""))
    monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=True))
    assert await state_enforcement._is_enforceable(test_db, generic) is False


@pytest.mark.asyncio
async def test_try_extract_configs_additional_branches(test_db, sample_lab, monkeypatch) -> None:
    host = _mk_host(test_db, host_id="extract-fallback", name="extract-fallback", status="online")
    sample_lab.agent_id = host.id
    test_db.commit()

    restart_node = _mk_ns(lab_id=sample_lab.id, node_name="r-extra", actual="error")
    _mk_node(test_db, lab_id=sample_lab.id, name="r-extra", host_id=host.id)
    test_db.add(restart_node)
    test_db.commit()

    # host_ids empty -> fallback to lab.agent_id, but offline => no agents branch.
    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda _h: False)
    await state_enforcement._try_extract_configs(test_db, sample_lab, [restart_node])

    monkeypatch.setattr(state_enforcement.agent_client, "is_agent_online", lambda _h: True)

    # Result exception branch.
    async def _return_exception(_agent, _lab_id):
        return RuntimeError("extract failed")

    monkeypatch.setattr(state_enforcement.agent_client, "extract_configs_on_agent", _return_exception)
    await state_enforcement._try_extract_configs(test_db, sample_lab, [restart_node])

    # unsuccessful result branch.
    monkeypatch.setattr(
        state_enforcement.agent_client,
        "extract_configs_on_agent",
        AsyncMock(return_value={"success": False, "configs": []}),
    )
    await state_enforcement._try_extract_configs(test_db, sample_lab, [restart_node])

    # no configs branch.
    monkeypatch.setattr(
        state_enforcement.agent_client,
        "extract_configs_on_agent",
        AsyncMock(return_value={"success": True, "configs": []}),
    )
    await state_enforcement._try_extract_configs(test_db, sample_lab, [restart_node])

    # Exception path around ConfigService.
    monkeypatch.setattr(
        state_enforcement.agent_client,
        "extract_configs_on_agent",
        AsyncMock(return_value={"success": True, "configs": [{"node_name": "r-extra", "content": "hostname r-extra"}]}),
    )
    import app.services.config_service as config_service

    class BrokenConfigService:
        def __init__(self, _session):
            raise RuntimeError("config service down")

    monkeypatch.setattr(config_service, "ConfigService", BrokenConfigService)
    await state_enforcement._try_extract_configs(test_db, sample_lab, [restart_node])


@pytest.mark.asyncio
async def test_enforce_lab_states_parses_active_job_actions_and_exception_fallback(test_db, monkeypatch) -> None:
    from contextlib import contextmanager

    lab = models.Lab(
        name="parser-lab",
        owner_id="user1",
        provider="docker",
        state="running",
        workspace_path="/tmp/parser-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    n1 = _mk_ns(lab_id=lab.id, node_id="node-1", node_name="node-1")
    n2 = _mk_ns(lab_id=lab.id, node_id="node-2", node_name="node-2")
    test_db.add_all([n1, n2])
    test_db.commit()

    test_db.add_all(
        [
            models.Job(lab_id=lab.id, action="node:stop:node-1", status="queued"),
            models.Job(lab_id=lab.id, action="sync:node:node-1", status="running"),
            models.Job(lab_id=lab.id, action="sync:agent:agent-1:node-2,node-3", status="running"),
        ]
    )
    test_db.commit()

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(state_enforcement, "get_session", _fake_get_session)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    monkeypatch.setattr(state_enforcement, "_is_enforceable", AsyncMock(return_value=False))

    await state_enforcement.enforce_lab_states()

    # Trigger the nested rollback fallback branch in exception handling.
    monkeypatch.setattr(state_enforcement, "_is_enforceable", AsyncMock(side_effect=RuntimeError("filter boom")))
    original_commit = test_db.commit
    monkeypatch.setattr(test_db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit boom")))
    await state_enforcement.enforce_lab_states()
    monkeypatch.setattr(test_db, "commit", original_commit)


@pytest.mark.asyncio
async def test_enforce_lab_states_preload_hosts_and_batch_error_path(test_db, monkeypatch) -> None:
    from contextlib import contextmanager

    lab = models.Lab(
        name="host-preload-lab",
        owner_id="user1",
        provider="docker",
        state="running",
        workspace_path="/tmp/host-preload-lab",
    )
    host = _mk_host(test_db, host_id="preload-host", name="preload-host", status="online")
    lab.agent_id = host.id
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    ns = _mk_ns(lab_id=lab.id, node_id="node-preload", node_name="node-preload")
    ns.enforcement_failed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    test_db.add(ns)
    test_db.commit()

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(state_enforcement, "get_session", _fake_get_session)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    monkeypatch.setattr(state_enforcement, "_is_enforceable", AsyncMock(return_value=True))
    monkeypatch.setattr(state_enforcement, "_has_lab_wide_active_job", lambda *_a, **_kw: False)
    monkeypatch.setattr(state_enforcement, "_try_extract_configs", AsyncMock(return_value=None))
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))

    import app.tasks.jobs as jobs_module
    import app.utils.lab as lab_utils

    monkeypatch.setattr(jobs_module, "run_node_reconcile", AsyncMock(return_value=None))
    monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _lab: (_ for _ in ()).throw(RuntimeError("provider fail")))

    await state_enforcement.enforce_lab_states()

    test_db.refresh(ns)
    assert ns.enforcement_attempts == 1


@pytest.mark.asyncio
async def test_enforce_lab_states_disabled_and_no_mismatches(test_db, monkeypatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(state_enforcement, "get_session", _fake_get_session)

    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", False)
    await state_enforcement.enforce_lab_states()

    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    await state_enforcement.enforce_lab_states()


@pytest.mark.asyncio
async def test_enforce_lab_states_missing_lab_and_batch_rollback_failure(test_db, monkeypatch) -> None:
    from contextlib import contextmanager

    lab = models.Lab(
        name="edge-lab",
        owner_id="user1",
        provider="docker",
        state="running",
        workspace_path="/tmp/edge-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    ns = _mk_ns(lab_id=lab.id, node_id="edge-node", node_name="edge-node")
    test_db.add(ns)
    test_db.commit()

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(state_enforcement, "get_session", _fake_get_session)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
    monkeypatch.setattr(state_enforcement, "_is_enforceable", AsyncMock(return_value=True))
    monkeypatch.setattr(state_enforcement, "_has_lab_wide_active_job", lambda *_a, **_kw: False)
    monkeypatch.setattr(state_enforcement, "_try_extract_configs", AsyncMock(return_value=None))

    # Missing-lab branch in phase-2 loop.
    original_get = test_db.get
    get_calls = {"lab": 0}

    def _get_with_missing_phase2(model, ident, *args, **kwargs):
        if model is models.Lab and ident == lab.id:
            get_calls["lab"] += 1
            if get_calls["lab"] >= 2:
                return None
        return original_get(model, ident, *args, **kwargs)

    monkeypatch.setattr(test_db, "get", _get_with_missing_phase2)
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))
    await state_enforcement.enforce_lab_states()

    # Batch exception path with rollback failure in handler.
    monkeypatch.setattr(test_db, "get", original_get)
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(side_effect=RuntimeError("cooldown fail")))
    monkeypatch.setattr(test_db, "rollback", lambda: (_ for _ in ()).throw(RuntimeError("rollback fail")))
    await state_enforcement.enforce_lab_states()
