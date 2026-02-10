from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from agent.plugins.builtin.arista import AristaPlugin
import agent.providers.libvirt as libvirt_provider
from agent.providers.base import NodeStatus


class APIError(Exception):
    pass


class NotFound(Exception):
    pass


def _load_docker_networks(monkeypatch):
    errors_mod = types.ModuleType("docker.errors")
    errors_mod.APIError = APIError
    errors_mod.NotFound = NotFound

    docker_mod = types.ModuleType("docker")
    docker_mod.errors = errors_mod
    docker_mod.DockerClient = object

    monkeypatch.setitem(sys.modules, "docker", docker_mod)
    monkeypatch.setitem(sys.modules, "docker.errors", errors_mod)

    import agent.providers.docker_networks as docker_networks
    importlib.reload(docker_networks)
    return docker_networks


class FakeNetwork:
    def __init__(self, name: str, manager: "FakeNetworks") -> None:
        self.name = name
        self._manager = manager
        self.removed = False
        self.connected: set[str] = set()
        self.raise_already_exists = False

    def connect(self, container_name: str) -> None:
        if self.raise_already_exists:
            raise APIError("already exists")
        self.connected.add(container_name)

    def disconnect(self, container_name: str) -> None:
        self.connected.discard(container_name)

    def remove(self) -> None:
        self.removed = True


class FakeNetworks:
    def __init__(self, existing: list[str] | None = None) -> None:
        self._networks: dict[str, FakeNetwork] = {}
        for name in existing or []:
            self._networks[name] = FakeNetwork(name, self)

    def get(self, name: str) -> FakeNetwork:
        network = self._networks.get(name)
        if not network or network.removed:
            raise NotFound("network not found")
        return network

    def create(self, name: str, driver: str, options: dict) -> FakeNetwork:
        network = FakeNetwork(name, self)
        self._networks[name] = network
        return network

    def list(self, filters: dict | None = None) -> list[FakeNetwork]:
        return [network for network in self._networks.values() if not network.removed]


class FakeContainer:
    def __init__(self, networks: list[str]) -> None:
        self.attrs = {
            "NetworkSettings": {
                "Networks": {name: {} for name in networks},
            }
        }


class FakeContainers:
    def __init__(self, containers: dict[str, FakeContainer]) -> None:
        self._containers = containers

    def get(self, name: str) -> FakeContainer:
        if name not in self._containers:
            raise NotFound("container not found")
        return self._containers[name]


class FakeDocker:
    def __init__(self, networks: FakeNetworks, containers: FakeContainers) -> None:
        self.networks = networks
        self.containers = containers


def test_arista_plugin_metadata_and_config() -> None:
    plugin = AristaPlugin()
    metadata = plugin.metadata
    assert metadata.name == "arista"
    assert metadata.version == "1.0.0"
    assert metadata.description == "Support for Arista EOS devices"

    configs = plugin.vendor_configs
    assert len(configs) == 1
    config = configs[0]
    assert config.kind == "ceos"
    assert config.vendor == "Arista"
    assert config.port_naming == "Ethernet"
    assert config.port_start_index == 1


def test_arista_on_container_create_merges_env() -> None:
    plugin = AristaPlugin()
    base_config = {"environment": {"CEOS": "0", "CUSTOM": "1"}}
    updated = plugin.on_container_create("ceos1", base_config)

    env = updated["environment"]
    assert env["CEOS"] == "0"
    assert env["CUSTOM"] == "1"
    assert env["EOS_PLATFORM"] == "ceoslab"
    assert env["INTFTYPE"] == "eth"


def test_arista_is_boot_ready_patterns() -> None:
    plugin = AristaPlugin()
    assert plugin.is_boot_ready("ceos1", "System ready") is True
    assert plugin.is_boot_ready("ceos1", "Startup complete") is True
    assert plugin.is_boot_ready("ceos1", "booting...") is False


def test_arista_get_interface_name() -> None:
    plugin = AristaPlugin()
    config = plugin.vendor_configs[0]
    assert plugin.get_interface_name(3, config) == "Ethernet3"


@pytest.mark.asyncio
async def test_docker_network_manager_create_attach_detach(monkeypatch) -> None:
    docker_networks = _load_docker_networks(monkeypatch)
    monkeypatch.setattr(docker_networks, "get_docker_ovs_plugin", lambda: object())

    networks = FakeNetworks(existing=["lab1-eth1"])
    containers = FakeContainers({"node1": FakeContainer(["lab1-eth1", "lab1-eth2"])})
    docker_client = FakeDocker(networks, containers)

    manager = docker_networks.DockerNetworkManager(docker_client)

    created = await manager.create_lab_networks("lab1", interface_count=2, start_index=1)
    assert created == ["lab1-eth1", "lab1-eth2"]

    attached = await manager.attach_container_to_networks(
        "node1",
        "lab1",
        interface_count=2,
        start_index=1,
    )
    assert attached == ["lab1-eth1", "lab1-eth2"]
    assert "node1" in networks.get("lab1-eth1").connected

    detached = await manager.detach_container_from_networks("node1", "lab1")
    assert detached == 2


@pytest.mark.asyncio
async def test_docker_network_manager_delete_lab(monkeypatch) -> None:
    docker_networks = _load_docker_networks(monkeypatch)
    monkeypatch.setattr(docker_networks, "get_docker_ovs_plugin", lambda: object())

    networks = FakeNetworks(existing=["lab2-eth1", "lab2-eth2", "other-eth1"])
    containers = FakeContainers({})
    docker_client = FakeDocker(networks, containers)

    manager = docker_networks.DockerNetworkManager(docker_client)
    deleted = await manager.delete_lab_networks("lab2")
    assert deleted == 2


def _make_libvirt_provider() -> libvirt_provider.LibvirtProvider:
    provider = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    provider._vlan_allocations = {}
    provider._next_vlan = {}
    provider._conn = None
    provider._uri = "qemu:///system"
    return provider


def test_libvirt_log_name() -> None:
    assert libvirt_provider._log_name("node1", {"_display_name": "Node 1"}) == "Node 1(node1)"
    assert libvirt_provider._log_name("node1", {"_display_name": "node1"}) == "node1"
    assert libvirt_provider._log_name("node1", None) == "node1"


def test_libvirt_domain_name_and_prefix() -> None:
    provider = _make_libvirt_provider()
    domain_name = provider._domain_name("lab!@#", "node$%")
    prefix = provider._lab_prefix("lab!@#")
    assert domain_name.startswith("arch-lab")
    assert prefix == "arch-lab"


def test_libvirt_allocate_vlans_reuse() -> None:
    provider = _make_libvirt_provider()
    vlans = provider._allocate_vlans("lab1", "node1", 3)
    assert vlans == [2000, 2001, 2002]

    reused = provider._allocate_vlans("lab1", "node1", 2)
    assert reused == [2000, 2001]


def test_libvirt_translate_container_path_to_host(monkeypatch, tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    host_path = tmp_path / "images"
    host_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARCHETYPE_HOST_IMAGE_PATH", str(host_path))

    translated = provider._translate_container_path_to_host("/var/lib/archetype/images/test.qcow2")
    assert translated == str(host_path / "test.qcow2")


def test_libvirt_generate_mac_address() -> None:
    provider = _make_libvirt_provider()
    mac = provider._generate_mac_address("domain1", 0)
    assert mac.startswith("52:54:00:")
    assert mac == provider._generate_mac_address("domain1", 0)


def test_libvirt_generate_domain_xml(monkeypatch, tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    monkeypatch.setattr(libvirt_provider.settings, "ovs_bridge_name", "arch-ovs-test", raising=False)

    overlay = tmp_path / "overlay.qcow2"
    data_volume = tmp_path / "data.qcow2"
    overlay.touch()
    data_volume.touch()

    class _FixedUUID:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self):
            self.calls += 1
            return "00000000-0000-0000-0000-000000000000"

    fixed_uuid = _FixedUUID()
    monkeypatch.setattr(libvirt_provider.uuid, "uuid4", fixed_uuid)

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {"memory": 1024, "cpu": 2, "disk_driver": "virtio", "nic_driver": "virtio"},
        overlay,
        data_volume_path=data_volume,
        interface_count=2,
        vlan_tags=[100, 101],
        kind="ceos",
    )

    assert "<name>arch-lab-node1</name>" in xml
    assert "<memory unit='MiB'>1024</memory>" in xml
    assert "arch-ovs-test" in xml
    assert "<tag id='100'/>" in xml
    assert "<archetype:kind>ceos</archetype:kind>" in xml


def test_libvirt_domain_status_mapping(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_NOSTATE = 0
        VIR_DOMAIN_RUNNING = 1
        VIR_DOMAIN_BLOCKED = 2
        VIR_DOMAIN_PAUSED = 3
        VIR_DOMAIN_SHUTDOWN = 4
        VIR_DOMAIN_SHUTOFF = 5
        VIR_DOMAIN_CRASHED = 6
        VIR_DOMAIN_PMSUSPENDED = 7

    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)

    class DummyDomain:
        def __init__(self, state: int) -> None:
            self._state = state

        def state(self):
            return self._state, 0

    assert provider._get_domain_status(DummyDomain(DummyLibvirt.VIR_DOMAIN_RUNNING)) == NodeStatus.RUNNING
    assert provider._get_domain_status(DummyDomain(DummyLibvirt.VIR_DOMAIN_SHUTOFF)) == NodeStatus.STOPPED
