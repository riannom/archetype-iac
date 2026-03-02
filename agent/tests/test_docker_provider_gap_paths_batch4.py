from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config import settings
from agent.providers import docker as docker_mod
from agent.providers.docker import (
    LABEL_NODE_KIND,
    DockerProvider,
    ParsedTopology,
    TopologyLink,
    TopologyNode,
)


class _Proc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


def _make_sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


@pytest.mark.asyncio
async def test_fix_interface_names_handles_missing_plugin_and_container_errors(monkeypatch):
    _make_sync_to_thread(monkeypatch)
    provider = DockerProvider()

    monkeypatch.setattr(docker_mod, "get_docker_ovs_plugin", lambda: None)
    result = await provider._fix_interface_names("ctr-a", "lab1")
    assert result == {"fixed": 0, "already_correct": 0, "reconnected": 0, "errors": []}

    plugin = SimpleNamespace(networks={})
    monkeypatch.setattr(docker_mod, "get_docker_ovs_plugin", lambda: plugin)
    provider._docker = MagicMock()
    provider.docker.containers.get.side_effect = RuntimeError("boom")
    result = await provider._fix_interface_names("ctr-a", "lab1")
    assert any("Failed to get container" in err for err in result["errors"])


@pytest.mark.asyncio
async def test_fix_interface_names_handles_not_running_and_no_matching_networks(monkeypatch):
    _make_sync_to_thread(monkeypatch)
    provider = DockerProvider()

    plugin = SimpleNamespace(
        networks={"nid-x": SimpleNamespace(lab_id="other-lab", network_id="nid-x", interface_name="eth9", bridge_name="br-x")}
    )
    monkeypatch.setattr(docker_mod, "get_docker_ovs_plugin", lambda: plugin)

    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = SimpleNamespace(
        attrs={"State": {"Pid": 0}, "NetworkSettings": {"Networks": {}}},
        labels={LABEL_NODE_KIND: "linux"},
    )
    result = await provider._fix_interface_names("ctr-a", "lab1")
    assert "Container not running" in result["errors"]

    provider.docker.containers.get.return_value = SimpleNamespace(
        attrs={"State": {"Pid": 1234}, "NetworkSettings": {"Networks": {}}},
        labels={LABEL_NODE_KIND: "linux"},
    )
    result = await provider._fix_interface_names("ctr-a", "lab1")
    assert result["errors"] == []
    assert result["fixed"] == 0
    assert result["already_correct"] == 0
    assert result["reconnected"] == 0


@pytest.mark.asyncio
async def test_fix_interface_names_reconnects_and_tracks_already_correct(monkeypatch):
    _make_sync_to_thread(monkeypatch)
    provider = DockerProvider()

    plugin = SimpleNamespace(
        networks={
            "nid-1": SimpleNamespace(
                lab_id="lab1",
                network_id="nid-1",
                interface_name="eth1",
                bridge_name="br-ovs",
            )
        }
    )
    monkeypatch.setattr(docker_mod, "get_docker_ovs_plugin", lambda: plugin)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    net_info = {"NetworkID": "nid-1", "EndpointID": "abcde12345"}
    container = SimpleNamespace(
        attrs={"State": {"Pid": 2468}, "NetworkSettings": {"Networks": {"lab1-eth1": net_info}}},
        labels={LABEL_NODE_KIND: "ceos"},
    )
    network = SimpleNamespace(disconnect=MagicMock(), connect=MagicMock())
    provider._docker = MagicMock()
    provider.docker.containers.get.side_effect = [container, container]
    provider.docker.networks.get.return_value = network

    procs = [
        _Proc(stdout=b"", returncode=0),              # list-ports shared bridge -> missing endpoint
        _Proc(stdout=b"vhabcdeZZ\n", returncode=0),   # list-ports per-network bridge
        _Proc(stdout=b"42\n", returncode=0),           # cat iflink
    ]

    async def _fake_subproc(*_args, **_kwargs):
        return procs.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    provider._find_interface_by_ifindex = AsyncMock(return_value="eth1")
    provider._run_post_boot_commands = AsyncMock(return_value=None)

    result = await provider._fix_interface_names("ctr-a", "lab1")

    assert result["reconnected"] == 1
    assert result["already_correct"] == 1
    assert result["fixed"] == 0
    assert result["errors"] == []
    network.disconnect.assert_called_once_with("ctr-a")
    network.connect.assert_called_once_with("ctr-a")
    provider._run_post_boot_commands.assert_awaited_once_with("ctr-a", "ceos")


@pytest.mark.asyncio
async def test_find_interface_by_ifindex_success_and_failure(monkeypatch):
    provider = DockerProvider()

    async def _ok_subproc(*_args, **_kwargs):
        return _Proc(stdout=b"7: lo: <LOOPBACK>\n42: eth6@if42: <BROADCAST>\n", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _ok_subproc)
    assert await provider._find_interface_by_ifindex(1111, "42") == "eth6"

    async def _fail_subproc(*_args, **_kwargs):
        return _Proc(stdout=b"", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fail_subproc)
    assert await provider._find_interface_by_ifindex(1111, "42") is None


@pytest.mark.asyncio
async def test_rename_container_interface_handles_file_exists_conflict(monkeypatch):
    provider = DockerProvider()
    monkeypatch.setattr(settings, "local_mtu", 9100)

    procs = [
        _Proc(returncode=0),                                     # set actual down
        _Proc(stderr=b"File exists", returncode=1),              # rename actual -> intended (fail)
        _Proc(returncode=0),                                     # check temp exists
        _Proc(returncode=1),                                     # check temp_1 free
        _Proc(returncode=0),                                     # set actual -> intended (retry success)
        _Proc(returncode=0),                                     # set mtu
        _Proc(returncode=0),                                     # set intended up
    ]

    async def _fake_subproc(*_args, **_kwargs):
        if procs:
            return procs.pop(0)
        return _Proc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    result = {"fixed": 0, "errors": []}

    await provider._rename_container_interface(
        pid=1234,
        actual_name="eth9",
        intended_name="eth1",
        container_name="ctr-a",
        result=result,
    )

    assert result["fixed"] == 1
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_rename_container_interface_records_non_file_exists_error(monkeypatch):
    provider = DockerProvider()
    monkeypatch.setattr(settings, "local_mtu", 0)

    procs = [
        _Proc(returncode=0),                         # set actual down
        _Proc(stderr=b"permission denied", returncode=1),  # rename fails
    ]

    async def _fake_subproc(*_args, **_kwargs):
        return procs.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    result = {"fixed": 0, "errors": []}

    await provider._rename_container_interface(
        pid=1234,
        actual_name="eth9",
        intended_name="eth1",
        container_name="ctr-a",
        result=result,
    )

    assert result["fixed"] == 0
    assert any("Failed to rename eth9 -> eth1" in err for err in result["errors"])


@pytest.mark.asyncio
async def test_plugin_hot_connect_success_and_missing_port(monkeypatch):
    _make_sync_to_thread(monkeypatch)
    provider = DockerProvider()
    provider._docker = MagicMock()
    provider.docker.containers.get.side_effect = [
        SimpleNamespace(attrs={"State": {"Pid": 101}}),
        SimpleNamespace(attrs={"State": {"Pid": 202}}),
        SimpleNamespace(attrs={"State": {"Pid": 101}}),
        SimpleNamespace(attrs={"State": {"Pid": 202}}),
    ]

    monkeypatch.setattr(settings, "ovs_bridge_name", "arch-ovs")

    success_procs = [
        _Proc(stdout=b"10\n", returncode=0),                                      # iflink a
        _Proc(stdout=b"10: vetha@if1: <UP>\n", returncode=0),                     # ip link a
        _Proc(stdout=b"arch-ovs\n", returncode=0),                                # port-to-br a
        _Proc(stdout=b"20\n", returncode=0),                                      # iflink b
        _Proc(stdout=b"20: vethb@if2: <UP>\n", returncode=0),                     # ip link b
        _Proc(stdout=b"arch-ovs\n", returncode=0),                                # port-to-br b
        _Proc(stdout=b"300\n", returncode=0),                                     # get tag
        _Proc(stdout=b"", returncode=0),                                          # set tag
    ]

    async def _success_subproc(*_args, **_kwargs):
        return success_procs.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _success_subproc)
    ok = await provider._plugin_hot_connect("lab1", "ctr-a", "eth1", "ctr-b", "eth2")
    assert ok is True

    missing_procs = [
        _Proc(stdout=b"10\n", returncode=0),                                      # iflink a
        _Proc(stdout=b"10: vetha@if1: <UP>\n", returncode=0),                     # ip link a
        _Proc(stdout=b"arch-ovs\n", returncode=0),                                # port-to-br a
        _Proc(stdout=b"", returncode=1),                                          # iflink b fails
    ]

    async def _missing_subproc(*_args, **_kwargs):
        return missing_procs.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _missing_subproc)
    ok = await provider._plugin_hot_connect("lab1", "ctr-a", "eth1", "ctr-b", "eth2")
    assert ok is False


@pytest.mark.asyncio
async def test_create_links_plugin_legacy_and_local_branches(monkeypatch):
    provider = DockerProvider()

    topology_plugin = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(name="n1", kind="linux"),
            "n2": TopologyNode(name="n2", kind="linux"),
        },
        links=[
            TopologyLink(endpoints=["n1:eth1", "n2:eth2"]),
            TopologyLink(endpoints=["n3"]),  # ignored: insufficient endpoints
        ],
    )
    provider._plugin_hot_connect = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)
    created = await provider._create_links(topology_plugin, "lab1")
    assert created == 1
    provider._plugin_hot_connect.assert_awaited_once()

    topology_legacy = ParsedTopology(
        name="lab",
        nodes={},
        links=[TopologyLink(endpoints=["n1", "n2"])],
    )
    provider._ovs_manager = SimpleNamespace(_initialized=True, hot_connect=AsyncMock(return_value=True))
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    created = await provider._create_links(topology_legacy, "lab1")
    assert created == 1
    provider.ovs_manager.hot_connect.assert_awaited_once()

    topology_local = ParsedTopology(
        name="lab",
        nodes={},
        links=[
            TopologyLink(endpoints=["n1:eth1:10.0.0.1/24", "n2:eth2:10.0.0.2/24"]),
            TopologyLink(endpoints=["n3:eth3", "n4:eth4"]),
        ],
    )
    provider._local_network = SimpleNamespace(create_link=AsyncMock(side_effect=[None, RuntimeError("boom")]))
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    created = await provider._create_links(topology_local, "lab1")
    assert created == 1
    assert provider.local_network.create_link.await_count == 2
