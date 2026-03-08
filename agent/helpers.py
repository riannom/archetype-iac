"""Pure and near-pure helper functions for the Archetype agent.

Extracted from agent/main.py to reduce its size. These functions do not
depend on the FastAPI ``app`` object.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import docker
from fastapi import HTTPException

import agent.agent_state as _state
from agent.config import settings
from agent.docker_client import get_docker_client
from agent.n9kv_poap import render_poap_script
from agent.providers import NodeStatus as ProviderNodeStatus, get_provider, list_providers
from agent.providers.base import Provider as BaseProvider
from agent.schemas import (
    AgentCapabilities,
    AgentInfo,
    DockerImageInfo,
    DockerPruneRequest,
    DockerPruneResponse,
    NodeStatus,
    Provider,
)
from agent.updater import detect_deployment_mode

logger = logging.getLogger(__name__)


# =========================================================================
# Workspace / Provider helpers
# =========================================================================

def get_workspace(lab_id: str) -> Path:
    """Get workspace directory for a lab."""
    if not _state._SAFE_ID_RE.match(lab_id):
        raise ValueError(f"Invalid lab_id: {lab_id}")
    workspace = Path(settings.workspace_path) / lab_id
    resolved = workspace.resolve()
    if not resolved.is_relative_to(Path(settings.workspace_path).resolve()):
        raise ValueError(f"Path traversal detected in lab_id: {lab_id}")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def get_provider_for_request(provider_name: str = "docker") -> BaseProvider:
    """Get a provider instance for handling a request.

    Args:
        provider_name: Name of the provider to use (default: docker)

    Returns:
        Provider instance

    Raises:
        HTTPException: If the requested provider is not available
    """
    provider = get_provider(provider_name)
    if provider is None:
        available = list_providers()
        raise HTTPException(
            status_code=503,
            detail=f"Provider '{provider_name}' not available. Available: {available}"
        )
    return provider


def provider_status_to_schema(status: ProviderNodeStatus) -> NodeStatus:
    """Convert provider NodeStatus to schema NodeStatus."""
    mapping = {
        ProviderNodeStatus.PENDING: NodeStatus.PENDING,
        ProviderNodeStatus.STARTING: NodeStatus.STARTING,
        ProviderNodeStatus.RUNNING: NodeStatus.RUNNING,
        ProviderNodeStatus.STOPPING: NodeStatus.STOPPING,
        ProviderNodeStatus.STOPPED: NodeStatus.STOPPED,
        ProviderNodeStatus.ERROR: NodeStatus.ERROR,
        ProviderNodeStatus.UNKNOWN: NodeStatus.UNKNOWN,
    }
    return mapping.get(status, NodeStatus.UNKNOWN)


# =========================================================================
# Agent Info & Capabilities
# =========================================================================

def get_capabilities() -> AgentCapabilities:
    """Determine agent capabilities based on config and available tools."""
    providers = []
    if settings.enable_docker:
        providers.append(Provider.DOCKER)
    if settings.enable_libvirt:
        providers.append(Provider.LIBVIRT)

    features = ["console", "status"]
    if settings.enable_vxlan:
        features.append("vxlan")

    return AgentCapabilities(
        providers=providers,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        features=features,
    )


def _sync_get_resource_usage() -> dict:
    """Gather system resource metrics (synchronous implementation)."""
    import psutil

    try:
        # CPU usage (average across all cores)
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count() or 0

        # Memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_gb = round(memory.used / (1024 ** 3), 2)
        memory_total_gb = round(memory.total / (1024 ** 3), 2)

        # Disk usage for workspace partition
        disk_path = settings.workspace_path if settings.workspace_path else "/"
        disk = psutil.disk_usage(disk_path)
        disk_percent = disk.percent
        disk_used_gb = round(disk.used / (1024 ** 3), 2)
        disk_total_gb = round(disk.total / (1024 ** 3), 2)

        # Docker container counts and details
        containers_running = 0
        containers_total = 0
        container_details = []
        try:
            client = get_docker_client()

            container_list = client.api.containers(all=True)
            all_containers = []
            for container_info in container_list:
                try:
                    c = client.containers.get(container_info["Id"])
                    all_containers.append(c)
                except (docker.errors.NotFound, docker.errors.APIError) as e:
                    container_name = container_info.get("Names", ["unknown"])[0].lstrip("/")
                    logger.warning(f"Skipping corrupted/dead container {container_name}: {e}")
                    continue

            for c in all_containers:
                labels = c.labels
                is_archetype_node = bool(labels.get("archetype.node_name"))
                is_archetype_system = c.name.startswith("archetype-") and not is_archetype_node

                if not is_archetype_node and not is_archetype_system:
                    continue

                containers_total += 1
                if c.status == "running":
                    containers_running += 1

                lab_prefix = labels.get("archetype.lab_id", "")
                node_name = labels.get("archetype.node_name")
                node_kind = labels.get("archetype.node_kind")

                try:
                    image_name = c.image.tags[0] if c.image.tags else c.image.short_id
                except Exception:
                    image_name = "unknown"

                host_config = c.attrs.get("HostConfig", {})
                nano_cpus = host_config.get("NanoCpus") or 0
                container_vcpus = nano_cpus / 1e9 if nano_cpus else 1
                mem_limit = host_config.get("Memory") or 0
                container_memory_mb = mem_limit // (1024 * 1024) if mem_limit else 0

                container_details.append({
                    "name": c.name,
                    "status": c.status,
                    "lab_prefix": lab_prefix,
                    "node_name": node_name,
                    "node_kind": node_kind,
                    "image": image_name,
                    "is_system": is_archetype_system,
                    "vcpus": container_vcpus,
                    "memory_mb": container_memory_mb,
                })
        except Exception as e:
            logger.warning(f"Docker container collection failed: {type(e).__name__}: {e}")

        return {
            "cpu_percent": cpu_percent,
            "cpu_count": cpu_count,
            "memory_percent": memory_percent,
            "memory_used_gb": memory_used_gb,
            "memory_total_gb": memory_total_gb,
            "disk_percent": disk_percent,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "containers_running": containers_running,
            "containers_total": containers_total,
            "container_details": container_details,
        }
    except Exception as e:
        logger.warning(f"Failed to gather resource usage: {e}")
        return {}


async def get_resource_usage() -> dict:
    """Gather system resource metrics for heartbeat (async wrapper).

    Docker stats run in asyncio.to_thread (thread-safe).
    Libvirt stats run through the provider's single-thread executor
    to avoid thread-safety violations.
    """
    from agent.providers.registry import get_provider as _get_provider

    result = await asyncio.to_thread(_sync_get_resource_usage)
    if not result:
        return result

    vms_running = 0
    vms_total = 0
    vm_details: list[dict] = []
    if settings.enable_libvirt:
        try:
            libvirt_provider = _get_provider("libvirt")
            if libvirt_provider:
                vm_details = await libvirt_provider._run_libvirt(
                    libvirt_provider.get_vm_stats_sync
                )
                for vm in vm_details:
                    vms_total += 1
                    if vm.get("status") == "running":
                        vms_running += 1
        except Exception as e:
            logger.warning(f"Libvirt VM collection failed: {type(e).__name__}: {e}")

    result["vms_running"] = vms_running
    result["vms_total"] = vms_total
    result["vm_details"] = vm_details
    return result


def get_agent_info() -> AgentInfo:
    """Build agent info for registration."""
    advertise_host = settings.advertise_host
    if not advertise_host:
        if settings.agent_host != "0.0.0.0":
            advertise_host = settings.agent_host
        elif settings.local_ip:
            advertise_host = settings.local_ip
        else:
            detected_ip = _state._detect_local_ip()
            if detected_ip:
                advertise_host = detected_ip
            else:
                advertise_host = settings.agent_name

    address = f"{advertise_host}:{settings.agent_port}"

    from agent.network.transport import get_data_plane_ip
    dp_ip = get_data_plane_ip()

    return AgentInfo(
        agent_id=_state.AGENT_ID,
        name=settings.agent_name,
        address=address,
        capabilities=get_capabilities(),
        started_at=_state.AGENT_STARTED_AT,
        is_local=settings.is_local,
        deployment_mode=detect_deployment_mode().value,
        data_plane_ip=dp_ip,
    )


# Default memory for containers without explicit memory limits
DEFAULT_CONTAINER_MEMORY_MB = 1024


def _get_allocated_resources(usage: dict) -> dict:
    """Sum CPU and memory allocations from running Docker containers + libvirt VMs."""
    total_vcpus = 0
    total_memory_mb = 0

    for c in usage.get("container_details", []):
        if c.get("status") != "running" or c.get("is_system"):
            continue
        total_vcpus += c.get("vcpus", 1)
        total_memory_mb += c.get("memory_mb", DEFAULT_CONTAINER_MEMORY_MB)

    for vm in usage.get("vm_details", []):
        if vm.get("status") != "running":
            continue
        total_vcpus += vm.get("vcpus", 1)
        total_memory_mb += vm.get("memory_mb", 0)

    return {"vcpus": total_vcpus, "memory_mb": total_memory_mb}


# =========================================================================
# Validation
# =========================================================================

def _validate_port_name(name: str) -> bool:
    """Validate OVS port name to prevent command injection."""
    return bool(name) and len(name) <= 64 and _state._PORT_NAME_RE.match(name) is not None


def _validate_container_name(name: str) -> bool:
    """Validate container name has expected prefix."""
    return bool(name) and _state._CONTAINER_PREFIX_RE.match(name) is not None


# =========================================================================
# Config Loading
# =========================================================================

def _load_node_startup_config(lab_id: str, node_name: str) -> str:
    """Load startup-config content from workspace for POAP/bootstrap delivery."""
    if not _state._SAFE_ID_RE.match(lab_id):
        raise HTTPException(status_code=400, detail="Invalid lab_id")
    if not _state._SAFE_ID_RE.match(node_name):
        raise HTTPException(status_code=400, detail="Invalid node_name")

    config_path = get_workspace(lab_id) / "configs" / node_name / "startup-config"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="startup-config not found")
    content = config_path.read_text(encoding="utf-8")
    if not content.strip():
        raise HTTPException(status_code=404, detail="startup-config is empty")
    return content


def _render_n9kv_poap_script(config_url: str) -> str:
    """Render a minimal NX-OS POAP Python script that applies startup config."""
    return render_poap_script(config_url)


# =========================================================================
# OVS / VLAN Helpers
# =========================================================================

@dataclass
class OVSPortInfo:
    """Resolved OVS port for a node interface."""
    port_name: str   # OVS port name (e.g., "vh3a4b5" for Docker, "vnet3" for libvirt)
    vlan_tag: int     # Current VLAN tag on this port
    provider: str     # "docker" or "libvirt"


def _interface_name_to_index(interface_name: str) -> int:
    """Convert a normalized ethN name to a 0-based data interface index.

    Normalized names always use eth1 = first data port, eth2 = second, etc.
    (eth0 is Docker management). So the index is simply number - 1.

    Examples:
        eth1 -> 0, eth2 -> 1, eth3 -> 2
    """
    match = re.search(r"(\d+)$", interface_name)
    if not match:
        raise ValueError(f"Cannot extract interface index from '{interface_name}'")
    number = int(match.group(1))
    return max(0, number - 1)


def _resolve_ifindex_sync(
    container_name: str,
    interface_name: str,
) -> int | None:
    """Read peer ifindex for a container interface -- blocking Docker call."""
    try:
        client = get_docker_client()
        container = client.containers.get(container_name)
        exit_code, output = container.exec_run(
            ["cat", f"/sys/class/net/{interface_name}/iflink"],
            demux=False,
        )
        if exit_code != 0:
            return None
        return int(output.decode().strip())
    except Exception:
        return None


async def _resolve_ovs_port_via_ifindex(
    container_name: str,
    interface_name: str,
) -> tuple[str, int] | None:
    """Find the correct OVS port for a container interface using ifindex matching.

    The Docker OVS plugin can swap veth-to-interface mappings after restart.
    This function uses kernel ifindex to find the correct host-side veth for
    a given container interface, then reads its OVS VLAN tag.

    Returns:
        (port_name, vlan_tag) tuple, or None if not found.
    """
    peer_ifindex = await asyncio.to_thread(_resolve_ifindex_sync, container_name, interface_name)
    if peer_ifindex is None:
        return None

    proc = await asyncio.create_subprocess_exec(
        "ovs-vsctl",
        "--data=bare",
        "--no-heading",
        "--columns=name",
        "find",
        "Interface",
        f"ifindex={peer_ifindex}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode == 0:
        exact_matches = [
            port_name.strip()
            for port_name in stdout.decode().strip().splitlines()
            if port_name.strip()
        ]
        if exact_matches:
            vlan_tag = await _ovs_get_port_vlan(exact_matches[0])
            return (exact_matches[0], vlan_tag or 0)

    return None


async def _resolve_ovs_port(
    lab_id: str,
    node_name: str,
    interface_name: str,
) -> OVSPortInfo | None:
    """Find the OVS port for a node interface, trying Docker then libvirt.

    Both Docker containers and libvirt VMs connect to the same arch-ovs
    bridge. This function finds the correct OVS port regardless of provider.

    Returns:
        OVSPortInfo with port name, current VLAN, and provider type.
        None if the port cannot be found via any provider.
    """
    libvirt_provider = get_provider("libvirt")
    libvirt_kind: str | None = None
    if libvirt_provider is not None:
        try:
            libvirt_kind = await libvirt_provider.get_node_kind_async(lab_id, node_name)
        except Exception:
            libvirt_kind = None
    is_libvirt_node = libvirt_kind is not None
    logger.debug(
        "Resolving OVS port for %s:%s in lab %s (libvirt_node=%s, libvirt_kind=%s)",
        node_name,
        interface_name,
        lab_id,
        is_libvirt_node,
        libvirt_kind,
    )

    # --- Try Docker first, using ifindex verification to prevent port swap bugs ---
    docker_provider = get_provider("docker")
    if docker_provider is not None and not is_libvirt_node:
        try:
            container_name = docker_provider.get_container_name(lab_id, node_name)
            resolved = await _resolve_ovs_port_via_ifindex(
                container_name, interface_name
            )
            if resolved:
                logger.debug(
                    "Resolved OVS port for %s:%s via docker ifindex lookup: %s",
                    node_name,
                    interface_name,
                    resolved[0],
                )
                return OVSPortInfo(
                    port_name=resolved[0],
                    vlan_tag=resolved[1],
                    provider="docker",
                )
            # Ifindex lookup failed (container not running?), fall back to plugin
            plugin = _get_docker_ovs_plugin()
            ep = await plugin._discover_endpoint(lab_id, container_name, interface_name)
            if not ep:
                for endpoint in plugin.endpoints.values():
                    if endpoint.container_name == container_name and endpoint.interface_name == interface_name:
                        ep = endpoint
                        break
            if ep:
                if not await plugin._validate_endpoint_exists(ep):
                    logger.warning(
                        f"Endpoint for {container_name}:{interface_name} stale "
                        f"-- OVS port {ep.host_veth} missing"
                    )
                    return None
                logger.debug(
                    "Resolved OVS port for %s:%s via docker plugin state: %s",
                    node_name,
                    interface_name,
                    ep.host_veth,
                )
                return OVSPortInfo(
                    port_name=ep.host_veth,
                    vlan_tag=ep.vlan_tag,
                    provider="docker",
                )
        except Exception as e:
            logger.debug(
                "Docker OVS lookup failed for %s:%s in lab %s: %s",
                node_name,
                interface_name,
                lab_id,
                e,
            )

    # --- Try libvirt (OVS/MAC introspection) ---
    if libvirt_provider is not None:
        try:
            intf_index = _interface_name_to_index(interface_name)
            port_name = await libvirt_provider.get_vm_interface_port(
                lab_id, node_name, intf_index,
            )
            if port_name:
                vlan_tag = await _ovs_get_port_vlan(port_name)
                if vlan_tag is None:
                    vlans = libvirt_provider.get_node_vlans(lab_id, node_name)
                    vlan_tag = vlans[intf_index] if intf_index < len(vlans) else 0
                logger.debug(
                    "Resolved OVS port for %s:%s via libvirt lookup: %s",
                    node_name,
                    interface_name,
                    port_name,
                )
                return OVSPortInfo(
                    port_name=port_name,
                    vlan_tag=vlan_tag,
                    provider="libvirt",
                )
        except Exception as e:
            logger.debug(f"Libvirt lookup failed for {node_name}:{interface_name}: {e}")

    logger.debug(
        "Failed to resolve OVS port for %s:%s in lab %s",
        node_name,
        interface_name,
        lab_id,
    )
    return None


async def _ovs_set_port_vlan(port_name: str, vlan_tag: int) -> bool:
    """Set VLAN tag on an OVS port."""
    proc = await asyncio.create_subprocess_exec(
        "ovs-vsctl", "set", "port", port_name, f"tag={vlan_tag}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"Failed to set VLAN {vlan_tag} on port {port_name}: {stderr.decode().strip()}")
        return False
    return True


async def _ovs_get_port_vlan(port_name: str) -> int | None:
    """Get VLAN tag from an OVS port."""
    proc = await asyncio.create_subprocess_exec(
        "ovs-vsctl", "get", "port", port_name, "tag",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    tag = stdout.decode().strip()
    if not tag or tag == "[]":
        return None
    try:
        return int(tag)
    except ValueError:
        return None


async def _ovs_list_used_vlans(bridge: str) -> set[int]:
    """Return the set of VLAN tags currently used on an OVS bridge."""
    used: set[int] = set()
    proc = await asyncio.create_subprocess_exec(
        "ovs-vsctl", "list-ports", bridge,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0 or not stdout:
        return used

    ports = [p.strip() for p in stdout.decode().splitlines() if p.strip()]
    for p in ports:
        tag = await _ovs_get_port_vlan(p)
        if tag is not None:
            used.add(tag)
    return used


def _pick_free_vlan(used: set[int], start: int, end: int) -> int | None:
    """Pick the first free VLAN tag in [start, end]."""
    for vlan in range(start, end + 1):
        if vlan not in used:
            return vlan
    return None


def _pick_isolation_vlan(used: set[int], bridge: str, port_name: str) -> int | None:
    """Pick an isolation VLAN from ordered pools with fallback."""
    vlan = _pick_free_vlan(used, 100, 2049)
    if vlan is not None:
        return vlan

    logger.warning(
        "Isolated VLAN range exhausted on %s while disconnecting %s; "
        "falling back to linked range",
        bridge,
        port_name,
    )
    return _pick_free_vlan(used, 2050, 4000)


async def _ovs_allocate_link_vlan(bridge: str) -> int | None:
    """Allocate a fresh shared VLAN for an active link.

    Prefers linked range 2050-4000 and falls back to isolated range 100-2049.
    """
    used = await _ovs_list_used_vlans(bridge)
    vlan = _pick_free_vlan(used, 2050, 4000)
    if vlan is not None:
        return vlan

    logger.warning(
        "Linked VLAN range exhausted on %s while creating link; "
        "falling back to isolated range",
        bridge,
    )
    return _pick_free_vlan(used, 100, 2049)


async def _ovs_allocate_unique_vlan(port_name: str) -> int | None:
    """Allocate a fresh unique VLAN for a port to isolate it.

    Uses deterministic ordered allocation with collision checks:
    1. Prefer isolated range (100-2049)
    2. Fall back to linked range (2050-4000) if isolated is exhausted
    """
    bridge = settings.ovs_bridge_name or "arch-ovs"

    used = await _ovs_list_used_vlans(bridge)
    new_vlan = _pick_isolation_vlan(used, bridge, port_name)
    if new_vlan is None:
        logger.error("No free VLAN available on %s for port %s", bridge, port_name)
        return None

    if await _ovs_set_port_vlan(port_name, new_vlan):
        return new_vlan
    return None


# =========================================================================
# Docker Helpers
# =========================================================================

def _get_docker_ovs_plugin():
    """Get the Docker OVS plugin instance."""
    from agent.network.docker_plugin import get_docker_ovs_plugin
    return get_docker_ovs_plugin()


# Alias for test patchability (tests use agent.main.get_docker_ovs_plugin)
get_docker_ovs_plugin = _get_docker_ovs_plugin


def _get_docker_images() -> list[DockerImageInfo]:
    """Get list of Docker images on this agent."""
    try:
        from agent.image_metadata import lookup_device_id_by_image_id

        client = get_docker_client()
        images = []

        for img in client.images.list():
            image_id = img.id
            tags = img.tags or []
            size_bytes = img.attrs.get("Size", 0)
            created = img.attrs.get("Created", None)
            device_id = lookup_device_id_by_image_id(image_id)

            images.append(DockerImageInfo(
                id=image_id,
                tags=tags,
                size_bytes=size_bytes,
                created=created,
                device_id=device_id,
                kind="docker",
            ))

        return images
    except Exception as e:
        logger.error(f"Error listing Docker images: {e}")
        return []


def _get_file_images() -> list[DockerImageInfo]:
    """Get list of file-based images on this agent."""
    try:
        from agent.image_metadata import lookup_device_id_by_path

        roots: list[Path] = []
        for raw_path in [settings.image_store_path, settings.qcow2_store_path]:
            if not raw_path:
                continue
            path = Path(raw_path)
            if path not in roots:
                roots.append(path)

        images: list[DockerImageInfo] = []
        seen_paths: set[Path] = set()
        valid_suffixes = {".qcow2", ".img", ".iol"}

        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in valid_suffixes:
                    continue
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                stat = path.stat()
                created = datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                ).isoformat()
                images.append(
                    DockerImageInfo(
                        id=str(resolved),
                        tags=[],
                        size_bytes=stat.st_size,
                        created=created,
                        device_id=lookup_device_id_by_path(str(resolved)),
                        kind=path.suffix.lower().lstrip("."),
                        reference=str(resolved),
                    )
                )

        return images
    except Exception as e:
        logger.error(f"Error listing file-based images: {e}")
        return []


def _sync_prune_docker(request: DockerPruneRequest) -> DockerPruneResponse:
    """Run Docker prune operations synchronously (called via asyncio.to_thread)."""
    images_removed = 0
    build_cache_removed = 0
    volumes_removed = 0
    containers_removed = 0
    networks_removed = 0
    space_reclaimed = 0
    errors = []

    try:
        client = get_docker_client()

        # Get images used by running containers (to protect them)
        protected_image_ids = set()
        try:
            containers = client.containers.list(all=True)
            for container in containers:
                labels = container.labels
                lab_id = labels.get("archetype.lab_id", "")
                is_valid_lab = lab_id in request.valid_lab_ids if lab_id else False

                if is_valid_lab or container.status == "running":
                    if container.image:
                        protected_image_ids.add(container.image.id)

        except Exception as e:
            errors.append(f"Error getting container info: {e}")
            logger.warning(f"Error getting container info for protection: {e}")

        if request.prune_dangling_images:
            try:
                result = client.images.prune(filters={"dangling": True})
                deleted = result.get("ImagesDeleted") or []
                images_removed = len([d for d in deleted if d.get("Deleted")])
                space_reclaimed += result.get("SpaceReclaimed", 0)
                logger.info(
                    f"Pruned {images_removed} dangling images, reclaimed "
                    f"{result.get('SpaceReclaimed', 0)} bytes"
                )
            except Exception as e:
                errors.append(f"Error pruning images: {e}")
                logger.warning(f"Error pruning dangling images: {e}")

        if request.prune_build_cache:
            try:
                result = client.api.prune_builds()
                build_cache_removed = len(result.get("CachesDeleted") or [])
                space_reclaimed += result.get("SpaceReclaimed", 0)
                logger.info(
                    f"Pruned {build_cache_removed} build cache entries, reclaimed "
                    f"{result.get('SpaceReclaimed', 0)} bytes"
                )
            except Exception as e:
                errors.append(f"Error pruning build cache: {e}")
                logger.warning(f"Error pruning build cache: {e}")

        if request.prune_unused_volumes:
            try:
                result = client.volumes.prune()
                deleted = result.get("VolumesDeleted") or []
                volumes_removed = len(deleted)
                space_reclaimed += result.get("SpaceReclaimed", 0)
                logger.info(
                    f"Pruned {volumes_removed} volumes, reclaimed "
                    f"{result.get('SpaceReclaimed', 0)} bytes"
                )
            except Exception as e:
                errors.append(f"Error pruning volumes: {e}")
                logger.warning(f"Error pruning volumes: {e}")

        if request.prune_stopped_containers:
            try:
                stopped = client.containers.list(filters={"status": "exited"}, sparse=True)
                for container in stopped:
                    labels = container.labels
                    lab_id = labels.get("archetype.lab_id", "")
                    if lab_id and lab_id in request.valid_lab_ids:
                        continue
                    try:
                        container.remove(force=False)
                        containers_removed += 1
                    except Exception as ce:
                        errors.append(f"Error removing container {container.short_id}: {ce}")
                logger.info(f"Removed {containers_removed} stopped containers")
            except Exception as e:
                errors.append(f"Error pruning containers: {e}")
                logger.warning(f"Error pruning stopped containers: {e}")

        if request.prune_unused_networks:
            try:
                result = client.networks.prune()
                pruned = result.get("NetworksDeleted") or []
                networks_removed = len(pruned)
                logger.info(f"Pruned {networks_removed} unused networks")
            except Exception as e:
                errors.append(f"Error pruning networks: {e}")
                logger.warning(f"Error pruning networks: {e}")

        return DockerPruneResponse(
            success=True,
            images_removed=images_removed,
            build_cache_removed=build_cache_removed,
            volumes_removed=volumes_removed,
            containers_removed=containers_removed,
            networks_removed=networks_removed,
            space_reclaimed=space_reclaimed,
            errors=errors,
        )

    except Exception as e:
        logger.error(f"Docker prune failed: {e}")
        return DockerPruneResponse(
            success=False,
            errors=[str(e)],
        )


# =========================================================================
# Background Helpers
# =========================================================================

async def _fix_running_interfaces() -> None:
    """Fix interface naming/attachments for already-running containers after agent restart."""
    await asyncio.sleep(5)

    provider = get_provider("docker")
    if provider is None:
        return

    for attempt in range(2):
        try:
            containers = await asyncio.to_thread(
                provider.docker.containers.list,
                all=True,
                filters={"label": "archetype.lab_id"},
            )
        except Exception as e:
            logger.warning(f"Failed to list containers for interface fixup: {e}")
            return

        for container in containers:
            lab_id = container.labels.get("archetype.lab_id")
            if not lab_id:
                continue

            # Restart containers that failed due to OVS plugin socket race
            if container.status == "exited":
                exit_code = container.attrs.get("State", {}).get("ExitCode", 0)
                error_msg = container.attrs.get("State", {}).get("Error", "")
                if exit_code == 255 or "archetype-ovs.sock" in error_msg:
                    logger.info(f"Restarting {container.name} (OVS socket race, exit {exit_code})")
                    try:
                        await asyncio.to_thread(container.start)
                        await asyncio.sleep(2)
                        await provider._fix_interface_names(container.name, lab_id)
                    except Exception as e:
                        logger.warning(f"Failed to restart {container.name}: {e}")
                continue

            try:
                await provider._fix_interface_names(container.name, lab_id)
            except Exception as e:
                logger.warning(
                    f"Failed to fix interfaces for {container.name}: {e}"
                )

        if attempt == 0:
            await asyncio.sleep(5)


async def _cleanup_lingering_virsh_sessions() -> None:
    """Best-effort shutdown cleanup for active virsh console sessions."""
    try:
        from agent.console_session_registry import list_active_domains, unregister_session
        from agent.virsh_console_lock import kill_orphaned_virsh

        for domain in list_active_domains():
            try:
                unregister_session(domain)
            except Exception:
                pass

            try:
                killed = await asyncio.to_thread(kill_orphaned_virsh, domain)
                if killed > 0:
                    logger.info(
                        "Terminated %d lingering virsh console process(es) for %s",
                        killed,
                        domain,
                    )
            except Exception as e:
                logger.debug(
                    "Failed to clean up lingering virsh console session for %s: %s",
                    domain,
                    e,
                )
    except Exception as e:
        logger.debug(f"Virsh session cleanup skipped: {e}")
