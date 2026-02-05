from __future__ import annotations

import sys
import types

from agent.providers import registry


class DummyProvider:
    name = "dummy"


def test_provider_registry_discovers_docker(monkeypatch) -> None:
    dummy_module = types.SimpleNamespace(DockerProvider=DummyProvider)
    monkeypatch.setitem(sys.modules, "agent.providers.docker", dummy_module)

    monkeypatch.setattr("agent.config.settings.enable_docker", True)
    monkeypatch.setattr("agent.config.settings.enable_libvirt", False)

    registry._registry.reset()
    provider = registry.get_provider("docker")

    assert provider is not None
    assert isinstance(provider, DummyProvider)
    assert registry.is_provider_available("docker") is True


def test_provider_registry_default_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("agent.config.settings.enable_docker", False)
    monkeypatch.setattr("agent.config.settings.enable_libvirt", False)

    registry._registry.reset()
    assert registry.get_default_provider() is None
    assert registry.list_providers() == []
