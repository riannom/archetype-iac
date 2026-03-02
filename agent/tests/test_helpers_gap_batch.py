from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import docker
import pytest

import agent.helpers as helpers_mod
from agent.schemas import DockerPruneRequest


class _Proc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.fixture
def sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


def test_sync_get_resource_usage_collects_docker_details(monkeypatch):
    fake_psutil = SimpleNamespace(
        cpu_percent=lambda interval=0.1: 12.5,
        cpu_count=lambda: 8,
        virtual_memory=lambda: SimpleNamespace(percent=55.0, used=4 * 1024**3, total=8 * 1024**3),
        disk_usage=lambda _path: SimpleNamespace(percent=40.0, used=50 * 1024**3, total=100 * 1024**3),
    )

    good_container = MagicMock()
    good_container.name = "archetype-lab1-r1"
    good_container.status = "running"
    good_container.labels = {
        "archetype.node_name": "r1",
        "archetype.node_kind": "linux",
        "archetype.lab_id": "lab1",
    }
    good_container.image.tags = ["vendor/r1:1"]
    good_container.image.short_id = "sha256:abc"
    good_container.attrs = {"HostConfig": {"NanoCpus": int(2e9), "Memory": 512 * 1024 * 1024}}

    docker_client = MagicMock()
    docker_client.api.containers.return_value = [
        {"Id": "good", "Names": ["/archetype-lab1-r1"]},
        {"Id": "bad", "Names": ["/dead"]},
    ]

    def _get_container(container_id: str):
        if container_id == "good":
            return good_container
        raise docker.errors.NotFound("gone")

    docker_client.containers.get.side_effect = _get_container

    monkeypatch.setattr(helpers_mod.settings, "workspace_path", "/tmp")

    with patch.dict("sys.modules", {"psutil": fake_psutil}):
        with patch.object(helpers_mod, "get_docker_client", return_value=docker_client):
            usage = helpers_mod._sync_get_resource_usage()

    assert usage["cpu_percent"] == 12.5
    assert usage["containers_total"] == 1
    assert usage["containers_running"] == 1
    assert usage["container_details"][0]["name"] == "archetype-lab1-r1"
    assert usage["container_details"][0]["vcpus"] == 2
    assert usage["container_details"][0]["memory_mb"] == 512


def test_sync_get_resource_usage_returns_empty_on_failure(monkeypatch):
    fake_psutil = SimpleNamespace(
        cpu_percent=lambda interval=0.1: (_ for _ in ()).throw(RuntimeError("psutil failed")),
        cpu_count=lambda: 0,
        virtual_memory=lambda: None,
        disk_usage=lambda _path: None,
    )
    with patch.dict("sys.modules", {"psutil": fake_psutil}):
        usage = helpers_mod._sync_get_resource_usage()
    assert usage == {}


@pytest.mark.asyncio
async def test_get_resource_usage_adds_vm_stats(monkeypatch):
    monkeypatch.setattr(helpers_mod.settings, "enable_libvirt", True)
    base = {"cpu_percent": 1.0, "container_details": []}

    libvirt_provider = MagicMock()
    libvirt_provider._run_libvirt = AsyncMock(
        return_value=[
            {"status": "running", "name": "vm1"},
            {"status": "stopped", "name": "vm2"},
        ]
    )
    libvirt_provider.get_vm_stats_sync = object()

    with patch.object(helpers_mod, "_sync_get_resource_usage", return_value=base):
        with patch("agent.providers.registry.get_provider", return_value=libvirt_provider):
            result = await helpers_mod.get_resource_usage()

    assert result["vms_total"] == 2
    assert result["vms_running"] == 1
    assert len(result["vm_details"]) == 2


@pytest.mark.asyncio
async def test_get_resource_usage_tolerates_vm_collection_error(monkeypatch):
    monkeypatch.setattr(helpers_mod.settings, "enable_libvirt", True)
    base = {"cpu_percent": 1.0, "container_details": []}
    libvirt_provider = MagicMock()
    libvirt_provider._run_libvirt = AsyncMock(side_effect=RuntimeError("vm fail"))

    with patch.object(helpers_mod, "_sync_get_resource_usage", return_value=base):
        with patch("agent.providers.registry.get_provider", return_value=libvirt_provider):
            result = await helpers_mod.get_resource_usage()

    assert result["vms_total"] == 0
    assert result["vms_running"] == 0
    assert result["vm_details"] == []


@pytest.mark.asyncio
async def test_resolve_ovs_port_via_ifindex_handles_invalid_ifindex(sync_to_thread, monkeypatch):
    monkeypatch.setattr(helpers_mod.settings, "ovs_bridge_name", "arch-ovs")
    monkeypatch.setattr(helpers_mod, "_resolve_ifindex_sync", lambda *_args: 22)

    proc_calls = iter(
        [
            _Proc(stdout=b"vh-bad\nvh-good\n"),
            _Proc(stdout=b"not-a-number\n"),
            _Proc(stdout=b"22\n"),
        ]
    )

    async def _fake_subproc(*args, **kwargs):
        return next(proc_calls)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    with patch.object(helpers_mod, "_ovs_get_port_vlan", new=AsyncMock(return_value=305)):
        result = await helpers_mod._resolve_ovs_port_via_ifindex("ctr1", "eth1")

    assert result == ("vh-good", 305)


@pytest.mark.asyncio
async def test_resolve_ovs_port_libvirt_fallback_uses_node_vlan_when_port_tag_missing():
    libvirt_provider = MagicMock()
    libvirt_provider.get_node_kind_async = AsyncMock(return_value="iosv")
    libvirt_provider.get_vm_interface_port = AsyncMock(return_value="vnet2")
    libvirt_provider.get_node_vlans.return_value = [404]

    with patch.object(
        helpers_mod,
        "get_provider",
        side_effect=lambda p: None if p == "docker" else libvirt_provider,
    ):
        with patch.object(helpers_mod, "_ovs_get_port_vlan", new=AsyncMock(return_value=None)):
            port = await helpers_mod._resolve_ovs_port("lab1", "vm1", "eth1")

    assert port is not None
    assert port.port_name == "vnet2"
    assert port.vlan_tag == 404
    assert port.provider == "libvirt"


@pytest.mark.asyncio
async def test_ovs_set_port_vlan_returns_false_on_command_failure(monkeypatch):
    async def _fake_subproc(*args, **kwargs):
        return _Proc(stderr=b"failed", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    ok = await helpers_mod._ovs_set_port_vlan("vh123", 200)
    assert ok is False


@pytest.mark.asyncio
async def test_ovs_get_port_vlan_parsing(monkeypatch):
    async def _fake_subproc(*args, **kwargs):
        return _Proc(stdout=b"abc\n", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    assert await helpers_mod._ovs_get_port_vlan("vh1") is None

    async def _fake_subproc_brackets(*args, **kwargs):
        return _Proc(stdout=b"[]\n", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc_brackets)
    assert await helpers_mod._ovs_get_port_vlan("vh1") is None


@pytest.mark.asyncio
async def test_ovs_list_used_vlans_collects_non_null_tags(monkeypatch):
    async def _fake_subproc(*args, **kwargs):
        return _Proc(stdout=b"vh1\nvh2\n", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subproc)
    with patch.object(helpers_mod, "_ovs_get_port_vlan", new=AsyncMock(side_effect=[100, None])):
        used = await helpers_mod._ovs_list_used_vlans("arch-ovs")
    assert used == {100}


@pytest.mark.asyncio
async def test_ovs_allocate_unique_vlan_returns_none_when_no_free_vlan():
    with patch.object(helpers_mod, "_ovs_list_used_vlans", new=AsyncMock(return_value=set())):
        with patch.object(helpers_mod, "_pick_isolation_vlan", return_value=None):
            vlan = await helpers_mod._ovs_allocate_unique_vlan("vh1")
    assert vlan is None


def test_get_docker_images_includes_created_and_size():
    image = SimpleNamespace(
        id="sha256:1",
        tags=["img:1"],
        attrs={"Size": 123, "Created": "2026-01-01T00:00:00Z"},
    )
    client = MagicMock()
    client.images.list.return_value = [image]

    with patch.object(helpers_mod, "get_docker_client", return_value=client):
        images = helpers_mod._get_docker_images()

    assert len(images) == 1
    assert images[0].size_bytes == 123
    assert images[0].created == "2026-01-01T00:00:00Z"


def test_sync_prune_docker_runs_all_selected_prunes():
    req = DockerPruneRequest(
        valid_lab_ids=["lab-valid"],
        prune_dangling_images=True,
        prune_build_cache=True,
        prune_unused_volumes=True,
        prune_stopped_containers=True,
        prune_unused_networks=True,
    )

    protected = MagicMock()
    protected.labels = {"archetype.lab_id": "lab-valid"}
    protected.status = "exited"
    protected.image = SimpleNamespace(id="sha256:keep")

    stopped_skip = MagicMock()
    stopped_skip.labels = {"archetype.lab_id": "lab-valid"}
    stopped_skip.short_id = "skip"
    stopped_skip.remove = MagicMock()

    stopped_remove = MagicMock()
    stopped_remove.labels = {"archetype.lab_id": "lab-other"}
    stopped_remove.short_id = "rm1"
    stopped_remove.remove = MagicMock()

    stopped_fail = MagicMock()
    stopped_fail.labels = {}
    stopped_fail.short_id = "rm2"
    stopped_fail.remove = MagicMock(side_effect=RuntimeError("cannot remove"))

    client = MagicMock()

    def _list_containers(*args, **kwargs):
        if kwargs.get("all") is True:
            return [protected]
        if kwargs.get("filters") == {"status": "exited"}:
            return [stopped_skip, stopped_remove, stopped_fail]
        return []

    client.containers.list.side_effect = _list_containers
    client.images.prune.return_value = {
        "ImagesDeleted": [{"Deleted": "sha256:a"}, {"Untagged": "img:old"}],
        "SpaceReclaimed": 10,
    }
    client.api.prune_builds.return_value = {
        "CachesDeleted": ["c1", "c2"],
        "SpaceReclaimed": 20,
    }
    client.volumes.prune.return_value = {
        "VolumesDeleted": ["v1"],
        "SpaceReclaimed": 30,
    }
    client.networks.prune.return_value = {"NetworksDeleted": ["n1", "n2"]}

    with patch.object(helpers_mod, "get_docker_client", return_value=client):
        result = helpers_mod._sync_prune_docker(req)

    assert result.success is True
    assert result.images_removed == 1
    assert result.build_cache_removed == 2
    assert result.volumes_removed == 1
    assert result.containers_removed == 1
    assert result.networks_removed == 2
    assert result.space_reclaimed == 60
    assert any("cannot remove" in e for e in result.errors)


def test_sync_prune_docker_returns_failure_when_client_unavailable():
    with patch.object(helpers_mod, "get_docker_client", side_effect=RuntimeError("docker down")):
        result = helpers_mod._sync_prune_docker(DockerPruneRequest(valid_lab_ids=[]))
    assert result.success is False
    assert result.errors == ["docker down"]


@pytest.mark.asyncio
async def test_fix_running_interfaces_restarts_ovs_socket_race_container(sync_to_thread, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    race_container = MagicMock()
    race_container.name = "archetype-lab1-r1"
    race_container.status = "exited"
    race_container.labels = {"archetype.lab_id": "lab1"}
    race_container.attrs = {"State": {"ExitCode": 255, "Error": "archetype-ovs.sock timeout"}}

    provider = MagicMock()
    provider.docker.containers.list.side_effect = [[race_container], []]
    provider._fix_interface_names = AsyncMock(side_effect=RuntimeError("fix failed"))

    with patch.object(helpers_mod, "get_provider", return_value=provider):
        await helpers_mod._fix_running_interfaces()

    race_container.start.assert_called_once()
    provider._fix_interface_names.assert_awaited()


@pytest.mark.asyncio
async def test_cleanup_lingering_virsh_sessions_tolerates_unregister_failure(sync_to_thread):
    with patch("agent.console_session_registry.list_active_domains", return_value=["vm1"]):
        with patch("agent.console_session_registry.unregister_session", side_effect=RuntimeError("busy")):
            with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
                await helpers_mod._cleanup_lingering_virsh_sessions()
