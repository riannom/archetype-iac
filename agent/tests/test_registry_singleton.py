from __future__ import annotations

from agent.registry import LazySingleton


def test_lazy_singleton_factory_called_once():
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        return object()

    singleton = LazySingleton(factory)
    a = singleton.get()
    b = singleton.get()

    assert a is b
    assert calls["count"] == 1
