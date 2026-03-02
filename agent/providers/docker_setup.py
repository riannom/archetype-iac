"""Docker provider setup and configuration helpers.

Extracted from docker.py to reduce file size. These standalone functions
handle directory setup, container configuration building, image validation,
and interface counting.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.vendors import (
    get_config_by_device,
    get_container_config,
    is_ceos_kind,
)

if TYPE_CHECKING:
    from agent.providers.docker import ParsedTopology, TopologyNode

logger = logging.getLogger(__name__)

# Duplicated from docker.py so this module is self-contained for labels.
LABEL_LAB_ID = "archetype.lab_id"
LABEL_NODE_NAME = "archetype.node_name"
LABEL_NODE_DISPLAY_NAME = "archetype.node_display_name"
LABEL_NODE_KIND = "archetype.node_kind"
LABEL_NODE_INTERFACE_COUNT = "archetype.node_interface_count"
LABEL_NODE_READINESS_PROBE = "archetype.readiness_probe"
LABEL_NODE_READINESS_PATTERN = "archetype.readiness_pattern"
LABEL_NODE_READINESS_TIMEOUT = "archetype.readiness_timeout"
LABEL_PROVIDER = "archetype.provider"

# Interface wait script for cEOS (imported from docker.py at call site)
# We import it lazily to avoid circular dependencies.


def _get_if_wait_script() -> str:
    """Import IF_WAIT_SCRIPT from docker.py to avoid duplication."""
    from agent.providers.docker import IF_WAIT_SCRIPT
    return IF_WAIT_SCRIPT


def setup_ceos_directories(
    node_name: str,
    node: TopologyNode,
    workspace: Path,
) -> None:
    """Set up cEOS directories and config files.

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    flash_dir = workspace / "configs" / node_name / "flash"
    flash_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Created flash directory: {flash_dir}")

    # Create systemd environment config for cEOS
    systemd_dir = workspace / "configs" / node_name / "systemd"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    env_file = systemd_dir / "ceos-env.conf"
    env_file.write_text(
        "[Manager]\n"
        "DefaultEnvironment=EOS_PLATFORM=ceoslab CEOS=1 "
        "container=docker ETBA=1 SKIP_ZEROTOUCH_BARRIER_IN_SYSDBINIT=1 "
        "INTFTYPE=eth MGMT_INTF=eth0 CEOS_NOZEROTOUCH=1\n"
    )
    logger.debug(f"Created cEOS systemd env config: {env_file}")

    # Write startup-config to flash directory
    startup_config_path = flash_dir / "startup-config"
    extracted_config = workspace / "configs" / node_name / "startup-config"

    if node.startup_config:
        startup_config_path.write_text(node.startup_config)
        logger.debug(f"Wrote startup-config from topology for {node.log_name()}")
    elif extracted_config.exists():
        shutil.copy2(extracted_config, startup_config_path)
        logger.debug(f"Copied extracted startup-config for {node.log_name()}")
    elif not startup_config_path.exists():
        hostname = node.display_name or node_name
        minimal_config = f"""! Minimal cEOS startup config
hostname {hostname}
!
no aaa root
!
username admin privilege 15 role network-admin nopassword
!
! Remove iptables DROP rules on data interfaces at boot.
! EOS adds per-interface DROP rules in the EOS_FORWARD chain
! which block forwarding until the forwarding agent is ready.
! In a lab environment these rules are unnecessary and cause
! connectivity issues.
event-handler IPTABLES_CLEANUP
   trigger on-boot
   action bash for i in $(seq 1 64); do iptables -D EOS_FORWARD -i eth$i -j DROP 2>/dev/null; done
!
"""
        startup_config_path.write_text(minimal_config)
        logger.debug(f"Created minimal startup-config for {node.log_name()}")

    # Create zerotouch-config to disable ZTP
    zerotouch_config = flash_dir / "zerotouch-config"
    if not zerotouch_config.exists():
        zerotouch_config.write_text("DISABLE=True\n")
        logger.debug(f"Created zerotouch-config for {node.log_name()}")

    # Create if-wait.sh script
    if_wait_script = flash_dir / "if-wait.sh"
    if_wait_script.write_text(_get_if_wait_script())
    if_wait_script.chmod(0o755)
    logger.debug(f"Created if-wait.sh for {node.log_name()}")


def setup_cjunos_directories(
    node_name: str,
    node: TopologyNode,
    workspace: Path,
) -> None:
    """Set up cJunOS directories and startup config.

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    config_dir = workspace / "configs" / node_name / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Created cJunOS config directory: {config_dir}")

    startup_config_path = config_dir / "startup-config.cfg"
    extracted_config = workspace / "configs" / node_name / "startup-config"

    if node.startup_config:
        startup_config_path.write_text(node.startup_config)
        logger.debug(f"Wrote startup-config from topology for {node.log_name()}")
    elif extracted_config.exists():
        shutil.copy2(extracted_config, startup_config_path)
        logger.debug(f"Copied extracted startup-config for {node.log_name()}")
    else:
        logger.debug(f"No startup-config for {node.log_name()}, booting with factory defaults")


def validate_images(
    topology: ParsedTopology,
    docker_client: Any,
) -> list[tuple[str, str]]:
    """Check that all required images exist.

    Returns list of (node_name, image) tuples for missing images.
    """
    from docker.errors import ImageNotFound, APIError

    missing = []
    for node_name, node in topology.nodes.items():
        config = get_config_by_device(node.kind)
        image = node.image or (config.default_image if config else None)
        if not image:
            continue

        # File-based images (qcow2/img) -- check filesystem, not Docker
        if image.startswith("/") or image.endswith((".qcow2", ".img", ".iol")):
            if not os.path.exists(image):
                missing.append((node_name, image))
            continue

        try:
            docker_client.images.get(image)
        except ImageNotFound:
            missing.append((node_name, image))
        except APIError as e:
            logger.warning(f"Error checking image {image}: {e}")

    return missing


def create_container_config(
    node: TopologyNode,
    lab_id: str,
    workspace: Path,
    interface_count: int,
    provider_name: str,
    container_name_func: Any,
) -> dict[str, Any]:
    """Build Docker container configuration for a node.

    Args:
        node: The topology node configuration
        lab_id: Lab identifier
        workspace: Path to lab workspace
        interface_count: Number of interfaces this node has
        provider_name: Provider name string (e.g. "docker")
        container_name_func: Callable(lab_id, node_name) -> container name

    Returns a dict suitable for docker.containers.create().
    """
    runtime_config = get_container_config(
        device=node.kind,
        node_name=node.name,
        image=node.image,
        workspace=str(workspace),
    )

    # Merge environment variables (topology overrides vendor defaults)
    env = dict(runtime_config.environment)
    env.update(node.env)

    # Build labels
    labels = {
        LABEL_LAB_ID: lab_id,
        LABEL_NODE_NAME: node.name,
        LABEL_NODE_KIND: node.kind,
        LABEL_PROVIDER: provider_name,
    }
    if interface_count and interface_count > 0:
        labels[LABEL_NODE_INTERFACE_COUNT] = str(interface_count)
    if node.display_name:
        labels[LABEL_NODE_DISPLAY_NAME] = node.display_name
    if node.readiness_probe:
        labels[LABEL_NODE_READINESS_PROBE] = node.readiness_probe
    if node.readiness_pattern:
        labels[LABEL_NODE_READINESS_PATTERN] = node.readiness_pattern
    if node.readiness_timeout and node.readiness_timeout > 0:
        labels[LABEL_NODE_READINESS_TIMEOUT] = str(node.readiness_timeout)

    # Process binds from runtime config and node-specific binds
    binds = list(runtime_config.binds)
    binds.extend(node.binds)

    config: dict[str, Any] = {
        "image": runtime_config.image,
        "name": container_name_func(lab_id, node.name),
        "hostname": runtime_config.hostname,
        "environment": env,
        "labels": labels,
        "detach": True,
        "tty": True,
        "stdin_open": True,
        "restart_policy": {"Name": "no"},
    }

    # Capabilities
    if runtime_config.capabilities:
        config["cap_add"] = runtime_config.capabilities

    # Privileged mode
    if runtime_config.privileged:
        config["privileged"] = True

    config["cgroupns"] = "host"

    # Volume binds
    if binds:
        config["volumes"] = {}
        for bind in binds:
            if ":" in bind:
                host_path, container_path = bind.split(":", 1)
                ro = False
                if container_path.endswith(":ro"):
                    container_path = container_path[:-3]
                    ro = True
                config["volumes"][host_path] = {
                    "bind": container_path,
                    "mode": "ro" if ro else "rw",
                }

    # Sysctls
    if runtime_config.sysctls:
        config["sysctls"] = runtime_config.sysctls

    # Entry command
    if is_ceos_kind(node.kind) and interface_count > 0:
        config["environment"]["CLAB_INTFS"] = str(interface_count)
        config["entrypoint"] = ["/bin/bash", "-c"]
        config["command"] = ["/mnt/flash/if-wait.sh ; exec /sbin/init"]
        logger.debug(f"cEOS {node.name}: using if-wait.sh wrapper with CLAB_INTFS={interface_count}")
    elif runtime_config.entrypoint:
        if isinstance(runtime_config.entrypoint, str):
            config["entrypoint"] = [runtime_config.entrypoint]
        else:
            config["entrypoint"] = runtime_config.entrypoint

    if runtime_config.cmd and "command" not in config:
        config["command"] = runtime_config.cmd

    if "entrypoint" not in config and "command" not in config:
        config["command"] = ["sleep", "infinity"]

    # CPU limit as cgroup quota
    if node.cpu_limit is not None:
        limit_pct = max(1, min(100, int(node.cpu_limit)))
        vcpus = node.cpu if node.cpu and node.cpu > 0 else 1
        nano_cpus = int((vcpus * limit_pct / 100.0) * 1_000_000_000)
        if nano_cpus > 0:
            config["nano_cpus"] = nano_cpus

    return config


def calculate_required_interfaces(topology: ParsedTopology) -> int:
    """Calculate the maximum interface index needed for pre-provisioning.

    Returns:
        Number of interfaces to create (max index found + buffer)
    """
    max_index = 0

    for node in topology.nodes.values():
        if node.interface_count and node.interface_count > max_index:
            max_index = node.interface_count

    for link in topology.links:
        for endpoint in link.endpoints:
            if ":" in endpoint:
                _, interface = endpoint.split(":", 1)
                match = re.search(r"(\d+)$", interface)
                if match:
                    index = int(match.group(1))
                    max_index = max(max_index, index)

    return max(max_index + 4, 4)


def count_node_interfaces(node_name: str, topology: ParsedTopology) -> int:
    """Count the number of interfaces connected to a specific node.

    Returns:
        Max interface index required for this node
    """
    node = topology.nodes.get(node_name)
    if node and node.interface_count:
        return node.interface_count

    max_index = 0

    for link in topology.links:
        for endpoint in link.endpoints:
            if ":" in endpoint:
                ep_node, interface = endpoint.split(":", 1)
                if ep_node == node_name:
                    match = re.search(r"(\d+)$", interface)
                    if match:
                        max_index = max(max_index, int(match.group(1)))

    return max_index
