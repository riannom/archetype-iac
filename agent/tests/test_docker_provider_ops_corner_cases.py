from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound, APIError

from agent.config import settings
from agent.providers.base import StatusResult
from agent.providers.docker import (
    DOCKER_OP_MAX_RETRIES,
    DOCKER_OP_RETRY_BASE_SECONDS,
    DockerProvider,
)
from agent.schemas import DeployNode, DeployTopology


def _run(coro):
    return asyncio.run(coro)


def _api_conflict(message: str = "already exists") -> APIError:
    response = MagicMock()
    response.status_code = 409
    return APIError(message, response=response)


def _api_error(status_code: int, message: str = "api error") -> APIError:
    response = MagicMock()
    response.status_code = status_code
    return APIError(message, response=response)


def test_create_lab_networks_uses_sanitized_prefix_and_labels(monkeypatch):
    provider = DockerProvider()
    docker_client = MagicMock()
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.networks.create = MagicMock()
    provider._docker = docker_client

    lab_id = "lab$with#bad!chars-and-very-very-long-name"
    _run(provider._create_lab_networks(lab_id, max_interfaces=1))

    args, kwargs = docker_client.networks.create.call_args
    assert kwargs["name"].startswith(provider._lab_network_prefix(lab_id))
    assert kwargs["labels"]["archetype.lab_id"] == lab_id


def test_create_lab_networks_reuses_valid_network_on_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    existing = MagicMock()
    existing.attrs = {
        "Driver": "archetype-ovs",
        "Labels": {
            "archetype.lab_id": "lab1",
            "archetype.provider": "docker",
            "archetype.type": "lab-interface",
        },
        "Options": {
            "lab_id": "lab1",
            "interface_name": "eth0",
        },
        "Containers": {},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [NotFound("missing"), existing]
    docker_client.networks.create.side_effect = _api_conflict()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    result = _run(provider._create_lab_networks("lab1", max_interfaces=0))

    assert result == {"eth0": "lab1-eth0"}
    assert docker_client.networks.create.call_count == 1
    existing.remove.assert_not_called()


def test_create_lab_networks_recreates_stale_unused_network_on_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    stale = MagicMock()
    stale.attrs = {
        "Driver": "bridge",
        "Labels": {},
        "Options": {},
        "Containers": {},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [NotFound("missing"), stale]
    docker_client.networks.create.side_effect = [_api_conflict(), MagicMock()]
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    result = _run(provider._create_lab_networks("lab1", max_interfaces=0))

    assert result == {"eth0": "lab1-eth0"}
    stale.remove.assert_called_once()
    assert docker_client.networks.create.call_count == 2


def test_create_lab_networks_refuses_delete_for_in_use_stale_network_on_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    stale_in_use = MagicMock()
    stale_in_use.attrs = {
        "Driver": "bridge",
        "Labels": {},
        "Options": {},
        "Containers": {"cid1": {"Name": "n1"}},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [NotFound("missing"), stale_in_use]
    docker_client.networks.create.side_effect = _api_conflict()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    with pytest.raises(RuntimeError, match="active endpoints"):
        _run(provider._create_lab_networks("lab1", max_interfaces=0))

    stale_in_use.remove.assert_not_called()


def test_retry_docker_call_retries_transient_then_succeeds(monkeypatch):
    provider = DockerProvider()
    attempts = {"count": 0}
    sleep_delays: list[float] = []

    def _flaky_call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _api_error(503, "service unavailable")
        return "ok"

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def _fake_sleep(delay: float):
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    result = _run(provider._retry_docker_call("flaky op", _flaky_call))

    assert result == "ok"
    assert attempts["count"] == 3
    assert sleep_delays == pytest.approx(
        [DOCKER_OP_RETRY_BASE_SECONDS, DOCKER_OP_RETRY_BASE_SECONDS * 2]
    )


def test_retry_docker_call_does_not_retry_non_transient(monkeypatch):
    provider = DockerProvider()
    attempts = {"count": 0}

    def _permanent_failure():
        attempts["count"] += 1
        raise RuntimeError("permanent failure")

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def _sleep_should_not_run(_delay: float):
        raise AssertionError("sleep should not be called for non-transient failures")

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(asyncio, "sleep", _sleep_should_not_run)

    with pytest.raises(RuntimeError, match="permanent failure"):
        _run(provider._retry_docker_call("non transient op", _permanent_failure))

    assert attempts["count"] == 1


def test_retry_docker_call_stops_after_max_retries(monkeypatch):
    provider = DockerProvider()
    attempts = {"count": 0}
    sleep_delays: list[float] = []

    def _always_transient_failure():
        attempts["count"] += 1
        raise _api_error(503, "service unavailable")

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def _fake_sleep(delay: float):
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    with pytest.raises(APIError):
        _run(provider._retry_docker_call("always failing op", _always_transient_failure))

    assert attempts["count"] == DOCKER_OP_MAX_RETRIES
    expected_delays = [
        DOCKER_OP_RETRY_BASE_SECONDS * (2 ** attempt)
        for attempt in range(DOCKER_OP_MAX_RETRIES - 1)
    ]
    assert sleep_delays == pytest.approx(expected_delays)


def test_create_lab_networks_recreates_stale_existing_network_without_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    stale = MagicMock()
    stale.attrs = {
        "Driver": "bridge",
        "Labels": {},
        "Options": {},
        "Containers": {},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [stale, stale]
    docker_client.networks.create = MagicMock(return_value=MagicMock())
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    result = _run(provider._create_lab_networks("lab1", max_interfaces=0))

    assert result == {"eth0": "lab1-eth0"}
    stale.remove.assert_called_once()
    assert docker_client.networks.create.call_count == 1


def test_resolve_conflicting_lab_network_rolls_back_when_recreate_fails(monkeypatch):
    provider = DockerProvider()

    stale = MagicMock()
    stale.attrs = {
        "Driver": "bridge",
        "Labels": {"legacy": "1"},
        "Options": {"com.docker.network.bridge.name": "br-test"},
        "Containers": {},
    }

    docker_client = MagicMock()
    docker_client.networks.get.return_value = stale
    docker_client.networks.create.side_effect = [_api_error(400, "bad request"), MagicMock()]
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    with pytest.raises(APIError):
        _run(provider._resolve_conflicting_lab_network("lab1-eth0", "lab1", "eth0"))

    stale.remove.assert_called_once()
    assert docker_client.networks.create.call_count == 2

    recreate_kwargs = docker_client.networks.create.call_args_list[0].kwargs
    rollback_kwargs = docker_client.networks.create.call_args_list[1].kwargs
    assert recreate_kwargs["driver"] == "archetype-ovs"
    assert rollback_kwargs["name"] == "lab1-eth0"
    assert rollback_kwargs["driver"] == "bridge"
    assert rollback_kwargs["labels"] == {"legacy": "1"}
    assert rollback_kwargs["options"] == {"com.docker.network.bridge.name": "br-test"}


@pytest.mark.asyncio
async def test_create_lab_networks_serializes_concurrent_calls_per_lab(monkeypatch):
    provider = DockerProvider()

    active_calls = 0
    max_concurrent_calls = 0

    async def _tracked_prune(_lab_id: str):
        nonlocal active_calls, max_concurrent_calls
        active_calls += 1
        max_concurrent_calls = max(max_concurrent_calls, active_calls)
        await asyncio.sleep(0.05)
        active_calls -= 1

    provider._prune_legacy_lab_networks = AsyncMock(side_effect=_tracked_prune)

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.networks.create = MagicMock()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    await asyncio.gather(
        provider._create_lab_networks("lab-lock", max_interfaces=0),
        provider._create_lab_networks("lab-lock", max_interfaces=0),
    )

    # The per-lab lock should keep this critical section non-overlapping.
    assert max_concurrent_calls == 1


def test_local_cleanup_runs_orphan_veth_cleanup(monkeypatch):
    from agent.network import local as local_mod

    called = {"count": 0}

    class _FakeCleanupMgr:
        async def cleanup_orphaned_veths(self):
            called["count"] += 1

    monkeypatch.setattr("agent.network.cleanup.NetworkCleanupManager", _FakeCleanupMgr)

    mgr = local_mod.LocalNetworkManager()
    mgr._links = {}

    _run(mgr.cleanup_lab("lab1"))
    assert called["count"] == 1


def test_recover_stale_networks_defaults_to_eth1_when_label_missing(monkeypatch):
    provider = DockerProvider()

    net_eth1 = MagicMock()
    net_eth1.name = "lab1-eth1"
    net_eth2 = MagicMock()
    net_eth2.name = "lab1-eth2"

    docker_client = MagicMock()
    docker_client.networks.list.return_value = [net_eth1, net_eth2]
    docker_client.networks.get.side_effect = lambda name: {
        "lab1-eth1": net_eth1,
        "lab1-eth2": net_eth2,
    }[name]

    provider._docker = docker_client

    container = MagicMock()
    container.name = "archetype-lab1-n1"
    container.labels = {}
    container.attrs = {
        "NetworkSettings": {
            "Networks": {
                "lab1-eth1": {},
                "lab1-eth2": {},
            }
        }
    }

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    _run(provider._recover_stale_networks(container, "lab1"))

    assert net_eth1.connect.call_count == 1
    assert net_eth2.connect.call_count == 0


def test_deploy_fails_on_start_failure(monkeypatch, tmp_path):
    provider = DockerProvider()

    provider._recover_stale_network = AsyncMock(return_value={})
    provider._validate_images = MagicMock(return_value=[])
    provider._ensure_directories = AsyncMock(return_value=None)
    provider._create_containers = AsyncMock(return_value={"n1": MagicMock(name="c1")})
    provider._start_containers = AsyncMock(return_value=["n1"])
    provider._create_links = AsyncMock(return_value=0)
    provider._capture_container_vlans = AsyncMock(return_value=None)
    provider._wait_for_readiness = AsyncMock(return_value={})
    provider.status = AsyncMock(return_value=StatusResult(lab_exists=True, nodes=[]))

    topology = DeployTopology(
        nodes=[DeployNode(name="n1", kind="linux", interface_count=1)],
        links=[],
    )

    result = _run(provider.deploy("lab1", topology, tmp_path))
    assert result.success is False
    assert "Failed to start" in (result.error or "")


def test_destroy_cleans_networks_before_local_and_ovs(monkeypatch, tmp_path):
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_ovs = settings.enable_ovs
    settings.enable_ovs_plugin = True
    settings.enable_ovs = True

    provider = DockerProvider()

    calls: list[str] = []

    provider._delete_lab_networks = AsyncMock(side_effect=lambda lab_id: calls.append("networks") or 0)
    provider._local_network = MagicMock()
    provider._local_network.cleanup_lab = AsyncMock(side_effect=lambda lab_id: calls.append("local") or {})
    provider._ovs_manager = MagicMock()
    provider._ovs_manager._initialized = True
    provider._ovs_manager.cleanup_lab = AsyncMock(side_effect=lambda lab_id: calls.append("ovs") or {})

    docker_client = MagicMock()
    docker_client.containers.list.return_value = []
    provider._docker = docker_client

    provider._cleanup_lab_volumes = AsyncMock(return_value=0)

    try:
        _run(provider.destroy("lab1", tmp_path))
    finally:
        settings.enable_ovs_plugin = original_enable_ovs_plugin
        settings.enable_ovs = original_enable_ovs

    assert calls[:3] == ["networks", "local", "ovs"]


def test_cleanup_lab_resources_if_empty_skips_cleanup_when_containers_remain():
    provider = DockerProvider()

    docker_client = MagicMock()
    docker_client.containers.list.return_value = [MagicMock()]
    provider._docker = docker_client

    provider._delete_lab_networks = AsyncMock(return_value=9)
    provider._local_network = MagicMock()
    provider._local_network.cleanup_lab = AsyncMock()
    provider._ovs_manager = MagicMock()
    provider._ovs_manager._initialized = True
    provider._ovs_manager.cleanup_lab = AsyncMock()
    provider._remove_vlan_file = MagicMock()

    result = _run(provider.cleanup_lab_resources_if_empty("lab1"))

    assert result["cleaned"] is False
    assert result["remaining"] == 1
    provider._delete_lab_networks.assert_not_awaited()
    provider._local_network.cleanup_lab.assert_not_awaited()
    provider._ovs_manager.cleanup_lab.assert_not_awaited()
    provider._remove_vlan_file.assert_not_called()


def test_cleanup_lab_resources_if_empty_with_workspace_none_skips_vlan_file(
    monkeypatch,
):
    provider = DockerProvider()
    monkeypatch.setattr(settings, "enable_ovs", False)

    docker_client = MagicMock()
    docker_client.containers.list.return_value = []
    provider._docker = docker_client

    provider._delete_lab_networks = AsyncMock(return_value=2)
    provider._local_network = MagicMock()
    provider._local_network.cleanup_lab = AsyncMock()
    provider._remove_vlan_file = MagicMock()
    provider._vlan_allocations["lab1"] = [100]
    provider._next_vlan["lab1"] = 101

    result = _run(provider.cleanup_lab_resources_if_empty("lab1", workspace=None))

    assert result["cleaned"] is True
    assert result["networks_deleted"] == 2
    assert result["local_cleanup"] is True
    assert result["ovs_cleanup"] is False
    provider._remove_vlan_file.assert_not_called()
    assert "lab1" not in provider._vlan_allocations
    assert "lab1" not in provider._next_vlan


def test_cleanup_lab_resources_if_empty_runs_ovs_cleanup_when_initialized(
    monkeypatch,
    tmp_path,
):
    provider = DockerProvider()
    monkeypatch.setattr(settings, "enable_ovs", True)

    docker_client = MagicMock()
    docker_client.containers.list.return_value = []
    provider._docker = docker_client

    provider._delete_lab_networks = AsyncMock(return_value=0)
    provider._local_network = MagicMock()
    provider._local_network.cleanup_lab = AsyncMock()
    provider._ovs_manager = MagicMock()
    provider._ovs_manager._initialized = True
    provider._ovs_manager.cleanup_lab = AsyncMock()
    provider._remove_vlan_file = MagicMock()

    result = _run(provider.cleanup_lab_resources_if_empty("lab1", tmp_path))

    assert result["cleaned"] is True
    assert result["ovs_cleanup"] is True
    provider._ovs_manager.cleanup_lab.assert_awaited_once_with("lab1")
    provider._remove_vlan_file.assert_called_once_with("lab1", tmp_path)
