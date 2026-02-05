from __future__ import annotations

import types

import agent.providers.registry as registry


def test_provider_registry_discovers_docker(monkeypatch) -> None:
    registry._registry.reset()

    fake_module = types.SimpleNamespace()

    class FakeProvider:
        pass

    fake_module.DockerProvider = FakeProvider

    monkeypatch.setattr("agent.config.settings.enable_docker", True)
    monkeypatch.setattr("agent.config.settings.enable_libvirt", False)
    monkeypatch.setitem(__import__("sys").modules, "agent.providers.docker", fake_module)

    provider = registry.get_provider("docker")
    assert isinstance(provider, FakeProvider)
    assert "docker" in registry.list_providers()


def test_provider_registry_default_none(monkeypatch) -> None:
    registry._registry.reset()

    monkeypatch.setattr("agent.config.settings.enable_docker", False)
    monkeypatch.setattr("agent.config.settings.enable_libvirt", False)

    assert registry.get_default_provider() is None
