from __future__ import annotations

import agent.network.backends.registry as registry


def test_get_network_backend_defaults_to_ovs(monkeypatch) -> None:
    registry.reset_network_backend()

    class FakeBackend:
        pass

    monkeypatch.setattr("agent.config.settings.network_backend", "invalid")
    monkeypatch.setattr("agent.network.backends.registry.OVSBackend", FakeBackend)

    backend = registry.get_network_backend()
    assert isinstance(backend, FakeBackend)


def test_get_network_backend_singleton(monkeypatch) -> None:
    registry.reset_network_backend()

    class FakeBackend:
        pass

    monkeypatch.setattr("agent.config.settings.network_backend", "ovs")
    monkeypatch.setattr("agent.network.backends.registry.OVSBackend", FakeBackend)

    first = registry.get_network_backend()
    second = registry.get_network_backend()

    assert first is second
