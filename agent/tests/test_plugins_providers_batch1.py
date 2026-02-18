from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

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


def test_libvirt_prepare_startup_config_for_n9kv_strips_console_noise() -> None:
    provider = _make_libvirt_provider()
    raw = (
        "\r\n"
        "N9K-4# show running-config\r\n"
        "Building configuration...\r\n"
        "\r\n"
        "version 10.3(3)\r\n"
        "hostname N9K-4\r\n"
        "N9K-4#\r\n"
    )
    cleaned = provider._prepare_startup_config_for_injection("cisco_n9kv", raw)

    assert "show running-config" not in cleaned
    assert "N9K-4#" not in cleaned
    assert "version 10.3(3)" in cleaned
    assert cleaned.endswith("\n")


def test_libvirt_prepare_startup_config_for_n9kv_alias_strips_console_noise() -> None:
    provider = _make_libvirt_provider()
    raw = "N9K-4# show running-config\r\nhostname N9K-4\r\nN9K-4#\r\n"
    cleaned = provider._prepare_startup_config_for_injection("nxosv9000", raw)

    assert cleaned == "hostname N9K-4\n"


def test_libvirt_prepare_startup_config_for_n9kv_strips_extraction_headers() -> None:
    provider = _make_libvirt_provider()
    raw = (
        "!Command: show running-config\r\n"
        "!Running configuration last done at: Wed Feb 18 05:19:18 2026\r\n"
        "!Time: Wed Feb 18 05:21:30 2026\r\n"
        "\r\n"
        "version 10.5(3)\r\n"
        "hostname N9K-4\r\n"
    )
    cleaned = provider._prepare_startup_config_for_injection("cisco_n9kv", raw)

    assert "!Command:" not in cleaned
    assert "!Running configuration" not in cleaned
    assert "!Time:" not in cleaned
    assert cleaned.startswith("version 10.5(3)\n")


def test_libvirt_prepare_startup_config_non_n9kv_only_normalizes_newlines() -> None:
    provider = _make_libvirt_provider()
    raw = "Router# show running-config\r\nhostname R1\r\n"
    cleaned = provider._prepare_startup_config_for_injection("cisco_iosv", raw)

    assert cleaned == "Router# show running-config\nhostname R1\n"


def test_libvirt_undefine_domain_falls_back_to_nvram(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class _FakeLibvirtError(Exception):
        pass

    class _DummyLibvirt:
        VIR_DOMAIN_UNDEFINE_NVRAM = 4
        libvirtError = _FakeLibvirtError

    class _DummyDomain:
        def __init__(self) -> None:
            self.calls = []

        def undefine(self) -> None:
            self.calls.append("undefine")
            raise _FakeLibvirtError("cannot undefine domain with nvram")

        def undefineFlags(self, flags: int) -> None:
            self.calls.append(("undefineFlags", flags))

    monkeypatch.setattr(libvirt_provider, "libvirt", _DummyLibvirt)
    domain = _DummyDomain()

    provider._undefine_domain(domain, "arch-lab-node1")

    assert domain.calls == ["undefine", ("undefineFlags", 4)]


def test_libvirt_allocate_vlans_reuse() -> None:
    provider = _make_libvirt_provider()
    vlans = provider._allocate_vlans("lab1", "node1", 3)
    assert vlans == [100, 101, 102]

    reused = provider._allocate_vlans("lab1", "node1", 2)
    assert reused == [100, 101]


@pytest.mark.asyncio
async def test_libvirt_remove_vm_clears_post_boot_cache(monkeypatch, tmp_path: Path) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_SHUTOFF = 5
        VIR_DOMAIN_CRASHED = 6

    class DummyDomain:
        def state(self):
            return (DummyLibvirt.VIR_DOMAIN_SHUTOFF, 0)

    class DummyConn:
        def isAlive(self):
            return True

        def lookupByName(self, _name):
            return DummyDomain()

    provider._conn = DummyConn()
    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)

    workspace = tmp_path / "workspace"
    disks_dir = workspace / "disks"
    disks_dir.mkdir(parents=True, exist_ok=True)
    (disks_dir / "node1.qcow2").write_text("disk")

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider,
        "_undefine_domain",
        lambda _domain, domain_name: calls.append(("undefine", domain_name)),
    )
    monkeypatch.setattr(
        provider,
        "_clear_vm_post_boot_commands_cache",
        lambda domain_name: calls.append(("clear", domain_name)),
    )
    monkeypatch.setattr(provider, "_disks_dir", lambda _workspace: disks_dir)

    await provider._remove_vm("lab1", "node1", workspace)

    domain_name = provider._domain_name("lab1", "node1")
    assert ("undefine", domain_name) in calls
    assert ("clear", domain_name) in calls

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
        {
            "memory": 1024,
            "cpu": 2,
            "disk_driver": "virtio",
            "nic_driver": "virtio",
            "readiness_probe": "log_pattern",
            "readiness_pattern": "Press RETURN",
            "readiness_timeout": 2400,
        },
        overlay,
        data_volume_path=data_volume,
        interface_count=2,
        vlan_tags=[100, 101],
        kind="ceos",
    )

    assert "<name>arch-lab-node1</name>" in xml
    assert "<domain type='kvm'>" in xml
    assert "<memory unit='MiB'>1024</memory>" in xml
    assert "arch-ovs-test" in xml
    assert "<tag id='100'/>" in xml
    assert "<archetype:kind>ceos</archetype:kind>" in xml
    assert "<archetype:readiness_probe>log_pattern</archetype:readiness_probe>" in xml
    assert "<archetype:readiness_pattern>Press RETURN</archetype:readiness_pattern>" in xml
    assert "<archetype:readiness_timeout>2400</archetype:readiness_timeout>" in xml


def test_libvirt_generate_domain_xml_with_dedicated_mgmt_interface(monkeypatch, tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    monkeypatch.setattr(libvirt_provider.settings, "ovs_bridge_name", "arch-ovs-test", raising=False)

    overlay = tmp_path / "overlay.qcow2"
    overlay.touch()

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {
            "memory": 1024,
            "cpu": 2,
            "disk_driver": "virtio",
            "nic_driver": "e1000",
        },
        overlay,
        interface_count=2,
        vlan_tags=[100, 101],
        kind="cisco_n9kv",
        include_management_interface=True,
        management_network="default",
    )

    assert "<interface type='network'>" in xml
    assert "<source network='default'/>" in xml
    assert "<source bridge='arch-ovs-test'/>" in xml
    # Data-plane interface MACs are offset by one when mgmt NIC is present.
    assert provider._generate_mac_address("arch-lab-node1", 1) in xml
    assert provider._generate_mac_address("arch-lab-node1", 2) in xml


def test_libvirt_generate_domain_xml_efi_stateless(monkeypatch, tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    overlay = tmp_path / "overlay.qcow2"
    overlay.touch()

    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_find_ovmf_code_path",
        lambda self: "/usr/share/OVMF/OVMF_CODE.fd",
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_find_ovmf_vars_template",
        lambda self: "/usr/share/OVMF/OVMF_VARS.fd",
    )

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {
            "memory": 1024,
            "cpu": 2,
            "disk_driver": "virtio",
            "nic_driver": "e1000",
            "efi_boot": True,
            "efi_vars": "stateless",
        },
        overlay,
        interface_count=1,
        vlan_tags=[100],
        kind="cisco_n9kv",
    )

    # Stateless EFI uses qemu:commandline passthrough with a single read-only
    # pflash drive instead of <os firmware='efi'> + <loader>.
    assert "<os firmware='efi'>" not in xml
    assert "<loader " not in xml
    assert "<nvram " not in xml
    assert "<qemu:commandline>" in xml
    assert "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd" in xml
    assert "xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'" in xml


def test_libvirt_generate_domain_xml_cpu_limit_adds_cputune(tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    overlay = tmp_path / "overlay.qcow2"
    overlay.touch()

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {
            "memory": 1024,
            "cpu": 2,
            "cpu_limit": 25,
            "disk_driver": "virtio",
            "nic_driver": "e1000",
        },
        overlay,
        interface_count=1,
        vlan_tags=[100],
        kind="cisco_n9kv",
    )

    assert "<cputune>" in xml
    assert "<period>100000</period>" in xml
    # 2 vCPU * 100000 period * 25%
    assert "<quota>50000</quota>" in xml


def test_libvirt_generate_domain_xml_invalid_cpu_limit_skips_cputune(tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    overlay = tmp_path / "overlay.qcow2"
    overlay.touch()

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {
            "memory": 1024,
            "cpu": 2,
            "cpu_limit": "not-a-number",
            "disk_driver": "virtio",
            "nic_driver": "e1000",
        },
        overlay,
        interface_count=1,
        vlan_tags=[100],
        kind="cisco_n9kv",
    )

    assert "<cputune>" not in xml


def test_libvirt_generate_domain_xml_invalid_driver_falls_back_to_kvm(tmp_path: Path) -> None:
    provider = _make_libvirt_provider()
    overlay = tmp_path / "overlay.qcow2"
    overlay.touch()

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {
            "memory": 1024,
            "cpu": 2,
            "disk_driver": "virtio",
            "nic_driver": "e1000",
            "libvirt_driver": "not-valid",
        },
        overlay,
        interface_count=1,
        vlan_tags=[100],
        kind="cisco_n9kv",
    )

    assert "<domain type='kvm'>" in xml


def test_libvirt_parse_readiness_overrides_from_domain_metadata() -> None:
    provider = _make_libvirt_provider()

    class DummyDomain:
        def XMLDesc(self):
            return """<domain type='kvm'>
  <name>arch-lab-node1</name>
  <metadata>
    <archetype:node xmlns:archetype="http://archetype.io/libvirt/1">
      <archetype:kind>cat9000v-uadp</archetype:kind>
      <archetype:readiness_probe>log_pattern</archetype:readiness_probe>
      <archetype:readiness_pattern>Press RETURN</archetype:readiness_pattern>
      <archetype:readiness_timeout>2400</archetype:readiness_timeout>
    </archetype:node>
  </metadata>
</domain>"""

    overrides = provider._get_domain_readiness_overrides(DummyDomain())
    assert overrides == {
        "readiness_probe": "log_pattern",
        "readiness_pattern": "Press RETURN",
        "readiness_timeout": 2400,
    }


def test_libvirt_get_runtime_profile(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_RUNNING = 1
        VIR_DOMAIN_SHUTOFF = 5
        VIR_DOMAIN_SHUTDOWN = 4
        VIR_DOMAIN_PAUSED = 3
        VIR_DOMAIN_CRASHED = 6
        VIR_DOMAIN_NOSTATE = 0
        VIR_DOMAIN_BLOCKED = 2
        VIR_DOMAIN_PMSUSPENDED = 7

    class DummyDomain:
        def state(self):
            return (DummyLibvirt.VIR_DOMAIN_RUNNING, 0)

        def XMLDesc(self):
            return """<domain type='kvm'>
  <name>arch-lab-node1</name>
  <memory unit='MiB'>18432</memory>
  <vcpu>4</vcpu>
  <os><type arch='x86_64' machine='pc-i440fx-6.2'>hvm</type></os>
  <devices>
    <disk type='file' device='disk'>
      <source file='/var/lib/archetype/disks/node1.qcow2'/>
      <target dev='hda' bus='ide'/>
    </disk>
    <interface type='bridge'>
      <model type='e1000'/>
    </interface>
  </devices>
  <metadata>
    <archetype:node xmlns:archetype='http://archetype.io/libvirt/1'>
      <archetype:kind>cat9000v-uadp</archetype:kind>
      <archetype:readiness_probe>log_pattern</archetype:readiness_probe>
      <archetype:readiness_pattern>Press RETURN</archetype:readiness_pattern>
      <archetype:readiness_timeout>2400</archetype:readiness_timeout>
    </archetype:node>
  </metadata>
</domain>"""

    class DummyConn:
        def lookupByName(self, _name):
            return DummyDomain()

    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "conn",
        property(lambda self: DummyConn()),
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_domain_name",
        lambda self, _lab_id, _node_name: "arch-lab-node1",
    )

    profile = provider.get_runtime_profile("lab", "node1")
    runtime = profile["runtime"]
    assert profile["provider"] == "libvirt"
    assert profile["state"] == "running"
    assert runtime["memory"] == 18432
    assert runtime["cpu"] == 4
    assert runtime["disk_driver"] == "ide"
    assert runtime["nic_driver"] == "e1000"
    assert runtime["machine_type"] == "pc-i440fx-6.2"
    assert runtime["libvirt_driver"] == "kvm"
    assert runtime["kind"] == "cat9000v-uadp"
    assert runtime["readiness_probe"] == "log_pattern"
    assert runtime["readiness_timeout"] == 2400


def test_libvirt_get_runtime_profile_kib_memory_conversion(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_RUNNING = 1
        VIR_DOMAIN_SHUTOFF = 5
        VIR_DOMAIN_SHUTDOWN = 4
        VIR_DOMAIN_PAUSED = 3
        VIR_DOMAIN_CRASHED = 6
        VIR_DOMAIN_NOSTATE = 0
        VIR_DOMAIN_BLOCKED = 2
        VIR_DOMAIN_PMSUSPENDED = 7

    class DummyDomain:
        def state(self):
            return (DummyLibvirt.VIR_DOMAIN_RUNNING, 0)

        def XMLDesc(self):
            # libvirt default unit is KiB when omitted
            return """<domain type='kvm'>
  <name>arch-lab-node1</name>
  <memory>2097152</memory>
  <vcpu>1</vcpu>
  <os><type arch='x86_64' machine='pc-q35-6.2'>hvm</type></os>
  <devices>
    <disk type='file' device='disk'><target dev='vda' bus='virtio'/></disk>
    <interface type='bridge'><model type='virtio'/></interface>
  </devices>
</domain>"""

    class DummyConn:
        def lookupByName(self, _name):
            return DummyDomain()

    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "conn",
        property(lambda self: DummyConn()),
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_domain_name",
        lambda self, _lab_id, _node_name: "arch-lab-node1",
    )

    profile = provider.get_runtime_profile("lab", "node1")
    assert profile["runtime"]["memory"] == 2048


@pytest.mark.asyncio
async def test_libvirt_check_readiness_ssh_console_waits_for_management_ip(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_RUNNING = 1

        class libvirtError(Exception):
            pass

    class DummyDomain:
        def state(self):
            return (DummyLibvirt.VIR_DOMAIN_RUNNING, 0)

        def XMLDesc(self):
            return "<domain/>"

    class DummyConn:
        def lookupByName(self, _name):
            return DummyDomain()

    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "conn",
        property(lambda self: DummyConn()),
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_domain_name",
        lambda self, _lab_id, _node_name: "arch-lab-node1",
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_get_vm_management_ip",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(libvirt_provider, "get_console_method", lambda _kind: "ssh")
    class DummyCfg:
        readiness_probe = "ssh"
    monkeypatch.setattr(libvirt_provider, "get_libvirt_config", lambda _kind: DummyCfg())

    result = await provider.check_readiness("lab", "node1", "cisco_n9kv")

    assert result.is_ready is False
    assert result.progress_percent == 30
    assert "management IP" in result.message


@pytest.mark.asyncio
async def test_libvirt_check_readiness_ssh_console_marks_ready_when_ssh_open(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_RUNNING = 1

        class libvirtError(Exception):
            pass

    class DummyDomain:
        def state(self):
            return (DummyLibvirt.VIR_DOMAIN_RUNNING, 0)

        def XMLDesc(self):
            return "<domain/>"

    class DummyConn:
        def lookupByName(self, _name):
            return DummyDomain()

    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "conn",
        property(lambda self: DummyConn()),
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_domain_name",
        lambda self, _lab_id, _node_name: "arch-lab-node1",
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_get_vm_management_ip",
        AsyncMock(return_value="192.0.2.10"),
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_check_tcp_port",
        staticmethod(lambda _host, _port, _timeout: True),
    )
    monkeypatch.setattr(libvirt_provider, "get_console_method", lambda _kind: "ssh")
    class DummyCfg:
        readiness_probe = "ssh"
    monkeypatch.setattr(libvirt_provider, "get_libvirt_config", lambda _kind: DummyCfg())

    result = await provider.check_readiness("lab", "node1", "cisco_n9kv")

    assert result.is_ready is True
    assert result.progress_percent == 100
    assert "SSH ready" in result.message


@pytest.mark.asyncio
async def test_libvirt_check_readiness_ssh_console_uses_probe_when_not_ssh_readiness(monkeypatch) -> None:
    provider = _make_libvirt_provider()

    class DummyLibvirt:
        VIR_DOMAIN_RUNNING = 1

        class libvirtError(Exception):
            pass

    class DummyDomain:
        def state(self):
            return (DummyLibvirt.VIR_DOMAIN_RUNNING, 0)

        def XMLDesc(self):
            return "<domain/>"

    class DummyConn:
        def lookupByName(self, _name):
            return DummyDomain()

    monkeypatch.setattr(libvirt_provider, "libvirt", DummyLibvirt)
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "conn",
        property(lambda self: DummyConn()),
    )
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_domain_name",
        lambda self, _lab_id, _node_name: "arch-lab-node1",
    )
    monkeypatch.setattr(libvirt_provider, "get_console_method", lambda _kind: "ssh")

    class DummyCfg:
        readiness_probe = "log_pattern"

    monkeypatch.setattr(libvirt_provider, "get_libvirt_config", lambda _kind: DummyCfg())
    monkeypatch.setattr(
        libvirt_provider.LibvirtProvider,
        "_get_vm_management_ip",
        AsyncMock(return_value=None),
    )

    class DummyProbe:
        async def check(self, _node_name):
            return libvirt_provider.ReadinessResult(
                is_ready=True,
                message="Boot complete",
                progress_percent=100,
            )

    monkeypatch.setattr(libvirt_provider, "get_libvirt_probe", lambda *args, **kwargs: DummyProbe())

    result = await provider.check_readiness("lab", "node1", "cat9000v-q200")

    assert result.is_ready is True
    assert result.message == "Boot complete"


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
