"""Additional branch coverage for agent.routers.labs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import docker
import pytest
from fastapi import HTTPException

from agent.config import settings
from agent.providers import NodeStatus as ProviderNodeStatus
from agent.routers import labs
from agent.schemas import CleanupLabOrphansRequest, CleanupOrphansRequest, CleanupWorkspacesRequest, DockerPruneRequest


@pytest.mark.asyncio
async def test_lab_status_and_reconcile_nodes(monkeypatch, tmp_path):
    monkeypatch.setattr(labs, "get_workspace", lambda _lab: tmp_path)
    docker_provider = SimpleNamespace(
        status=AsyncMock(
            return_value=SimpleNamespace(
                nodes=[
                        SimpleNamespace(
                            name="r1",
                            status=ProviderNodeStatus.RUNNING,
                            container_id="cid1",
                            runtime_id="runtime-cid1",
                            node_definition_id="node-def-r1",
                            image="img",
                            ip_addresses=["10.0.0.1"],
                        )
                ],
                error=None,
            )
        )
    )
    libvirt_provider = SimpleNamespace(
        status=AsyncMock(
            return_value=SimpleNamespace(
                nodes=[
                        SimpleNamespace(
                            name="vm1",
                            status=ProviderNodeStatus.STOPPED,
                            container_id="dom1",
                            runtime_id="runtime-dom1",
                            node_definition_id="node-def-vm1",
                            image="qcow2",
                            ip_addresses=[],
                        )
                ],
                error="minor warning",
            )
        )
    )
    monkeypatch.setattr(labs, "get_provider", lambda name: docker_provider if name == "docker" else libvirt_provider)

    status = await labs.lab_status("lab-1")
    assert len(status.nodes) == 2
    assert "Libvirt" in (status.error or "")

    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(labs.asyncio, "to_thread", _run_direct)
    target = SimpleNamespace(container_name="archetype-lab-1-r1", desired_state="running")
    container = SimpleNamespace(status="running")
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _n: container))
    monkeypatch.setattr(labs, "get_docker_client", lambda: client)
    result = await labs._reconcile_single_node("lab-1", target, tmp_path)
    assert result.action == "already_running"

    req = SimpleNamespace(nodes=[target])
    with patch("agent.routers.labs._reconcile_single_node", new_callable=AsyncMock, return_value=result):
        resp = await labs.reconcile_nodes("lab-1", req)
    assert resp.lab_id == "lab-1"
    assert len(resp.results) == 1


@pytest.mark.asyncio
async def test_reconcile_single_node_docker_stop_and_fallback_paths(monkeypatch, tmp_path):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(labs.asyncio, "to_thread", _run_direct)
    container = SimpleNamespace(
        status="running",
        labels={"archetype.node_name": "r2"},
        stop=Mock(),
        remove=Mock(),
    )
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _n: container))
    monkeypatch.setattr(labs, "get_docker_client", lambda: client)

    cleanup = AsyncMock(return_value={"cleaned": True, "networks_deleted": 1})
    stop_node = AsyncMock(return_value=SimpleNamespace(success=True, error=None))
    docker_provider = SimpleNamespace(
        stop_node=stop_node,
        cleanup_lab_resources_if_empty=cleanup,
    )
    monkeypatch.setattr(labs, "get_provider", lambda name: docker_provider if name == "docker" else None)

    target = SimpleNamespace(container_name="archetype-lab-1-r2", desired_state="stopped")
    stopped = await labs._reconcile_single_node("lab-1", target, tmp_path)
    assert stopped.action == "removed"
    stop_node.assert_awaited_once_with(
        lab_id="lab-1",
        node_name="r2",
        workspace=tmp_path,
    )
    container.stop.assert_not_called()
    container.remove.assert_not_called()

    missing_client = SimpleNamespace(containers=SimpleNamespace(get=Mock(side_effect=docker.errors.NotFound("x"))))
    monkeypatch.setattr(labs, "get_docker_client", lambda: missing_client)
    monkeypatch.setattr(labs, "get_provider", lambda _name: None)
    already_stopped = await labs._reconcile_single_node("lab-1", target, tmp_path)
    assert already_stopped.action == "already_stopped"

    running_target = SimpleNamespace(container_name="archetype-lab-1-r3", desired_state="running")
    not_found = await labs._reconcile_single_node("lab-1", running_target, tmp_path)
    assert not_found.success is False
    assert "not found" in (not_found.error or "").lower()


@pytest.mark.asyncio
async def test_extract_configs_and_extract_node_config(monkeypatch, tmp_path):
    monkeypatch.setattr(labs, "get_workspace", lambda _lab: tmp_path)
    monkeypatch.setattr(settings, "enable_docker", True)
    monkeypatch.setattr(settings, "enable_libvirt", False)

    docker_provider = SimpleNamespace(
        _extract_all_ceos_configs=AsyncMock(return_value=[("r1", "hostname r1")]),
    )
    monkeypatch.setattr(labs, "get_provider_for_request", lambda _name: docker_provider)
    extracted = await labs.extract_configs("lab-1")
    assert extracted.success is True
    assert extracted.extracted_count == 1

    container = SimpleNamespace(status="running", labels={"archetype.node_kind": "eos"})
    docker_provider = SimpleNamespace(
        get_container_name=lambda lab_id, node_name: f"archetype-{lab_id}-{node_name}",
        docker=SimpleNamespace(containers=SimpleNamespace(get=Mock(return_value=container))),
        _extract_config_via_docker=AsyncMock(return_value="hostname r1"),
    )
    monkeypatch.setattr(labs, "get_provider_for_request", lambda _name: docker_provider)
    monkeypatch.setattr(
        "agent.vendors.get_config_extraction_settings",
        lambda _kind: SimpleNamespace(method="docker", command="show run"),
    )

    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(labs.asyncio, "to_thread", _run_direct)
    node_cfg = await labs.extract_node_config("lab-1", "r1")
    assert node_cfg.success is True
    assert "hostname" in (node_cfg.content or "")

    monkeypatch.setattr(settings, "enable_docker", False)
    monkeypatch.setattr(settings, "enable_libvirt", False)
    missing = await labs.extract_node_config("lab-1", "r9")
    assert missing.success is False


@pytest.mark.asyncio
async def test_update_node_config_and_container_control(monkeypatch, tmp_path):
    monkeypatch.setattr(labs, "get_workspace", lambda _lab: tmp_path)
    update = await labs.update_node_config("lab-1", "r1", SimpleNamespace(content="hostname r1\n"))
    assert update.success is True
    assert (tmp_path / "configs" / "r1" / "startup-config").exists()

    with patch("pathlib.Path.write_text", side_effect=RuntimeError("write failed")):
        failed = await labs.update_node_config("lab-1", "r2", SimpleNamespace(content="x"))
    assert failed.success is False

    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(labs.asyncio, "to_thread", _run_direct)
    container = SimpleNamespace(status="exited", start=Mock(), stop=Mock(), remove=Mock(), labels={})
    client = SimpleNamespace(containers=SimpleNamespace(get=Mock(return_value=container)))
    monkeypatch.setattr(labs, "get_docker_client", lambda: client)

    started = await labs.start_container("archetype-lab-node")
    assert started["success"] is True
    assert container.start.call_count == 1

    container.status = "running"
    stopped = await labs.stop_container("archetype-lab-node")
    assert stopped["success"] is True
    assert container.stop.call_count == 1

    removed = await labs.remove_container("archetype-lab-node", force=True)
    assert removed["success"] is True
    assert container.remove.call_count == 1

    with pytest.raises(HTTPException):
        await labs.start_container("bad name")
    with pytest.raises(HTTPException):
        await labs.stop_container("bad name")
    with pytest.raises(HTTPException):
        await labs.remove_container("bad name")

    client_nf = SimpleNamespace(containers=SimpleNamespace(get=Mock(side_effect=docker.errors.NotFound("x"))))
    monkeypatch.setattr(labs, "get_docker_client", lambda: client_nf)
    with pytest.raises(HTTPException):
        await labs.start_container("archetype-lab-node")


@pytest.mark.asyncio
async def test_remove_container_for_lab_discover_and_cleanup(monkeypatch, tmp_path):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(labs.asyncio, "to_thread", _run_direct)

    running = SimpleNamespace(
        status="running",
        labels={"archetype.lab_id": "lab-1"},
        stop=Mock(),
        remove=Mock(),
    )
    client = SimpleNamespace(containers=SimpleNamespace(get=Mock(return_value=running)))
    monkeypatch.setattr(labs, "get_docker_client", lambda: client)
    ok = await labs.remove_container_for_lab("lab-1", "archetype-lab-node", force=False)
    assert ok["success"] is True

    client_nf = SimpleNamespace(containers=SimpleNamespace(get=Mock(side_effect=docker.errors.NotFound("x"))))
    monkeypatch.setattr(labs, "get_docker_client", lambda: client_nf)
    not_found = await labs.remove_container_for_lab("lab-1", "archetype-lab-node")
    assert not_found["success"] is True

    provider_ok = SimpleNamespace(
        discover_labs=AsyncMock(return_value={"lab-1": [SimpleNamespace(name="r1", status=ProviderNodeStatus.RUNNING, container_id="c1", runtime_id="runtime-c1", node_definition_id="node-def-r1", image="img", ip_addresses=[])]}),
        cleanup_orphan_containers=AsyncMock(return_value=["archetype-old"]),
    )
    provider_fail = SimpleNamespace(
        discover_labs=AsyncMock(side_effect=RuntimeError("boom")),
        cleanup_orphan_containers=AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(labs, "list_providers", lambda: ["docker", "libvirt"])
    monkeypatch.setattr(labs, "get_provider", lambda name: provider_ok if name == "docker" else provider_fail)

    discovered = await labs.discover_labs()
    assert len(discovered.labs) == 1
    cleaned = await labs.cleanup_orphans(CleanupOrphansRequest(valid_lab_ids=["lab-1"]))
    assert "archetype-old" in cleaned.removed_containers
    assert cleaned.errors

    cleanup_containers = [
        SimpleNamespace(name="arch-r1", labels={"archetype.node_name": "r1"}, remove=Mock()),
        SimpleNamespace(name="arch-r2", labels={"archetype.node_name": "r2"}, remove=Mock()),
    ]
    docker_client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: cleanup_containers))
    monkeypatch.setattr(labs, "get_docker_client", lambda: docker_client)
    monkeypatch.setattr(settings, "enable_libvirt", False)
    req = CleanupLabOrphansRequest(lab_id="lab-1", keep_node_names=["r1"])
    cleaned_lab = await labs.cleanup_lab_orphans(req)
    assert "arch-r2" in cleaned_lab.removed_containers
    assert "arch-r1" in cleaned_lab.kept_containers

    prune_resp = SimpleNamespace(success=True)
    monkeypatch.setattr(labs.asyncio, "to_thread", AsyncMock(return_value=prune_resp))
    out = await labs.prune_docker(DockerPruneRequest(valid_lab_ids=["lab-1"]))
    assert out.success is True


@pytest.mark.asyncio
async def test_workspace_cleanup_endpoints(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    missing = await labs.delete_lab_workspace("lab-missing")
    assert missing["success"] is True

    existing = tmp_path / "lab-a"
    existing.mkdir()
    deleted = await labs.delete_lab_workspace("lab-a")
    assert deleted["success"] is True

    failed_dir = tmp_path / "lab-b"
    failed_dir.mkdir()
    with patch("shutil.rmtree", side_effect=RuntimeError("rm failed")):
        failed = await labs.delete_lab_workspace("lab-b")
    assert failed["success"] is False

    root = tmp_path / "cleanup-root"
    monkeypatch.setattr(settings, "workspace_path", str(root))
    empty = await labs.cleanup_workspaces(CleanupWorkspacesRequest(valid_lab_ids=["keep"]))
    assert empty["success"] is True

    root.mkdir()
    (root / "keep").mkdir()
    (root / "orphan1").mkdir()
    (root / "images").mkdir()
    result = await labs.cleanup_workspaces(CleanupWorkspacesRequest(valid_lab_ids=["keep"]))
    assert "orphan1" in result["removed"]
    assert "keep" not in result["removed"]
