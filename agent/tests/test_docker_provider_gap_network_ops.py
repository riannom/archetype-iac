from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound

from agent.config import settings
from agent.providers.docker import (
    LABEL_NODE_NAME,
    DockerProvider,
    ParsedTopology,
    TopologyNode,
)
from agent.schemas import DeployNode, DeployTopology


def _make_sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


class _Proc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""


@pytest.mark.asyncio
async def test_attach_container_to_networks_handles_partial_failures(monkeypatch):
    provider = DockerProvider()
    container = SimpleNamespace(id="cid-1", name="archetype-lab1-r1")

    net_ok = MagicMock()
    net_dup = MagicMock()
    net_dup.connect.side_effect = RuntimeError("already exists")
    net_err = MagicMock()
    net_err.connect.side_effect = RuntimeError("permission denied")

    docker_client = MagicMock()

    def _get_network(name: str):
        if name.endswith("-eth1"):
            return net_ok
        if name.endswith("-eth2"):
            return net_dup
        if name.endswith("-eth3"):
            raise RuntimeError("not found")
        if name.endswith("-eth4"):
            return net_err
        raise AssertionError(f"unexpected network name {name}")

    docker_client.networks.get.side_effect = _get_network
    provider._docker = docker_client
    _make_sync_to_thread(monkeypatch)

    attached = await provider._attach_container_to_networks(
        container=container,
        lab_id="lab1",
        interface_count=4,
        interface_prefix="eth",
        start_index=1,
    )

    assert attached == ["lab1-eth1", "lab1-eth2"]
    net_ok.connect.assert_called_once_with("cid-1")
    net_dup.connect.assert_called_once_with("cid-1")
    net_err.connect.assert_called_once_with("cid-1")


@pytest.mark.asyncio
async def test_recover_stale_network_keeps_only_nodes_with_existing_containers(monkeypatch, tmp_path):
    provider = DockerProvider()
    provider._vlan_allocations["lab1"] = {
        "r1": [100],
        "r2": [101],
    }
    provider._load_vlan_allocations = MagicMock(return_value=True)
    provider._save_vlan_allocations = MagicMock()

    c1 = SimpleNamespace(labels={LABEL_NODE_NAME: "r1"})
    c2 = SimpleNamespace(labels={})
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [c1, c2]
    provider._docker = docker_client
    _make_sync_to_thread(monkeypatch)

    recovered = await provider._recover_stale_network("lab1", tmp_path)

    assert recovered == {"r1": [100]}
    assert provider._vlan_allocations["lab1"] == {"r1": [100]}
    provider._save_vlan_allocations.assert_called_once_with("lab1", tmp_path)


@pytest.mark.asyncio
async def test_capture_container_vlans_updates_tracking_and_saves(monkeypatch, tmp_path):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={"r1": TopologyNode(name="r1", kind="linux")},
        links=[],
    )

    container = MagicMock()
    container.attrs = {"State": {"Pid": 1234}}
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    provider._docker = docker_client

    async def _fake_subproc(*args, **kwargs):
        # nsenter ls /sys/class/net
        return _Proc(b"lo\neth1\neth2\n")

    provider._get_interface_vlan = AsyncMock(side_effect=[100, None])
    provider._save_vlan_allocations = MagicMock()
    _make_sync_to_thread(monkeypatch)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)

    await provider._capture_container_vlans("lab1", topology, tmp_path)

    assert provider._vlan_allocations["lab1"]["r1"] == [100]
    assert provider._next_vlan["lab1"] >= 101
    provider._save_vlan_allocations.assert_called_once_with("lab1", tmp_path)


@pytest.mark.asyncio
async def test_get_interface_vlan_parses_host_veth_and_returns_tag(monkeypatch):
    provider = DockerProvider()
    procs = iter(
        [
            _Proc(b"123\n"),  # iflink in container
            _Proc(b"1: lo: <LOOPBACK>\n123: vethabc@if9: <BROADCAST>\n"),  # host link table
            _Proc(b"205\n"),  # ovs-vsctl get port tag
        ]
    )

    async def _fake_subproc(*args, **kwargs):
        return next(procs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)

    vlan = await provider._get_interface_vlan(1111, "eth1")

    assert vlan == 205


@pytest.mark.asyncio
async def test_create_containers_cleans_up_partial_state_when_creation_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)
    provider = DockerProvider()
    _make_sync_to_thread(monkeypatch)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    topology = ParsedTopology(
        name="lab",
        nodes={
            "r1": TopologyNode(name="r1", kind="linux", interface_count=1),
            "r2": TopologyNode(name="r2", kind="linux", interface_count=1),
        },
        links=[],
    )

    first_container = SimpleNamespace(id="cid-r1", name="archetype-lab1-r1")
    first_container.remove = MagicMock()

    docker_client = MagicMock()
    docker_client.containers.get.side_effect = [NotFound("missing"), NotFound("missing")]
    docker_client.containers.create.side_effect = [first_container, RuntimeError("create failed")]
    provider._docker = docker_client

    provider._create_lab_networks = AsyncMock(return_value={})
    provider._attach_container_to_networks = AsyncMock(return_value=[])
    provider._delete_lab_networks = AsyncMock(side_effect=RuntimeError("network cleanup fail"))

    def _fake_container_config(node, lab_id, workspace, interface_count=0):
        return {
            "image": "alpine:latest",
            "name": f"archetype-{lab_id}-{node.name}",
            "hostname": node.name,
            "detach": True,
            "tty": True,
            "stdin_open": True,
            "restart_policy": {"Name": "no"},
            "labels": {},
            "environment": {},
        }

    provider._create_container_config = _fake_container_config  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="create failed"):
        await provider._create_containers(topology, "lab1", tmp_path)

    first_container.remove.assert_called_once_with(force=True, v=True)
    provider._delete_lab_networks.assert_awaited_once_with("lab1")


@pytest.mark.asyncio
async def test_start_containers_handles_ovs_initialize_failure(monkeypatch):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    provider = DockerProvider()
    _make_sync_to_thread(monkeypatch)

    provider._ovs_manager = MagicMock()
    provider._ovs_manager.initialize = AsyncMock(side_effect=RuntimeError("ovs init fail"))
    provider._ovs_manager._initialized = False
    provider._local_network = MagicMock()
    provider._local_network.provision_dummy_interfaces = AsyncMock(return_value=None)

    topology = ParsedTopology(
        name="lab",
        nodes={"r1": TopologyNode(name="r1", kind="linux", interface_count=1)},
        links=[],
    )
    container = SimpleNamespace(name="archetype-lab1-r1", status="exited", start=MagicMock())

    failed = await provider._start_containers({"r1": container}, topology, "lab1")

    assert failed == []
    container.start.assert_called_once()


@pytest.mark.asyncio
async def test_start_containers_plugin_mode_tolerates_fix_interface_failure(monkeypatch):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)
    provider = DockerProvider()
    _make_sync_to_thread(monkeypatch)

    provider._fix_interface_names = AsyncMock(side_effect=RuntimeError("fix failed"))
    topology = ParsedTopology(
        name="lab",
        nodes={"r1": TopologyNode(name="r1", kind="linux", interface_count=1)},
        links=[],
    )
    container = SimpleNamespace(name="archetype-lab1-r1", status="running", start=MagicMock())

    failed = await provider._start_containers({"r1": container}, topology, "lab1")

    assert failed == []
    provider._fix_interface_names.assert_awaited_once_with("archetype-lab1-r1", "lab1")


@pytest.mark.asyncio
async def test_deploy_missing_images_returns_detailed_error(tmp_path):
    provider = DockerProvider()
    provider._recover_stale_network = AsyncMock(return_value={})
    provider._validate_images = MagicMock(return_value=[("r1", "vendor/image:1"), ("r2", "vendor/image:2")])

    topology = DeployTopology(
        nodes=[
            DeployNode(name="r1", display_name="R1", kind="linux"),
            DeployNode(name="r2", kind="linux"),
        ],
        links=[],
    )

    result = await provider.deploy("lab1", topology, tmp_path)

    assert result.success is False
    assert result.error == "Missing 2 image(s)"
    assert "Missing images:" in (result.stderr or "")
    assert "R1(r1)" in (result.stderr or "")
    assert "Please upload images" in (result.stderr or "")


@pytest.mark.asyncio
async def test_destroy_collects_container_remove_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    provider = DockerProvider()
    _make_sync_to_thread(monkeypatch)

    c_ok = SimpleNamespace(id="1", name="c-ok")
    c_ok.remove = MagicMock()
    c_fail = SimpleNamespace(id="2", name="c-fail")
    c_fail.remove = MagicMock(side_effect=RuntimeError("remove failed"))

    docker_client = MagicMock()
    docker_client.containers.list.side_effect = [[c_ok, c_fail], []]
    provider._docker = docker_client

    provider._cleanup_lab_volumes = AsyncMock(return_value=0)
    provider._local_network = MagicMock()
    provider._local_network.cleanup_lab = AsyncMock(return_value={})
    provider._remove_vlan_file = MagicMock()

    result = await provider.destroy("lab1", tmp_path)

    assert result.success is False
    assert "Removed 1 containers" in (result.stdout or "")
    assert "Failed to remove c-fail" in (result.stderr or "")
