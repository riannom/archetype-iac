from __future__ import annotations

import types

import pytest

from agent.plugins import (
    VendorPlugin,
    PluginMetadata,
    VendorConfig,
    DeviceType,
    _plugin_registry,
)
from agent.plugins import loader as loader_mod


class DummyPlugin(VendorPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="dummy", version="1.0.0")

    @property
    def vendor_configs(self) -> list[VendorConfig]:
        return [VendorConfig(kind="dummy", vendor="Dummy", device_type=DeviceType.ROUTER)]


@pytest.fixture(autouse=True)
def clear_registry():
    _plugin_registry.clear()
    yield
    _plugin_registry.clear()


def test_discover_entrypoint_plugins(monkeypatch):
    class FakeEP:
        def __init__(self, plugin_class):
            self._plugin_class = plugin_class
            self.name = "dummy"

        def load(self):
            return self._plugin_class

    class FakeEPS:
        def select(self, group=None):
            assert group == "agent.plugins"
            return [FakeEP(DummyPlugin)]

    monkeypatch.setattr("importlib.metadata.entry_points", lambda: FakeEPS())

    plugins = list(loader_mod._discover_entrypoint_plugins())
    assert plugins == [DummyPlugin]


def test_load_entrypoint_plugins_skips_duplicates(monkeypatch):
    class FakeEP:
        def __init__(self, plugin_class):
            self._plugin_class = plugin_class
            self.name = "dummy"

        def load(self):
            return self._plugin_class

    class FakeEPS:
        def select(self, group=None):
            return [FakeEP(DummyPlugin)]

    monkeypatch.setattr("importlib.metadata.entry_points", lambda: FakeEPS())

    # Register first time
    loaded = loader_mod.load_entrypoint_plugins()
    assert len(loaded) == 1

    # Attempt again should skip duplicates
    loaded_again = loader_mod.load_entrypoint_plugins()
    assert loaded_again == []


def test_discover_builtin_plugins(monkeypatch):
    module = types.SimpleNamespace(DummyPlugin=DummyPlugin)

    class ModuleInfo:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(loader_mod.pkgutil, "iter_modules", lambda *_args, **_kwargs: [ModuleInfo("dummy")])
    monkeypatch.setattr(loader_mod.importlib, "import_module", lambda name: module)

    plugins = list(loader_mod._discover_builtin_plugins())
    assert plugins == [DummyPlugin]


def test_load_builtin_plugins_registers(monkeypatch):
    monkeypatch.setattr(loader_mod, "_discover_builtin_plugins", lambda: [DummyPlugin])

    loaded = loader_mod.load_builtin_plugins()
    assert len(loaded) == 1
    assert _plugin_registry.get("dummy") is loaded[0]


def test_get_plugin_for_kind():
    plugin = DummyPlugin()
    _plugin_registry[plugin.metadata.name] = plugin

    found = loader_mod.get_plugin_for_kind("dummy")
    assert found is plugin

    assert loader_mod.get_plugin_for_kind("missing") is None


def test_builtin_package_imports():
    import agent.plugins.builtin as builtin_pkg
    assert hasattr(builtin_pkg, "__file__")
