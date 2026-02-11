from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.enums import GlobalRole, LabRole
from app.services.permissions import PermissionService
from app.utils import agents as agents_utils
from app.utils import cache as cache_utils
from app.utils import http as http_utils
from app.utils import logs as logs_utils
from app.utils import nodes as nodes_utils
from app.utils import pagination as pagination_utils
from app.utils import supervisor as supervisor_utils
from app.utils import time_range as time_range_utils
from app.utils import timeouts as timeouts_utils
from app import models


class FakeRedis:
    def __init__(self):
        self.store: dict[str, object] = {}

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: object):
        self.store[key] = value


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._offset = 0
        self._limit = None

    def offset(self, value: int):
        self._offset = value
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def all(self):
        start = self._offset
        end = start + (self._limit or len(self.rows))
        return self.rows[start:end]


def test_parse_relative_duration_valid():
    assert time_range_utils.parse_relative_duration("15m") == timedelta(minutes=15)
    assert time_range_utils.parse_relative_duration("2h") == timedelta(hours=2)
    assert time_range_utils.parse_relative_duration("3d") == timedelta(days=3)


def test_parse_relative_duration_invalid_or_disallowed():
    assert time_range_utils.parse_relative_duration(None) is None
    assert time_range_utils.parse_relative_duration("") is None
    assert time_range_utils.parse_relative_duration("5x") is None
    assert time_range_utils.parse_relative_duration("15m", allowed={"1h"}) is None


def test_cache_get_set_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_utils, "get_redis", lambda: fake)

    cache_utils.cache_set("answer", {"value": 42})
    assert cache_utils.cache_get("answer") == {"value": 42}


def test_cache_errors_are_ignored(monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_utils, "get_redis", boom)

    assert cache_utils.cache_get("missing") is None
    cache_utils.cache_set("key", "value")


def test_get_log_content_from_path(tmp_path):
    path = tmp_path / "job.log"
    path.write_text("hello", encoding="utf-8")

    assert logs_utils.get_log_content(str(path)) == "hello"


def test_get_log_content_inline_or_missing():
    assert logs_utils.get_log_content("=inline log") == "=inline log"
    assert logs_utils.get_log_content("/no/such/file.log") == "/no/such/file.log"
    assert logs_utils.get_log_content(None) is None


def test_paginated_query_batches():
    query = FakeQuery([1, 2, 3, 4, 5])
    results = list(pagination_utils.paginated_query(query, batch_size=2))
    assert results == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_with_timeout_success():
    async def work():
        return "ok"

    result = await timeouts_utils.with_timeout(work(), timeout=1, description="test")
    assert result == "ok"


@pytest.mark.asyncio
async def test_with_timeout_timeout():
    with pytest.raises(asyncio.TimeoutError):
        await timeouts_utils.with_timeout(asyncio.sleep(0.05), timeout=0.01)


@pytest.mark.asyncio
async def test_supervised_task_restarts_and_stops(monkeypatch):
    attempts = {"count": 0}
    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    async def worker():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("boom")
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await supervisor_utils.supervised_task(
        lambda: worker(),
        name="test",
        max_restarts=5,
        base_backoff=0.0,
        max_backoff=0.0,
    )

    assert attempts["count"] == 3
    assert len(sleep_calls) == 2


@pytest.mark.asyncio
async def test_supervised_task_cancellation_propagates():
    async def worker():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await supervisor_utils.supervised_task(lambda: worker(), name="cancel", max_restarts=1)


def test_global_role_ordering():
    assert GlobalRole.ADMIN > GlobalRole.OPERATOR
    assert GlobalRole.SUPER_ADMIN >= GlobalRole.ADMIN
    assert GlobalRole.VIEWER < GlobalRole.OPERATOR
    assert LabRole.OWNER > LabRole.EDITOR


def test_permission_service_global_role_defaults():
    user = SimpleNamespace(global_role="not-a-role")
    assert PermissionService.get_user_global_role(user) == GlobalRole.OPERATOR


def test_permission_service_require_global_role():
    user = SimpleNamespace(global_role="viewer")
    with pytest.raises(HTTPException):
        PermissionService.require_global_role(user, GlobalRole.ADMIN)


def test_http_utils_require_admin():
    user = SimpleNamespace(global_role="admin")
    http_utils.require_admin(user)


def test_http_utils_require_lab_owner_needs_db(sample_lab, test_user):
    with pytest.raises(ValueError):
        http_utils.require_lab_owner(test_user, sample_lab)


def test_http_utils_raise_helpers():
    with pytest.raises(HTTPException) as exc:
        http_utils.raise_not_found("missing")
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        http_utils.raise_unavailable("down")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_get_online_agent_for_lab(monkeypatch, sample_lab, test_db):
    agent = SimpleNamespace(id="agent-1")

    async def fake_get_agent_for_lab(*args, **kwargs):
        return agent

    monkeypatch.setattr(agents_utils.agent_client, "get_agent_for_lab", fake_get_agent_for_lab)
    monkeypatch.setattr(agents_utils.agent_client, "is_agent_online", lambda _agent: True)

    result = await agents_utils.get_online_agent_for_lab(test_db, sample_lab)
    assert result is agent


@pytest.mark.asyncio
async def test_get_online_agent_for_lab_offline(monkeypatch, sample_lab, test_db):
    agent = SimpleNamespace(id="agent-1")

    async def fake_get_agent_for_lab(*args, **kwargs):
        return agent

    monkeypatch.setattr(agents_utils.agent_client, "get_agent_for_lab", fake_get_agent_for_lab)
    monkeypatch.setattr(agents_utils.agent_client, "is_agent_online", lambda _agent: False)

    result = await agents_utils.get_online_agent_for_lab(test_db, sample_lab)
    assert result is None


def test_get_node_by_any_id(test_db, sample_lab):
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="gui-1",
        display_name="R1",
        container_name="r1",
    )
    test_db.add(node)
    test_db.commit()

    found = nodes_utils.get_node_by_any_id(test_db, sample_lab.id, "r1")
    assert found is not None
    assert found.id == node.id

    found = nodes_utils.get_node_by_any_id(test_db, sample_lab.id, "gui-1")
    assert found is not None
    assert found.id == node.id


def test_resolve_node_host_id_prefers_node_host_id(test_db, sample_lab):
    host = models.Host(id="host-1", name="h1", address="127.0.0.1")
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="gui-2",
        display_name="R2",
        container_name="r2",
        host_id=host.id,
    )
    test_db.add_all([host, node])
    test_db.commit()

    host_id = nodes_utils.resolve_node_host_id(test_db, sample_lab.id, "r2")
    assert host_id == host.id


def test_resolve_node_host_id_falls_back_to_placement(test_db, sample_lab):
    host = models.Host(id="host-2", name="h2", address="127.0.0.2")
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="gui-3",
        display_name="R3",
        container_name="r3",
    )
    placement = models.NodePlacement(
        lab_id=sample_lab.id,
        node_name="r3",
        host_id=host.id,
    )
    test_db.add_all([host, node, placement])
    test_db.commit()

    host_id = nodes_utils.resolve_node_host_id(test_db, sample_lab.id, "r3")
    assert host_id == host.id

