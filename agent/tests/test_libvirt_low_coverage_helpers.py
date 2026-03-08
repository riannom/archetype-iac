from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agent.providers.libvirt as libvirt_mod


def _make_provider() -> libvirt_mod.LibvirtProvider:
    p = libvirt_mod.LibvirtProvider.__new__(libvirt_mod.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._n9kv_loader_recovery_attempts = {}
    p._n9kv_loader_recovery_last_at = {}
    p._n9kv_poap_skip_attempted = set()
    p._n9kv_admin_password_completed = set()
    p._n9kv_panic_recovery_attempts = {}
    p._n9kv_panic_recovery_last_at = {}
    p._n9kv_panic_last_log_size = {}
    p._conn = None
    p._uri = "qemu:///system"
    return p


def test_coalesce_prefers_non_none_value():
    assert libvirt_mod._coalesce("x", "y") == "x"
    assert libvirt_mod._coalesce(None, "y") == "y"


def test_log_name_uses_display_name_when_present():
    assert libvirt_mod._log_name("r1", {"_display_name": "R1"}) == "R1(r1)"
    assert libvirt_mod._log_name("r1", {"_display_name": "r1"}) == "r1"
    assert libvirt_mod._log_name("r1", {}) == "r1"


def test_init_raises_when_libvirt_not_available(monkeypatch):
    monkeypatch.setattr(libvirt_mod, "LIBVIRT_AVAILABLE", False)
    with pytest.raises(ImportError, match="libvirt-python"):
        libvirt_mod.LibvirtProvider()


def test_init_sets_core_state_and_properties(monkeypatch):
    monkeypatch.setattr(libvirt_mod, "LIBVIRT_AVAILABLE", True)
    monkeypatch.setattr(libvirt_mod.settings, "libvirt_uri", "qemu:///test", raising=False)
    monkeypatch.setattr(
        libvirt_mod.concurrent.futures,
        "ThreadPoolExecutor",
        lambda **_kwargs: SimpleNamespace(shutdown=lambda **_k: None),
    )

    provider = libvirt_mod.LibvirtProvider()

    assert provider.name == "libvirt"
    assert provider.display_name == "Libvirt/QEMU"
    assert "deploy" in provider.capabilities
    assert provider._uri == "qemu:///test"
    assert provider._vm_port_cache == {}


def test_conn_reuses_alive_connection():
    provider = _make_provider()
    alive_conn = SimpleNamespace(isAlive=lambda: True)
    provider._conn = alive_conn
    assert provider.conn is alive_conn


def test_conn_reopens_when_dead_and_raises_when_open_fails(monkeypatch):
    provider = _make_provider()
    provider._conn = SimpleNamespace(isAlive=lambda: False)

    opened = SimpleNamespace(isAlive=lambda: True)
    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(open=lambda _uri: opened))
    assert provider.conn is opened

    provider._conn = None
    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(open=lambda _uri: None))
    with pytest.raises(RuntimeError, match="Failed to connect"):
        _ = provider.conn


def test_get_vm_stats_sync_filters_and_extracts_fields(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(VIR_DOMAIN_RUNNING=1))

    class _Domain:
        def __init__(self, name: str, state: int, info: tuple[int, int, int, int, int]):
            self._name = name
            self._state = state
            self._info = info

        def name(self):
            return self._name

        def state(self):
            return (self._state, 0)

        def info(self):
            return self._info

    domains = [
        _Domain("arch-lab1-node1", 1, (1, 2_048_000, 1_024_000, 2, 10)),
        _Domain("other-domain", 1, (1, 1_024_000, 512_000, 1, 10)),
        _Domain("arch-lab2-node2", 5, (5, 1_024_000, 256_000, 1, 20)),
    ]
    provider._conn = SimpleNamespace(isAlive=lambda: True, listAllDomains=lambda _flags=0: domains)
    monkeypatch.setattr(
        provider,
        "_get_domain_metadata_values",
        lambda domain: {
            "arch-lab1-node1": {"lab_id": "lab1", "node_name": "node1"},
            "other-domain": {},
            "arch-lab2-node2": {"lab_id": "lab2", "node_name": "node2"},
        }[domain.name()],
    )

    stats = provider.get_vm_stats_sync()

    assert len(stats) == 2
    assert stats[0]["status"] == "running"
    assert stats[0]["lab_prefix"] == "lab1"
    assert stats[0]["node_name"] == "node1"
    assert stats[0]["memory_mb"] == 2000
    assert stats[1]["status"] == "stopped"


def test_get_vm_stats_sync_returns_empty_on_error():
    provider = _make_provider()
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert provider.get_vm_stats_sync() == []


def test_canonical_kind_and_disks_dir(tmp_path):
    provider = _make_provider()
    assert provider._canonical_kind(None) == ""
    assert provider._canonical_kind(" IOSV ") == "iosv"

    disks = provider._disks_dir(tmp_path)
    assert disks == tmp_path / "disks"
    assert disks.exists()


def test_undefine_domain_raises_when_nvram_flag_unavailable(monkeypatch):
    provider = _make_provider()

    class _DummyErr(Exception):
        pass

    class _Domain:
        def undefine(self):
            raise _DummyErr("fail")

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(libvirtError=_DummyErr, VIR_DOMAIN_UNDEFINE_NVRAM=None),
    )

    with pytest.raises(_DummyErr):
        provider._undefine_domain(_Domain(), "arch-lab-node1")


def test_get_used_vlan_tags_on_ovs_bridge_handles_failures(monkeypatch):
    provider = _make_provider()

    monkeypatch.setattr(
        libvirt_mod.subprocess,
        "run",
        lambda args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:2] == ["ovs-vsctl", "list-ports"]
        else SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert provider._get_used_vlan_tags_on_ovs_bridge() == set()

    def _csv_fails(args, **_kwargs):
        if args[:2] == ["ovs-vsctl", "list-ports"]:
            return SimpleNamespace(returncode=0, stdout="p1\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(libvirt_mod.subprocess, "run", _csv_fails)
    assert provider._get_used_vlan_tags_on_ovs_bridge() == set()

    monkeypatch.setattr(
        libvirt_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no ovs")),
    )
    assert provider._get_used_vlan_tags_on_ovs_bridge() == set()


def test_extract_domain_vlan_tags_parses_valid_and_skips_invalid():
    provider = _make_provider()
    domain = SimpleNamespace(
        XMLDesc=lambda _flags=0: """
<domain>
  <devices>
    <interface type='bridge'>
      <vlan>
        <tag id='100'/>
        <tag id='abc'/>
        <tag/>
      </vlan>
    </interface>
    <interface type='network'>
      <vlan><tag id='200'/></vlan>
    </interface>
  </devices>
</domain>
"""
    )

    assert provider._extract_domain_vlan_tags(domain) == [100]

    broken = SimpleNamespace(XMLDesc=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("bad xml")))
    assert provider._extract_domain_vlan_tags(broken) == []


def test_discover_vlan_allocations_from_domains_handles_name_errors():
    provider = _make_provider()

    class _BrokenNameDomain:
        def name(self):
            raise RuntimeError("bad name")

    class _Domain:
        def __init__(self, name: str, tags: list[int]):
            self._name = name
            self._tags = tags

        def name(self):
            return self._name

        def XMLDesc(self, _flags=0):
            return "<domain/>"

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: [
            _BrokenNameDomain(),
            _Domain("arch-lab1-r1", [2001]),
            _Domain("arch-other-r2", [2002]),
        ],
    )
    provider._extract_domain_vlan_tags = lambda d: d._tags if hasattr(d, "_tags") else []
    provider._get_domain_metadata_values = lambda domain: {
        "arch-lab1-r1": {"lab_id": "lab1", "node_name": "r1"},
        "arch-other-r2": {"lab_id": "other", "node_name": "r2"},
    }.get(getattr(domain, "_name", ""), {})

    discovered = provider._discover_vlan_allocations_from_domains("lab1")
    assert discovered == {"r1": [2001]}

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert provider._discover_vlan_allocations_from_domains("lab1") == {}


def test_ovs_port_exists_backend_and_fallbacks(monkeypatch):
    provider = _make_provider()

    backend = SimpleNamespace(check_port_exists=lambda name: name == "tap0")
    monkeypatch.setattr("agent.network.backends.registry.get_network_backend", lambda: backend)
    assert provider._ovs_port_exists("tap0") is True
    assert provider._ovs_port_exists("tap1") is False

    monkeypatch.setattr(
        "agent.network.backends.registry.get_network_backend",
        lambda: (_ for _ in ()).throw(RuntimeError("no backend")),
    )
    monkeypatch.setattr(
        libvirt_mod.subprocess,
        "run",
        lambda _args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert provider._ovs_port_exists("tap2") is True

    monkeypatch.setattr(
        libvirt_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no ovs")),
    )
    assert provider._ovs_port_exists("tap3") is False


def test_recover_stale_network_keeps_only_existing_nodes_and_handles_errors(tmp_path):
    provider = _make_provider()
    provider._vlan_allocations["lab1"] = {"nodeA": [2001], "stale": [2002]}
    provider._load_vlan_allocations = lambda *_args, **_kwargs: None
    provider._save_vlan_allocations = MagicMock()
    provider._discover_vlan_allocations_from_domains = lambda _lab_id: {}
    domain = SimpleNamespace(name=lambda: "arch-lab1-nodeA")
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: [domain],
    )
    provider._get_domain_metadata_values = lambda d: {"lab_id": "lab1", "node_name": "nodeA"} if d is domain else {}

    recovered = provider._recover_stale_network("lab1", tmp_path)
    assert recovered == {"nodeA": [2001]}
    provider._save_vlan_allocations.assert_called_once_with("lab1", tmp_path)

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("down")),
    )
    assert provider._recover_stale_network("lab1", tmp_path) == {}


def test_recover_stale_network_skips_metadata_missing_domains(tmp_path):
    provider = _make_provider()
    provider._vlan_allocations["lab1"] = {"nodeA": [2001]}
    provider._load_vlan_allocations = lambda *_args, **_kwargs: None
    provider._save_vlan_allocations = MagicMock()
    domain = SimpleNamespace(name=lambda: "arch-lab1-nodeA")
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: [domain],
    )
    provider._discover_vlan_allocations_from_domains = lambda _lab_id: {}
    provider._get_domain_metadata_values = lambda _domain: {}

    assert provider._recover_stale_network("lab1", tmp_path) == {}


def test_recover_stale_network_increments_skip_metric(tmp_path):
    provider = _make_provider()
    provider._vlan_allocations["lab1"] = {"nodeA": [2001]}
    provider._load_vlan_allocations = lambda *_args, **_kwargs: None
    provider._save_vlan_allocations = MagicMock()
    provider._discover_vlan_allocations_from_domains = lambda _lab_id: {}
    domain = SimpleNamespace(name=lambda: "legacy-domain")
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: [domain],
    )
    provider._get_domain_metadata_values = lambda _domain: {}
    metric = MagicMock()
    metric.labels.return_value = metric

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(libvirt_mod, "runtime_identity_skips", metric)
        assert provider._recover_stale_network("lab1", tmp_path) == {}

    metric.labels.assert_called_once_with(
        resource_type="libvirt_domain",
        operation="recover_stale_network",
        reason="missing_runtime_metadata",
    )
    metric.inc.assert_called_once_with(1)


def test_allocate_vlans_reallocates_when_recovered_insufficient_and_saves(tmp_path):
    provider = _make_provider()
    provider._vlan_allocations["lab1"] = {"node1": [100]}
    provider._next_vlan["lab1"] = provider.VLAN_RANGE_START
    provider._get_used_vlan_tags_on_ovs_bridge = lambda: set()
    provider._save_vlan_allocations = MagicMock()

    vlans = provider._allocate_vlans("lab1", "node1", 2, workspace=tmp_path)
    assert len(vlans) == 2
    provider._save_vlan_allocations.assert_called_once_with("lab1", tmp_path)


def test_allocate_vlans_raises_when_no_tags_available():
    provider = _make_provider()
    provider._next_vlan["lab1"] = provider.VLAN_RANGE_START
    provider._vlan_allocations["lab1"] = {}
    provider._get_used_vlan_tags_on_ovs_bridge = lambda: set(
        range(provider.VLAN_RANGE_START, provider.VLAN_RANGE_END + 1)
    )

    with pytest.raises(RuntimeError, match="No free VLAN tags"):
        provider._allocate_vlans("lab1", "node1", 1)


@pytest.mark.asyncio
async def test_set_vm_tap_mtu_branches(monkeypatch):
    provider = _make_provider()

    monkeypatch.setattr(libvirt_mod.settings, "local_mtu", 0, raising=False)
    provider.get_vm_interface_port = AsyncMock()
    provider._vlan_allocations["lab1"] = {"node1": [2001]}
    await provider._set_vm_tap_mtu("lab1", "node1")
    provider.get_vm_interface_port.assert_not_awaited()

    monkeypatch.setattr(libvirt_mod.settings, "local_mtu", 1500, raising=False)
    provider.get_vm_interface_port = AsyncMock(return_value="tap1")
    proc = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"err")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    await provider._set_vm_tap_mtu("lab1", "node1")

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=RuntimeError("ip command failed")),
    )
    await provider._set_vm_tap_mtu("lab1", "node1")


def test_get_base_image_resolution_paths(tmp_path, monkeypatch):
    provider = _make_provider()

    assert provider._get_base_image({}) is None

    missing_abs = str(tmp_path / "missing.qcow2")
    assert provider._get_base_image({"image": missing_abs}) is None

    abs_path = tmp_path / "base.qcow2"
    abs_path.write_text("x")
    assert provider._get_base_image({"image": str(abs_path)}) == str(abs_path)

    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(libvirt_mod.settings, "workspace_path", str(tmp_path), raising=False)
    monkeypatch.setattr(libvirt_mod.settings, "qcow2_store_path", None, raising=False)

    exact = images_dir / "iosv.qcow2"
    exact.write_text("x")
    assert provider._get_base_image({"image": "iosv.qcow2"}) == str(exact)

    ext = images_dir / "n9kv.qcow2"
    ext.write_text("x")
    assert provider._get_base_image({"image": "n9kv"}) == str(ext)

    partial = images_dir / "router-latest.qcow"
    partial.write_text("x")
    assert provider._get_base_image({"image": "latest"}) == str(partial)


def test_delegate_wrappers_for_libvirt_xml_helpers(monkeypatch, tmp_path):
    provider = _make_provider()
    called: dict[str, object] = {}

    monkeypatch.setattr(
        libvirt_mod,
        "_translate_container_path_to_host",
        lambda path: called.setdefault("translate", path) or "/host/path",
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_create_overlay_disk_sync",
        lambda base, overlay: called.setdefault("overlay", (base, overlay)) or True,
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_create_data_volume_sync",
        lambda path, size: called.setdefault("data", (path, size)) or True,
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_find_ovmf_code_path",
        lambda: called.setdefault("ovmf_code", True) or "/ovmf/code.fd",
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_find_ovmf_vars_template",
        lambda: called.setdefault("ovmf_vars", True) or "/ovmf/vars.fd",
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_resolve_domain_driver",
        lambda requested, node_name, allowed: (called.setdefault("driver", (requested, node_name, allowed)) or "kvm"),
    )
    monkeypatch.setattr(
        libvirt_mod,
        "_patch_vjunos_svm_compat",
        lambda overlay: called.setdefault("patch", overlay) or True,
    )

    assert provider._translate_container_path_to_host("/container/path") == "/container/path"
    assert provider._create_overlay_disk_sync("base.qcow2", tmp_path / "overlay.qcow2") == ("base.qcow2", tmp_path / "overlay.qcow2")
    assert provider._create_data_volume_sync(tmp_path / "data.qcow2", 20) == (tmp_path / "data.qcow2", 20)
    assert provider._find_ovmf_code_path() is True
    assert provider._find_ovmf_vars_template() is True
    assert provider._resolve_domain_driver("qemu", "node1") == ("qemu", "node1", provider.ALLOWED_DOMAIN_DRIVERS)
    assert provider._patch_vjunos_svm_compat(tmp_path / "overlay.qcow2") == (tmp_path / "overlay.qcow2")


@pytest.mark.asyncio
async def test_async_disk_wrappers_delegate_with_to_thread(monkeypatch, tmp_path):
    provider = _make_provider()

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(libvirt_mod, "_create_overlay_disk_sync", lambda *_args: True)
    monkeypatch.setattr(libvirt_mod, "_create_data_volume_sync", lambda *_args: True)

    assert await provider._create_overlay_disk("base.qcow2", tmp_path / "overlay.qcow2") is True
    assert await provider._create_data_volume(tmp_path / "data.qcow2", 5) is True


def test_domain_xml_and_serial_port_wrappers(monkeypatch, tmp_path):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_mod, "_allocate_tcp_serial_port", lambda: 2024)
    monkeypatch.setattr(libvirt_mod, "_get_tcp_serial_port", lambda _domain: 2025)
    monkeypatch.setattr(libvirt_mod, "_generate_domain_xml", lambda *args, **kwargs: "<domain/>")

    xml = provider._generate_domain_xml(
        "arch-lab-node1",
        {"memory": 512, "cpu": 1},
        tmp_path / "overlay.qcow2",
        interface_count=1,
        vlan_tags=[100],
    )

    assert xml == "<domain/>"
    assert provider._allocate_tcp_serial_port() == 2024
    assert provider._get_tcp_serial_port(SimpleNamespace()) == 2025


def test_get_domain_status_and_node_from_domain(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(
            VIR_DOMAIN_NOSTATE=0,
            VIR_DOMAIN_RUNNING=1,
            VIR_DOMAIN_BLOCKED=2,
            VIR_DOMAIN_PAUSED=3,
            VIR_DOMAIN_SHUTDOWN=4,
            VIR_DOMAIN_SHUTOFF=5,
            VIR_DOMAIN_CRASHED=6,
            VIR_DOMAIN_PMSUSPENDED=7,
        ),
    )

    running_domain = SimpleNamespace(state=lambda: (1, 0))
    assert provider._get_domain_status(running_domain).value == "running"

    unknown_domain = SimpleNamespace(state=lambda: (999, 0))
    assert provider._get_domain_status(unknown_domain).value == "unknown"

    domain = SimpleNamespace(
        name=lambda: "arch-lab1-r1",
        state=lambda: (1, 0),
        UUIDString=lambda: "abcdef1234567890",
    )
    monkeypatch.setattr(provider, "_get_domain_metadata_values", lambda _domain: {
        "lab_id": "lab1",
        "node_name": "r1",
        "node_definition_id": "node-def-r1",
    })
    node = provider._node_from_domain(domain, "lab1")
    assert node is not None
    assert node.name == "r1"
    assert node.container_id == "abcdef123456"

    monkeypatch.setattr(provider, "_get_domain_metadata_values", lambda _domain: {})
    assert provider._node_from_domain(domain, "lab1") is None

    other = SimpleNamespace(name=lambda: "arch-other-r2")
    monkeypatch.setattr(provider, "_get_domain_metadata_values", lambda _domain: {"lab_id": "other"})
    assert provider._node_from_domain(other, "lab1") is None


@pytest.mark.asyncio
async def test_vm_management_ip_wrapper_delegates(monkeypatch):
    provider = _make_provider()

    async def _fake_get_vm_ip(domain_name: str, uri: str):
        assert domain_name == "arch-lab-node1"
        assert uri == provider._uri
        return "192.0.2.10"

    monkeypatch.setattr(libvirt_mod, "_get_vm_management_ip", _fake_get_vm_ip)
    assert await provider._get_vm_management_ip("arch-lab-node1") == "192.0.2.10"


def test_n9kv_wrapper_helpers_delegate_and_pass_connection(monkeypatch, tmp_path):
    provider = _make_provider()
    provider._conn = SimpleNamespace(isAlive=lambda: True)
    called: dict[str, object] = {}

    monkeypatch.setattr(libvirt_mod, "_node_uses_dedicated_mgmt_interface", lambda kind: kind == "cisco_n9kv")
    monkeypatch.setattr(libvirt_mod, "_n9kv_poap_network_name", lambda lab, node: f"net-{lab}-{node}")
    monkeypatch.setattr(libvirt_mod, "_n9kv_poap_bridge_name", lambda lab, node: f"br-{lab}-{node}")
    monkeypatch.setattr(libvirt_mod, "_n9kv_poap_subnet", lambda lab, node: ("10.0.0.1", "10.0.0.2", "255.255.255.0"))
    monkeypatch.setattr(libvirt_mod, "_n9kv_poap_config_url", lambda lab, node, gw: f"http://{gw}/{lab}/{node}")
    monkeypatch.setattr(libvirt_mod, "_n9kv_poap_tftp_root", lambda lab, node: tmp_path / f"{lab}-{node}")
    monkeypatch.setattr(libvirt_mod, "_n9kv_poap_bootfile_name", lambda: "poap.py")
    monkeypatch.setattr(libvirt_mod, "_stage_n9kv_poap_tftp_script", lambda lab, node, gw: (tmp_path / "poap.py", f"{lab}-{node}-{gw}"))
    def _ensure_poap(conn, lab, node):
        called["ensure_poap"] = (conn, lab, node)
        return "poap-net"

    def _teardown_poap(conn, lab, node):
        called["teardown_poap"] = (conn, lab, node)

    def _resolve_mgmt(conn, lab, node, kind, canonical_kind_fn):
        called["resolve_mgmt"] = (conn, lab, node, kind, canonical_kind_fn)
        return (True, "default")

    def _ensure_libvirt(conn, network_name):
        called["ensure_libvirt"] = (conn, network_name)
        return True

    monkeypatch.setattr(libvirt_mod, "_ensure_n9kv_poap_network", _ensure_poap)
    monkeypatch.setattr(libvirt_mod, "_teardown_n9kv_poap_network", _teardown_poap)
    monkeypatch.setattr(libvirt_mod, "_resolve_management_network", _resolve_mgmt)
    monkeypatch.setattr(libvirt_mod, "_ensure_libvirt_network", _ensure_libvirt)

    assert provider._node_uses_dedicated_mgmt_interface("cisco_n9kv") is True
    assert provider._n9kv_poap_network_name("lab1", "n9k1") == "net-lab1-n9k1"
    assert provider._n9kv_poap_bridge_name("lab1", "n9k1") == "br-lab1-n9k1"
    assert provider._n9kv_poap_subnet("lab1", "n9k1") == ("10.0.0.1", "10.0.0.2", "255.255.255.0")
    assert provider._n9kv_poap_config_url("lab1", "n9k1", "10.0.0.1") == "http://10.0.0.1/lab1/n9k1"
    assert provider._n9kv_poap_tftp_root("lab1", "n9k1") == tmp_path / "lab1-n9k1"
    assert provider._n9kv_poap_bootfile_name() == "poap.py"
    assert provider._stage_n9kv_poap_tftp_script("lab1", "n9k1", "10.0.0.1") == (
        tmp_path / "poap.py",
        "lab1-n9k1-10.0.0.1",
    )
    assert provider._ensure_n9kv_poap_network("lab1", "n9k1") == "poap-net"
    provider._teardown_n9kv_poap_network("lab1", "n9k1")
    assert provider._resolve_management_network("lab1", "n9k1", "cisco_n9kv") == (True, "default")
    assert provider._ensure_libvirt_network("default") is True
    assert called["ensure_poap"] == (provider._conn, "lab1", "n9k1")
    assert called["teardown_poap"] == (provider._conn, "lab1", "n9k1")
    assert called["resolve_mgmt"][:4] == (provider._conn, "lab1", "n9k1", "cisco_n9kv")
    assert called["ensure_libvirt"] == (provider._conn, "default")


def test_domain_has_dedicated_management_interface():
    provider = _make_provider()
    with_mgmt = SimpleNamespace(
        XMLDesc=lambda _flags=0: "<domain><devices><interface type='network'><source network='default'/></interface></devices></domain>"
    )
    without_mgmt = SimpleNamespace(
        XMLDesc=lambda _flags=0: "<domain><devices><interface type='bridge'/></devices></domain>"
    )
    broken = SimpleNamespace(XMLDesc=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("bad xml")))

    assert provider._domain_has_dedicated_mgmt_interface(with_mgmt) is True
    assert provider._domain_has_dedicated_mgmt_interface(without_mgmt) is False
    assert provider._domain_has_dedicated_mgmt_interface(broken) is False


def test_resolve_data_interface_mac_sync_accounts_for_mgmt_and_reserved(monkeypatch):
    provider = _make_provider()
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: SimpleNamespace(),
    )
    monkeypatch.setattr(provider, "_get_domain_kind", lambda _domain: "xrv9k")
    monkeypatch.setattr(provider, "_node_uses_dedicated_mgmt_interface", lambda _kind: True)
    monkeypatch.setattr(provider, "_domain_has_dedicated_mgmt_interface", lambda _domain: True)
    monkeypatch.setattr(libvirt_mod, "get_vendor_config", lambda _kind: SimpleNamespace(reserved_nics=2))
    monkeypatch.setattr(provider, "_generate_mac_address", lambda _domain_name, index: f"mac-{index}")

    assert provider._resolve_data_interface_mac_sync("lab1", "r1", 0) == "mac-3"

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(RuntimeError("lookup failed")),
    )
    assert provider._resolve_data_interface_mac_sync("lab1", "r1", 1) == "mac-1"


def test_get_vm_interface_port_sync_paths(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(provider, "_resolve_data_interface_mac_sync", lambda *_args: "52:54:00:aa:bb:cc")
    provider._domain_name = lambda _lab_id, _node_name: "arch-lab1-r1"
    provider._generate_ovs_interface_id = lambda _domain_name, _role, _index: "iface-id-1"
    provider._ovs_port_exists = lambda port_name: port_name == "vnet-direct"

    domain = SimpleNamespace(
        XMLDesc=lambda *_args: """
<domain>
  <devices>
    <interface type='bridge'>
      <mac address='52:54:00:aa:bb:cc'/>
      <target dev='vnet-direct'/>
    </interface>
  </devices>
</domain>
"""
    )
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain)

    def _run_xml_direct(args, **_kwargs):
        if args[:6] == [
            "ovs-vsctl",
            "--data=bare",
            "--no-heading",
            "--columns=name",
            "find",
            "Interface",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected fallback args: {args!r}")

    monkeypatch.setattr(libvirt_mod.subprocess, "run", _run_xml_direct)
    assert provider._get_vm_interface_port_sync("lab1", "r1", 0) == "vnet-direct"

    def _run_success(args, **_kwargs):
        if args[:6] == [
            "ovs-vsctl",
            "--data=bare",
            "--no-heading",
            "--columns=name",
            "find",
            "Interface",
        ]:
            return SimpleNamespace(returncode=0, stdout="vnet-ifaceid\n", stderr="")
        raise AssertionError(f"unexpected args: {args!r}")

    monkeypatch.setattr(libvirt_mod.subprocess, "run", _run_success)
    assert provider._get_vm_interface_port_sync("lab1", "r1", 0) == "vnet-ifaceid"

    def _run_mac_fallback(args, **_kwargs):
        if args[:6] == [
            "ovs-vsctl",
            "--data=bare",
            "--no-heading",
            "--columns=name",
            "find",
            "Interface",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ovs-vsctl", "--format=json", "list-ports", libvirt_mod.settings.ovs_bridge_name]:
            return SimpleNamespace(returncode=0, stdout="vnet1\nvnet2\n", stderr="")
        if args[:4] == ["ovs-vsctl", "get", "interface", "vnet1"]:
            return SimpleNamespace(returncode=0, stdout='"00:11:22:33:44:55"\n', stderr="")
        if args[:4] == ["ovs-vsctl", "get", "interface", "vnet2"]:
            return SimpleNamespace(returncode=0, stdout='"fe:54:00:aa:bb:cc"\n', stderr="")
        raise AssertionError(f"unexpected args: {args!r}")

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(RuntimeError("lookup failed")),
    )
    provider._ovs_port_exists = lambda _port_name: False
    monkeypatch.setattr(libvirt_mod.subprocess, "run", _run_mac_fallback)
    assert provider._get_vm_interface_port_sync("lab1", "r1", 0) == "vnet2"

    monkeypatch.setattr(
        libvirt_mod.subprocess,
        "run",
        lambda args, **_kwargs: (
            SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:6] == [
                "ovs-vsctl",
                "--data=bare",
                "--no-heading",
                "--columns=name",
                "find",
                "Interface",
            ]
            else SimpleNamespace(returncode=1, stdout="", stderr="")
            if args[:4] == ["ovs-vsctl", "--format=json", "list-ports", libvirt_mod.settings.ovs_bridge_name]
            else SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    assert provider._get_vm_interface_port_sync("lab1", "r1", 0) is None

    monkeypatch.setattr(
        libvirt_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ovs failed")),
    )
    assert provider._get_vm_interface_port_sync("lab1", "r1", 0) is None


@pytest.mark.asyncio
async def test_get_vm_interface_port_async_wrapper():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(return_value="vnet9")

    port = await provider.get_vm_interface_port("lab1", "r1", 2)

    assert port == "vnet9"
    provider._run_libvirt.assert_awaited_once()


def test_domain_kind_and_readiness_overrides_metadata_paths(monkeypatch):
    provider = _make_provider()

    domain = SimpleNamespace(
        XMLDesc=lambda: """
<domain>
  <metadata>
    <a:archetype xmlns:a='urn:archetype'>
      <a:kind>iosv</a:kind>
      <a:readiness_probe>log_pattern</a:readiness_probe>
      <a:readiness_pattern>READY</a:readiness_pattern>
      <a:readiness_timeout>240</a:readiness_timeout>
    </a:archetype>
  </metadata>
</domain>
"""
    )

    values = provider._get_domain_metadata_values(domain)
    assert values["kind"] == "iosv"
    assert values["readiness_probe"] == "log_pattern"
    assert values["readiness_pattern"] == "READY"
    assert values["readiness_timeout"] == "240"
    assert provider._get_domain_kind(domain) == "iosv"
    assert provider._get_domain_readiness_overrides(domain) == {
        "readiness_probe": "log_pattern",
        "readiness_pattern": "READY",
        "readiness_timeout": 240,
    }

    bad_timeout = SimpleNamespace(XMLDesc=lambda: "<domain><metadata><kind>iosv</kind><readiness_timeout>bad</readiness_timeout></metadata></domain>")
    assert provider._get_domain_readiness_overrides(bad_timeout) == {}

    monkeypatch.setattr(provider, "_get_domain_metadata_values", lambda _domain: (_ for _ in ()).throw(RuntimeError("metadata fail")))
    assert provider._get_domain_kind(SimpleNamespace()) is None


def test_get_runtime_profile_sync_parses_domain_xml(monkeypatch):
    provider = _make_provider()
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
            libvirtError=RuntimeError,
        ),
    )

    domain_xml = """
<domain type='kvm'>
  <memory unit='MiB'>2048</memory>
  <vcpu>4</vcpu>
  <os firmware='efi'>
    <type machine='pc-q35-8.2'>hvm</type>
    <nvram>/var/lib/libvirt/nvram/node_VARS.fd</nvram>
  </os>
  <devices>
    <disk device='disk'>
      <target bus='virtio' dev='vda'/>
      <source file='/var/lib/archetype/images/base.qcow2'/>
    </disk>
    <interface type='bridge'>
      <model type='e1000'/>
    </interface>
  </devices>
</domain>
"""
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: SimpleNamespace(
            state=lambda: (1, 0),
            XMLDesc=lambda: domain_xml,
        ),
    )
    monkeypatch.setattr(
        provider,
        "_get_domain_metadata_values",
        lambda _domain: {
            "kind": "iosv",
            "readiness_probe": "log_pattern",
            "readiness_pattern": "READY",
            "readiness_timeout": "300",
        },
    )

    profile = provider._get_runtime_profile_sync("lab1", "node1")

    assert profile["provider"] == "libvirt"
    assert profile["domain_name"] == provider._domain_name("lab1", "node1")
    assert profile["state"] == "running"
    runtime = profile["runtime"]
    assert runtime["memory"] == 2048
    assert runtime["cpu"] == 4
    assert runtime["machine_type"] == "pc-q35-8.2"
    assert runtime["libvirt_driver"] == "kvm"
    assert runtime["efi_boot"] is True
    assert runtime["efi_vars"] == "stateful"
    assert runtime["disk_driver"] == "virtio"
    assert runtime["nic_driver"] == "e1000"
    assert runtime["disk_source"] == "/var/lib/archetype/images/base.qcow2"
    assert runtime["kind"] == "iosv"
    assert runtime["readiness_timeout"] == 300


def test_runtime_profile_wrappers(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(
        provider,
        "_get_runtime_profile_sync",
        lambda lab_id, node_name: {"lab_id": lab_id, "node_name": node_name},
    )
    assert provider.get_runtime_profile("lab1", "n1") == {"lab_id": "lab1", "node_name": "n1"}


@pytest.mark.asyncio
async def test_runtime_profile_async_wrapper():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(return_value={"ok": True})
    result = await provider.get_runtime_profile_async("lab1", "n1")
    assert result == {"ok": True}
    provider._run_libvirt.assert_awaited_once()


def test_check_domain_running_sync_states(monkeypatch):
    provider = _make_provider()

    class _DummyErr(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(VIR_DOMAIN_RUNNING=1, libvirtError=_DummyErr),
    )

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: SimpleNamespace(state=lambda: (1, 0)),
    )
    assert provider._check_domain_running_sync("arch-lab-node1") is True

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: SimpleNamespace(state=lambda: (5, 0)),
    )
    assert provider._check_domain_running_sync("arch-lab-node1") is False

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(_DummyErr("missing")),
    )
    assert provider._check_domain_running_sync("arch-lab-node1") is None


@pytest.mark.asyncio
async def test_extract_config_wrappers_delegate(monkeypatch):
    provider = _make_provider()
    provider._run_libvirt = AsyncMock()
    provider._check_domain_running_sync = MagicMock(return_value=True)
    provider._run_ssh_command = AsyncMock(return_value="ok")

    called: dict[str, tuple] = {}

    async def _fake_extract_config(lab_id, node_name, kind, **kwargs):
        called["extract"] = (lab_id, node_name, kind, kwargs)
        return (node_name, "hostname R1")

    async def _fake_extract_ssh(domain_name, kind, node_name, **kwargs):
        called["ssh"] = (domain_name, kind, node_name, kwargs)
        return "hostname R1"

    monkeypatch.setattr(libvirt_mod, "_extract_config", _fake_extract_config)
    monkeypatch.setattr(libvirt_mod, "_extract_config_via_ssh", _fake_extract_ssh)

    res1 = await provider._extract_config("lab1", "r1", "iosv")
    res2 = await provider._extract_config_via_ssh("arch-lab1-r1", "iosv", "r1")

    assert res1 == ("r1", "hostname R1")
    assert res2 == "hostname R1"
    assert called["extract"][0:3] == ("lab1", "r1", "iosv")
    assert called["extract"][3]["domain_name"] == provider._domain_name("lab1", "r1")
    assert called["ssh"][0:3] == ("arch-lab1-r1", "iosv", "r1")


def test_list_lab_vm_kinds_sync_filters_and_handles_errors(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(VIR_CONNECT_LIST_DOMAINS_ACTIVE=1))
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: [
            SimpleNamespace(name=lambda: "arch-lab1-r1"),
            SimpleNamespace(name=lambda: "arch-lab1-r2"),
            SimpleNamespace(name=lambda: "arch-other-r3"),
        ],
    )
    kinds = {"arch-lab1-r1": "iosv", "arch-lab1-r2": None}
    monkeypatch.setattr(provider, "_get_domain_kind", lambda domain: kinds[domain.name()])
    monkeypatch.setattr(
        provider,
        "_get_domain_metadata_values",
        lambda domain: {
            "arch-lab1-r1": {"lab_id": "lab1", "node_name": "r1"},
            "arch-lab1-r2": {"lab_id": "lab1", "node_name": "r2"},
            "arch-other-r3": {"lab_id": "other", "node_name": "r3"},
        }[domain.name()],
    )

    assert provider._list_lab_vm_kinds_sync("lab1") == [("r1", "iosv")]

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("oops")),
    )
    assert provider._list_lab_vm_kinds_sync("lab1") == []


@pytest.mark.asyncio
async def test_deploy_validates_topology_and_skips_non_libvirt_nodes(tmp_path):
    provider = _make_provider()

    res_none = await provider.deploy("lab1", None, tmp_path / "ws1")
    assert res_none.success is False
    assert "No topology provided" in (res_none.error or "")

    empty_topo = SimpleNamespace(nodes=[])
    res_empty = await provider.deploy("lab1", empty_topo, tmp_path / "ws2")
    assert res_empty.success is False
    assert "No nodes found" in (res_empty.error or "")

    non_libvirt = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                name="n1",
                kind="linux",
                image="alpine:latest",
                display_name=None,
                memory=None,
                cpu=None,
                cpu_limit=None,
                machine_type=None,
                disk_driver=None,
                nic_driver=None,
                libvirt_driver=None,
                efi_boot=None,
                efi_vars=None,
                data_volume_gb=None,
                readiness_probe=None,
                readiness_pattern=None,
                readiness_timeout=None,
                interface_count=1,
            )
        ]
    )
    res_skip = await provider.deploy("lab1", non_libvirt, tmp_path / "ws3")
    assert res_skip.success is True
    assert "No libvirt-compatible nodes" in (res_skip.stdout or "")


@pytest.mark.asyncio
async def test_deploy_mixed_success_and_errors(monkeypatch, tmp_path):
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(return_value={})
    provider._disks_dir = lambda _workspace: tmp_path / "disks"

    cfg = SimpleNamespace(
        source="vendor",
        memory_mb=2048,
        cpu_count=2,
        machine_type="pc-q35-8.2",
        disk_driver="virtio",
        nic_driver="virtio",
        efi_boot=False,
        efi_vars=None,
        data_volume_gb=None,
        readiness_probe="none",
        readiness_pattern=None,
        readiness_timeout=120,
        serial_type="pty",
        nographic=True,
        serial_port_count=1,
        smbios_product=None,
        reserved_nics=0,
        cpu_sockets=1,
        needs_nested_vmx=False,
    )
    monkeypatch.setattr(libvirt_mod, "get_libvirt_config", lambda _kind: cfg)

    node_ok = SimpleNamespace(
        name="r1",
        kind="iosv",
        image="iosv.qcow2",
        display_name="R1",
        memory=None,
        cpu=None,
        cpu_limit=None,
        machine_type=None,
        disk_driver=None,
        nic_driver=None,
        libvirt_driver=None,
        efi_boot=None,
        efi_vars=None,
        data_volume_gb=None,
        readiness_probe=None,
        readiness_pattern=None,
        readiness_timeout=None,
        interface_count=2,
    )
    node_fail = SimpleNamespace(**{**node_ok.__dict__, "name": "r2", "display_name": "R2"})
    topo = SimpleNamespace(nodes=[node_ok, node_fail])

    provider._deploy_node = AsyncMock(
        side_effect=[
            libvirt_mod.NodeInfo(name="r1", status=libvirt_mod.NodeStatus.RUNNING, container_id="abc123"),
            RuntimeError("deploy failed"),
        ]
    )

    result = await provider.deploy("lab1", topo, tmp_path / "ws")
    assert result.success is True
    assert len(result.nodes) == 1
    assert "Errors: 1" in (result.stdout or "")
    assert "deploy failed" in (result.stderr or "")


@pytest.mark.asyncio
async def test_deploy_returns_failure_when_all_nodes_fail(monkeypatch, tmp_path):
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(return_value={})
    provider._disks_dir = lambda _workspace: tmp_path / "disks"

    cfg = SimpleNamespace(
        source="vendor",
        memory_mb=1024,
        cpu_count=1,
        machine_type="pc",
        disk_driver="virtio",
        nic_driver="virtio",
        efi_boot=False,
        efi_vars=None,
        data_volume_gb=None,
        readiness_probe="none",
        readiness_pattern=None,
        readiness_timeout=120,
        serial_type="pty",
        nographic=True,
        serial_port_count=1,
        smbios_product=None,
        reserved_nics=0,
        cpu_sockets=1,
        needs_nested_vmx=False,
    )
    monkeypatch.setattr(libvirt_mod, "get_libvirt_config", lambda _kind: cfg)

    node = SimpleNamespace(
        name="r1",
        kind="iosv",
        image="iosv.qcow2",
        display_name=None,
        memory=None,
        cpu=None,
        cpu_limit=None,
        machine_type=None,
        disk_driver=None,
        nic_driver=None,
        libvirt_driver=None,
        efi_boot=None,
        efi_vars=None,
        data_volume_gb=None,
        readiness_probe=None,
        readiness_pattern=None,
        readiness_timeout=None,
        interface_count=1,
    )
    provider._deploy_node = AsyncMock(side_effect=RuntimeError("boom"))

    result = await provider.deploy("lab1", SimpleNamespace(nodes=[node]), tmp_path / "ws")
    assert result.success is False
    assert "Failed to deploy any nodes" in (result.error or "")


def test_node_precheck_sync_running_and_stale_cleanup_paths(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))

    domain_running = SimpleNamespace(
        UUIDString=lambda: "abcdef1234567890",
    )
    provider._recover_stale_network = MagicMock()
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain_running)
    provider._get_domain_status = lambda _domain: libvirt_mod.NodeStatus.RUNNING

    running = provider._node_precheck_sync(
        "lab1",
        "node1",
        provider._domain_name("lab1", "node1"),
        tmp_path,
        tmp_path / "disks",
    )
    assert running == (True, "abcdef123456", libvirt_mod.NodeStatus.RUNNING)

    disks = tmp_path / "disks"
    disks.mkdir(parents=True, exist_ok=True)
    (disks / "node1.qcow2").write_text("x")
    (disks / "node1-data.qcow2").write_text("x")

    domain_stale = SimpleNamespace(UUIDString=lambda: "deadbeef")
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain_stale)
    provider._get_domain_status = lambda _domain: libvirt_mod.NodeStatus.STOPPED
    provider._undefine_domain = MagicMock()
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._teardown_n9kv_poap_network = MagicMock()
    provider._save_vlan_allocations = MagicMock()
    provider._vlan_allocations["lab1"] = {"node1": [100]}

    stale = provider._node_precheck_sync(
        "lab1",
        "node1",
        provider._domain_name("lab1", "node1"),
        tmp_path,
        disks,
    )
    assert stale == (False, None, None)
    assert not (disks / "node1.qcow2").exists()
    assert not (disks / "node1-data.qcow2").exists()
    assert "node1" not in provider._vlan_allocations["lab1"]
    provider._save_vlan_allocations.assert_called_once()


def test_node_precheck_sync_handles_recovery_and_lookup_errors(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))
    provider._recover_stale_network = lambda *_args: (_ for _ in ()).throw(RuntimeError("recover fail"))
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(_LibvirtError("missing")),
    )

    res = provider._node_precheck_sync(
        "lab1",
        "node1",
        provider._domain_name("lab1", "node1"),
        tmp_path,
        tmp_path / "disks",
    )
    assert res == (False, None, None)


def test_deploy_node_pre_sync_and_define_start_sync(monkeypatch):
    provider = _make_provider()

    monkeypatch.setattr(
        provider,
        "_node_precheck_sync",
        lambda *_args, **_kwargs: (True, "abc123", libvirt_mod.NodeStatus.RUNNING),
    )
    existing = provider._deploy_node_pre_sync("lab1", "r1", "arch-lab1-r1", Path("/tmp/disks"))
    assert existing is not None
    assert existing.name == "r1"

    monkeypatch.setattr(provider, "_node_precheck_sync", lambda *_args, **_kwargs: (False, None, None))
    assert provider._deploy_node_pre_sync("lab1", "r1", "arch-lab1-r1", Path("/tmp/disks")) is None

    domain = SimpleNamespace(create=MagicMock(), UUIDString=lambda: "uuid-1234567890")
    provider._conn = SimpleNamespace(isAlive=lambda: True, defineXML=lambda _xml: domain)
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._mark_post_boot_console_ownership_pending = MagicMock()
    assert provider._deploy_node_define_start_sync("arch-lab1-r1", "<domain/>", "iosv") == "uuid-1234567"
    provider._clear_vm_post_boot_commands_cache.assert_called_once()
    provider._mark_post_boot_console_ownership_pending.assert_called_once_with("arch-lab1-r1", "iosv")

    provider._conn = SimpleNamespace(isAlive=lambda: True, defineXML=lambda _xml: None)
    with pytest.raises(RuntimeError, match="Failed to define domain"):
        provider._deploy_node_define_start_sync("arch-lab1-r1", "<domain/>", "iosv")


@pytest.mark.asyncio
async def test_deploy_node_paths(monkeypatch, tmp_path):
    provider = _make_provider()
    disks_dir = tmp_path / "disks"
    disks_dir.mkdir(parents=True, exist_ok=True)

    existing = libvirt_mod.NodeInfo(name="r1", status=libvirt_mod.NodeStatus.RUNNING, container_id="abc123")
    provider._run_libvirt = AsyncMock(return_value=existing)
    provider._create_overlay_disk = AsyncMock()
    out_existing = await provider._deploy_node("lab1", "r1", {"image": "iosv.qcow2"}, disks_dir, kind="iosv")
    assert out_existing is existing
    provider._create_overlay_disk.assert_not_called()

    provider._run_libvirt = AsyncMock(return_value=None)
    monkeypatch.setattr(provider, "_get_base_image", lambda _cfg: None)
    with pytest.raises(ValueError, match="No base image found"):
        await provider._deploy_node("lab1", "r1", {"image": "iosv.qcow2"}, disks_dir, kind="iosv")

    monkeypatch.setattr(provider, "_get_base_image", lambda _cfg: "/images/base.qcow2")
    provider._create_overlay_disk = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="Failed to create overlay disk"):
        await provider._deploy_node("lab1", "r1", {"image": "iosv.qcow2"}, disks_dir, kind="iosv")

    provider._create_overlay_disk = AsyncMock(return_value=True)
    provider._create_data_volume = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="Failed to create data volume"):
        await provider._deploy_node(
            "lab1",
            "r1",
            {"image": "iosv.qcow2", "data_volume_gb": 10},
            disks_dir,
            kind="iosv",
        )

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    provider._run_libvirt = AsyncMock(side_effect=[None, (True, "default"), "uuid123"])
    provider._create_overlay_disk = AsyncMock(return_value=True)
    provider._create_data_volume = AsyncMock(return_value=True)
    provider._allocate_vlans = MagicMock(return_value=[100, 101, 102])
    provider._patch_vjunos_svm_compat = MagicMock(return_value=True)
    provider._generate_domain_xml = MagicMock(return_value="<domain/>")
    provider._set_vm_tap_mtu = AsyncMock()

    node_info = await provider._deploy_node(
        "lab1",
        "r2",
        {
            "image": "iosv.qcow2",
            "interface_count": 1,
            "reserved_nics": 2,
            "needs_nested_vmx": True,
            "data_volume_gb": 5,
        },
        disks_dir,
        kind="vjunos",
    )
    assert node_info.name == "r2"
    assert node_info.status == libvirt_mod.NodeStatus.RUNNING
    assert node_info.container_id == "uuid123"
    provider._patch_vjunos_svm_compat.assert_called_once()
    provider._set_vm_tap_mtu.assert_awaited_once_with("lab1", "r2")


def test_destroy_sync_cleans_domains_and_handles_errors(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(
            VIR_CONNECT_LIST_DOMAINS_ACTIVE=1,
            VIR_CONNECT_LIST_DOMAINS_INACTIVE=2,
            VIR_DOMAIN_RUNNING=1,
            libvirtError=_LibvirtError,
        ),
    )

    class _Domain:
        def __init__(self, name: str, state: int):
            self._name = name
            self._state = state
            self.destroy = MagicMock()

        def name(self):
            return self._name

        def state(self):
            return (self._state, 0)

    d_ok = _Domain("arch-lab1-r1", 1)
    d_err = _Domain("arch-lab1-r2", 5)
    d_other = _Domain("arch-other-r3", 1)

    def _list_domains(flag: int):
        if flag == 1:
            return [d_ok, d_other]
        return [d_err]

    provider._conn = SimpleNamespace(isAlive=lambda: True, listAllDomains=_list_domains)
    provider._get_domain_metadata_values = lambda domain: {
        d_ok: {"lab_id": "lab1", "node_name": "r1"},
        d_err: {"lab_id": "lab1", "node_name": "r2"},
        d_other: {"lab_id": "other", "node_name": "r3"},
    }.get(domain, {})
    provider._undefine_domain = MagicMock(
        side_effect=lambda domain, _name: (_ for _ in ()).throw(_LibvirtError("undefine fail"))
        if domain is d_err
        else None
    )
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._teardown_n9kv_poap_network = MagicMock()

    disks_dir = tmp_path / "disks"
    disks_dir.mkdir(parents=True, exist_ok=True)
    (disks_dir / "r1.qcow2").write_text("x")
    serial_dir = tmp_path / "serial-logs"
    serial_dir.mkdir(parents=True, exist_ok=True)
    (serial_dir / "arch-lab1-r1.log").write_text("x")
    provider._vlan_allocations["lab1"] = {"r1": [100]}
    provider._next_vlan["lab1"] = 101
    provider._remove_vlan_file = MagicMock()

    destroyed, errors, fatal = provider._destroy_sync("lab1", tmp_path)
    assert fatal is None
    assert destroyed == 1
    assert len(errors) == 1
    assert not (disks_dir / "r1.qcow2").exists()
    assert not serial_dir.exists()
    assert "lab1" not in provider._vlan_allocations
    assert "lab1" not in provider._next_vlan
    provider._remove_vlan_file.assert_called_once_with("lab1", tmp_path)


def test_destroy_sync_returns_fatal_on_outer_exception(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(
            VIR_CONNECT_LIST_DOMAINS_ACTIVE=1,
            VIR_CONNECT_LIST_DOMAINS_INACTIVE=2,
        ),
    )
    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("conn fail")),
    )

    destroyed, errors, fatal = provider._destroy_sync("lab1", Path("/tmp/ws"))
    assert destroyed == 0
    assert errors == []
    assert "conn fail" in (fatal or "")


@pytest.mark.asyncio
async def test_destroy_wrapper_paths(monkeypatch):
    provider = _make_provider()

    provider._run_libvirt = AsyncMock(return_value=(0, [], "fatal error"))
    fatal_res = await provider.destroy("lab1", Path("/tmp/ws"))
    assert fatal_res.success is False
    assert fatal_res.error == "fatal error"

    provider._run_libvirt = AsyncMock(return_value=(0, ["r1: fail"], None))
    err_res = await provider.destroy("lab1", Path("/tmp/ws"))
    assert err_res.success is False
    assert "Failed to destroy domains" in (err_res.error or "")

    backend = SimpleNamespace(ovs_manager=SimpleNamespace(_initialized=True, cleanup_lab=AsyncMock(return_value={"ok": 1})))
    monkeypatch.setattr("agent.network.backends.registry.get_network_backend", lambda: backend)
    provider._run_libvirt = AsyncMock(return_value=(2, [], None))
    ok_res = await provider.destroy("lab1", Path("/tmp/ws"))
    assert ok_res.success is True
    assert "Destroyed 2 VM domains" in (ok_res.stdout or "")
    backend.ovs_manager.cleanup_lab.assert_awaited_once_with("lab1")

    backend_fail = SimpleNamespace(ovs_manager=SimpleNamespace(_initialized=True, cleanup_lab=AsyncMock(side_effect=RuntimeError("ovs down"))))
    monkeypatch.setattr("agent.network.backends.registry.get_network_backend", lambda: backend_fail)
    provider._run_libvirt = AsyncMock(return_value=(1, [], None))
    ok_res2 = await provider.destroy("lab1", Path("/tmp/ws"))
    assert ok_res2.success is True


def test_status_sync_and_wrapper():
    provider = _make_provider()
    d1 = SimpleNamespace(name=lambda: "arch-lab1-r1")
    d2 = SimpleNamespace(name=lambda: "arch-other-r2")
    provider._conn = SimpleNamespace(isAlive=lambda: True, listAllDomains=lambda _flags=0: [d1, d2])
    provider._node_from_domain = lambda domain, _lab_id: (
        libvirt_mod.NodeInfo(name="r1", status=libvirt_mod.NodeStatus.RUNNING, container_id="abc")
        if domain is d1
        else None
    )

    s = provider._status_sync("lab1")
    assert s.lab_exists is True
    assert [n.name for n in s.nodes] == ["r1"]

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        listAllDomains=lambda _flags=0: (_ for _ in ()).throw(RuntimeError("oops")),
    )
    s2 = provider._status_sync("lab1")
    assert s2.lab_exists is False
    assert "oops" in (s2.error or "")


@pytest.mark.asyncio
async def test_status_wrapper_async():
    provider = _make_provider()
    expected = libvirt_mod.StatusResult(lab_exists=True, nodes=[])
    provider._run_libvirt = AsyncMock(return_value=expected)
    got = await provider.status("lab1", Path("/tmp/ws"))
    assert got is expected


def test_start_node_sync_branches(monkeypatch):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(VIR_DOMAIN_RUNNING=1, libvirtError=_LibvirtError),
    )

    domain_running = SimpleNamespace(state=lambda: (1, 0))
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain_running)
    assert provider._start_node_sync("arch-lab1-r1")[0] == "already_running"

    created = MagicMock()
    domain_stopped = SimpleNamespace(state=lambda: (5, 0), create=created)
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain_stopped)
    provider._get_domain_kind = lambda _domain: "iosv"
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._mark_post_boot_console_ownership_pending = MagicMock()
    status, kind, err = provider._start_node_sync("arch-lab1-r1")
    assert (status, kind, err) == ("started", "iosv", None)
    created.assert_called_once()

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(_LibvirtError("bad libvirt")),
    )
    assert provider._start_node_sync("arch-lab1-r1")[0] == "error"

    provider._conn = SimpleNamespace(
        isAlive=lambda: True,
        lookupByName=lambda _name: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert provider._start_node_sync("arch-lab1-r1")[0] == "error"


@pytest.mark.asyncio
async def test_start_node_wrapper_paths():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(side_effect=[("already_running", None, None), True])
    res1 = await provider.start_node("lab1", "r1", Path("/tmp/ws"))
    assert res1.success is True
    assert "already running" in (res1.stdout or "")

    provider._run_libvirt = AsyncMock(return_value=("error", None, "bad"))
    res2 = await provider.start_node("lab1", "r1", Path("/tmp/ws"))
    assert res2.success is False
    assert res2.error == "bad"

    provider._run_libvirt = AsyncMock(return_value=("started", "iosv", None))
    provider._set_vm_tap_mtu = AsyncMock()
    res3 = await provider.start_node("lab1", "r1", Path("/tmp/ws"))
    assert res3.success is True
    provider._set_vm_tap_mtu.assert_awaited_once_with("lab1", "r1")


@pytest.mark.asyncio
async def test_start_node_already_running_but_not_metadata_visible():
    provider = _make_provider()
    provider._run_libvirt = AsyncMock(side_effect=[("already_running", None, None), False])

    result = await provider.start_node("lab1", "r1", Path("/tmp/ws"))

    assert result.success is False
    assert "metadata-backed status" in (result.error or "")


def test_remove_vm_sync_cleans_resources(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(
        libvirt_mod,
        "libvirt",
        SimpleNamespace(
            VIR_DOMAIN_SHUTOFF=5,
            VIR_DOMAIN_CRASHED=6,
            libvirtError=_LibvirtError,
        ),
    )

    domain = SimpleNamespace(state=lambda: (1, 0), destroy=MagicMock(side_effect=_LibvirtError("already stopped")))
    provider._conn = SimpleNamespace(isAlive=lambda: True, lookupByName=lambda _name: domain)
    provider._undefine_domain = MagicMock()
    provider._clear_vm_post_boot_commands_cache = MagicMock()
    provider._teardown_n9kv_poap_network = MagicMock()
    provider._save_vlan_allocations = MagicMock()

    disks = tmp_path / "disks"
    disks.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-data"):
        (disks / f"r1{suffix}.qcow2").write_text("x")
    (disks / "r1-config.iso").write_text("x")
    (disks / "r1-config.img").write_text("x")
    serial = tmp_path / "serial-logs"
    serial.mkdir(parents=True, exist_ok=True)
    (serial / f"{provider._domain_name('lab1', 'r1')}.log").write_text("x")
    provider._vlan_allocations["lab1"] = {"r1": [100], "r2": [101]}

    provider._remove_vm_sync("lab1", "r1", tmp_path)
    assert not (disks / "r1.qcow2").exists()
    assert not (disks / "r1-data.qcow2").exists()
    assert not (disks / "r1-config.iso").exists()
    assert not (disks / "r1-config.img").exists()
    assert not (serial / f"{provider._domain_name('lab1', 'r1')}.log").exists()
    assert "r1" not in provider._vlan_allocations["lab1"]
    assert "r2" in provider._vlan_allocations["lab1"]


@pytest.mark.asyncio
async def test_stop_node_wrapper_paths(monkeypatch):
    provider = _make_provider()
    provider._remove_vm = AsyncMock(return_value=None)
    ok = await provider.stop_node("lab1", "r1", Path("/tmp/ws"))
    assert ok.success is True

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))

    provider._remove_vm = AsyncMock(side_effect=_LibvirtError("domain not found"))
    nf = await provider.stop_node("lab1", "r1", Path("/tmp/ws"))
    assert nf.success is True
    assert "already removed" in (nf.stdout or "")

    provider._remove_vm = AsyncMock(side_effect=_LibvirtError("domain is not running"))
    nr = await provider.stop_node("lab1", "r1", Path("/tmp/ws"))
    assert nr.success is True
    assert "already stopped" in (nr.stdout or "")

    provider._remove_vm = AsyncMock(side_effect=_LibvirtError("permission denied"))
    lib_err = await provider.stop_node("lab1", "r1", Path("/tmp/ws"))
    assert lib_err.success is False
    assert "Libvirt error" in (lib_err.error or "")

    provider._remove_vm = AsyncMock(side_effect=RuntimeError("boom"))
    gen_err = await provider.stop_node("lab1", "r1", Path("/tmp/ws"))
    assert gen_err.success is False
    assert gen_err.error == "boom"


def test_create_node_pre_and_define_sync(monkeypatch, tmp_path):
    provider = _make_provider()
    monkeypatch.setattr(
        provider,
        "_running_domain_identity_visible",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        provider,
        "_node_precheck_sync",
        lambda *_args, **_kwargs: (True, "abc", libvirt_mod.NodeStatus.RUNNING),
    )
    pre = provider._create_node_pre_sync("lab1", "r1", "arch-lab1-r1", tmp_path)
    assert pre is not None and pre.success is True

    monkeypatch.setattr(provider, "_node_precheck_sync", lambda *_args, **_kwargs: (False, None, None))
    assert provider._create_node_pre_sync("lab1", "r1", "arch-lab1-r1", tmp_path) is None

    provider._conn = SimpleNamespace(isAlive=lambda: True, defineXML=lambda _xml: object())
    assert provider._define_domain_sync("arch-lab1-r1", "<domain/>") is True
    provider._conn = SimpleNamespace(isAlive=lambda: True, defineXML=lambda _xml: None)
    assert provider._define_domain_sync("arch-lab1-r1", "<domain/>") is False


@pytest.mark.asyncio
async def test_create_node_early_and_error_paths(monkeypatch, tmp_path):
    provider = _make_provider()

    cfg = SimpleNamespace(
        memory_mb=1024,
        cpu_count=1,
        machine_type="pc",
        disk_driver="virtio",
        nic_driver="virtio",
        readiness_probe="none",
        readiness_pattern=None,
        readiness_timeout=120,
        efi_boot=False,
        efi_vars=None,
        serial_type="pty",
        nographic=True,
        serial_port_count=1,
        smbios_product=None,
        reserved_nics=0,
        cpu_sockets=1,
        needs_nested_vmx=False,
        data_volume_gb=None,
        config_inject_method="bootflash",
        config_inject_partition=1,
        config_inject_fs_type="vfat",
        config_inject_path="/startup-config",
        config_inject_iso_volume_label="config",
        config_inject_iso_filename="startup-config",
    )
    monkeypatch.setattr(libvirt_mod, "get_libvirt_config", lambda _kind: cfg)
    monkeypatch.setattr(libvirt_mod, "get_vendor_config", lambda _kind: SimpleNamespace(default_startup_config=None))

    early = libvirt_mod.NodeActionResult(success=True, node_name="r1", new_status=libvirt_mod.NodeStatus.RUNNING)
    provider._run_libvirt = AsyncMock(return_value=early)
    res_early = await provider.create_node("lab1", "r1", "iosv", tmp_path)
    assert res_early is early

    provider._run_libvirt = AsyncMock(return_value=None)
    monkeypatch.setattr(provider, "_get_base_image", lambda _cfg: None)
    res_no_img = await provider.create_node("lab1", "r1", "iosv", tmp_path, image="missing.qcow2")
    assert res_no_img.success is False
    assert "No base image found" in (res_no_img.error or "")

    monkeypatch.setattr(provider, "_get_base_image", lambda _cfg: "/images/base.qcow2")
    monkeypatch.setattr(provider, "_verify_backing_image", lambda *_args: (_ for _ in ()).throw(RuntimeError("hash mismatch")))
    res_hash = await provider.create_node("lab1", "r1", "iosv", tmp_path, image="iosv.qcow2")
    assert res_hash.success is False
    assert "hash mismatch" in (res_hash.error or "")

    monkeypatch.setattr(provider, "_verify_backing_image", lambda *_args: None)
    provider._create_overlay_disk = AsyncMock(return_value=False)
    res_overlay = await provider.create_node("lab1", "r1", "iosv", tmp_path, image="iosv.qcow2")
    assert res_overlay.success is False
    assert "Failed to create overlay disk" in (res_overlay.error or "")

    provider._create_overlay_disk = AsyncMock(return_value=True)
    cfg.data_volume_gb = 5
    provider._create_data_volume = AsyncMock(return_value=False)
    res_data = await provider.create_node("lab1", "r1", "iosv", tmp_path, image="iosv.qcow2")
    assert res_data.success is False
    assert "Failed to create data volume" in (res_data.error or "")
    cfg.data_volume_gb = None


@pytest.mark.asyncio
async def test_create_node_define_and_success_paths(monkeypatch, tmp_path):
    provider = _make_provider()

    cfg = SimpleNamespace(
        memory_mb=1024,
        cpu_count=1,
        machine_type="pc",
        disk_driver="virtio",
        nic_driver="virtio",
        readiness_probe="none",
        readiness_pattern=None,
        readiness_timeout=120,
        efi_boot=False,
        efi_vars=None,
        serial_type="pty",
        nographic=True,
        serial_port_count=1,
        smbios_product=None,
        reserved_nics=0,
        cpu_sockets=1,
        needs_nested_vmx=False,
        data_volume_gb=None,
        config_inject_method="bootflash",
        config_inject_partition=1,
        config_inject_fs_type="vfat",
        config_inject_path="/startup-config",
        config_inject_iso_volume_label="config",
        config_inject_iso_filename="startup-config",
    )
    monkeypatch.setattr(libvirt_mod, "get_libvirt_config", lambda _kind: cfg)
    monkeypatch.setattr(libvirt_mod, "get_vendor_config", lambda _kind: SimpleNamespace(default_startup_config=None))
    monkeypatch.setattr(provider, "_get_base_image", lambda _cfg: "/images/base.qcow2")
    monkeypatch.setattr(provider, "_verify_backing_image", lambda *_args: None)
    provider._create_overlay_disk = AsyncMock(return_value=True)
    provider._allocate_vlans = MagicMock(return_value=[100])
    provider._generate_domain_xml = MagicMock(return_value="<domain/>")
    provider._create_data_volume = AsyncMock(return_value=True)

    provider._run_libvirt = AsyncMock(side_effect=[None, (True, "default"), False])
    fail_define = await provider.create_node("lab1", "r1", "iosv", tmp_path, image="iosv.qcow2")
    assert fail_define.success is False
    assert "Failed to define domain" in (fail_define.error or "")

    monkeypatch.setattr(libvirt_mod.settings, "n9kv_boot_modifications_enabled", False, raising=False)
    provider._run_libvirt = AsyncMock(side_effect=[None, (True, "default"), True])
    success = await provider.create_node(
        "lab1",
        "n9k1",
        "cisco_n9kv",
        tmp_path,
        image="n9kv.qcow2",
        startup_config="hostname n9k1",
    )
    assert success.success is True
    assert "Config injection: skipped=n9kv_boot_modifications_disabled" in (success.stdout or "")


@pytest.mark.asyncio
async def test_create_node_exception_wrappers(monkeypatch, tmp_path):
    provider = _make_provider()

    class _LibvirtError(Exception):
        pass

    monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(libvirtError=_LibvirtError))
    provider._run_libvirt = AsyncMock(side_effect=_LibvirtError("libvirt down"))
    out1 = await provider.create_node("lab1", "r1", "iosv", tmp_path)
    assert out1.success is False
    assert "Libvirt error" in (out1.error or "")

    provider._run_libvirt = AsyncMock(side_effect=RuntimeError("boom"))
    out2 = await provider.create_node("lab1", "r1", "iosv", tmp_path)
    assert out2.success is False
    assert out2.error == "boom"


# ---------------------------------------------------------------------------
# _resolve_node_name_for_action_sync
# ---------------------------------------------------------------------------


class TestResolveNodeNameForActionSync:
    """Unit tests for _resolve_node_name_for_action_sync."""

    def _make_domain(self, name, metadata_values):
        """Build a mock libvirt domain with metadata."""
        domain = MagicMock()
        domain.name.return_value = name
        # Build minimal XML with metadata
        meta_elems = ""
        for k, v in metadata_values.items():
            meta_elems += f"<arch:{k}>{v}</arch:{k}>"
        xml = (
            f'<domain><metadata>'
            f'<arch:instance xmlns:arch="http://archetype.dev/ns/1">'
            f'{meta_elems}'
            f'</arch:instance></metadata></domain>'
        )
        domain.XMLDesc.return_value = xml
        return domain

    def test_uuid_lookup_returns_node_name(self):
        """UUID-based lookup resolves via metadata."""
        provider = _make_provider()
        domain = self._make_domain(
            "arch-lab1-router1",
            {"lab_id": "lab1", "node_name": "router1"},
        )
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        conn.lookupByName.side_effect = Exception("not found")
        provider._conn = conn

        result = provider._resolve_node_name_for_action_sync("lab1", "some-uuid-string")
        assert result == "router1"

    def test_hyphenated_node_name(self):
        """Hyphenated identifiers resolve via direct name lookup."""
        provider = _make_provider()
        domain = self._make_domain(
            "arch-lab1-my-router",
            {"lab_id": "lab1", "node_name": "my-router"},
        )
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = Exception("not uuid")
        conn.lookupByName.return_value = domain
        provider._conn = conn

        result = provider._resolve_node_name_for_action_sync("lab1", "arch-lab1-my-router")
        assert result == "my-router"

    def test_metadata_lab_id_mismatch_skips_domain(self):
        """Domain with wrong lab_id is not used."""
        provider = _make_provider()
        domain = self._make_domain(
            "arch-lab2-r1",
            {"lab_id": "lab2", "node_name": "r1"},
        )
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = Exception("no")
        conn.lookupByName.return_value = domain
        provider._conn = conn

        # "arch-lab2-r1" contains hyphen, so fallback returns None
        result = provider._resolve_node_name_for_action_sync("lab1", "arch-lab2-r1")
        assert result is None

    def test_all_lookups_fail_simple_name(self):
        """When all lookups fail, simple name (no hyphen) returns as-is."""
        provider = _make_provider()
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = Exception("no")
        conn.lookupByName.side_effect = Exception("no")
        provider._conn = conn

        result = provider._resolve_node_name_for_action_sync("lab1", "router1")
        assert result == "router1"

    def test_all_lookups_fail_hyphenated_returns_none(self):
        """When all lookups fail, hyphenated identifier returns None."""
        provider = _make_provider()
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = Exception("no")
        conn.lookupByName.side_effect = Exception("no")
        provider._conn = conn

        result = provider._resolve_node_name_for_action_sync("lab1", "some-uuid-ish")
        assert result is None

    def test_empty_identifier_returns_none(self):
        """Empty string returns None immediately."""
        provider = _make_provider()
        result = provider._resolve_node_name_for_action_sync("lab1", "")
        assert result is None

    def test_generated_domain_name_lookup(self):
        """Simple identifier triggers generated domain name lookup."""
        provider = _make_provider()
        domain = self._make_domain(
            "arch-lab1-r1",
            {"lab_id": "lab1", "node_name": "r1"},
        )
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = Exception("no")
        # Direct lookup fails, generated name lookup succeeds
        conn.lookupByName.side_effect = [Exception("no"), domain]
        provider._conn = conn

        result = provider._resolve_node_name_for_action_sync("lab1", "r1")
        assert result == "r1"


# ---------------------------------------------------------------------------
# generate_ovs_interface_id determinism
# ---------------------------------------------------------------------------


class TestGenerateOvsInterfaceId:
    """Property tests for generate_ovs_interface_id."""

    def test_same_inputs_same_uuid(self):
        """Same inputs always produce the same UUID5."""
        from agent.providers.libvirt_xml import generate_ovs_interface_id

        id1 = generate_ovs_interface_id("arch-lab1-r1", "data", 0)
        id2 = generate_ovs_interface_id("arch-lab1-r1", "data", 0)
        assert id1 == id2

    def test_different_index_different_uuid(self):
        """Different interface_index produces different UUID."""
        from agent.providers.libvirt_xml import generate_ovs_interface_id

        id1 = generate_ovs_interface_id("arch-lab1-r1", "data", 0)
        id2 = generate_ovs_interface_id("arch-lab1-r1", "data", 1)
        assert id1 != id2

    def test_different_domain_different_uuid(self):
        """Different domain_name produces different UUID."""
        from agent.providers.libvirt_xml import generate_ovs_interface_id

        id1 = generate_ovs_interface_id("arch-lab1-r1", "data", 0)
        id2 = generate_ovs_interface_id("arch-lab1-r2", "data", 0)
        assert id1 != id2

    def test_different_role_different_uuid(self):
        """Different interface_role produces different UUID."""
        from agent.providers.libvirt_xml import generate_ovs_interface_id

        id1 = generate_ovs_interface_id("arch-lab1-r1", "data", 0)
        id2 = generate_ovs_interface_id("arch-lab1-r1", "mgmt", 0)
        assert id1 != id2

    def test_valid_uuid_format(self):
        """Output is a valid UUID string."""
        import uuid
        from agent.providers.libvirt_xml import generate_ovs_interface_id

        result = generate_ovs_interface_id("domain", "data", 5)
        parsed = uuid.UUID(result)
        assert parsed.version == 5


# ---------------------------------------------------------------------------
# _cleanup_lab_orphan_domains_sync (metadata-aware, after Gap 1 fix)
# ---------------------------------------------------------------------------


class TestCleanupLabOrphanDomains:
    """Verify metadata-based orphan cleanup."""

    @pytest.fixture(autouse=True)
    def _mock_libvirt(self, monkeypatch):
        monkeypatch.setattr(
            libvirt_mod,
            "libvirt",
            SimpleNamespace(VIR_DOMAIN_RUNNING=1, libvirtError=Exception),
        )

    def _make_domain(self, name, metadata_values):
        domain = MagicMock()
        domain.name.return_value = name
        meta_elems = ""
        for k, v in metadata_values.items():
            meta_elems += f"<arch:{k}>{v}</arch:{k}>"
        xml = (
            f'<domain><metadata>'
            f'<arch:instance xmlns:arch="http://archetype.dev/ns/1">'
            f'{meta_elems}'
            f'</arch:instance></metadata></domain>'
        )
        domain.XMLDesc.return_value = xml
        domain.state.return_value = (1, 0)  # VIR_DOMAIN_RUNNING
        return domain

    def test_orphan_with_metadata_removed(self, tmp_path):
        """Domain with matching lab_id and node not in keep set is removed."""
        provider = _make_provider()
        orphan = self._make_domain(
            "arch-lab1-old-node",
            {"lab_id": "lab1", "node_name": "old-node"},
        )
        conn = MagicMock()
        conn.listAllDomains.return_value = [orphan]
        provider._conn = conn
        provider._undefine_domain = MagicMock()
        provider._clear_vm_post_boot_commands_cache = MagicMock()
        provider._teardown_n9kv_poap_network = MagicMock()

        result = provider._cleanup_lab_orphan_domains_sync(
            "lab1", {"r1", "r2"}, tmp_path,
        )

        assert "arch-lab1-old-node" in result["domains"]
        orphan.destroy.assert_called_once()
        provider._undefine_domain.assert_called_once()

    def test_domain_without_metadata_skipped(self, tmp_path):
        """Domain with no metadata is skipped (metric incremented)."""
        provider = _make_provider()
        no_meta = MagicMock()
        no_meta.name.return_value = "arch-lab1-mystery"
        no_meta.XMLDesc.return_value = "<domain></domain>"
        no_meta.state.return_value = (1, 0)

        conn = MagicMock()
        conn.listAllDomains.return_value = [no_meta]
        provider._conn = conn

        result = provider._cleanup_lab_orphan_domains_sync(
            "lab1", {"r1"}, tmp_path,
        )

        # No domains removed (skipped due to missing metadata)
        assert result["domains"] == []
        no_meta.destroy.assert_not_called()

    def test_domain_wrong_lab_id_excluded(self, tmp_path):
        """Domain belonging to different lab is excluded."""
        provider = _make_provider()
        other_lab = self._make_domain(
            "arch-lab2-r1",
            {"lab_id": "lab2", "node_name": "r1"},
        )
        conn = MagicMock()
        conn.listAllDomains.return_value = [other_lab]
        provider._conn = conn

        result = provider._cleanup_lab_orphan_domains_sync(
            "lab1", set(), tmp_path,
        )

        assert result["domains"] == []
        other_lab.destroy.assert_not_called()

    def test_domain_in_keep_set_preserved(self, tmp_path):
        """Domain whose node_name is in keep set is not removed."""
        provider = _make_provider()
        keeper = self._make_domain(
            "arch-lab1-r1",
            {"lab_id": "lab1", "node_name": "r1"},
        )
        conn = MagicMock()
        conn.listAllDomains.return_value = [keeper]
        provider._conn = conn

        result = provider._cleanup_lab_orphan_domains_sync(
            "lab1", {"r1"}, tmp_path,
        )

        assert result["domains"] == []
        keeper.destroy.assert_not_called()

    def test_disk_cleanup_uses_metadata_node_name(self, tmp_path):
        """Disk cleanup uses node_name from metadata, not name parsing."""
        provider = _make_provider()
        orphan = self._make_domain(
            "arch-lab1-mynode",
            {"lab_id": "lab1", "node_name": "mynode"},
        )
        conn = MagicMock()
        conn.listAllDomains.return_value = [orphan]
        provider._conn = conn
        provider._undefine_domain = MagicMock()
        provider._clear_vm_post_boot_commands_cache = MagicMock()
        provider._teardown_n9kv_poap_network = MagicMock()

        # Create workspace with disk files
        disks_dir = tmp_path / "lab1" / "disks"
        disks_dir.mkdir(parents=True)
        (disks_dir / "mynode.qcow2").touch()
        (disks_dir / "other.qcow2").touch()

        result = provider._cleanup_lab_orphan_domains_sync(
            "lab1", set(), tmp_path,
        )

        assert len(result["disks"]) == 1
        assert "mynode.qcow2" in result["disks"][0]
        # "other.qcow2" should NOT be removed
        assert (disks_dir / "other.qcow2").exists()
