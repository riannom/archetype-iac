from __future__ import annotations

import pytest

from agent.plugins import (
    VendorPlugin,
    PluginMetadata,
    VendorConfig,
    DeviceType,
    register_plugin,
    get_plugin,
    get_all_plugins,
    get_all_vendor_configs,
    _plugin_registry,
)
from agent.plugins import loader as loader_mod
from agent import virsh_console_lock as console_lock_mod
from agent.network import transport as transport_mod


class ReadyPlugin(VendorPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="ready", version="1.0.0")

    @property
    def vendor_configs(self) -> list[VendorConfig]:
        return [
            VendorConfig(
                kind="ready",
                vendor="Ready",
                device_type=DeviceType.ROUTER,
                readiness_pattern=r"READY",
            )
        ]

    def _get_config_for_container(self, container_name: str):
        return self.vendor_configs[0]


@pytest.fixture(autouse=True)
def clear_registry():
    _plugin_registry.clear()
    yield
    _plugin_registry.clear()


def test_plugin_registry_functions():
    plugin = ReadyPlugin()
    register_plugin(plugin)

    assert get_plugin("ready") is plugin
    assert get_all_plugins() == [plugin]

    configs = get_all_vendor_configs()
    assert len(configs) == 1
    assert configs[0].kind == "ready"


def test_plugin_interface_name_and_readiness():
    plugin = ReadyPlugin()
    cfg = plugin.vendor_configs[0]

    assert plugin.get_interface_name(1, cfg) == "eth0"
    assert plugin.get_interface_name(2, cfg) == "eth1"

    assert plugin.is_boot_ready("node1", "... READY ...") is True
    assert plugin.is_boot_ready("node1", "not yet") is False


def test_loader_load_all_plugins(monkeypatch):
    dummy = ReadyPlugin()

    monkeypatch.setattr(loader_mod, "load_builtin_plugins", lambda: [dummy])
    monkeypatch.setattr(loader_mod, "load_entrypoint_plugins", lambda: [])

    loaded = loader_mod.load_all_plugins()
    assert loaded == [dummy]


def test_kill_orphaned_virsh_no_matches(monkeypatch):
    class Result:
        def __init__(self, stdout: str = "", returncode: int = 1):
            self.stdout = stdout
            self.returncode = returncode

    monkeypatch.setattr(console_lock_mod.subprocess, "run", lambda *_args, **_kwargs: Result())
    assert console_lock_mod.kill_orphaned_virsh("vm2") == 0


def test_transport_get_data_plane_ip_value():
    transport_mod.set_data_plane_ip("10.10.0.1")
    assert transport_mod.get_data_plane_ip() == "10.10.0.1"
    transport_mod.set_data_plane_ip(None)
    assert transport_mod.get_data_plane_ip() is None


