from __future__ import annotations


from agent.plugins import VendorPlugin, PluginMetadata, VendorConfig, DeviceType, _plugin_registry
from agent.plugins import loader


class DummyPlugin(VendorPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="dummy", version="1.0.0")

    @property
    def vendor_configs(self) -> list[VendorConfig]:
        return [
            VendorConfig(
                kind="dummy-kind",
                vendor="Dummy",
                device_type=DeviceType.ROUTER,
            )
        ]


def test_load_builtin_plugins_registers(monkeypatch) -> None:
    _plugin_registry.clear()

    monkeypatch.setattr(
        loader,
        "_discover_builtin_plugins",
        lambda: [DummyPlugin],
    )

    loaded = loader.load_builtin_plugins()

    assert len(loaded) == 1
    assert loaded[0].metadata.name == "dummy"
    assert "dummy" in _plugin_registry


def test_get_plugin_for_kind() -> None:
    _plugin_registry.clear()
    plugin = DummyPlugin()
    _plugin_registry[plugin.metadata.name] = plugin

    found = loader.get_plugin_for_kind("dummy-kind")
    assert found is plugin
