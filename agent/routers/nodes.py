"""Per-node lifecycle, readiness, and exec endpoints."""
from __future__ import annotations

import asyncio
import logging

import docker
from fastapi import APIRouter, HTTPException, Request

from agent.config import settings
from agent.docker_client import get_docker_client
from agent.helpers import get_workspace, get_provider_for_request, _get_docker_ovs_plugin
from agent.providers import get_provider
from agent.readiness import get_probe_for_vendor, run_post_boot_commands
from agent.schemas import (
    CliCommandOutput,
    CliVerifyRequest,
    CliVerifyResponse,
    CreateNodeRequest,
    CreateNodeResponse,
    DestroyNodeResponse,
    FixInterfacesResponse,
    RepairEndpointsRequest,
    RepairEndpointsResponse,
    StartNodeRequest,
    StartNodeResponse,
    StopNodeResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nodes"])


@router.post("/labs/{lab_id}/nodes/{node_name}/fix-interfaces")
async def fix_node_interfaces(lab_id: str, node_name: str) -> FixInterfacesResponse:
    """Fix interface names for a running container.

    Docker may assign interface names based on network attachment order rather
    than the intended names. This endpoint renames interfaces to match their
    intended OVS network names (eth1, eth2, etc.).

    Useful for:
    - Containers restarted outside normal deployment flow
    - Recovering from agent restart that lost plugin state
    - Manual troubleshooting

    Args:
        lab_id: Lab identifier
        node_name: Node name (display name, not container name)

    Returns:
        FixInterfacesResponse with counts of fixed interfaces
    """
    provider = get_provider_for_request()
    container_name = provider.get_container_name(lab_id, node_name)

    logger.info(f"Fixing interface names for {container_name}")

    try:
        result = await provider._fix_interface_names(container_name, lab_id)
        return FixInterfacesResponse(
            success=True,
            node=node_name,
            fixed=result.get("fixed", 0),
            already_correct=result.get("already_correct", 0),
            errors=result.get("errors", []),
        )
    except Exception as e:
        logger.error(f"Failed to fix interfaces for {node_name}: {e}")
        return FixInterfacesResponse(
            success=False,
            node=node_name,
            errors=[str(e)],
        )


@router.post("/labs/{lab_id}/repair-endpoints")
async def repair_lab_endpoints(
    lab_id: str,
    request: RepairEndpointsRequest,
) -> RepairEndpointsResponse:
    """Repair missing veth pairs and OVS ports for lab containers.

    After agent/container restarts, the Docker OVS plugin may retain stale
    endpoint records where the physical veth pairs no longer exist. This
    endpoint recreates the veth pairs, attaches them to OVS, and moves
    the container side into the correct namespace.

    Args:
        lab_id: Lab identifier
        request: Optional list of node names to repair (all if empty)
    """
    plugin = _get_docker_ovs_plugin()
    provider = get_provider_for_request()

    # Determine which nodes to repair
    if request.nodes:
        node_names = request.nodes
    else:
        # Discover all nodes in this lab from plugin state
        lab_containers: set[str] = set()
        for ep in plugin.endpoints.values():
            net = plugin.networks.get(ep.network_id)
            if net and net.lab_id == lab_id and ep.container_name:
                lab_containers.add(ep.container_name)
        # Convert container names back to node names
        node_names = []
        for cname in lab_containers:
            # Container names are typically "{lab_id}_{node_name}" via provider
            try:
                # Try reverse-mapping through provider
                if cname.startswith(f"{lab_id}_"):
                    node_names.append(cname[len(f"{lab_id}_"):])
                else:
                    node_names.append(cname)
            except Exception:
                node_names.append(cname)

    all_results: dict[str, list] = {}
    total_repaired = 0
    nodes_with_repairs = 0

    for node_name in node_names:
        try:
            container_name = provider.get_container_name(lab_id, node_name)
        except Exception:
            container_name = f"{lab_id}_{node_name}"

        logger.info(f"Repairing endpoints for {container_name}")
        try:
            node_results = await plugin.repair_endpoints(lab_id, container_name)
            all_results[node_name] = [
                {
                    "interface": r["interface"],
                    "status": r["status"],
                    "host_veth": r.get("host_veth"),
                    "vlan_tag": r.get("vlan_tag"),
                    "message": r.get("message"),
                }
                for r in node_results
            ]
            repaired = sum(1 for r in node_results if r["status"] == "repaired")
            total_repaired += repaired
            if repaired > 0:
                nodes_with_repairs += 1
        except Exception as e:
            logger.error(f"Failed to repair endpoints for {node_name}: {e}")
            all_results[node_name] = [{
                "interface": "*",
                "status": "error",
                "message": str(e),
            }]

    return RepairEndpointsResponse(
        success=True,
        nodes_repaired=nodes_with_repairs,
        total_endpoints_repaired=total_repaired,
        results=all_results,
    )


# --- Per-Node Lifecycle Endpoints ---

@router.post("/labs/{lab_id}/nodes/{node_name}/create")
async def create_node(
    lab_id: str,
    node_name: str,
    request: CreateNodeRequest,
    provider: str = "docker",
) -> CreateNodeResponse:
    """Create a single node container without starting it."""
    import time as _time
    _t0 = _time.monotonic()

    provider_instance = get_provider_for_request(provider)
    workspace = get_workspace(lab_id)

    result = await provider_instance.create_node(
        lab_id=lab_id,
        node_name=node_name,
        kind=request.kind,
        workspace=workspace,
        image=request.image,
        display_name=request.display_name,
        interface_count=request.interface_count,
        binds=request.binds,
        env=request.env,
        startup_config=request.startup_config,
        memory=request.memory,
        cpu=request.cpu,
        cpu_limit=request.cpu_limit,
        disk_driver=request.disk_driver,
        nic_driver=request.nic_driver,
        machine_type=request.machine_type,
        libvirt_driver=request.libvirt_driver,
        # Older controller payloads / current schema may omit readiness overrides.
        readiness_probe=getattr(request, "readiness_probe", None),
        readiness_pattern=getattr(request, "readiness_pattern", None),
        readiness_timeout=getattr(request, "readiness_timeout", None),
        efi_boot=request.efi_boot,
        efi_vars=request.efi_vars,
        data_volume_gb=request.data_volume_gb,
        image_sha256=getattr(request, "image_sha256", None),
    )

    elapsed_ms = int((_time.monotonic() - _t0) * 1000)
    logger.info(
        "Container operation",
        extra={
            "event": "container_operation",
            "operation": "create",
            "lab_id": lab_id,
            "node_name": node_name,
            "provider": provider,
            "result": "success" if result.success else "error",
            "duration_ms": elapsed_ms,
            "error": result.error if not result.success else None,
        },
    )

    # Record metrics
    from agent.metrics import node_operation_duration, node_operation_errors
    node_operation_duration.labels(
        operation="create",
        status="success" if result.success else "error",
    ).observe(elapsed_ms / 1000)
    if not result.success:
        node_operation_errors.labels(operation="create").inc()

    return CreateNodeResponse(
        success=result.success,
        container_name=provider_instance.get_container_name(lab_id, node_name) if hasattr(provider_instance, "get_container_name") else f"archetype-{lab_id}-{node_name}",
        status=result.new_status.value if result.new_status else "unknown",
        details=result.stdout or result.stderr or None,
        error=result.error,
        duration_ms=elapsed_ms,
    )


@router.post("/labs/{lab_id}/nodes/{node_name}/start")
async def start_node(
    lab_id: str,
    node_name: str,
    request: StartNodeRequest | None = None,
    provider: str = "docker",
) -> StartNodeResponse:
    """Start a node with optional veth repair and interface fixing."""
    import time as _time
    _t0 = _time.monotonic()

    provider_instance = get_provider_for_request(provider)
    workspace = get_workspace(lab_id)

    # repair_endpoints and fix_interfaces are Docker-specific kwargs
    kwargs: dict = {
        "lab_id": lab_id,
        "node_name": node_name,
        "workspace": workspace,
    }
    if provider == "docker":
        kwargs["repair_endpoints"] = request.repair_endpoints if request else True
        kwargs["fix_interfaces"] = request.fix_interfaces if request else True

    result = await provider_instance.start_node(**kwargs)

    # Parse repair/fix counts from stdout if available
    endpoints_repaired = 0
    interfaces_fixed = 0
    if result.stdout:
        import re as _re
        ep_match = _re.search(r"repaired (\d+) endpoints", result.stdout)
        if_match = _re.search(r"fixed (\d+) interfaces", result.stdout)
        if ep_match:
            endpoints_repaired = int(ep_match.group(1))
        if if_match:
            interfaces_fixed = int(if_match.group(1))

    elapsed_ms = int((_time.monotonic() - _t0) * 1000)
    logger.info(
        "Container operation",
        extra={
            "event": "container_operation",
            "operation": "start",
            "lab_id": lab_id,
            "node_name": node_name,
            "provider": provider,
            "result": "success" if result.success else "error",
            "duration_ms": elapsed_ms,
            "error": result.error if not result.success else None,
        },
    )

    from agent.metrics import node_operation_duration, node_operation_errors
    node_operation_duration.labels(
        operation="start",
        status="success" if result.success else "error",
    ).observe(elapsed_ms / 1000)
    if not result.success:
        node_operation_errors.labels(operation="start").inc()

    return StartNodeResponse(
        success=result.success,
        status=result.new_status.value if result.new_status else "unknown",
        endpoints_repaired=endpoints_repaired,
        interfaces_fixed=interfaces_fixed,
        error=result.error,
        duration_ms=elapsed_ms,
    )


@router.post("/labs/{lab_id}/nodes/{node_name}/stop")
async def stop_node(lab_id: str, node_name: str, provider: str = "docker") -> StopNodeResponse:
    """Stop a running node."""
    import time as _time
    _t0 = _time.monotonic()

    provider_instance = get_provider_for_request(provider)
    workspace = get_workspace(lab_id)

    result = await provider_instance.stop_node(
        lab_id=lab_id,
        node_name=node_name,
        workspace=workspace,
    )

    elapsed_ms = int((_time.monotonic() - _t0) * 1000)
    logger.info(
        "Container operation",
        extra={
            "event": "container_operation",
            "operation": "stop",
            "lab_id": lab_id,
            "node_name": node_name,
            "provider": provider,
            "result": "success" if result.success else "error",
            "duration_ms": elapsed_ms,
            "error": result.error if not result.success else None,
        },
    )

    from agent.metrics import node_operation_duration, node_operation_errors
    node_operation_duration.labels(
        operation="stop",
        status="success" if result.success else "error",
    ).observe(elapsed_ms / 1000)
    if not result.success:
        node_operation_errors.labels(operation="stop").inc()

    return StopNodeResponse(
        success=result.success,
        status=result.new_status.value if result.new_status else "unknown",
        error=result.error,
        duration_ms=elapsed_ms,
    )


@router.delete("/labs/{lab_id}/nodes/{node_name}")
async def destroy_node(lab_id: str, node_name: str, provider: str = "auto") -> DestroyNodeResponse:
    """Destroy a node container and clean up resources."""
    import time as _time
    _t0 = _time.monotonic()

    # Auto-detect provider: check if a libvirt domain exists for this node.
    if provider == "auto":
        lv = get_provider("libvirt")
        if lv:
            domain_name = lv._domain_name(lab_id, node_name)
            try:
                lv.conn.lookupByName(domain_name)
                provider = "libvirt"
            except Exception:
                provider = "docker"
        else:
            provider = "docker"

    provider_instance = get_provider_for_request(provider)
    workspace = get_workspace(lab_id)

    result = await provider_instance.destroy_node(
        lab_id=lab_id,
        node_name=node_name,
        workspace=workspace,
    )

    elapsed_ms = int((_time.monotonic() - _t0) * 1000)
    logger.info(
        "Container operation",
        extra={
            "event": "container_operation",
            "operation": "destroy",
            "lab_id": lab_id,
            "node_name": node_name,
            "provider": provider,
            "result": "success" if result.success else "error",
            "duration_ms": elapsed_ms,
            "error": result.error if not result.success else None,
        },
    )

    from agent.metrics import node_operation_duration, node_operation_errors
    node_operation_duration.labels(
        operation="destroy",
        status="success" if result.success else "error",
    ).observe(elapsed_ms / 1000)
    if not result.success:
        node_operation_errors.labels(operation="destroy").inc()

    return DestroyNodeResponse(
        success=result.success,
        container_removed=result.success,
        error=result.error,
        duration_ms=elapsed_ms,
    )


@router.get("/labs/{lab_id}/nodes/{node_name}/linux-interfaces")
async def list_node_linux_interfaces(lab_id: str, node_name: str) -> dict:
    """List Linux interface names inside a container network namespace."""
    try:
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node_name)

        def _get_container_pid() -> int | None:
            c = provider.docker.containers.get(container_name)
            return c.attrs.get("State", {}).get("Pid")

        pid = await asyncio.to_thread(_get_container_pid)
        if not pid:
            return {"container": container_name, "interfaces": [], "error": "Container not running"}

        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", str(pid), "-n",
            "ip", "-o", "link", "show",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {
                "container": container_name,
                "interfaces": [],
                "error": stderr.decode().strip() or "Failed to list interfaces",
            }

        interfaces = []
        for line in stdout.decode().strip().split("\n"):
            parts = line.split(":", 2)
            if len(parts) >= 2:
                name = parts[1].strip().split("@")[0]
                interfaces.append(name)

        return {"container": container_name, "interfaces": interfaces, "error": None}
    except Exception as e:
        return {"container": node_name, "interfaces": [], "error": str(e)}


# --- Readiness & Post-Boot Endpoints ---

@router.get("/labs/{lab_id}/nodes/{node_name}/ready")
async def check_node_ready(
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
    kind: str | None = None,
) -> dict:
    """Check if a node has completed its boot sequence.

    Returns readiness status based on vendor-specific probes that check
    container logs or CLI output for boot completion patterns.

    When a node first becomes ready, any configured post-boot commands
    are executed (e.g., cEOS iptables fixes).

    Args:
        lab_id: Lab identifier
        node_name: Node name within the lab
        provider_type: Optional provider type ("docker" or "libvirt").
                       If not specified, tries Docker first, then libvirt.
        kind: Optional device kind. Required for libvirt VMs if not
              auto-detected.
    """
    from agent.readiness import (
        get_readiness_timeout,
    )

    # Try libvirt if explicitly requested or if Docker fails
    if provider_type == "libvirt":
        return await _check_libvirt_readiness(lab_id, node_name, kind)

    # Try Docker first
    docker_provider = get_provider("docker")
    if docker_provider is not None:
        container_name = docker_provider.get_container_name(lab_id, node_name)

        # Get the node kind to determine appropriate probe
        try:
            def _sync_get_container_labels(name):
                client = get_docker_client()
                c = client.containers.get(name)
                return {
                    "kind": c.labels.get("archetype.node_kind", ""),
                    "readiness_probe": c.labels.get("archetype.readiness_probe"),
                    "readiness_pattern": c.labels.get("archetype.readiness_pattern"),
                    "readiness_timeout": c.labels.get("archetype.readiness_timeout"),
                }

            info = await asyncio.to_thread(_sync_get_container_labels, container_name)
            detected_kind = info["kind"]
            kind = kind or detected_kind
            readiness_probe = info["readiness_probe"]
            readiness_pattern = info["readiness_pattern"]
            timeout_override = None
            timeout_raw = info["readiness_timeout"]
            if timeout_raw:
                try:
                    parsed_timeout = int(timeout_raw)
                    if parsed_timeout > 0:
                        timeout_override = parsed_timeout
                except ValueError:
                    timeout_override = None

            # Get and run the appropriate probe
            probe = get_probe_for_vendor(
                kind,
                readiness_probe=readiness_probe,
                readiness_pattern=readiness_pattern,
            )
            result = await probe.check(container_name)

            # If ready, run post-boot commands (idempotent - only runs once per container)
            if result.is_ready:
                await run_post_boot_commands(container_name, kind)

            return {
                "is_ready": result.is_ready,
                "message": result.message,
                "progress_percent": result.progress_percent,
                "details": result.details,
                "timeout": timeout_override if timeout_override is not None else get_readiness_timeout(kind),
                "provider": "docker",
            }
        except Exception:
            # Docker container not found, try libvirt if no provider specified
            if provider_type is None:
                return await _check_libvirt_readiness(lab_id, node_name, kind)
            return {
                "is_ready": False,
                "message": "Container not found",
                "progress_percent": 0,
                "provider": "docker",
            }

    # No Docker provider, try libvirt
    return await _check_libvirt_readiness(lab_id, node_name, kind)


@router.post("/labs/{lab_id}/nodes/{node_name}/run-post-boot")
async def run_node_post_boot(lab_id: str, node_name: str) -> dict:
    """Force re-run post-boot commands for a node.

    Clears the idempotency guard and re-executes vendor post-boot commands
    (e.g., cEOS iptables cleanup). Useful when cEOS re-adds DROP rules.
    """
    from agent.readiness import clear_post_boot_state

    docker_provider = get_provider("docker")
    if docker_provider is None:
        raise HTTPException(status_code=400, detail="Docker provider not available")

    container_name = docker_provider.get_container_name(lab_id, node_name)

    try:
        def _sync_get_node_kind(name):
            client = get_docker_client()
            c = client.containers.get(name)
            return c.labels.get("archetype.node_kind", "")

        kind = await asyncio.to_thread(_sync_get_node_kind, container_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Container {node_name} not found")

    # Clear idempotency guard and re-run
    clear_post_boot_state(container_name)
    success = await run_post_boot_commands(container_name, kind)

    return {"success": success, "container": container_name, "kind": kind}


@router.post("/labs/{lab_id}/nodes/{node_name}/exec")
async def exec_in_node(lab_id: str, node_name: str, request: Request) -> dict:
    """Execute a command inside a Docker container.

    Body: {"cmd": "command to run"}
    Returns: {"exit_code": int, "output": str}
    """
    body = await request.json()
    cmd = body.get("cmd", "")
    if not cmd:
        raise HTTPException(status_code=400, detail="Missing 'cmd' field")

    docker_provider = get_provider("docker")
    if docker_provider is None:
        raise HTTPException(status_code=400, detail="Docker provider not available")

    container_name = docker_provider.get_container_name(lab_id, node_name)

    def _exec_sync():
        client = get_docker_client()
        container = client.containers.get(container_name)
        exit_code, output = container.exec_run(["sh", "-c", cmd], demux=False)
        output_str = output.decode("utf-8", errors="replace") if output else ""
        return {"exit_code": exit_code, "output": output_str}

    try:
        return await asyncio.to_thread(_exec_sync)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/labs/{lab_id}/nodes/{node_name}/cli-verify")
async def verify_node_cli(
    lab_id: str,
    node_name: str,
    request: CliVerifyRequest,
    provider: str = "libvirt",
) -> CliVerifyResponse:
    """Run verification CLI commands and capture output.

    This endpoint is designed for post-boot troubleshooting checks where
    stable, structured command output is needed from VM consoles.
    """
    if provider != "libvirt":
        raise HTTPException(status_code=400, detail="cli-verify currently supports provider=libvirt only")

    libvirt_provider = get_provider("libvirt")
    if libvirt_provider is None:
        raise HTTPException(status_code=503, detail="Libvirt provider not available")

    try:
        runtime_profile = libvirt_provider.get_runtime_profile(lab_id, node_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Libvirt node not found: {e}") from e

    domain_name = runtime_profile.get("domain_name")
    if not domain_name:
        raise HTTPException(status_code=500, detail="Unable to resolve libvirt domain name")

    runtime_kind = ((runtime_profile.get("runtime") or {}).get("kind") or "").strip()
    kind = (request.kind or runtime_kind or libvirt_provider.get_node_kind(lab_id, node_name) or "").strip()
    if not kind:
        raise HTTPException(status_code=400, detail="Unable to determine node kind for CLI verification")

    commands = [cmd.strip() for cmd in request.commands if isinstance(cmd, str) and cmd.strip()]
    if not commands:
        if kind == "cisco_n9kv":
            commands = [
                "show running-config | include system no poap",
                "show startup-config | include system no poap",
                "show startup-config | include hostname",
                "show boot | include POAP",
            ]
        else:
            raise HTTPException(status_code=400, detail="No commands provided for CLI verification")

    from agent.console_extractor import run_vm_cli_commands

    try:
        result = await asyncio.to_thread(
            run_vm_cli_commands,
            domain_name=domain_name,
            kind=kind,
            commands=commands,
            libvirt_uri=getattr(libvirt_provider, "_uri", "qemu:///system"),
            username=request.username,
            password=request.password,
            enable_password=request.enable_password,
            prompt_pattern=request.prompt_pattern,
            paging_disable=request.paging_disable,
            attempt_enable=request.attempt_enable,
            timeout=request.timeout,
            retries=request.retries,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CLI verification execution failed: {e}") from e

    return CliVerifyResponse(
        success=result.success,
        provider="libvirt",
        node_name=node_name,
        domain_name=domain_name,
        commands_run=result.commands_run,
        outputs=[
            CliCommandOutput(
                command=item.command,
                success=item.success,
                output=item.output,
                error=item.error or None,
            )
            for item in result.outputs
        ],
        error=result.error or None,
    )


async def _check_libvirt_readiness(
    lab_id: str,
    node_name: str,
    kind: str | None,
) -> dict:
    """Check readiness for a libvirt VM.

    Args:
        lab_id: Lab identifier
        node_name: Node name
        kind: Device kind for vendor config lookup

    Returns:
        Readiness status dict
    """
    libvirt_provider = get_provider("libvirt")
    if libvirt_provider is None:
        return {
            "is_ready": False,
            "message": "Libvirt provider not available",
            "progress_percent": None,
            "provider": "libvirt",
        }

    if kind is None:
        return {
            "is_ready": False,
            "message": "Device kind required for VM readiness check",
            "progress_percent": None,
            "provider": "libvirt",
        }

    result = await libvirt_provider.check_readiness(lab_id, node_name, kind)
    timeout = libvirt_provider.get_readiness_timeout(kind, lab_id, node_name)

    return {
        "is_ready": result.is_ready,
        "message": result.message,
        "progress_percent": result.progress_percent,
        "details": result.details,
        "timeout": timeout,
        "provider": "libvirt",
    }


@router.get("/labs/{lab_id}/nodes/{node_name}/runtime")
async def get_node_runtime_profile(
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
) -> dict:
    """Get runtime profile for a node from the active provider."""
    if provider_type == "libvirt":
        libvirt_provider = get_provider("libvirt")
        if libvirt_provider is None:
            raise HTTPException(status_code=404, detail="Libvirt provider not available")
        try:
            return libvirt_provider.get_runtime_profile(lab_id, node_name)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Libvirt node not found: {e}") from e

    if provider_type == "docker":
        try:
            candidate_names = [node_name]
            docker_provider = get_provider("docker")
            if docker_provider and hasattr(docker_provider, "get_container_name"):
                try:
                    canonical = docker_provider.get_container_name(lab_id, node_name)
                    if canonical and canonical not in candidate_names:
                        candidate_names.append(canonical)
                except Exception:
                    pass

            def _sync_get_runtime_profile(candidates):
                client = get_docker_client()
                for candidate in candidates:
                    try:
                        c = client.containers.get(candidate)
                        host_cfg = c.attrs.get("HostConfig", {})
                        memory_bytes = int(host_cfg.get("Memory") or 0)
                        memory_mb = int(memory_bytes / (1024 * 1024)) if memory_bytes > 0 else None
                        cpu_quota = int(host_cfg.get("CpuQuota") or 0)
                        cpu_period = int(host_cfg.get("CpuPeriod") or 0)
                        cpu = None
                        if cpu_quota > 0 and cpu_period > 0:
                            cpu = round(cpu_quota / cpu_period, 2)
                        return {
                            "provider": "docker",
                            "node_name": node_name,
                            "runtime_name": c.name,
                            "state": c.status,
                            "runtime": {
                                "image": (c.image.tags[0] if c.image and c.image.tags else None),
                                "memory": memory_mb,
                                "cpu": cpu,
                            },
                        }
                    except docker.errors.NotFound:
                        continue
                return None

            result = await asyncio.to_thread(_sync_get_runtime_profile, candidate_names)
            if result is None:
                raise HTTPException(status_code=404, detail=f"Docker node not found: {node_name}")
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Docker node not found: {e}") from e

    # Auto-detect: try Docker first, then libvirt
    try:
        return await get_node_runtime_profile(lab_id, node_name, provider_type="docker")
    except HTTPException:
        return await get_node_runtime_profile(lab_id, node_name, provider_type="libvirt")
