from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound, APIError

from agent.providers import docker as docker_mod
from agent.providers.base import NodeInfo, NodeStatus
from agent.providers.docker import (
    LABEL_LAB_ID,
    LABEL_NODE_KIND,
    LABEL_NODE_NAME,
    LABEL_PROVIDER,
    DockerProvider,
)


@pytest.fixture
def sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


def _api_error(status_code: int, message: str = "api error") -> APIError:
    response = MagicMock()
    response.status_code = status_code
    return APIError(message, response=response)


def _container(
    *,
    container_id: str,
    node_name: str,
    lab_id: str | None,
    kind: str = "linux",
    status: str = "running",
    ips: list[str] | None = None,
):
    labels = {LABEL_NODE_NAME: node_name, LABEL_NODE_KIND: kind, LABEL_PROVIDER: "docker"}
    if lab_id is not None:
        labels[LABEL_LAB_ID] = lab_id

    networks = {}
    for idx, ip in enumerate(ips or []):
        networks[f"net{idx + 1}"] = {"IPAddress": ip}

    return SimpleNamespace(
        id=container_id,
        name=f"ctr-{node_name}",
        labels=labels,
        status=status,
        short_id=container_id[:12],
        image=SimpleNamespace(tags=["example/image:1"], id="sha256:abcdef1234567890"),
        attrs={"NetworkSettings": {"Networks": networks}},
        remove=MagicMock(),
    )


@pytest.mark.asyncio
async def test_status_merges_label_and_prefix_results_without_duplicate_ids(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client

    c1 = _container(container_id="id-1", node_name="r1", lab_id="lab-a", ips=["10.0.0.11"])
    c2 = _container(container_id="id-2", node_name="r2", lab_id="lab-a", ips=["10.0.0.12"])
    docker_client.containers.list.side_effect = [[c1], [c1, c2]]

    result = await provider.status("lab-a", Path("/tmp/workspace"))

    assert result.lab_exists is True
    assert result.error is None
    assert sorted(n.name for n in result.nodes) == ["r1", "r2"]
    assert docker_client.containers.list.call_count == 2


@pytest.mark.asyncio
async def test_status_falls_back_to_prefix_when_label_query_fails(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client

    c1 = _container(container_id="id-1", node_name="r1", lab_id="lab-a")
    docker_client.containers.list.side_effect = [RuntimeError("stale labels"), [c1]]

    result = await provider.status("lab-a", Path("/tmp/workspace"))

    assert result.lab_exists is True
    assert [n.name for n in result.nodes] == ["r1"]


@pytest.mark.asyncio
async def test_status_returns_error_on_outer_failure(sync_to_thread, monkeypatch):
    provider = DockerProvider()
    provider._docker = MagicMock()
    monkeypatch.setattr(provider, "_lab_prefix", lambda _lab_id: (_ for _ in ()).throw(RuntimeError("boom")))

    result = await provider.status("lab-a", Path("/tmp/workspace"))

    assert result.lab_exists is False
    assert result.error == "boom"


@pytest.mark.asyncio
async def test_status_skips_containers_that_fail_node_conversion(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client

    good = _container(container_id="id-1", node_name="r1", lab_id="lab-a")
    bad = _container(container_id="id-2", node_name="r2", lab_id="lab-a")
    docker_client.containers.list.side_effect = [[good, bad], []]

    provider._node_from_container = MagicMock(
        side_effect=[
            NodeInfo(
                name="r1",
                status=NodeStatus.RUNNING,
                container_id="id-1",
                image="example/image:1",
                ip_addresses=[],
            ),
            RuntimeError("inspect failed"),
        ]
    )

    result = await provider.status("lab-a", Path("/tmp/workspace"))

    assert result.lab_exists is True
    assert [n.name for n in result.nodes] == ["r1"]


@pytest.mark.asyncio
async def test_get_console_command_returns_docker_exec_for_non_ssh_kind(sync_to_thread, monkeypatch):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    docker_client.containers.get.return_value = _container(
        container_id="id-1",
        node_name="r1",
        lab_id="lab-a",
        kind="linux",
        status="running",
    )

    monkeypatch.setattr(docker_mod, "get_console_method", lambda _kind: "docker_exec")
    monkeypatch.setattr(docker_mod, "get_console_shell", lambda _kind: "/bin/bash")

    result = await provider.get_console_command("lab-a", "r1", Path("/tmp/workspace"))

    assert result == ["docker", "exec", "-it", provider.get_container_name("lab-a", "r1"), "/bin/bash"]


@pytest.mark.asyncio
async def test_get_console_command_returns_ssh_for_ssh_kind(sync_to_thread, monkeypatch):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    docker_client.containers.get.return_value = _container(
        container_id="id-1",
        node_name="r1",
        lab_id="lab-a",
        kind="iosv",
        status="running",
        ips=["192.0.2.10"],
    )

    monkeypatch.setattr(docker_mod, "get_console_method", lambda _kind: "ssh")
    monkeypatch.setattr(docker_mod, "get_console_credentials", lambda _kind: ("admin", "secret"))

    result = await provider.get_console_command("lab-a", "r1", Path("/tmp/workspace"))

    assert result == [
        "sshpass",
        "-p",
        "secret",
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "admin@192.0.2.10",
    ]


@pytest.mark.asyncio
async def test_get_console_command_returns_none_for_ssh_without_ip(sync_to_thread, monkeypatch):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    docker_client.containers.get.return_value = _container(
        container_id="id-1",
        node_name="r1",
        lab_id="lab-a",
        kind="iosv",
        status="running",
        ips=[],
    )
    monkeypatch.setattr(docker_mod, "get_console_method", lambda _kind: "ssh")

    result = await provider.get_console_command("lab-a", "r1", Path("/tmp/workspace"))

    assert result is None


@pytest.mark.asyncio
async def test_get_console_command_returns_none_when_stopped_or_missing(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client

    stopped = _container(container_id="id-1", node_name="r1", lab_id="lab-a", status="exited")
    docker_client.containers.get.side_effect = [stopped, NotFound("missing")]

    first = await provider.get_console_command("lab-a", "r1", Path("/tmp/workspace"))
    second = await provider.get_console_command("lab-a", "r2", Path("/tmp/workspace"))

    assert first is None
    assert second is None


@pytest.mark.asyncio
async def test_discover_labs_groups_nodes_by_lab_and_skips_non_labeled(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client

    c1 = _container(container_id="id-1", node_name="r1", lab_id="lab-a")
    c2 = _container(container_id="id-2", node_name="r2", lab_id="lab-a")
    c3 = _container(container_id="id-3", node_name="r3", lab_id="lab-b")
    c4 = _container(container_id="id-4", node_name="ignored", lab_id=None)
    docker_client.containers.list.return_value = [c1, c2, c3, c4]

    discovered = await provider.discover_labs()

    assert sorted(discovered.keys()) == ["lab-a", "lab-b"]
    assert sorted(node.name for node in discovered["lab-a"]) == ["r1", "r2"]
    assert [node.name for node in discovered["lab-b"]] == ["r3"]


@pytest.mark.asyncio
async def test_discover_labs_returns_empty_on_error(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    docker_client.containers.list.side_effect = RuntimeError("docker unavailable")

    discovered = await provider.discover_labs()

    assert discovered == {}


@pytest.mark.asyncio
async def test_cleanup_orphan_containers_removes_only_orphans(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    provider._local_network = SimpleNamespace(cleanup_lab=AsyncMock())
    provider._cleanup_orphan_vlans = MagicMock()

    live = _container(container_id="id-live", node_name="r1", lab_id="lab-live")
    orphan = _container(container_id="id-orphan", node_name="r2", lab_id="lab-dead")
    unknown = _container(container_id="id-unknown", node_name="r3", lab_id=None)
    docker_client.containers.list.return_value = [live, orphan, unknown]

    removed = await provider.cleanup_orphan_containers({"lab-live"})

    assert removed == [orphan.name]
    live.remove.assert_not_called()
    orphan.remove.assert_called_once_with(force=True)
    provider.local_network.cleanup_lab.assert_awaited_once_with("lab-dead")
    provider._cleanup_orphan_vlans.assert_called_once()
    assert provider._cleanup_orphan_vlans.call_args.args[0] == "lab-dead"
    assert provider._cleanup_orphan_vlans.call_args.args[1].name == "lab-dead"


@pytest.mark.asyncio
async def test_cleanup_orphan_containers_handles_errors(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    provider._local_network = SimpleNamespace(cleanup_lab=AsyncMock())

    orphan = _container(container_id="id-orphan", node_name="r2", lab_id="lab-dead")
    orphan.remove.side_effect = RuntimeError("cannot remove")
    docker_client.containers.list.return_value = [orphan]

    removed = await provider.cleanup_orphan_containers({"lab-live"})

    assert removed == []
    provider.local_network.cleanup_lab.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_lab_volumes_removes_each_volume_and_skips_remove_api_errors(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client

    v1 = SimpleNamespace(name="v1", remove=MagicMock())
    v2 = SimpleNamespace(name="v2", remove=MagicMock(side_effect=_api_error(409, "in use")))
    docker_client.volumes.list.return_value = [v1, v2]

    removed = await provider._cleanup_lab_volumes("lab-a")

    assert removed == 1
    docker_client.volumes.list.assert_called_once_with(filters={"label": f"{LABEL_LAB_ID}=lab-a"})
    v1.remove.assert_called_once_with(force=True)
    v2.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_cleanup_lab_volumes_returns_zero_when_listing_fails(sync_to_thread):
    provider = DockerProvider()
    docker_client = MagicMock()
    provider._docker = docker_client
    docker_client.volumes.list.side_effect = _api_error(500, "daemon down")

    removed = await provider._cleanup_lab_volumes("lab-a")

    assert removed == 0


@pytest.mark.asyncio
async def test_extract_all_container_configs_delegates_to_extract_module(monkeypatch):
    provider = DockerProvider()
    provider._docker = MagicMock()
    provider._run_ssh_command = AsyncMock()

    observed: dict[str, object] = {}

    async def _fake_extract_all(**kwargs):
        observed.update(kwargs)
        return [("r1", "hostname r1")]

    monkeypatch.setattr(docker_mod, "extract_all_container_configs", _fake_extract_all)

    result = await provider._extract_all_container_configs("lab-a", Path("/tmp/workspace"))

    assert result == [("r1", "hostname r1")]
    assert observed["lab_id"] == "lab-a"
    assert observed["docker_client"] is provider.docker
    assert observed["lab_prefix"] == provider._lab_prefix("lab-a")
    assert observed["provider_name"] == provider.name
    assert observed["get_container_ips_func"] == provider._get_container_ips
    assert observed["run_ssh_command_func"] == provider._run_ssh_command


@pytest.mark.asyncio
async def test_extract_config_wrappers_delegate_to_extract_helpers(monkeypatch):
    provider = DockerProvider()
    provider._run_ssh_command = AsyncMock()
    container = _container(container_id="id-1", node_name="r1", lab_id="lab-a")
    called: dict[str, tuple] = {}

    async def _fake_extract_docker(cont, cmd, log_name):
        called["docker"] = (cont, cmd, log_name)
        return "docker-config"

    async def _fake_extract_ssh(cont, kind, cmd, log_name, get_ips, run_ssh):
        called["ssh"] = (cont, kind, cmd, log_name, get_ips, run_ssh)
        return "ssh-config"

    async def _fake_extract_nvram(container_name, workspace):
        called["nvram"] = (container_name, workspace)
        return "nvram-config"

    monkeypatch.setattr(docker_mod, "extract_config_via_docker", _fake_extract_docker)
    monkeypatch.setattr(docker_mod, "extract_config_via_ssh", _fake_extract_ssh)
    monkeypatch.setattr(docker_mod, "extract_config_via_nvram", _fake_extract_nvram)

    docker_result = await provider._extract_config_via_docker(container, "show run", "r1")
    ssh_result = await provider._extract_config_via_ssh(container, "iosv", "show run", "r1")
    nvram_result = await provider._extract_config_via_nvram("ctr-r1", Path("/tmp/workspace"))

    assert docker_result == "docker-config"
    assert ssh_result == "ssh-config"
    assert nvram_result == "nvram-config"
    assert called["docker"] == (container, "show run", "r1")
    assert called["ssh"][:4] == (container, "iosv", "show run", "r1")
    assert called["ssh"][4] == provider._get_container_ips
    assert called["ssh"][5] == provider._run_ssh_command
    assert called["nvram"] == ("ctr-r1", Path("/tmp/workspace"))


@pytest.mark.asyncio
async def test_extract_all_ceos_configs_alias_calls_extract_all_container_configs():
    provider = DockerProvider()
    provider._extract_all_container_configs = AsyncMock(return_value=[("ceos1", "cfg")])

    result = await provider._extract_all_ceos_configs("lab-a", Path("/tmp/workspace"))

    assert result == [("ceos1", "cfg")]
    provider._extract_all_container_configs.assert_awaited_once_with("lab-a", Path("/tmp/workspace"))
