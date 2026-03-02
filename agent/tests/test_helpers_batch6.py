"""Additional unit coverage for agent.helpers."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from agent import helpers
from agent.config import settings
from agent.providers import NodeStatus as ProviderNodeStatus
from agent.schemas import DockerPruneRequest, NodeStatus


def _fake_psutil() -> SimpleNamespace:
    return SimpleNamespace(
        cpu_percent=lambda interval=0.1: 11.5,
        cpu_count=lambda: 8,
        virtual_memory=lambda: SimpleNamespace(percent=21.0, used=8 * 1024**3, total=32 * 1024**3),
        disk_usage=lambda _path: SimpleNamespace(percent=44.0, used=20 * 1024**3, total=40 * 1024**3),
    )


class _FakeContainer:
    def __init__(
        self,
        name: str,
        status: str,
        labels: dict[str, str] | None = None,
        image_id: str = "img-1",
        image_tags: list[str] | None = None,
        image_short: str = "sha256:abc",
        nano_cpus: int = 1_000_000_000,
        mem_bytes: int = 1024 * 1024 * 1024,
    ):
        self.name = name
        self.status = status
        self.labels = labels or {}
        self.image = SimpleNamespace(id=image_id, tags=image_tags or ["repo:tag"], short_id=image_short)
        self.attrs = {"HostConfig": {"NanoCpus": nano_cpus, "Memory": mem_bytes}}
        self.short_id = name[:12]

    def exec_run(self, *_args, **_kwargs):
        return (0, b"17\n")

    def remove(self, force: bool = False):
        _ = force
        return None


def test_get_provider_for_request_success_and_failure(monkeypatch):
    provider = object()
    monkeypatch.setattr(helpers, "get_provider", lambda _name: provider)
    assert helpers.get_provider_for_request("docker") is provider

    monkeypatch.setattr(helpers, "get_provider", lambda _name: None)
    monkeypatch.setattr(helpers, "list_providers", lambda: ["docker"])
    with pytest.raises(HTTPException) as exc:
        helpers.get_provider_for_request("libvirt")
    assert exc.value.status_code == 503


def test_provider_status_to_schema_mapping_and_fallback():
    assert helpers.provider_status_to_schema(ProviderNodeStatus.RUNNING) == NodeStatus.RUNNING
    assert helpers.provider_status_to_schema(object()) == NodeStatus.UNKNOWN


def test_get_capabilities_reflects_settings(monkeypatch):
    monkeypatch.setattr(settings, "enable_docker", True)
    monkeypatch.setattr(settings, "enable_libvirt", True)
    monkeypatch.setattr(settings, "enable_vxlan", True)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 9)

    caps = helpers.get_capabilities()
    assert caps.max_concurrent_jobs == 9
    assert "vxlan" in caps.features
    assert len(caps.providers) == 2


def test_sync_get_resource_usage_success_and_docker_skip_paths(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil())

    good = _FakeContainer(
        name="arch-lab-r1",
        status="running",
        labels={"archetype.lab_id": "lab-1", "archetype.node_name": "r1", "archetype.node_kind": "router"},
        nano_cpus=2_000_000_000,
        mem_bytes=2 * 1024 * 1024 * 1024,
    )
    system = _FakeContainer(name="archetype-agent", status="running", labels={})
    ignored = _FakeContainer(name="nginx", status="running", labels={})

    by_id = {
        "good": good,
        "system": system,
        "ignored": ignored,
    }

    class _Containers:
        def get(self, cid: str):
            if cid == "gone":
                import docker

                raise docker.errors.NotFound("gone")
            return by_id[cid]

    fake_client = SimpleNamespace(
        api=SimpleNamespace(
            containers=lambda all=True: [  # noqa: ARG005
                {"Id": "good", "Names": ["/arch-lab-r1"]},
                {"Id": "system", "Names": ["/archetype-agent"]},
                {"Id": "ignored", "Names": ["/nginx"]},
                {"Id": "gone", "Names": ["/gone"]},
            ]
        ),
        containers=_Containers(),
    )
    monkeypatch.setattr(helpers, "get_docker_client", lambda: fake_client)

    usage = helpers._sync_get_resource_usage()
    assert usage["cpu_count"] == 8
    assert usage["containers_running"] == 2
    assert usage["containers_total"] == 2
    assert any(entry["name"] == "arch-lab-r1" for entry in usage["container_details"])


def test_sync_get_resource_usage_handles_docker_and_outer_failures(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil())
    monkeypatch.setattr(helpers, "get_docker_client", Mock(side_effect=RuntimeError("docker down")))
    usage = helpers._sync_get_resource_usage()
    assert usage["containers_total"] == 0

    bad_psutil = SimpleNamespace(cpu_percent=Mock(side_effect=RuntimeError("psutil down")))
    monkeypatch.setitem(sys.modules, "psutil", bad_psutil)
    assert helpers._sync_get_resource_usage() == {}


@pytest.mark.asyncio
async def test_get_resource_usage_wrapper_empty_and_libvirt_paths(monkeypatch):
    monkeypatch.setattr(helpers.asyncio, "to_thread", AsyncMock(return_value={}))
    assert await helpers.get_resource_usage() == {}

    monkeypatch.setattr(
        helpers.asyncio,
        "to_thread",
        AsyncMock(return_value={"cpu_percent": 1, "container_details": []}),
    )
    monkeypatch.setattr(settings, "enable_libvirt", True)
    fake_provider = SimpleNamespace(
        get_vm_stats_sync=lambda: None,
        _run_libvirt=AsyncMock(
            return_value=[{"status": "running", "vcpus": 2}, {"status": "stopped", "vcpus": 2}]
        ),
    )
    with patch("agent.providers.registry.get_provider", return_value=fake_provider):
        usage = await helpers.get_resource_usage()

    assert usage["vms_total"] == 2
    assert usage["vms_running"] == 1
    assert len(usage["vm_details"]) == 2


@pytest.mark.parametrize(
    ("advertise_host", "agent_host", "local_ip", "detected", "expected_host"),
    [
        ("10.1.1.1", "0.0.0.0", "", "", "10.1.1.1"),
        ("", "192.168.5.10", "", "", "192.168.5.10"),
        ("", "0.0.0.0", "172.18.0.8", "", "172.18.0.8"),
        ("", "0.0.0.0", "", "10.10.10.10", "10.10.10.10"),
        ("", "0.0.0.0", "", "", "agent-name"),
    ],
)
def test_get_agent_info_host_selection(
    monkeypatch,
    advertise_host: str,
    agent_host: str,
    local_ip: str,
    detected: str,
    expected_host: str,
):
    monkeypatch.setattr(settings, "advertise_host", advertise_host)
    monkeypatch.setattr(settings, "agent_host", agent_host)
    monkeypatch.setattr(settings, "local_ip", local_ip)
    monkeypatch.setattr(settings, "agent_name", "agent-name")
    monkeypatch.setattr(settings, "agent_port", 8123)
    monkeypatch.setattr(settings, "is_local", False)
    monkeypatch.setattr(
        helpers._state,
        "AGENT_STARTED_AT",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(helpers._state, "AGENT_ID", "agent-id")
    monkeypatch.setattr(helpers._state, "_detect_local_ip", lambda: detected or None)

    with patch("agent.helpers.detect_deployment_mode", return_value=SimpleNamespace(value="docker")), patch(
        "agent.network.transport.get_data_plane_ip", return_value="10.99.0.2"
    ):
        info = helpers.get_agent_info()

    assert info.address == f"{expected_host}:8123"
    assert info.agent_id == "agent-id"
    assert info.data_plane_ip == "10.99.0.2"


def test_allocated_resources_and_validation_helpers():
    usage = {
        "container_details": [
            {"status": "running", "is_system": False, "vcpus": 2, "memory_mb": 256},
            {"status": "running", "is_system": True, "vcpus": 8, "memory_mb": 8192},
            {"status": "stopped", "is_system": False, "vcpus": 1, "memory_mb": 128},
        ],
        "vm_details": [
            {"status": "running", "vcpus": 4, "memory_mb": 1024},
            {"status": "stopped", "vcpus": 6, "memory_mb": 2048},
        ],
    }
    alloc = helpers._get_allocated_resources(usage)
    assert alloc == {"vcpus": 6, "memory_mb": 1280}
    assert helpers._validate_port_name("vh123") is True
    assert helpers._validate_container_name("arch-lab-node") is True
    assert helpers._validate_container_name("bad name") is False


def test_load_node_startup_config_validation_and_happy_path(monkeypatch, tmp_path):
    base = tmp_path / "workspace"
    monkeypatch.setattr(settings, "workspace_path", str(base))

    with pytest.raises(HTTPException) as lab_err:
        helpers._load_node_startup_config("../bad", "r1")
    assert lab_err.value.status_code == 400

    with pytest.raises(HTTPException) as node_err:
        helpers._load_node_startup_config("lab-1", "../bad")
    assert node_err.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        helpers._load_node_startup_config("lab-1", "r1")
    assert missing.value.status_code == 404

    cfg_dir = base / "lab-1" / "configs" / "r1"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "startup-config"
    cfg_file.write_text("   \n", encoding="utf-8")
    with pytest.raises(HTTPException) as empty:
        helpers._load_node_startup_config("lab-1", "r1")
    assert empty.value.status_code == 404

    cfg_file.write_text("hostname r1\n", encoding="utf-8")
    assert helpers._load_node_startup_config("lab-1", "r1") == "hostname r1\n"
    assert helpers._render_n9kv_poap_script("http://config") != ""


def test_interface_name_to_index():
    assert helpers._interface_name_to_index("eth1") == 0
    assert helpers._interface_name_to_index("eth7") == 6
    with pytest.raises(ValueError):
        helpers._interface_name_to_index("mgmt")


def test_resolve_ifindex_sync_success_failure_and_exception(monkeypatch):
    container = _FakeContainer(name="arch-lab-r1", status="running")
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container))
    monkeypatch.setattr(helpers, "get_docker_client", lambda: client)
    assert helpers._resolve_ifindex_sync("arch-lab-r1", "eth1") == 17

    bad_container = _FakeContainer(name="arch-lab-r1", status="running")
    bad_container.exec_run = lambda *_args, **_kwargs: (2, b"")
    client_bad = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: bad_container))
    monkeypatch.setattr(helpers, "get_docker_client", lambda: client_bad)
    assert helpers._resolve_ifindex_sync("arch-lab-r1", "eth1") is None

    monkeypatch.setattr(helpers, "get_docker_client", Mock(side_effect=RuntimeError("docker")))
    assert helpers._resolve_ifindex_sync("arch-lab-r1", "eth1") is None


@pytest.mark.asyncio
async def test_vlan_pick_and_allocate_helpers(monkeypatch):
    assert helpers._pick_free_vlan({100, 101}, 100, 102) == 102
    assert helpers._pick_free_vlan({100, 101, 102}, 100, 102) is None
    assert helpers._pick_isolation_vlan({100, 101}, "arch-ovs", "vh1") == 102
    assert helpers._pick_isolation_vlan(set(range(100, 4001)), "arch-ovs", "vh1") is None

    monkeypatch.setattr(helpers, "_ovs_list_used_vlans", AsyncMock(return_value={2050, 2051}))
    assert await helpers._ovs_allocate_link_vlan("arch-ovs") == 2052

    monkeypatch.setattr(helpers, "_ovs_list_used_vlans", AsyncMock(return_value=set(range(2050, 4001))))
    assert await helpers._ovs_allocate_link_vlan("arch-ovs") == 100

    monkeypatch.setattr(helpers, "_ovs_list_used_vlans", AsyncMock(return_value={100, 101}))
    monkeypatch.setattr(helpers, "_ovs_set_port_vlan", AsyncMock(return_value=True))
    assert await helpers._ovs_allocate_unique_vlan("vh1") == 102

    monkeypatch.setattr(helpers, "_ovs_set_port_vlan", AsyncMock(return_value=False))
    assert await helpers._ovs_allocate_unique_vlan("vh1") is None

    monkeypatch.setattr(helpers, "_pick_isolation_vlan", lambda _used, _bridge, _port: None)
    assert await helpers._ovs_allocate_unique_vlan("vh1") is None


def test_get_docker_images_success_and_error(monkeypatch):
    img = SimpleNamespace(
        id="sha256:1",
        tags=["repo:tag"],
        attrs={"Size": 123, "Created": "2026-03-01T00:00:00Z"},
    )
    client = SimpleNamespace(images=SimpleNamespace(list=lambda: [img]))
    monkeypatch.setattr(helpers, "get_docker_client", lambda: client)

    images = helpers._get_docker_images()
    assert len(images) == 1
    assert images[0].id == "sha256:1"

    monkeypatch.setattr(helpers, "get_docker_client", Mock(side_effect=RuntimeError("docker")))
    assert helpers._get_docker_images() == []


def test_sync_prune_docker_success_and_error_paths(monkeypatch):
    running = _FakeContainer(
        name="arch-lab-r1",
        status="running",
        labels={"archetype.lab_id": "lab-keep"},
        image_id="img-keep",
    )
    stopped_keep = _FakeContainer(
        name="arch-lab-r2",
        status="exited",
        labels={"archetype.lab_id": "lab-keep"},
    )
    stopped_drop = _FakeContainer(
        name="arch-lab-r3",
        status="exited",
        labels={"archetype.lab_id": "lab-drop"},
    )

    class _Containers:
        def list(self, *args, **kwargs):
            _ = args
            if kwargs.get("all"):
                return [running, stopped_keep, stopped_drop]
            if kwargs.get("filters") == {"status": "exited"}:
                return [stopped_keep, stopped_drop]
            return []

    fake_client = SimpleNamespace(
        containers=_Containers(),
        images=SimpleNamespace(
            prune=lambda filters=None: {  # noqa: ARG005
                "ImagesDeleted": [{"Deleted": "sha256:a"}, {"Untagged": "repo:tag"}],
                "SpaceReclaimed": 100,
            }
        ),
        api=SimpleNamespace(prune_builds=lambda: {"CachesDeleted": ["cache1"], "SpaceReclaimed": 50}),
        volumes=SimpleNamespace(prune=lambda: {"VolumesDeleted": ["v1", "v2"], "SpaceReclaimed": 25}),
        networks=SimpleNamespace(prune=lambda: {"NetworksDeleted": ["n1"]}),
    )

    monkeypatch.setattr(helpers, "get_docker_client", lambda: fake_client)
    request = DockerPruneRequest(
        valid_lab_ids=["lab-keep"],
        prune_dangling_images=True,
        prune_build_cache=True,
        prune_unused_volumes=True,
        prune_stopped_containers=True,
        prune_unused_networks=True,
    )
    resp = helpers._sync_prune_docker(request)
    assert resp.success is True
    assert resp.images_removed == 1
    assert resp.build_cache_removed == 1
    assert resp.volumes_removed == 2
    assert resp.containers_removed == 1
    assert resp.networks_removed == 1
    assert resp.space_reclaimed == 175

    monkeypatch.setattr(helpers, "get_docker_client", Mock(side_effect=RuntimeError("fatal")))
    failed = helpers._sync_prune_docker(request)
    assert failed.success is False
    assert "fatal" in failed.errors[0]
