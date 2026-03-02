from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import APIError, ImageNotFound, NotFound

from agent.config import settings
from agent.providers.base import NodeStatus
from agent.providers.docker import DockerProvider


def _api_error(status_code: int, message: str = "api error") -> APIError:
    response = MagicMock()
    response.status_code = status_code
    return APIError(message, response=response)


@pytest.fixture(autouse=True)
def _sync_to_thread_and_sleep(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_start_node_success_repairs_endpoints_and_fixes_interfaces(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)

    provider = DockerProvider()
    container = SimpleNamespace(
        status="running",
        labels={"kind": "linux"},
        start=MagicMock(),
        reload=MagicMock(),
    )
    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = container
    provider._fix_interface_names = AsyncMock(return_value={"fixed": 2})

    plugin = SimpleNamespace(
        repair_endpoints=AsyncMock(
            return_value=[
                {"status": "repaired"},
                {"status": "noop"},
            ]
        )
    )
    monkeypatch.setattr("agent.providers.docker.get_docker_ovs_plugin", lambda: plugin)

    result = await provider.start_node("lab1", "n1", tmp_path)

    assert result.success is True
    assert result.new_status == NodeStatus.RUNNING
    assert "Started container archetype-lab1-n1" in result.stdout
    assert "repaired 1 endpoints" in result.stdout
    assert "fixed 2 interfaces" in result.stdout
    plugin.repair_endpoints.assert_awaited_once_with("lab1", "archetype-lab1-n1")
    provider._fix_interface_names.assert_awaited_once_with("archetype-lab1-n1", "lab1")


@pytest.mark.asyncio
async def test_start_node_stale_network_recovery_success(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)

    provider = DockerProvider()
    api_err = APIError("network not found")
    container = SimpleNamespace(
        status="running",
        labels={},
        start=MagicMock(side_effect=[api_err, None]),
        reload=MagicMock(),
    )
    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = container
    provider._recover_stale_networks = AsyncMock(return_value=True)

    result = await provider.start_node("lab1", "n1", tmp_path, repair_endpoints=False, fix_interfaces=False)

    assert result.success is True
    assert result.new_status == NodeStatus.RUNNING
    provider._recover_stale_networks.assert_awaited_once_with(container, "lab1")
    assert container.start.call_count == 2


@pytest.mark.asyncio
async def test_start_node_stale_network_recovery_failure_returns_api_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)

    provider = DockerProvider()
    container = SimpleNamespace(
        status="created",
        labels={},
        start=MagicMock(side_effect=APIError("network not found")),
        reload=MagicMock(),
    )
    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = container
    provider._recover_stale_networks = AsyncMock(return_value=False)

    result = await provider.start_node("lab1", "n1", tmp_path, repair_endpoints=False, fix_interfaces=False)

    assert result.success is False
    assert "Docker API error" in (result.error or "")
    provider._recover_stale_networks.assert_awaited_once_with(container, "lab1")


@pytest.mark.asyncio
async def test_start_node_not_found_and_non_network_api_error_paths(monkeypatch, tmp_path):
    provider = DockerProvider()
    provider._docker = MagicMock()

    provider.docker.containers.get.side_effect = NotFound("missing")
    result = await provider.start_node("lab1", "n1", tmp_path)
    assert result.success is False
    assert "not found" in (result.error or "").lower()

    provider.docker.containers.get.side_effect = None
    provider.docker.containers.get.return_value = SimpleNamespace(
        status="created",
        labels={},
        start=MagicMock(side_effect=_api_error(500, "permission denied")),
        reload=MagicMock(),
    )
    result = await provider.start_node("lab1", "n1", tmp_path)
    assert result.success is False
    assert "Docker API error" in (result.error or "")


@pytest.mark.asyncio
async def test_start_node_warn_paths_when_repair_and_fix_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)

    provider = DockerProvider()
    container = SimpleNamespace(
        status="running",
        labels={},
        start=MagicMock(),
        reload=MagicMock(),
    )
    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = container
    provider._fix_interface_names = AsyncMock(side_effect=RuntimeError("fix failed"))

    plugin = SimpleNamespace(repair_endpoints=AsyncMock(side_effect=RuntimeError("repair failed")))
    monkeypatch.setattr("agent.providers.docker.get_docker_ovs_plugin", lambda: plugin)

    result = await provider.start_node("lab1", "n1", tmp_path)
    assert result.success is True


@pytest.mark.asyncio
async def test_remove_container_running_success_and_vlan_cleanup(monkeypatch, tmp_path):
    provider = DockerProvider()
    container = SimpleNamespace(
        status="running",
        stop=MagicMock(),
        remove=MagicMock(),
    )
    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = container
    provider._vlan_allocations["lab1"] = {"n1": [100], "n2": [200]}

    cleared: list[str] = []
    monkeypatch.setattr("agent.readiness.clear_post_boot_state", lambda name: cleared.append(name))

    await provider._remove_container("lab1", "n1", tmp_path)

    container.stop.assert_called_once()
    container.remove.assert_called_once_with(force=True, v=True)
    assert cleared == ["archetype-lab1-n1"]
    assert "n1" not in provider._vlan_allocations["lab1"]
    assert "n2" in provider._vlan_allocations["lab1"]


@pytest.mark.asyncio
async def test_remove_container_stop_and_remove_error_paths(monkeypatch, tmp_path):
    provider = DockerProvider()
    provider._docker = MagicMock()

    running = SimpleNamespace(
        status="running",
        stop=MagicMock(side_effect=RuntimeError("stop failed")),
        remove=MagicMock(),
    )
    provider.docker.containers.get.return_value = running
    with pytest.raises(RuntimeError, match="stop failed"):
        await provider._remove_container("lab1", "n1", tmp_path)

    stopped = SimpleNamespace(
        status="exited",
        stop=MagicMock(),
        remove=MagicMock(side_effect=RuntimeError("remove failed")),
    )
    provider.docker.containers.get.return_value = stopped
    with pytest.raises(RuntimeError, match="remove failed"):
        await provider._remove_container("lab1", "n1", tmp_path)


@pytest.mark.asyncio
async def test_stop_node_success_notfound_and_api_error(tmp_path):
    provider = DockerProvider()
    provider._remove_container = AsyncMock(return_value=None)

    ok = await provider.stop_node("lab1", "n1", tmp_path)
    assert ok.success is True
    assert ok.new_status == NodeStatus.STOPPED

    provider._remove_container = AsyncMock(side_effect=NotFound("missing"))
    already_gone = await provider.stop_node("lab1", "n1", tmp_path)
    assert already_gone.success is True
    assert "already removed" in already_gone.stdout

    provider._remove_container = AsyncMock(side_effect=_api_error(500, "boom"))
    err = await provider.stop_node("lab1", "n1", tmp_path)
    assert err.success is False
    assert "Docker API error" in (err.error or "")


@pytest.mark.asyncio
async def test_create_node_image_missing_returns_error(tmp_path):
    provider = DockerProvider()
    provider._docker = MagicMock()
    provider.docker.images.get.side_effect = ImageNotFound("missing")

    result = await provider.create_node(
        "lab1",
        "n1",
        "linux",
        tmp_path,
        image="vendor/missing:1",
    )

    assert result.success is False
    assert "image not found" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_create_node_existing_running_short_circuit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    monkeypatch.setattr("agent.providers.docker.get_config_by_device", lambda _kind: None)

    provider = DockerProvider()
    existing = SimpleNamespace(status="running")
    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = existing

    result = await provider.create_node("lab1", "n1", "linux", tmp_path)

    assert result.success is True
    assert result.new_status == NodeStatus.RUNNING
    assert "already running" in result.stdout


@pytest.mark.asyncio
async def test_create_node_non_ovs_removes_stopped_and_creates(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    monkeypatch.setattr("agent.providers.docker.get_config_by_device", lambda _kind: None)

    provider = DockerProvider()
    existing = SimpleNamespace(status="exited", remove=MagicMock())
    created = SimpleNamespace(short_id="abc123")

    provider._docker = MagicMock()
    provider.docker.containers.get.return_value = existing
    provider.docker.containers.create.return_value = created
    provider._create_container_config = lambda *_a, **_k: {"image": "alpine:latest"}  # type: ignore[method-assign]

    result = await provider.create_node("lab1", "n1", "linux", tmp_path, interface_count=3)

    assert result.success is True
    assert result.new_status == NodeStatus.STOPPED
    existing.remove.assert_called_once_with(force=True)
    provider.docker.containers.create.assert_called_once()
    kwargs = provider.docker.containers.create.call_args.kwargs
    assert kwargs["network_mode"] == "none"


@pytest.mark.asyncio
async def test_create_node_ovs_plugin_has_mgmt_reserved(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)
    monkeypatch.setattr(
        "agent.providers.docker.get_config_by_device",
        lambda _kind: SimpleNamespace(default_image=None, management_interface="Mgmt0", reserved_nics=2),
    )

    provider = DockerProvider()
    created = SimpleNamespace(short_id="c1")
    provider._docker = MagicMock()
    provider.docker.containers.get.side_effect = NotFound("missing")
    provider.docker.containers.create.return_value = created
    provider._create_lab_networks = AsyncMock(return_value={})
    provider._attach_container_to_networks = AsyncMock(return_value=[])
    provider._setup_ceos_directories = MagicMock()
    provider._create_container_config = lambda *_a, **_k: {"image": "alpine:latest"}  # type: ignore[method-assign]

    result = await provider.create_node("lab1", "n1", "ceos", tmp_path, interface_count=3)

    assert result.success is True
    provider._setup_ceos_directories.assert_called_once()
    provider._create_lab_networks.assert_awaited_once_with("lab1", max_interfaces=5)
    kwargs = provider.docker.containers.create.call_args.kwargs
    assert kwargs["network"].endswith("-eth0")
    _, attach_kwargs = provider._attach_container_to_networks.call_args
    assert attach_kwargs["interface_count"] == 5
    assert attach_kwargs["start_index"] == 1


@pytest.mark.asyncio
async def test_create_node_ovs_plugin_without_mgmt_and_cjunos_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)
    monkeypatch.setattr(
        "agent.providers.docker.get_config_by_device",
        lambda _kind: SimpleNamespace(default_image=None, management_interface=None, reserved_nics=0),
    )

    provider = DockerProvider()
    created = SimpleNamespace(short_id="c2")
    provider._docker = MagicMock()
    provider.docker.containers.get.side_effect = NotFound("missing")
    provider.docker.containers.create.return_value = created
    provider._create_lab_networks = AsyncMock(return_value={})
    provider._attach_container_to_networks = AsyncMock(return_value=[])
    provider._setup_cjunos_directories = MagicMock()
    provider._create_container_config = lambda *_a, **_k: {"image": "alpine:latest"}  # type: ignore[method-assign]

    result = await provider.create_node("lab1", "n1", "juniper_cjunos", tmp_path, interface_count=3)

    assert result.success is True
    provider._setup_cjunos_directories.assert_called_once()
    provider._create_lab_networks.assert_awaited_once_with("lab1", max_interfaces=3)
    kwargs = provider.docker.containers.create.call_args.kwargs
    assert kwargs["network"].endswith("-eth1")
    _, attach_kwargs = provider._attach_container_to_networks.call_args
    assert attach_kwargs["interface_count"] == 2
    assert attach_kwargs["start_index"] == 2


@pytest.mark.asyncio
async def test_create_node_apierror_and_generic_exception_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    monkeypatch.setattr("agent.providers.docker.get_config_by_device", lambda _kind: None)

    provider = DockerProvider()
    provider._docker = MagicMock()
    provider.docker.containers.get.side_effect = NotFound("missing")
    provider._create_container_config = lambda *_a, **_k: {"image": "alpine:latest"}  # type: ignore[method-assign]

    provider.docker.containers.create.side_effect = _api_error(500, "create failed")
    api_err = await provider.create_node("lab1", "n1", "linux", tmp_path)
    assert api_err.success is False
    assert "Docker API error" in (api_err.error or "")

    provider._create_container_config = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cfg explode"))  # type: ignore[method-assign]
    generic_err = await provider.create_node("lab1", "n1", "linux", tmp_path)
    assert generic_err.success is False
    assert "Container creation failed" in (generic_err.error or "")


@pytest.mark.asyncio
async def test_cleanup_lab_resources_if_empty_error_and_cleanup_warning_paths(monkeypatch, tmp_path):
    provider = DockerProvider()

    provider._retry_docker_call = AsyncMock(side_effect=RuntimeError("list failed"))
    err_result = await provider.cleanup_lab_resources_if_empty("lab1", tmp_path)
    assert err_result["cleaned"] is False
    assert "list failed" in (err_result["error"] or "")

    provider._retry_docker_call = AsyncMock(return_value=[])
    provider._delete_lab_networks = AsyncMock(return_value=1)
    provider._local_network = SimpleNamespace(cleanup_lab=AsyncMock(side_effect=RuntimeError("local fail")))
    monkeypatch.setattr(settings, "enable_ovs", False)

    local_fail = await provider.cleanup_lab_resources_if_empty("lab1", tmp_path)
    assert local_fail["cleaned"] is True
    assert local_fail["local_cleanup"] is False

    provider._retry_docker_call = AsyncMock(return_value=[])
    provider._delete_lab_networks = AsyncMock(return_value=0)
    provider._local_network = SimpleNamespace(cleanup_lab=AsyncMock(return_value=None))
    provider._ovs_manager = SimpleNamespace(_initialized=True, cleanup_lab=AsyncMock(side_effect=RuntimeError("ovs fail")))
    monkeypatch.setattr(settings, "enable_ovs", True)

    ovs_fail = await provider.cleanup_lab_resources_if_empty("lab1", tmp_path)
    assert ovs_fail["cleaned"] is True
    assert ovs_fail["local_cleanup"] is True
    assert ovs_fail["ovs_cleanup"] is False


@pytest.mark.asyncio
async def test_destroy_node_notfound_cleanup_error_and_exception_paths(tmp_path):
    provider = DockerProvider()

    provider._remove_container = AsyncMock(side_effect=NotFound("missing"))
    provider.cleanup_lab_resources_if_empty = AsyncMock(return_value={"error": "check failed"})
    ok = await provider.destroy_node("lab1", "n1", tmp_path)
    assert ok.success is True
    assert ok.new_status == NodeStatus.STOPPED

    provider._remove_container = AsyncMock(return_value=None)
    provider.cleanup_lab_resources_if_empty = AsyncMock(side_effect=_api_error(500, "boom"))
    api_err = await provider.destroy_node("lab1", "n1", tmp_path)
    assert api_err.success is False
    assert "Docker API error" in (api_err.error or "")

    provider.cleanup_lab_resources_if_empty = AsyncMock(side_effect=RuntimeError("explode"))
    generic_err = await provider.destroy_node("lab1", "n1", tmp_path)
    assert generic_err.success is False
    assert "Container destroy failed" in (generic_err.error or "")
