"""Docker provider config extraction helpers.

Extracted from docker.py to reduce file size. These standalone functions
handle extracting running configurations from containers via docker exec,
SSH, and NVRAM parsing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agent.vendors import (
    get_config_extraction_settings,
    get_console_credentials,
)

logger = logging.getLogger(__name__)

# Import label constants from docker.py (lazily to avoid circular imports)
LABEL_NODE_NAME = "archetype.node_name"
LABEL_NODE_KIND = "archetype.node_kind"
LABEL_NODE_DISPLAY_NAME = "archetype.node_display_name"
LABEL_PROVIDER = "archetype.provider"


def _log_name_from_labels(labels: dict[str, str]) -> str:
    """Format node name for logging from container labels."""
    node_name = labels.get(LABEL_NODE_NAME, "")
    display_name = labels.get(LABEL_NODE_DISPLAY_NAME, "")
    if display_name and display_name != node_name:
        return f"{display_name}({node_name})"
    return node_name


def _parse_iol_nvram(data: bytes) -> str | None:
    """Parse an IOL NVRAM binary file and extract the startup config.

    IOL NVRAM format:
    - Binary header (variable length, typically 76+ bytes)
    - The config is stored as plain text after a header section
    - Config starts after the binary preamble and ends at a null byte or EOF
    - Multiple config sections may exist; we want the startup-config
    """
    if not data or len(data) < 64:
        return None

    config_markers = [
        b"\nversion ",
        b"\nhostname ",
        b"\nno service ",
        b"\nservice ",
        b"\n!\n",
    ]

    earliest_pos = len(data)
    for marker in config_markers:
        pos = data.find(marker)
        if pos != -1 and pos < earliest_pos:
            earliest_pos = pos

    if earliest_pos >= len(data):
        return None

    config_start = earliest_pos + 1  # skip the leading \n

    config_bytes = data[config_start:]

    null_pos = config_bytes.find(b"\x00")
    if null_pos != -1:
        config_bytes = config_bytes[:null_pos]

    config_text = config_bytes.decode("ascii", errors="ignore")

    end_pos = config_text.rfind("\nend")
    if end_pos != -1:
        config_text = config_text[: end_pos + 4]

    config_text = config_text.strip()
    if len(config_text) < 10:
        return None

    return config_text


async def extract_all_container_configs(
    lab_id: str,
    workspace: Path,
    docker_client: Any,
    lab_prefix: str,
    provider_name: str,
    get_container_ips_func: Any,
    run_ssh_command_func: Any,
) -> list[tuple[str, str]]:
    """Extract running configs from all containers in a lab that support it.

    Args:
        lab_id: Lab identifier
        workspace: Lab workspace path
        docker_client: Docker client instance
        lab_prefix: Container name prefix for this lab
        provider_name: Provider name string
        get_container_ips_func: Callable(container) -> list[str]
        run_ssh_command_func: Async callable(ip, user, password, cmd, log_name) -> str|None

    Returns list of (node_name, config_content) tuples.
    Also saves configs to workspace/configs/{node}/startup-config.
    """
    extracted = []

    try:
        containers = await asyncio.to_thread(
            docker_client.containers.list,
            filters={
                "name": lab_prefix,
                "label": LABEL_PROVIDER + "=" + provider_name,
            },
        )

        for container in containers:
            labels = container.labels or {}
            node_name = labels.get(LABEL_NODE_NAME)
            kind = labels.get(LABEL_NODE_KIND, "")

            if not node_name or not kind:
                continue

            extraction_settings = get_config_extraction_settings(kind)

            if extraction_settings.method not in ("docker", "ssh"):
                continue

            log_name = _log_name_from_labels(labels)

            if container.status != "running":
                logger.warning(f"Skipping {log_name}: container not running")
                continue

            try:
                cmd = extraction_settings.command
                if not cmd:
                    logger.warning(f"No extraction command for {kind}, skipping {log_name}")
                    continue

                config_content = None

                if extraction_settings.method == "ssh":
                    config_content = await extract_config_via_ssh(
                        container, kind, cmd, log_name,
                        get_container_ips_func, run_ssh_command_func,
                    )
                else:
                    config_content = await extract_config_via_docker(
                        container, cmd, log_name
                    )

                if not config_content or not config_content.strip():
                    logger.warning(f"Empty config from {log_name}")
                    continue

                config_dir = workspace / "configs" / node_name
                config_dir.mkdir(parents=True, exist_ok=True)
                config_path = config_dir / "startup-config"
                config_path.write_text(config_content)

                extracted.append((node_name, config_content))
                logger.info(f"Extracted config from {log_name} ({kind})")

            except Exception as e:
                logger.error(f"Error extracting config from {log_name}: {e}")

    except Exception as e:
        logger.error(f"Error during config extraction for lab {lab_id}: {e}")

    return extracted


async def extract_config_via_docker(
    container: Any,
    cmd: str,
    log_name: str,
) -> str | None:
    """Extract config from container via docker exec.

    Args:
        container: Docker container object
        cmd: Command to run
        log_name: Display name for logging

    Returns:
        Config content string or None on failure
    """
    try:
        exec_cmd = ["sh", "-c", cmd]

        result = await asyncio.to_thread(
            container.exec_run,
            exec_cmd,
            demux=True,
        )
        stdout, stderr = result.output

        if result.exit_code != 0:
            stderr_str = stderr.decode("utf-8") if stderr else ""
            logger.warning(
                f"Failed to extract config from {log_name}: "
                f"exit={result.exit_code}, stderr={stderr_str}"
            )
            return None

        return stdout.decode("utf-8") if stdout else None

    except Exception as e:
        logger.error(f"Docker exec failed for {log_name}: {e}")
        return None


async def extract_config_via_ssh(
    container: Any,
    kind: str,
    cmd: str,
    log_name: str,
    get_container_ips_func: Any,
    run_ssh_command_func: Any,
) -> str | None:
    """Extract config from container via SSH."""
    ips = get_container_ips_func(container)
    if not ips:
        logger.warning(f"No IP address found for SSH extraction from {log_name}")
        return None

    user, password = get_console_credentials(kind)
    return await run_ssh_command_func(ips[0], user, password, cmd, log_name)


async def extract_config_via_nvram(
    container_name: str,
    workspace: Path,
) -> str | None:
    """Extract config from IOL container via NVRAM file.

    IOL stores its running config in a binary NVRAM file at
    {workspace}/configs/{node_name}/iol-data/nvram_00001.

    Args:
        container_name: Container name (used to derive node name)
        workspace: Lab workspace path

    Returns:
        Config content string or None on failure
    """
    try:
        parts = container_name.split("-", 2)
        node_name = parts[2] if len(parts) >= 3 else container_name

        nvram_path = workspace / "configs" / node_name / "iol-data" / "nvram_00001"
        if not nvram_path.exists():
            logger.debug(f"No NVRAM file found at {nvram_path}")
            return None

        data = nvram_path.read_bytes()
        if len(data) < 64:
            logger.warning(f"NVRAM file too small ({len(data)} bytes): {nvram_path}")
            return None

        config_text = _parse_iol_nvram(data)
        if config_text:
            logger.info(
                f"Extracted config from NVRAM for {node_name} "
                f"({len(config_text)} bytes)"
            )
        return config_text

    except Exception as e:
        logger.error(f"NVRAM extraction failed for {container_name}: {e}")
        return None
