from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agent.providers.libvirt as libvirt_mod


def _make_provider() -> libvirt_mod.LibvirtProvider:
    provider = libvirt_mod.LibvirtProvider.__new__(libvirt_mod.LibvirtProvider)
    provider._vlan_allocations = {}
    provider._next_vlan = {}
    provider._n9kv_loader_recovery_attempts = {}
    provider._n9kv_loader_recovery_last_at = {}
    provider._n9kv_poap_skip_attempted = set()
    provider._n9kv_admin_password_completed = set()
    provider._n9kv_panic_recovery_attempts = {}
    provider._n9kv_panic_recovery_last_at = {}
    provider._n9kv_panic_last_log_size = {}
    provider._conn = None
    provider._uri = "qemu:///system"
    return provider


@pytest.mark.asyncio
async def test_destroy_node_success_and_not_found_paths(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))

    provider._remove_vm = AsyncMock(return_value=None)
    ok = await provider.destroy_node("lab1", "r1", tmp_path)
    assert ok.success is True

    provider._remove_vm = AsyncMock(side_effect=_LibvirtError("domain not found"))
    not_found = await provider.destroy_node("lab1", "r1", tmp_path)
    assert not_found.success is True
    assert "Destroyed domain" in (not_found.stdout or "")


@pytest.mark.asyncio
async def test_destroy_node_returns_failures_for_errors(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))

    provider._remove_vm = AsyncMock(side_effect=_LibvirtError("permission denied"))
    libvirt_err = await provider.destroy_node("lab1", "r1", tmp_path)
    assert libvirt_err.success is False
    assert "Libvirt error" in (libvirt_err.error or "")

    provider._remove_vm = AsyncMock(side_effect=RuntimeError("boom"))
    generic_err = await provider.destroy_node("lab1", "r1", tmp_path)
    assert generic_err.success is False
    assert generic_err.error == "boom"


def test_get_console_info_sync_paths(monkeypatch):
    provider = _make_provider()

    class _Domain:
        def __init__(self, state_code: int):
            self._state = state_code

        def state(self):
            return (self._state, 0)

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(VIR_DOMAIN_RUNNING=1))
    monkeypatch.setattr(provider, "_get_domain_kind", lambda _domain: "iosv")
    monkeypatch.setattr(libvirt_mod, "get_console_method", lambda _kind: "virsh")
    monkeypatch.setattr(provider, "_get_tcp_serial_port", lambda _domain: 2222)

    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: _Domain(1))
    assert provider._get_console_info_sync("arch-lab1-r1") == ("virsh", "iosv", 2222)

    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: _Domain(5))
    assert provider._get_console_info_sync("arch-lab1-r1") is None

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    assert provider._get_console_info_sync("arch-lab1-r1") is None


@pytest.mark.asyncio
async def test_get_console_command_none_and_tcp_paths(monkeypatch, tmp_path):
    provider = _make_provider()

    provider._run_libvirt = AsyncMock(return_value=None)
    assert await provider.get_console_command("lab1", "r1", tmp_path) is None

    provider._run_libvirt = AsyncMock(return_value=("virsh", "iosv", 2301))
    tcp_cmd = await provider.get_console_command("lab1", "r1", tmp_path)
    assert tcp_cmd == ["python3", "-c", libvirt_mod._TCP_TELNET_CONSOLE_SCRIPT, "2301"]


@pytest.mark.asyncio
async def test_get_console_command_ssh_fallback_and_exception_paths(monkeypatch, tmp_path):
    provider = _make_provider()

    provider._run_libvirt = AsyncMock(return_value=("ssh", "cat9000v-q200", None))
    provider._get_vm_management_ip = AsyncMock(return_value=None)
    monkeypatch.setattr(libvirt_mod, "get_console_credentials", lambda _kind: ("admin", "admin"))
    no_ip = await provider.get_console_command("lab1", "r1", tmp_path)
    assert no_ip == ["virsh", "-c", provider._uri, "console", "--force", provider._domain_name("lab1", "r1")]

    provider._get_vm_management_ip = AsyncMock(return_value="192.0.2.20")
    monkeypatch.setattr(libvirt_mod, "get_console_credentials", lambda _kind: ("admin", "admin"))

    async def _to_thread_raises(*args, **kwargs):
        raise RuntimeError("probe error")

    monkeypatch.setattr(libvirt_mod.asyncio, "to_thread", _to_thread_raises)
    probe_error = await provider.get_console_command("lab1", "r1", tmp_path)
    assert probe_error == ["virsh", "-c", provider._uri, "console", "--force", provider._domain_name("lab1", "r1")]

    monkeypatch.setattr(
        libvirt_mod,
        "get_console_credentials",
        lambda _kind: (_ for _ in ()).throw(RuntimeError("bad creds")),
    )
    outer_error = await provider.get_console_command("lab1", "r1", tmp_path)
    assert outer_error is None


def test_readiness_and_probe_wrappers_delegate(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_mod, "_extract_probe_markers", lambda details: {"marker", details or ""})
    monkeypatch.setattr(libvirt_mod, "_classify_console_result", lambda result: f"class:{result}")
    monkeypatch.setattr(libvirt_mod, "_check_tcp_port", lambda host, port, timeout: host == "127.0.0.1")

    assert provider._extract_probe_markers("abc") == {"marker", "abc"}
    assert provider._classify_console_result("ok") == "class:ok"
    assert provider._check_tcp_port("127.0.0.1", 22, 0.5) is True


@pytest.mark.asyncio
async def test_n9kv_recovery_wrappers_delegate(monkeypatch):
    provider = _make_provider()
    provider._conn = SimpleNamespace(isAlive=lambda: True)

    monkeypatch.setattr(
        libvirt_mod,
        "_run_n9kv_loader_recovery",
        AsyncMock(return_value="loader-fixed"),
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_run_n9kv_panic_recovery",
        AsyncMock(return_value="panic-fixed"),
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_run_n9kv_poap_skip",
        AsyncMock(return_value="poap-skipped"),
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_run_n9kv_admin_password_setup",
        AsyncMock(return_value="admin-configured"),
    )

    assert await provider._run_n9kv_loader_recovery("arch-lab1-r1", "cisco_n9kv") == "loader-fixed"
    assert (
        await provider._run_n9kv_panic_recovery("arch-lab1-r1", "cisco_n9kv", "/tmp/serial.log")
        == "panic-fixed"
    )
    assert await provider._run_n9kv_poap_skip("arch-lab1-r1", "cisco_n9kv") == "poap-skipped"
    assert await provider._run_n9kv_admin_password_setup("arch-lab1-r1", "cisco_n9kv") == "admin-configured"


def test_readiness_timeout_and_kind_helpers(monkeypatch):
    provider = _make_provider()
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: object(),
    )
    monkeypatch.setattr(provider, "_get_domain_readiness_overrides", lambda _domain: {"readiness_timeout": 333})
    assert provider._get_readiness_timeout_sync("iosv", "lab1", "r1") == 333
    assert provider.get_readiness_timeout("iosv", "lab1", "r1") == 333

    monkeypatch.setattr(libvirt_mod, "get_readiness_timeout", lambda _kind: 120)
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    assert provider._get_readiness_timeout_sync("iosv", "lab1", "r1") == 120
    assert provider._get_readiness_timeout_sync("iosv", None, None) == 120

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: object(),
    )
    monkeypatch.setattr(provider, "_get_domain_kind", lambda _domain: "cisco_n9kv")
    assert provider._get_node_kind_sync("lab1", "r1") == "cisco_n9kv"
    assert provider.get_node_kind("lab1", "r1") == "cisco_n9kv"

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(RuntimeError("no domain")),
    )
    assert provider._get_node_kind_sync("lab1", "r1") is None


@pytest.mark.asyncio
async def test_async_timeout_and_kind_wrappers_delegate():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(side_effect=[200, "iosv"])
    timeout = await provider.get_readiness_timeout_async("iosv", "lab1", "r1")
    kind = await provider.get_node_kind_async("lab1", "r1")
    assert timeout == 200
    assert kind == "iosv"


def test_check_readiness_domain_sync_handles_lookup_error(monkeypatch):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))
    domain = SimpleNamespace(state=lambda: (1, 0))
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain)
    monkeypatch.setattr(provider, "_get_domain_readiness_overrides", lambda _domain: {"readiness_probe": "ssh"})
    assert provider._check_readiness_domain_sync("arch-lab1-r1") == (1, {"readiness_probe": "ssh"})

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(_LibvirtError("missing")),
    )
    assert provider._check_readiness_domain_sync("arch-lab1-r1") is None


def test_get_runtime_profile_sync_parses_additional_branches(monkeypatch):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(
            VIR_DOMAIN_RUNNING=1,
            VIR_DOMAIN_SHUTOFF=5,
            VIR_DOMAIN_SHUTDOWN=4,
            VIR_DOMAIN_PAUSED=3,
            VIR_DOMAIN_CRASHED=6,
            VIR_DOMAIN_NOSTATE=0,
            VIR_DOMAIN_BLOCKED=2,
            VIR_DOMAIN_PMSUSPENDED=7,
            libvirtError=_LibvirtError,
        ),
    )

    domain_xml = """
<domain type='qemu' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <memory unit='GiB'>2</memory>
  <vcpu>not-an-int</vcpu>
  <os>
    <type machine='pc-q35-9.0'>hvm</type>
  </os>
  <qemu:commandline>
    <qemu:arg value='if=pflash,readonly=on,file=/usr/share/OVMF.fd'/>
  </qemu:commandline>
  <devices>
    <disk device='cdrom'>
      <target bus='ide' dev='hdc'/>
    </disk>
    <disk device='disk'>
      <target bus='sata' dev='sda'/>
      <source file='/images/node.qcow2'/>
    </disk>
    <interface type='bridge'>
      <model type='virtio'/>
    </interface>
  </devices>
</domain>
""".strip()

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: SimpleNamespace(
            state=lambda: (1, 0),
            XMLDesc=lambda: domain_xml,
        ),
    )
    monkeypatch.setattr(provider, "_get_domain_metadata_values", lambda _domain: {})

    profile = provider._get_runtime_profile_sync("lab1", "r1")
    runtime = profile["runtime"]
    assert runtime["memory"] == 2048
    assert runtime["cpu"] is None
    assert runtime["efi_boot"] is True
    assert runtime["efi_vars"] == "stateless"
    assert runtime["disk_driver"] == "sata"
    assert runtime["disk_source"] == "/images/node.qcow2"


def test_get_runtime_profile_sync_handles_invalid_memory_value(monkeypatch):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(
            VIR_DOMAIN_RUNNING=1,
            VIR_DOMAIN_SHUTOFF=5,
            VIR_DOMAIN_SHUTDOWN=4,
            VIR_DOMAIN_PAUSED=3,
            VIR_DOMAIN_CRASHED=6,
            VIR_DOMAIN_NOSTATE=0,
            VIR_DOMAIN_BLOCKED=2,
            VIR_DOMAIN_PMSUSPENDED=7,
            libvirtError=_LibvirtError,
        ),
    )

    domain_xml = """
<domain type='kvm'>
  <memory unit='KiB'>bad-value</memory>
  <vcpu></vcpu>
  <devices></devices>
</domain>
""".strip()

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: SimpleNamespace(
            state=lambda: (1, 0),
            XMLDesc=lambda: domain_xml,
        ),
    )
    monkeypatch.setattr(provider, "_get_domain_metadata_values", lambda _domain: {})

    profile = provider._get_runtime_profile_sync("lab1", "r1")
    runtime = profile["runtime"]
    assert runtime["memory"] is None
    assert runtime["cpu"] is None


@pytest.mark.asyncio
async def test_extract_all_vm_configs_success_and_error_paths(tmp_path):
    provider = _make_provider()

    provider._run_libvirt = AsyncMock(return_value=[("r1", "iosv"), ("r2", "iosv")])
    provider._extract_config = AsyncMock(side_effect=[("r1", "hostname r1"), None])

    extracted = await provider._extract_all_vm_configs("lab1", tmp_path)
    assert extracted == [("r1", "hostname r1")]
    saved = tmp_path / "configs" / "r1" / "startup-config"
    assert saved.read_text() == "hostname r1"

    provider._run_libvirt = AsyncMock(side_effect=RuntimeError("list failed"))
    extracted_error = await provider._extract_all_vm_configs("lab1", tmp_path)
    assert extracted_error == []


def test_discover_labs_sync_success_and_error(monkeypatch):
    provider = _make_provider()

    d1 = SimpleNamespace(name=lambda: "arch-lab1-r1", UUIDString=lambda: "abcdef1234567890")
    d2 = SimpleNamespace(name=lambda: "arch-lab1-r2", UUIDString=lambda: "0123456789abcdef")
    malformed = SimpleNamespace(name=lambda: "arch-lab1", UUIDString=lambda: "ffffffffffffffff")
    other = SimpleNamespace(name=lambda: "not-managed", UUIDString=lambda: "1111111111111111")

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: [d1, d2, malformed, other],
    )
    monkeypatch.setattr(provider, "_get_domain_status", lambda _domain: libvirt_mod.NodeStatus.RUNNING)

    discovered = provider._discover_labs_sync()
    assert "lab1" in discovered
    assert sorted(node.name for node in discovered["lab1"]) == ["r1", "r2"]

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert provider._discover_labs_sync() == {}


@pytest.mark.asyncio
async def test_cleanup_orphan_domain_wrappers_delegate():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(return_value={"domains": ["arch-lab1-r1"], "disks": []})

    result = await provider.cleanup_orphan_domains({"lab1"})
    assert result["domains"] == ["arch-lab1-r1"]

    domains_only = await provider.cleanup_orphan_containers({"lab1"})
    assert domains_only == ["arch-lab1-r1"]


def test_cleanup_orphan_domains_sync_removes_domains_and_disks(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(VIR_DOMAIN_RUNNING=1, libvirtError=_LibvirtError),
    )

    provider._discover_labs_sync = lambda: {
        "lab-dead": [libvirt_mod.NodeInfo(name="r1", status=libvirt_mod.NodeStatus.RUNNING, container_id="cid1")],
        "lab-live": [libvirt_mod.NodeInfo(name="r2", status=libvirt_mod.NodeStatus.RUNNING, container_id="cid2")],
    }
    provider._is_orphan_lab = lambda lab_id, valid_lab_ids: lab_id not in valid_lab_ids
    provider._undefine_domain = MagicMock()
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._teardown_n9kv_poap_network = MagicMock()
    provider._cleanup_orphan_vlans = MagicMock()

    class _Domain:
        def __init__(self):
            self.destroy = MagicMock()

        def state(self):
            return (1, 0)

    domains = {"arch-lab-dead-r1": _Domain()}
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda name: domains[name])

    dead_disks = tmp_path / "lab-dead" / "disks"
    dead_disks.mkdir(parents=True, exist_ok=True)
    disk_path = dead_disks / "r1.qcow2"
    disk_path.write_text("x")

    removed = provider._cleanup_orphan_domains_sync(valid_lab_ids={"lab-live"}, workspace_base=tmp_path)
    assert removed["domains"] == ["arch-lab-dead-r1"]
    assert str(disk_path) in removed["disks"]
    provider._cleanup_orphan_vlans.assert_called_once()


def test_cleanup_orphan_domains_sync_handles_exceptions(monkeypatch, tmp_path):
    provider = _make_provider()
    provider._discover_labs_sync = lambda: (_ for _ in ()).throw(RuntimeError("discover failed"))
    removed = provider._cleanup_orphan_domains_sync(valid_lab_ids=set(), workspace_base=tmp_path)
    assert removed == {"domains": [], "disks": []}


@pytest.mark.asyncio
async def test_cleanup_lab_orphan_domains_wrapper_delegates():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(return_value={"domains": ["arch-lab1-r2"], "disks": []})
    out = await provider.cleanup_lab_orphan_domains("lab1", {"r1"})
    assert out["domains"] == ["arch-lab1-r2"]


def test_cleanup_lab_orphan_domains_sync_removes_orphans_and_disks(monkeypatch, tmp_path):
    provider = _make_provider()
    provider._vlan_allocations["lab1"] = {"r1": [100], "r2": [101]}

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(VIR_DOMAIN_RUNNING=1, libvirtError=_LibvirtError),
    )

    class _Domain:
        def __init__(self, name: str):
            self._name = name
            self.destroy = MagicMock()

        def name(self):
            return self._name

        def state(self):
            return (1, 0)

    d_keep = _Domain("arch-lab1-r1")
    d_remove = _Domain("arch-lab1-r2")
    d_other = _Domain("arch-lab2-r3")
    provider._conn = SimpleNamespace(isAlive=lambda: True, listAllDomains=lambda _flags=0: [d_keep, d_remove, d_other])
    provider._undefine_domain = MagicMock()
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._teardown_n9kv_poap_network = MagicMock()

    disks = tmp_path / "lab1" / "disks"
    disks.mkdir(parents=True, exist_ok=True)
    d1 = disks / "r2.qcow2"
    d2 = disks / "r2-data.qcow2"
    d1.write_text("x")
    d2.write_text("x")

    removed = provider._cleanup_lab_orphan_domains_sync("lab1", {"r1"}, tmp_path)
    assert removed["domains"] == ["arch-lab1-r2"]
    assert str(d1) in removed["disks"]
    assert str(d2) in removed["disks"]
    assert "r2" not in provider._vlan_allocations["lab1"]


def test_cleanup_lab_orphan_domains_sync_handles_outer_error(tmp_path):
    provider = _make_provider()
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("libvirt down")),
    )
    removed = provider._cleanup_lab_orphan_domains_sync("lab1", {"r1"}, tmp_path)
    assert removed == {"domains": [], "disks": []}


@pytest.mark.asyncio
async def test_hot_connect_validation_and_failure_paths(monkeypatch):
    provider = _make_provider()

    provider.get_node_vlans = lambda lab_id, node: [100] if node == "r1" else [200]
    out_of_range = await provider.hot_connect("lab1", "r1", 2, "r2", 0)
    assert out_of_range is False

    provider.get_node_vlans = lambda lab_id, node: [100, 200]
    provider.get_vm_interface_port = AsyncMock(side_effect=[None, "vnet2"])
    missing_port = await provider.hot_connect("lab1", "r1", 0, "r2", 0)
    assert missing_port is False

    provider.get_vm_interface_port = AsyncMock(side_effect=["vnet1", "vnet2"])
    proc = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"ovs error")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    ovs_fail = await provider.hot_connect("lab1", "r1", 0, "r2", 0)
    assert ovs_fail is False

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    provider.get_vm_interface_port = AsyncMock(side_effect=["vnet1", "vnet2"])
    exception_fail = await provider.hot_connect("lab1", "r1", 0, "r2", 0)
    assert exception_fail is False
