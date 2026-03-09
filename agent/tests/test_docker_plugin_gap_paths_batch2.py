from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.network import docker_plugin as plugin_mod
from agent.network.docker_plugin import DockerOVSPlugin, EndpointState, LabBridge, NetworkState


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path), raising=False)
    monkeypatch.setattr(plugin_mod.settings, "ovs_bridge_name", "arch-ovs", raising=False)
    monkeypatch.setattr(plugin_mod.settings, "local_mtu", 1450, raising=False)
    monkeypatch.setattr(plugin_mod.settings, "lab_ttl_enabled", True, raising=False)
    monkeypatch.setattr(plugin_mod.settings, "lab_ttl_seconds", 60, raising=False)
    monkeypatch.setattr(plugin_mod.settings, "lab_ttl_check_interval", 1, raising=False)
    monkeypatch.setattr(plugin_mod.settings, "endpoint_binding_audit_enabled", True, raising=False)
    monkeypatch.setattr(plugin_mod.settings, "endpoint_binding_audit_interval_seconds", 1, raising=False)
    monkeypatch.setattr(plugin_mod.settings, "plugin_vxlan_dst_port", 4789, raising=False)
    return DockerOVSPlugin()


def _ep(eid: str, net_id: str, iface: str, host_veth: str, vlan: int, container: str | None = None) -> EndpointState:
    return EndpointState(
        endpoint_id=eid,
        network_id=net_id,
        interface_name=iface,
        host_veth=host_veth,
        cont_veth=f"c-{eid}",
        vlan_tag=vlan,
        container_name=container,
    )


@pytest.mark.asyncio
async def test_ovs_vsctl_exception_and_validate_endpoint_paths(plugin, monkeypatch):
    plugin._run_cmd = AsyncMock(side_effect=RuntimeError("cmd failed"))

    metric = SimpleNamespace(
        labels=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("metric failed"))
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "agent.metrics",
        SimpleNamespace(ovs_operation_duration=metric),
    )

    with pytest.raises(RuntimeError):
        await plugin._ovs_vsctl("list-ports", "arch-ovs")

    ep_no_veth = _ep("e1", "n1", "eth1", "", 200)
    assert await plugin._validate_endpoint_exists(ep_no_veth) is False

    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    ep_ok = _ep("e2", "n1", "eth1", "vh2", 201)
    assert await plugin._validate_endpoint_exists(ep_ok) is True


@pytest.mark.asyncio
async def test_health_check_and_ovs_health_paths(plugin, monkeypatch):
    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.networks["n1"] = NetworkState("n1", "lab1", "eth1", "arch-ovs")
    plugin.endpoints["e1"] = _ep("e1", "n1", "eth1", "vh1", 200, "c1")
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(plugin_mod.os.path, "exists", lambda path: True)

    status = await plugin.health_check()
    assert status["healthy"] is True
    assert status["checks"]["bridges_count"] == 1
    assert status["checks"]["networks_count"] == 1
    assert status["checks"]["endpoints_count"] == 1

    plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "bad"))
    assert await plugin._check_ovs_health() is False


class _CancelledAwaitTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def __await__(self):
        async def _inner():
            raise asyncio.CancelledError

        return _inner().__await__()


@pytest.mark.asyncio
async def test_start_stop_background_tasks_and_loops(plugin, monkeypatch):
    created: list[_CancelledAwaitTask] = []

    def _fake_create_task(coro):
        try:
            coro.close()
        except Exception:
            pass
        task = _CancelledAwaitTask()
        created.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)

    await plugin._start_ttl_cleanup()
    await plugin._start_endpoint_binding_audit()
    assert plugin._cleanup_task is created[0]
    assert plugin._binding_audit_task is created[1]

    await plugin._stop_ttl_cleanup()
    await plugin._stop_endpoint_binding_audit()
    assert plugin._cleanup_task is None
    assert plugin._binding_audit_task is None

    sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(asyncio, "sleep", sleeps)
    plugin._cleanup_expired_labs = AsyncMock(side_effect=RuntimeError("ttl error"))
    await plugin._ttl_cleanup_loop()

    plugin._stale_gc_counter = 9
    sleeps = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
    monkeypatch.setattr(asyncio, "sleep", sleeps)
    plugin._audit_endpoint_bindings = AsyncMock(
        side_effect=[
            {"checked": 1, "drifted": 1, "repaired": 1, "failed": 1},
            {"checked": 1, "drifted": 0, "repaired": 0, "failed": 0},
        ]
    )
    plugin.cleanup_stale_state = AsyncMock(side_effect=RuntimeError("gc failed"))
    await plugin._endpoint_binding_audit_loop()


@pytest.mark.asyncio
async def test_audit_endpoint_bindings_and_expired_labs(plugin):
    plugin.networks["n-ok"] = NetworkState("n-ok", "lab1", "eth1", "arch-ovs")
    plugin.networks["n-drift"] = NetworkState("n-drift", "lab1", "eth2", "arch-ovs")
    plugin.endpoints["e-skip"] = _ep("e-skip", "n-ok", "eth0", "vh-skip", 200, None)
    plugin.endpoints["e-failed"] = _ep("e-failed", "n-ok", "eth1", "vh-old", 201, "c1")
    plugin.endpoints["e-drift"] = _ep("e-drift", "n-drift", "eth2", "vh-old2", 202, "c2")

    plugin._discover_endpoint = AsyncMock(
        side_effect=[
            None,
            _ep("e-drift", "n-drift", "eth2", "vh-new", 202, "c2"),
        ]
    )
    stats = await plugin._audit_endpoint_bindings()
    assert stats == {"checked": 2, "drifted": 1, "repaired": 1, "failed": 1}

    now = datetime.now(timezone.utc)
    plugin.lab_bridges["lab-keep"] = LabBridge(
        lab_id="lab-keep",
        bridge_name="arch-ovs",
        last_activity=now - timedelta(seconds=120),
    )
    plugin.lab_bridges["lab-clean"] = LabBridge(
        lab_id="lab-clean",
        bridge_name="arch-ovs",
        last_activity=now - timedelta(seconds=180),
    )
    plugin._lab_has_any_containers = AsyncMock(side_effect=[True, False])
    plugin._full_lab_cleanup = AsyncMock()
    await plugin._cleanup_expired_labs()
    plugin._full_lab_cleanup.assert_awaited_once_with("lab-clean")


@pytest.mark.asyncio
async def test_lab_has_any_containers_fail_safe(plugin, monkeypatch):
    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("docker.from_env", lambda timeout=30: (_ for _ in ()).throw(RuntimeError("docker down")))
    assert await plugin._lab_has_any_containers("lab1") is True


@pytest.mark.asyncio
async def test_full_lab_cleanup_paths(plugin):
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    plugin._delete_port = AsyncMock()
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._release_vlan = lambda _vlan: None

    lb = LabBridge(
        lab_id="lab1",
        bridge_name="arch-ovs",
        vxlan_tunnels={5000: "vx5000"},
        external_ports={"ens5": 300},
    )
    plugin.lab_bridges["lab1"] = lb
    plugin.networks["n1"] = NetworkState("n1", "lab1", "eth1", "arch-ovs")
    plugin.networks["n2"] = NetworkState("n2", "lab2", "eth1", "arch-ovs")
    plugin.endpoints["e1"] = _ep("e1", "n1", "eth1", "vh1", 200, "c1")
    plugin.endpoints["e2"] = _ep("e2", "n2", "eth1", "vh2", 201, "c2")

    await plugin._full_lab_cleanup("lab1")
    assert "lab1" not in plugin.lab_bridges
    assert "n1" not in plugin.networks and "n2" in plugin.networks
    assert "e1" not in plugin.endpoints and "e2" in plugin.endpoints
    plugin._mark_dirty_and_save.assert_awaited_once()

    await plugin._full_lab_cleanup("missing-lab")


@pytest.mark.asyncio
async def test_repair_endpoints_wrapper_uses_lock(plugin):
    plugin._repair_endpoints_locked = AsyncMock(return_value=[{"status": "ok"}])
    out = await plugin.repair_endpoints("lab1", "c1")
    assert out == [{"status": "ok"}]
    plugin._repair_endpoints_locked.assert_awaited_once_with("lab1", "c1")


@pytest.mark.asyncio
async def test_repair_endpoints_locked_no_stale_and_pid_missing(plugin, monkeypatch):
    plugin.endpoints["e1"] = _ep("e1", "n1", "eth1", "vh1", 200, "c1")
    plugin._cleanup_stale_ovs_ports = AsyncMock()
    plugin._validate_endpoint_exists = AsyncMock(return_value=True)
    plugin._get_container_pid = AsyncMock(return_value=1234)
    plugin._find_interface_in_container = AsyncMock(return_value="eth1")

    async def _to_thread_raises(func, *args, **kwargs):
        raise RuntimeError("inspect failed")

    monkeypatch.setattr(asyncio, "to_thread", _to_thread_raises)
    out_ok = await plugin._repair_endpoints_locked("lab1", "c1")
    assert out_ok[0]["status"] == "ok"

    plugin.endpoints = {"e2": _ep("e2", "n1", "eth2", "vh2", 201, "c1")}
    plugin._validate_endpoint_exists = AsyncMock(return_value=False)
    plugin._get_container_pid = AsyncMock(return_value=0)
    out_pid = await plugin._repair_endpoints_locked("lab1", "c1")
    assert out_pid[0]["status"] == "error"
    assert "no PID" in out_pid[0]["message"]


@pytest.mark.asyncio
async def test_repair_endpoints_locked_repairs_binding_drift(plugin):
    ep = _ep("e1", "n1", "eth1", "vh-old", 200, "c1")
    plugin.endpoints["e1"] = ep
    plugin.networks["n1"] = NetworkState("n1", "lab1", "eth1", "arch-ovs")
    plugin._cleanup_stale_ovs_ports = AsyncMock()
    plugin._validate_endpoint_exists = AsyncMock(return_value=True)
    plugin._get_container_pid = AsyncMock(return_value=1234)
    plugin._find_interface_in_container = AsyncMock(return_value="eth9")
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    plugin._generate_veth_names = lambda endpoint_id: ("vh-new", "vc-new")
    plugin._create_veth_pair = AsyncMock(return_value=True)
    plugin._attach_to_ovs = AsyncMock(return_value=True)
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

    out = await plugin._repair_endpoints_locked("lab1", "c1")

    assert out == [{
        "interface": "eth1",
        "status": "repaired",
        "host_veth": "vh-new",
        "vlan_tag": 200,
    }]
    assert ep.host_veth == "vh-new"
    plugin._create_veth_pair.assert_awaited_once_with("vh-new", "vc-new")
    plugin._attach_to_ovs.assert_awaited_once()


@pytest.mark.asyncio
async def test_repair_endpoints_locked_repair_branches(plugin):
    eps = {
        "ea": _ep("ea", "na", "eth1", "vh-a", 210, "c1"),   # create_veth_pair fail
        "eb": _ep("eb", "nb", "eth2", "vh-b", 211, "c1"),   # attach_to_ovs fail
        "ec": _ep("ec", "nc", "eth3", "vh-c", 212, "c1"),   # move netns fail
        "ed": _ep("ed", "nd", "eth4", "vh-d", 213, "c1"),   # rename fail
        "ee": _ep("ee", "ne", "eth5", "vh-e", 214, "c1"),   # success
        "ef": _ep("ef", "nf", "eth6", "vh-f", 215, "c1"),   # exception
    }
    plugin.endpoints = eps
    plugin._cleanup_stale_ovs_ports = AsyncMock()
    plugin._validate_endpoint_exists = AsyncMock(return_value=False)
    plugin._get_container_pid = AsyncMock(return_value=1234)
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

    def _gen_names(endpoint_id: str):
        if endpoint_id == "ef":
            raise RuntimeError("name gen failed")
        suffix = endpoint_id[-1]
        return f"vh-{suffix}", f"vc-{suffix}"

    plugin._generate_veth_names = _gen_names

    async def _create_veth_pair(host_veth: str, cont_veth: str):
        return host_veth != "vh-a"

    async def _attach_to_ovs(bridge: str, host_veth: str, vlan: int, **kwargs):
        return host_veth != "vh-b"

    async def _run_cmd(cmd: list[str]):
        if cmd[:4] == ["ip", "link", "set", "vc-c"] and "netns" in cmd:
            return 1, "", "move failed"
        if cmd[:6] == ["nsenter", "-t", "1234", "-n", "ip", "link"] and "vc-d" in cmd:
            return 1, "", "rename failed"
        return 0, "", ""

    plugin._create_veth_pair = _create_veth_pair
    plugin._attach_to_ovs = _attach_to_ovs
    plugin._run_cmd = AsyncMock(side_effect=_run_cmd)

    out = await plugin._repair_endpoints_locked("lab1", "c1")
    status_by_iface = {item["interface"]: item["status"] for item in out}
    assert status_by_iface["eth1"] == "error"
    assert status_by_iface["eth2"] == "error"
    assert status_by_iface["eth3"] == "error"
    assert status_by_iface["eth4"] == "error"
    assert status_by_iface["eth5"] == "repaired"
    assert status_by_iface["eth6"] == "error"
    plugin._mark_dirty_and_save.assert_awaited()


@pytest.mark.asyncio
async def test_repair_endpoints_locked_network_inspection_matching(plugin, monkeypatch):
    ep_eid = _ep("eid1", "nid1", "eth1", "vh-eid", 300, None)
    ep_nid = _ep("ep2", "nid2", "eth2", "vh-nid", 301, None)
    plugin.endpoints = {"eid1": ep_eid, "ep2": ep_nid}
    plugin._cleanup_stale_ovs_ports = AsyncMock()
    plugin._validate_endpoint_exists = AsyncMock(return_value=False)
    plugin._get_container_pid = AsyncMock(return_value=1111)
    plugin._generate_veth_names = lambda endpoint_id: (f"vh-{endpoint_id}", f"vc-{endpoint_id}")
    plugin._create_veth_pair = AsyncMock(return_value=True)
    plugin._attach_to_ovs = AsyncMock(return_value=True)
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    plugin._mark_dirty_and_save = AsyncMock()

    async def _to_thread(func, *args, **kwargs):
        return {
            "net-a": {"EndpointID": "eid1", "NetworkID": "nid1"},
            "net-b": {"EndpointID": "other", "NetworkID": "nid2"},
        }

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    out = await plugin._repair_endpoints_locked("lab1", "container-a")
    assert len([item for item in out if item["status"] == "repaired"]) == 2
    assert ep_eid.container_name == "container-a"
    assert ep_nid.container_name == "container-a"


def test_create_app_registers_plugin_reference(plugin):
    app = plugin.create_app()
    assert app["plugin"] is plugin
    assert app.router is not None


@pytest.mark.asyncio
async def test_start_create_spec_and_shutdown_paths(plugin, monkeypatch, tmp_path):
    plugin._ensure_shared_bridge = AsyncMock()
    plugin._migrate_per_lab_bridges = AsyncMock()
    plugin._discover_existing_state = AsyncMock()
    plugin._start_ttl_cleanup = AsyncMock()
    plugin._start_endpoint_binding_audit = AsyncMock()
    plugin._post_start_reconcile = AsyncMock()

    class _FakeRunner:
        def __init__(self, app):
            self.app = app
            self.setup = AsyncMock()
            self.cleanup = AsyncMock()

    class _FakeSite:
        def __init__(self, runner, socket_path):
            self.runner = runner
            self.socket_path = socket_path
            self.start = AsyncMock()

    monkeypatch.setattr(plugin_mod.web, "AppRunner", _FakeRunner)
    monkeypatch.setattr(plugin_mod.web, "UnixSite", _FakeSite)
    monkeypatch.setattr(plugin_mod.os.path, "exists", lambda path: True)
    monkeypatch.setattr(plugin_mod.os, "remove", lambda path: None)
    monkeypatch.setattr(plugin_mod.os, "makedirs", lambda path, exist_ok=True: None)
    monkeypatch.setattr(plugin_mod.os, "chmod", lambda path, mode: None)
    created_tasks = []
    monkeypatch.setattr(
        asyncio,
        "create_task",
        lambda coro: created_tasks.append(coro) or SimpleNamespace(),
    )
    plugin._create_plugin_spec = AsyncMock()

    runner = await plugin.start(str(tmp_path / "plugin.sock"))
    assert isinstance(runner, _FakeRunner)
    plugin._create_plugin_spec.assert_awaited_once()
    assert len(created_tasks) == 1
    created_tasks[0].close()

    spec_path = tmp_path / "archetype-ovs.spec"
    monkeypatch.setattr(plugin_mod, "PLUGIN_SPEC_PATH", str(spec_path))
    await plugin_mod.DockerOVSPlugin._create_plugin_spec(plugin, str(tmp_path / "sock2"))
    assert spec_path.read_text() == f"unix://{tmp_path / 'sock2'}\n"

    plugin._stop_ttl_cleanup = AsyncMock()
    plugin._stop_endpoint_binding_audit = AsyncMock()
    plugin._save_state = AsyncMock()
    plugin._state_dirty = False
    await plugin.shutdown()
    plugin._save_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_singleton_getter_and_run_standalone(monkeypatch):
    plugin_mod._plugin_instance = None
    one = plugin_mod.get_docker_ovs_plugin()
    two = plugin_mod.get_docker_ovs_plugin()
    assert one is two

    fake_runner = SimpleNamespace(cleanup=AsyncMock())
    fake_plugin = SimpleNamespace(
        start=AsyncMock(return_value=fake_runner),
        shutdown=AsyncMock(),
    )
    monkeypatch.setattr(plugin_mod, "get_docker_ovs_plugin", lambda: fake_plugin)
    monkeypatch.setattr(plugin_mod.logging, "basicConfig", lambda **kwargs: None)

    class _FakeEvent:
        def __init__(self):
            self._set = False

        def set(self):
            self._set = True

        async def wait(self):
            return None

    event = _FakeEvent()
    monkeypatch.setattr(asyncio, "Event", lambda: event)

    class _FakeLoop:
        def add_signal_handler(self, sig, handler):
            handler()

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop())

    await plugin_mod.run_plugin_standalone()
    fake_plugin.start.assert_awaited_once()
    fake_plugin.shutdown.assert_awaited_once()
    fake_runner.cleanup.assert_awaited_once()
