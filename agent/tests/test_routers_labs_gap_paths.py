from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import docker
import pytest
from fastapi import HTTPException

import agent.routers.labs as labs_mod
from agent.schemas import (
    CleanupLabOrphansRequest,
    CleanupOrphansRequest,
    CleanupWorkspacesRequest,
    DockerPruneRequest,
    NodeReconcileRequest,
    NodeReconcileResult,
    NodeReconcileTarget,
    NodeStatus,
    RuntimeIdentityBackfillRequest,
    UpdateConfigRequest,
)


def _container(
    name: str = "archetype-lab1-r1",
    status: str = "running",
    labels: dict | None = None,
) -> MagicMock:
    c = MagicMock()
    c.name = name
    c.status = status
    c.labels = labels or {}
    return c


def _docker_client_for_get(container_or_exc) -> MagicMock:
    client = MagicMock()
    if isinstance(container_or_exc, BaseException):
        client.containers.get.side_effect = container_or_exc
    else:
        client.containers.get.return_value = container_or_exc
    return client


def _status_node(name: str, status=NodeStatus.RUNNING) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        status=status,
        container_id=f"id-{name}",
        runtime_id=f"runtime-{name}",
        node_definition_id=f"node-def-{name}",
        image=f"img-{name}",
        ip_addresses=["10.0.0.1"],
    )


@pytest.mark.asyncio
async def test_lab_status_merges_docker_and_libvirt(tmp_path):
    docker_provider = MagicMock()
    docker_provider.status = AsyncMock(
        return_value=SimpleNamespace(
            nodes=[_status_node("r1")],
            error="docker-warn",
        )
    )
    libvirt_provider = MagicMock()
    libvirt_provider.status = AsyncMock(
        return_value=SimpleNamespace(
            nodes=[_status_node("vm1", status=NodeStatus.STOPPED)],
            error=None,
        )
    )

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: docker_provider if p == "docker" else libvirt_provider,
        ):
            with patch.object(labs_mod, "provider_status_to_schema", side_effect=lambda s: s):
                result = await labs_mod.lab_status("lab1")

    assert len(result.nodes) == 2
    assert {n.name for n in result.nodes} == {"r1", "vm1"}
    assert "Docker: docker-warn" in result.error


@pytest.mark.asyncio
async def test_lab_status_handles_docker_query_exception(tmp_path):
    docker_provider = MagicMock()
    docker_provider.status = AsyncMock(side_effect=RuntimeError("docker down"))

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: docker_provider if p == "docker" else None,
        ):
            result = await labs_mod.lab_status("lab1")

    assert result.error is not None
    assert "Docker query failed" in result.error


@pytest.mark.asyncio
async def test_lab_status_ignores_libvirt_status_exception(tmp_path):
    libvirt_provider = MagicMock()
    libvirt_provider.status = AsyncMock(side_effect=RuntimeError("libvirt unavailable"))

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: libvirt_provider if p == "libvirt" else None,
        ):
            result = await labs_mod.lab_status("lab1")

    assert result.error is None
    assert result.nodes == []


@pytest.mark.asyncio
async def test_reconcile_single_node_running_starts_stopped_container(tmp_path):
    target = SimpleNamespace(container_name="archetype-lab1-r1", desired_state="running")
    c = _container(status="exited")
    client = _docker_client_for_get(c)

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "started"
    c.start.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_single_node_running_already_running(tmp_path):
    target = SimpleNamespace(container_name="archetype-lab1-r1", desired_state="running")
    c = _container(status="running")
    client = _docker_client_for_get(c)

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "already_running"
    c.start.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_single_node_stop_removes_and_logs_cleanup_error(tmp_path):
    target = SimpleNamespace(container_name="archetype-lab1-r1", desired_state="stopped")
    c = _container(status="exited")
    client = _docker_client_for_get(c)
    docker_provider = MagicMock()
    docker_provider.cleanup_lab_resources_if_empty = AsyncMock(return_value={"error": "in use"})

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(labs_mod, "get_provider", return_value=docker_provider):
            with patch("agent.readiness.clear_post_boot_state"):
                result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "removed"
    c.stop.assert_not_called()
    c.remove.assert_called_once_with(force=True, v=True)
    docker_provider.cleanup_lab_resources_if_empty.assert_awaited_once_with("lab1", tmp_path)


@pytest.mark.asyncio
async def test_reconcile_single_node_stop_cleanup_exception_still_succeeds(tmp_path):
    target = SimpleNamespace(container_name="archetype-lab1-r1", desired_state="stopped")
    c = _container(status="running")
    client = _docker_client_for_get(c)
    docker_provider = MagicMock()
    docker_provider.cleanup_lab_resources_if_empty = AsyncMock(side_effect=RuntimeError("cleanup boom"))

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(labs_mod, "get_provider", return_value=docker_provider):
            with patch("agent.readiness.clear_post_boot_state"):
                result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "removed"
    c.stop.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_single_node_returns_error_on_docker_exception(tmp_path):
    target = SimpleNamespace(container_name="archetype-lab1-r1", desired_state="running")
    client = _docker_client_for_get(RuntimeError("docker exploded"))

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is False
    assert result.action == "error"
    assert "docker exploded" in (result.error or "")


@pytest.mark.asyncio
async def test_reconcile_single_node_falls_back_to_libvirt_start(tmp_path):
    target = SimpleNamespace(container_name="archetype-lab1-r1", desired_state="running")
    client = _docker_client_for_get(docker.errors.NotFound("missing"))
    libvirt_provider = MagicMock()
    libvirt_provider.start_node = AsyncMock(
        return_value=SimpleNamespace(success=True, new_status=NodeStatus.RUNNING, error=None)
    )

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: libvirt_provider if p == "libvirt" else None,
        ):
            result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "started"
    libvirt_provider.start_node.assert_awaited_once_with("lab1", "r1", tmp_path)


@pytest.mark.asyncio
async def test_reconcile_single_node_falls_back_to_libvirt_stop(tmp_path):
    target = SimpleNamespace(container_name="arch-lab1-r2", desired_state="stopped")
    client = _docker_client_for_get(docker.errors.NotFound("missing"))
    libvirt_provider = MagicMock()
    libvirt_provider.stop_node = AsyncMock(
        return_value=SimpleNamespace(success=True, new_status=NodeStatus.STOPPED, error=None)
    )

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: libvirt_provider if p == "libvirt" else None,
        ):
            result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "stopped"
    libvirt_provider.stop_node.assert_awaited_once_with("lab1", "r2", tmp_path)


@pytest.mark.asyncio
async def test_reconcile_single_node_libvirt_failure_returns_error(tmp_path):
    target = SimpleNamespace(container_name="arch-lab1-r2", desired_state="running")
    client = _docker_client_for_get(docker.errors.NotFound("missing"))
    libvirt_provider = MagicMock()
    libvirt_provider.start_node = AsyncMock(
        return_value=SimpleNamespace(success=False, new_status=None, error=None)
    )

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: libvirt_provider if p == "libvirt" else None,
        ):
            result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is False
    assert result.action == "error"
    assert "Failed to start VM" in (result.error or "")


@pytest.mark.asyncio
async def test_reconcile_single_node_libvirt_exception_then_already_stopped(tmp_path):
    target = SimpleNamespace(container_name="arch-lab1-r2", desired_state="stopped")
    client = _docker_client_for_get(docker.errors.NotFound("missing"))
    libvirt_provider = MagicMock()
    libvirt_provider.stop_node = AsyncMock(side_effect=RuntimeError("vm error"))

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: libvirt_provider if p == "libvirt" else None,
        ):
            result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "already_stopped"


@pytest.mark.asyncio
async def test_reconcile_single_node_not_found_running_returns_error(tmp_path):
    target = SimpleNamespace(container_name="arch-lab1-r2", desired_state="running")
    client = _docker_client_for_get(docker.errors.NotFound("missing"))

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch.object(labs_mod, "get_provider", return_value=None):
            result = await labs_mod._reconcile_single_node("lab1", target, tmp_path)

    assert result.success is False
    assert result.action == "error"
    assert "Node not found" in (result.error or "")


@pytest.mark.asyncio
async def test_reconcile_nodes_returns_collected_results(tmp_path):
    req = NodeReconcileRequest(
        nodes=[
            NodeReconcileTarget(container_name="archetype-lab1-r1", desired_state="running"),
            NodeReconcileTarget(container_name="archetype-lab1-r2", desired_state="stopped"),
        ]
    )
    r1 = NodeReconcileResult(
        success=True,
        container_name="archetype-lab1-r1",
        action="already_running",
    )
    r2 = NodeReconcileResult(
        success=True,
        container_name="archetype-lab1-r2",
        action="already_stopped",
    )

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "_reconcile_single_node", new=AsyncMock(side_effect=[r1, r2])):
            response = await labs_mod.reconcile_nodes("lab1", req)

    assert response.lab_id == "lab1"
    assert [r.container_name for r in response.results] == [
        "archetype-lab1-r1",
        "archetype-lab1-r2",
    ]


@pytest.mark.asyncio
async def test_extract_configs_docker_only_success(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)
    docker_provider = MagicMock()
    docker_provider._extract_all_ceos_configs = AsyncMock(return_value=[("r1", "hostname r1")])

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider_for_request", return_value=docker_provider):
            result = await labs_mod.extract_configs("lab1")

    assert result.success is True
    assert result.extracted_count == 1
    assert result.configs[0].node_name == "r1"


@pytest.mark.asyncio
async def test_extract_configs_handles_docker_error_and_uses_libvirt(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", True)
    docker_provider = MagicMock()
    docker_provider._extract_all_ceos_configs = AsyncMock(side_effect=RuntimeError("docker fail"))
    libvirt_provider = MagicMock()
    libvirt_provider._extract_all_vm_configs = AsyncMock(return_value=[("vm1", "vm cfg")])

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider_for_request", return_value=docker_provider):
            with patch("agent.providers.libvirt.LIBVIRT_AVAILABLE", True):
                with patch("agent.providers.libvirt.LibvirtProvider", return_value=libvirt_provider):
                    result = await labs_mod.extract_configs("lab1")

    assert result.success is True
    assert result.extracted_count == 1
    assert result.configs[0].node_name == "vm1"


@pytest.mark.asyncio
async def test_extract_configs_libvirt_disabled_by_availability_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", False)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", True)

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch("agent.providers.libvirt.LIBVIRT_AVAILABLE", False):
            result = await labs_mod.extract_configs("lab1")

    assert result.success is True
    assert result.extracted_count == 0


@pytest.mark.asyncio
async def test_extract_configs_outer_exception_returns_failure(monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)

    with patch.object(labs_mod, "get_workspace", side_effect=RuntimeError("workspace boom")):
        result = await labs_mod.extract_configs("lab1")

    assert result.success is False
    assert "workspace boom" in (result.error or "")


@pytest.mark.asyncio
async def test_extract_node_config_from_docker_via_nvram(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"
    provider.docker.containers.get.return_value = _container(
        status="running",
        labels={"archetype.node_kind": "iol"},
    )
    provider._extract_config_via_nvram = AsyncMock(return_value="hostname r1\n")

    extraction_settings = SimpleNamespace(method="nvram", command=None)

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider_for_request", return_value=provider):
            with patch("agent.vendors.get_config_extraction_settings", return_value=extraction_settings):
                result = await labs_mod.extract_node_config("lab1", "r1")

    assert result.success is True
    assert "hostname r1" in (result.content or "")
    assert (tmp_path / "configs" / "r1" / "startup-config").exists()


@pytest.mark.asyncio
async def test_extract_node_config_from_docker_via_ssh(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"
    running = _container(status="running", labels={"archetype.node_kind": "ceos"})
    provider.docker.containers.get.return_value = running
    provider._extract_config_via_ssh = AsyncMock(return_value="ssh config")
    extraction_settings = SimpleNamespace(method="ssh", command="show running-config")

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider_for_request", return_value=provider):
            with patch("agent.vendors.get_config_extraction_settings", return_value=extraction_settings):
                result = await labs_mod.extract_node_config("lab1", "r1")

    assert result.success is True
    assert result.content == "ssh config"
    provider._extract_config_via_ssh.assert_awaited_once_with(
        running, "ceos", "show running-config", "r1"
    )


@pytest.mark.asyncio
async def test_extract_node_config_falls_back_to_libvirt(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", True)
    docker_provider = MagicMock()
    docker_provider.get_container_name.return_value = "archetype-lab1-r1"
    docker_provider.docker.containers.get.return_value = _container(
        status="running",
        labels={"archetype.node_kind": "ceos"},
    )
    docker_provider._extract_config_via_docker = AsyncMock(return_value="   ")
    extraction_settings = SimpleNamespace(method="docker", command="show run")

    libvirt_provider = MagicMock()
    libvirt_provider._domain_name.return_value = "arch-lab1-r1"
    libvirt_provider._run_libvirt = AsyncMock(return_value="iosv")
    libvirt_provider._extract_config = AsyncMock(return_value=("r1", "vm running config"))

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider_for_request", return_value=docker_provider):
            with patch.object(labs_mod, "get_provider", return_value=libvirt_provider):
                with patch("agent.vendors.get_config_extraction_settings", return_value=extraction_settings):
                    result = await labs_mod.extract_node_config("lab1", "r1")

    assert result.success is True
    assert result.content == "vm running config"


@pytest.mark.asyncio
async def test_extract_node_config_returns_failure_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", True)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"
    provider.docker.containers.get.return_value = _container(status="exited")

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider_for_request", return_value=provider):
            result = await labs_mod.extract_node_config("lab1", "r1")

    assert result.success is False
    assert "not running" in (result.error or "")


@pytest.mark.asyncio
async def test_extract_node_config_libvirt_kind_missing_returns_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", False)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", True)
    libvirt_provider = MagicMock()
    libvirt_provider._domain_name.return_value = "arch-lab1-r1"
    libvirt_provider._run_libvirt = AsyncMock(return_value=None)

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(labs_mod, "get_provider", return_value=libvirt_provider):
            result = await labs_mod.extract_node_config("lab1", "r1")

    assert result.success is False


@pytest.mark.asyncio
async def test_extract_node_config_outer_exception_returns_failure(monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_docker", False)
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)

    with patch.object(labs_mod, "get_workspace", side_effect=RuntimeError("workspace fail")):
        result = await labs_mod.extract_node_config("lab1", "r1")

    assert result.success is False
    assert "workspace fail" in (result.error or "")


@pytest.mark.asyncio
async def test_update_node_config_success(tmp_path):
    req = UpdateConfigRequest(content="hostname test\n")

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        result = await labs_mod.update_node_config("lab1", "r1", req)

    assert result.success is True
    assert (tmp_path / "configs" / "r1" / "startup-config").read_text() == "hostname test\n"


@pytest.mark.asyncio
async def test_update_node_config_failure_returns_error(tmp_path):
    req = UpdateConfigRequest(content="hostname test\n")

    with patch.object(labs_mod, "get_workspace", return_value=tmp_path):
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = await labs_mod.update_node_config("lab1", "r1", req)

    assert result.success is False
    assert "disk full" in (result.error or "")


@pytest.mark.asyncio
async def test_start_container_invalid_name_raises_400():
    with patch.object(labs_mod, "_validate_container_name", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await labs_mod.start_container("bad-name")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_start_container_running_and_started_paths():
    c_running = _container(status="running")
    c_stopped = _container(status="exited")
    client = MagicMock()
    client.containers.get.side_effect = [c_running, c_stopped]

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(labs_mod, "get_docker_client", return_value=client):
            r1 = await labs_mod.start_container("archetype-lab1-r1")
            r2 = await labs_mod.start_container("archetype-lab1-r2")

    assert r1["message"] == "Container already running"
    assert r2["message"] == "Container started"
    c_stopped.start.assert_called_once()


@pytest.mark.asyncio
async def test_start_container_not_found_and_api_error_paths():
    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.NotFound("missing")),
        ):
            with pytest.raises(HTTPException) as exc:
                await labs_mod.start_container("archetype-lab1-r1")
    assert exc.value.status_code == 404

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.APIError("api fail")),
        ):
            result = await labs_mod.start_container("archetype-lab1-r1")
    assert result["success"] is False
    assert "api fail" in result["error"]


@pytest.mark.asyncio
async def test_stop_container_paths_and_errors(monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "container_stop_timeout", 7)
    c_running = _container(status="running")
    c_stopped = _container(status="exited")
    client = MagicMock()
    client.containers.get.side_effect = [c_stopped, c_running]

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(labs_mod, "get_docker_client", return_value=client):
            r1 = await labs_mod.stop_container("archetype-lab1-r1")
            r2 = await labs_mod.stop_container("archetype-lab1-r2")
    assert r1["message"] == "Container already stopped"
    assert r2["message"] == "Container stopped"
    c_running.stop.assert_called_once_with(timeout=7)

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.NotFound("missing")),
        ):
            with pytest.raises(HTTPException) as exc:
                await labs_mod.stop_container("archetype-lab1-r3")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_remove_container_paths_and_errors():
    c = _container(status="running")
    client = _docker_client_for_get(c)

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(labs_mod, "get_docker_client", return_value=client):
            ok = await labs_mod.remove_container("archetype-lab1-r1", force=True)
    assert ok["success"] is True
    c.remove.assert_called_once_with(force=True)

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.APIError("api fail")),
        ):
            err = await labs_mod.remove_container("archetype-lab1-r2")
    assert err["success"] is False

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.NotFound("missing")),
        ):
            with pytest.raises(HTTPException):
                await labs_mod.remove_container("archetype-lab1-r3")


@pytest.mark.asyncio
async def test_remove_container_for_lab_success_and_mismatch_label():
    c = _container(
        name="archetype-lab1-r1",
        status="running",
        labels={"archetype.lab_id": "other-lab"},
    )
    client = _docker_client_for_get(c)

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(labs_mod, "get_docker_client", return_value=client):
            result = await labs_mod.remove_container_for_lab("lab1", "archetype-lab1-r1", force=True)

    assert result["success"] is True
    c.stop.assert_called_once_with(timeout=10)
    c.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_remove_container_for_lab_error_paths():
    with patch.object(labs_mod, "_validate_container_name", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await labs_mod.remove_container_for_lab("lab1", "bad")
    assert exc.value.status_code == 400

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.NotFound("gone")),
        ):
            not_found = await labs_mod.remove_container_for_lab("lab1", "archetype-lab1-r1")
    assert not_found["success"] is True
    assert "already removed" in not_found["message"]

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(docker.errors.APIError("api fail")),
        ):
            api_err = await labs_mod.remove_container_for_lab("lab1", "archetype-lab1-r1")
    assert api_err["success"] is False

    with patch.object(labs_mod, "_validate_container_name", return_value=True):
        with patch.object(
            labs_mod,
            "get_docker_client",
            return_value=_docker_client_for_get(RuntimeError("boom")),
        ):
            generic = await labs_mod.remove_container_for_lab("lab1", "archetype-lab1-r1")
    assert generic["success"] is False
    assert "boom" in generic["error"]


@pytest.mark.asyncio
async def test_discover_labs_merges_nodes_and_skips_failing_provider():
    p1 = MagicMock()
    p1.discover_labs = AsyncMock(return_value={"lab1": [_status_node("r1")]})
    p2 = MagicMock()
    p2.discover_labs = AsyncMock(
        return_value={
            "lab1": [_status_node("vm1", status=NodeStatus.STOPPED)],
            "lab2": [_status_node("r2")],
        }
    )
    p3 = MagicMock()
    p3.discover_labs = AsyncMock(side_effect=RuntimeError("bad provider"))

    providers = {"docker": p1, "libvirt": p2, "broken": p3}

    with patch.object(labs_mod, "list_providers", return_value=list(providers.keys())):
        with patch.object(labs_mod, "get_provider", side_effect=lambda p: providers.get(p)):
            with patch.object(labs_mod, "provider_status_to_schema", side_effect=lambda s: s):
                result = await labs_mod.discover_labs()

    by_id = {lab.lab_id: lab for lab in result.labs}
    assert set(by_id.keys()) == {"lab1", "lab2"}
    assert {n.name for n in by_id["lab1"].nodes} == {"r1", "vm1"}


@pytest.mark.asyncio
async def test_runtime_identity_audit_aggregates_provider_reports():
    p1 = MagicMock()
    p1.audit_runtime_identity = AsyncMock(return_value={
        "provider": "docker",
        "managed_runtimes": 2,
        "resolved_by_metadata": 1,
        "name_only": 1,
        "missing_node_definition_id": 1,
        "missing_runtime_id": 0,
        "inconsistent_metadata": 0,
        "nodes": [{
            "provider": "docker",
            "runtime_name": "ctr-r1",
            "lab_id": "lab1",
            "node_name": "r1",
            "node_definition_id": "node-def-r1",
            "runtime_id": "runtime-1",
            "resolved_by_metadata": True,
            "name_only": False,
            "missing_node_definition_id": False,
            "missing_runtime_id": False,
            "inconsistent_metadata": False,
        }],
    })
    p2 = MagicMock()
    p2.audit_runtime_identity = AsyncMock(side_effect=RuntimeError("bad provider"))

    with patch.object(labs_mod, "list_providers", return_value=["docker", "libvirt"]):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: p1 if p == "docker" else p2,
        ):
            result = await labs_mod.runtime_identity_audit()

    assert len(result.providers) == 1
    assert result.providers[0].provider == "docker"
    assert result.providers[0].managed_runtimes == 2
    assert len(result.errors) == 1
    assert "bad provider" in result.errors[0]


@pytest.mark.asyncio
async def test_runtime_identity_backfill_groups_entries_by_provider():
    req = RuntimeIdentityBackfillRequest(
        dry_run=True,
        entries=[
            {
                "lab_id": "lab1",
                "node_name": "r1",
                "node_definition_id": "node-def-r1",
                "provider": "docker",
            },
            {
                "lab_id": "lab1",
                "node_name": "vm1",
                "node_definition_id": "node-def-vm1",
                "provider": "libvirt",
            },
        ],
    )
    p1 = MagicMock()
    p1.backfill_runtime_identity = AsyncMock(return_value={
        "provider": "docker",
        "updated": 0,
        "recreate_required": 1,
        "missing": 0,
        "skipped": 0,
        "nodes": [{
            "lab_id": "lab1",
            "node_name": "r1",
            "node_definition_id": "node-def-r1",
            "runtime_name": "ctr-r1",
            "outcome": "recreate_required",
            "dry_run": True,
        }],
        "errors": [],
    })
    p2 = MagicMock()
    p2.backfill_runtime_identity = AsyncMock(return_value={
        "provider": "libvirt",
        "updated": 1,
        "recreate_required": 0,
        "missing": 0,
        "skipped": 0,
        "nodes": [{
            "lab_id": "lab1",
            "node_name": "vm1",
            "node_definition_id": "node-def-vm1",
            "runtime_name": "arch-lab1-vm1",
            "outcome": "would_update",
            "dry_run": True,
        }],
        "errors": [],
    })

    with patch.object(labs_mod, "get_provider", side_effect=lambda p: p1 if p == "docker" else p2):
        result = await labs_mod.backfill_runtime_identity(req)

    assert len(result.providers) == 2
    assert result.providers[0].provider == "docker"
    assert result.providers[1].provider == "libvirt"
    assert result.providers[0].recreate_required == 1
    assert result.providers[1].updated == 1


@pytest.mark.asyncio
async def test_cleanup_orphans_aggregates_and_collects_errors():
    req = CleanupOrphansRequest(valid_lab_ids=["lab1", "lab2"])
    p1 = MagicMock()
    p1.cleanup_orphan_containers = AsyncMock(return_value=["c1"])
    p2 = MagicMock()
    p2.cleanup_orphan_containers = AsyncMock(side_effect=RuntimeError("provider fail"))

    with patch.object(labs_mod, "list_providers", return_value=["docker", "libvirt"]):
        with patch.object(
            labs_mod,
            "get_provider",
            side_effect=lambda p: p1 if p == "docker" else p2,
        ):
            result = await labs_mod.cleanup_orphans(req)

    assert result.removed_containers == ["c1"]
    assert len(result.errors) == 1
    assert "provider fail" in result.errors[0]


@pytest.mark.asyncio
async def test_cleanup_lab_orphans_docker_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", False)
    req = CleanupLabOrphansRequest(lab_id="lab1", keep_node_names=["keep-me"])

    keep = _container(
        name="archetype-lab1-keep",
        labels={"archetype.node_name": "keep-me"},
    )
    remove_ok = _container(
        name="archetype-lab1-old",
        labels={"archetype.node_name": "old"},
    )
    remove_fail = _container(
        name="archetype-lab1-bad",
        labels={"archetype.node_name": "bad"},
    )
    remove_fail.remove.side_effect = docker.errors.APIError("remove fail")

    client = MagicMock()
    client.containers.list.return_value = [keep, remove_ok, remove_fail]

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        result = await labs_mod.cleanup_lab_orphans(req)

    assert "archetype-lab1-old" in result.removed_containers
    assert "archetype-lab1-keep" in result.kept_containers
    assert any("remove fail" in e for e in result.errors)


@pytest.mark.asyncio
async def test_cleanup_lab_orphans_docker_exception_and_libvirt_success(monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", True)
    monkeypatch.setattr(labs_mod.settings, "workspace_path", "/tmp/ws")
    req = CleanupLabOrphansRequest(lab_id="lab1", keep_node_names=[])
    libvirt_provider = MagicMock()
    libvirt_provider.cleanup_lab_orphan_domains = AsyncMock(return_value={"domains": ["arch-lab1-vm1"]})

    with patch.object(labs_mod, "get_docker_client", side_effect=RuntimeError("docker down")):
        with patch("agent.providers.libvirt.LIBVIRT_AVAILABLE", True):
            with patch("agent.providers.libvirt.LibvirtProvider", return_value=libvirt_provider):
                result = await labs_mod.cleanup_lab_orphans(req)

    assert "arch-lab1-vm1" in result.removed_containers
    assert any("Docker orphan cleanup" in e for e in result.errors)


@pytest.mark.asyncio
async def test_cleanup_lab_orphans_libvirt_exception_recorded(monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "enable_libvirt", True)
    monkeypatch.setattr(labs_mod.settings, "workspace_path", "/tmp/ws")
    req = CleanupLabOrphansRequest(lab_id="lab1", keep_node_names=[])
    client = MagicMock()
    client.containers.list.return_value = []
    libvirt_provider = MagicMock()
    libvirt_provider.cleanup_lab_orphan_domains = AsyncMock(side_effect=RuntimeError("vm cleanup fail"))

    with patch.object(labs_mod, "get_docker_client", return_value=client):
        with patch("agent.providers.libvirt.LIBVIRT_AVAILABLE", True):
            with patch("agent.providers.libvirt.LibvirtProvider", return_value=libvirt_provider):
                result = await labs_mod.cleanup_lab_orphans(req)

    assert any("VM orphan cleanup" in e for e in result.errors)


@pytest.mark.asyncio
async def test_prune_docker_uses_sync_prune_helper():
    req = DockerPruneRequest(valid_lab_ids=["lab1"])
    expected = SimpleNamespace(success=True, images_removed=3)

    with patch.object(labs_mod.asyncio, "to_thread", new=AsyncMock(return_value=expected)) as mock_to_thread:
        result = await labs_mod.prune_docker(req)

    assert result is expected
    mock_to_thread.assert_awaited_once_with(labs_mod._sync_prune_docker, req)


@pytest.mark.asyncio
async def test_delete_lab_workspace_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "workspace_path", str(tmp_path))

    missing = await labs_mod.delete_lab_workspace("lab-missing")
    assert missing["success"] is True
    assert "does not exist" in missing["message"]

    existing = tmp_path / "lab1"
    existing.mkdir()
    ok = await labs_mod.delete_lab_workspace("lab1")
    assert ok["success"] is True
    assert not existing.exists()


@pytest.mark.asyncio
async def test_delete_lab_workspace_handles_rmtree_error(tmp_path, monkeypatch):
    monkeypatch.setattr(labs_mod.settings, "workspace_path", str(tmp_path))
    existing = tmp_path / "lab1"
    existing.mkdir()

    with patch("shutil.rmtree", side_effect=OSError("perm denied")):
        result = await labs_mod.delete_lab_workspace("lab1")

    assert result["success"] is False
    assert "perm denied" in result["error"]


@pytest.mark.asyncio
async def test_cleanup_workspaces_paths(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setattr(labs_mod.settings, "workspace_path", str(root))

    result = await labs_mod.cleanup_workspaces(CleanupWorkspacesRequest(valid_lab_ids=["lab1"]))
    assert result["success"] is True
    assert result["removed"] == []

    root.mkdir()
    for keep_dir in ["images", "uploads", ".tmp", "configs", ".poap-tftp", "lab1"]:
        (root / keep_dir).mkdir()
    (root / "lab2").mkdir()
    (root / "lab3").mkdir()
    (root / "README.txt").write_text("not a dir")

    real_rmtree = __import__("shutil").rmtree

    def _fake_rmtree(path):
        if Path(path).name == "lab3":
            raise OSError("busy")
        return real_rmtree(path)

    with patch("shutil.rmtree", side_effect=_fake_rmtree):
        cleaned = await labs_mod.cleanup_workspaces(
            CleanupWorkspacesRequest(valid_lab_ids=["lab1"])
        )

    assert cleaned["success"] is True
    assert "lab2" in cleaned["removed"]
    assert any("lab3: busy" in e for e in cleaned["errors"])
