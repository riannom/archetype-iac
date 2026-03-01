from __future__ import annotations

import builtins
import sys
import types
from typing import Any

from app import worker


def test_start_metrics_server_starts_http_server(monkeypatch):
    calls: list[tuple[int, str]] = []
    fake_prom_module = types.SimpleNamespace(
        start_http_server=lambda port, addr="0.0.0.0": calls.append((port, addr))
    )

    monkeypatch.setitem(sys.modules, "prometheus_client", fake_prom_module)
    monkeypatch.setitem(sys.modules, "app.metrics", types.ModuleType("app.metrics"))
    monkeypatch.setattr(worker, "getenv", lambda name, default=None: "8123" if name == "WORKER_METRICS_PORT" else default)

    worker._start_metrics_server()

    assert calls == [(8123, "0.0.0.0")]


def test_start_metrics_server_handles_missing_prometheus_client(monkeypatch, caplog):
    real_import = builtins.__import__

    def fake_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0):
        if name == "prometheus_client":
            raise ImportError("prometheus_client missing")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("WARNING"):
        worker._start_metrics_server()

    assert "prometheus_client not installed" in caplog.text


def test_main_uses_simple_worker_by_default(monkeypatch):
    class FakeSimpleWorker:
        instances: list["FakeSimpleWorker"] = []
        work_calls = 0

        def __init__(self, queues):
            self.queues = queues
            self.__class__.instances.append(self)

        def work(self):
            self.__class__.work_calls += 1

    class FakeWorker:
        instances = []

        def __init__(self, _queues):
            self.__class__.instances.append(self)

        def work(self):
            return None

    context_calls: list[str] = []

    class FakeConnection:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            context_calls.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            context_calls.append("exit")
            return False

    redis_conn = object()
    monkeypatch.setattr(worker, "_start_metrics_server", lambda: None)
    monkeypatch.setattr(worker.Redis, "from_url", lambda _url: redis_conn)
    monkeypatch.setattr(worker, "Connection", FakeConnection)
    monkeypatch.setattr(worker, "SimpleWorker", FakeSimpleWorker)
    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker, "getenv", lambda name, default=None: "simple" if name == "WORKER_EXECUTION_MODE" else default)
    monkeypatch.setattr(worker.settings, "redis_url", "redis://example:6379/0")

    worker.main()

    assert context_calls == ["enter", "exit"]
    assert len(FakeSimpleWorker.instances) == 1
    assert FakeSimpleWorker.instances[0].queues == ["archetype"]
    assert FakeSimpleWorker.work_calls == 1
    assert len(FakeWorker.instances) == 0


def test_main_uses_forking_worker_when_mode_is_not_simple(monkeypatch):
    class FakeSimpleWorker:
        instances = []

        def __init__(self, _queues):
            self.__class__.instances.append(self)

        def work(self):
            return None

    class FakeWorker:
        instances: list["FakeWorker"] = []
        work_calls = 0

        def __init__(self, queues):
            self.queues = queues
            self.__class__.instances.append(self)

        def work(self):
            self.__class__.work_calls += 1

    class FakeConnection:
        def __init__(self, _conn):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(worker, "_start_metrics_server", lambda: None)
    monkeypatch.setattr(worker.Redis, "from_url", lambda _url: object())
    monkeypatch.setattr(worker, "Connection", FakeConnection)
    monkeypatch.setattr(worker, "SimpleWorker", FakeSimpleWorker)
    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker, "getenv", lambda name, default=None: "fork" if name == "WORKER_EXECUTION_MODE" else default)
    monkeypatch.setattr(worker.settings, "redis_url", "redis://example:6379/0")

    worker.main()

    assert len(FakeSimpleWorker.instances) == 0
    assert len(FakeWorker.instances) == 1
    assert FakeWorker.instances[0].queues == ["archetype"]
    assert FakeWorker.work_calls == 1
