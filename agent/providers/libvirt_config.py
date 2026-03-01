"""Config extraction and injection helpers for LibvirtProvider.

Extracted from libvirt.py to reduce file size.  Functions accept explicit
parameters (domain_name, kind, etc.) instead of ``self``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import Any

from agent.vendors import (
    get_config_extraction_settings,
    get_kind_for_device,
    get_vendor_config,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VM management IP lookup
# ---------------------------------------------------------------------------

async def get_vm_management_ip(domain_name: str, uri: str) -> str | None:
    """Get the management IP address for a VM.

    Uses virsh domifaddr to query the guest agent or DHCP leases
    for the VM's IP address.

    Args:
        domain_name: Libvirt domain name
        uri: Libvirt connection URI

    Returns:
        IP address string or None if not found
    """
    try:
        # Try guest agent first (most accurate)
        result = await asyncio.to_thread(
            subprocess.run,
            ["virsh", "-c", uri, "domifaddr", domain_name, "--source", "agent"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            # Parse output: "Name       MAC address          Protocol     Address"
            for line in result.stdout.strip().split("\n")[2:]:  # Skip header
                parts = line.split()
                if len(parts) >= 4:
                    # Address is like "192.168.1.100/24"
                    ip = parts[3].split("/")[0]
                    if ip and not ip.startswith("127."):
                        return ip

        # Fall back to DHCP leases
        result = await asyncio.to_thread(
            subprocess.run,
            ["virsh", "-c", uri, "domifaddr", domain_name, "--source", "lease"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n")[2:]:
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[3].split("/")[0]
                    if ip and not ip.startswith("127."):
                        return ip

        # Fall back to ARP (least reliable)
        result = await asyncio.to_thread(
            subprocess.run,
            ["virsh", "-c", uri, "domifaddr", domain_name, "--source", "arp"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n")[2:]:
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[3].split("/")[0]
                    if ip and not ip.startswith("127."):
                        return ip

        return None

    except Exception as e:
        logger.debug(f"Error getting VM IP for {domain_name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Config extraction
# ---------------------------------------------------------------------------

async def extract_config(
    lab_id: str,
    node_name: str,
    kind: str,
    *,
    domain_name: str,
    uri: str,
    run_libvirt_fn,
    check_domain_running_sync_fn,
    run_ssh_command_fn,
) -> tuple[str, str] | None:
    """Extract running config from a single VM.

    Supports:
    - config_extract_method="serial": Use virsh console + pexpect
    - config_extract_method="ssh": Use SSH to VM's management IP

    Args:
        lab_id: Lab identifier
        node_name: Node name within the lab
        kind: Device kind for vendor config lookup
        domain_name: Full libvirt domain name
        uri: Libvirt connection URI
        run_libvirt_fn: Async callable to run blocking libvirt ops
        check_domain_running_sync_fn: Sync callable checking if domain is running
        run_ssh_command_fn: Async callable to run SSH commands

    Returns:
        Tuple of (node_name, config_content) or None if extraction failed
    """
    running = await run_libvirt_fn(check_domain_running_sync_fn, domain_name)
    if running is None:
        logger.warning(f"Cannot extract config from {node_name}: domain not found")
        return None
    if not running:
        logger.warning(f"Cannot extract config from {node_name}: VM not running")
        return None

    # Check extraction method
    extraction_settings = get_config_extraction_settings(kind)

    if extraction_settings.method == "ssh":
        # Extract via SSH
        config = await extract_config_via_ssh(
            domain_name, kind, node_name,
            uri=uri,
            run_ssh_command_fn=run_ssh_command_fn,
        )
        if config:
            # Guard against obvious non-config noise while allowing
            # legitimately small configs on freshly booted devices.
            compact = config.strip()
            if len(compact) < 64 and not re.search(
                r"(version|hostname|interface|current configuration|^!$)",
                compact,
                re.IGNORECASE | re.MULTILINE,
            ):
                logger.warning(
                    f"Discarding suspiciously short extracted config for {node_name} via SSH "
                    f"({len(compact)} bytes)"
                )
                return None
            logger.info(f"Extracted config from {node_name} via SSH ({len(config)} bytes)")
            return (node_name, config)
        return None

    elif extraction_settings.method == "serial":
        # Extract via serial console (pexpect)
        from agent.console_extractor import extract_vm_config, PEXPECT_AVAILABLE

        if not PEXPECT_AVAILABLE:
            logger.warning("pexpect not available, skipping VM config extraction")
            return None

        # Run extraction in thread pool to avoid blocking
        result = await asyncio.to_thread(
            extract_vm_config,
            domain_name,
            kind,
            uri,
        )

        if result.success:
            # Extra safety net even if extractor reported success.
            compact = result.config.strip()
            if len(compact) < 64 and not re.search(
                r"(version|hostname|interface|current configuration|^!$)",
                compact,
                re.IGNORECASE | re.MULTILINE,
            ):
                logger.warning(
                    f"Discarding suspiciously short extracted config for {node_name} "
                    f"({len(compact)} bytes)"
                )
                return None
            logger.info(f"Extracted config from {node_name} ({len(result.config)} bytes)")
            return (node_name, result.config)
        else:
            logger.warning(f"Failed to extract config from {node_name}: {result.error}")
            return None

    else:
        logger.debug(f"No extraction method for {node_name} (method={extraction_settings.method})")
        return None


async def extract_config_via_ssh(
    domain_name: str,
    kind: str,
    node_name: str,
    *,
    uri: str,
    run_ssh_command_fn,
) -> str | None:
    """Extract config from VM via SSH."""
    ip = await get_vm_management_ip(domain_name, uri)
    if not ip:
        logger.warning(f"No IP address found for SSH extraction from {node_name}")
        return None

    extraction_settings = get_config_extraction_settings(kind)
    user = extraction_settings.user or "admin"
    password = extraction_settings.password or "admin"
    cmd = extraction_settings.command

    if not cmd:
        logger.warning(f"No extraction command for {kind}, skipping {node_name}")
        return None

    return await run_ssh_command_fn(ip, user, password, cmd, node_name)


# ---------------------------------------------------------------------------
# Startup config normalization / injection support
# ---------------------------------------------------------------------------

def prepare_startup_config_for_injection(
    kind: str,
    startup_config: str,
    *,
    node_name: str = "",
    n9kv_config_preamble: str,
) -> str:
    """Normalize startup-config content before disk/ISO injection.

    N9Kv extraction may include serial-console prompt/echo artifacts
    (for example, 'switch# show running-config') that break bootstrap
    parsing when staged directly into bootflash.

    Args:
        kind: Device kind
        startup_config: Raw config text
        node_name: Node name for hostname substitution
        n9kv_config_preamble: The N9Kv preamble template string
    """
    text = startup_config or ""
    if not text:
        return ""

    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    text = ansi_escape.sub("", text).replace("\r", "")

    vendor = get_vendor_config(kind)
    canonical_kind = vendor.kind if vendor else get_kind_for_device(kind)

    # IOS-XR: strip SSH extraction artifacts before ISO injection
    if canonical_kind == "cisco_iosxr":
        iosxr_lines: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Building configuration"):
                continue
            if stripped.startswith("!! IOS XR Configuration"):
                continue
            if stripped.startswith("!! Last configuration change"):
                continue
            if re.match(r"^RP/\d+/RP\d+/CPU\d+:[\w\-]+#", stripped):
                continue
            iosxr_lines.append(line)
        # Strip leading/trailing blank lines
        while iosxr_lines and not iosxr_lines[0].strip():
            iosxr_lines.pop(0)
        while iosxr_lines and not iosxr_lines[-1].strip():
            iosxr_lines.pop()
        text = "\n".join(iosxr_lines)
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    # N9Kv-specific normalization for bootflash staging
    if canonical_kind != "cisco_n9kv":
        return text

    cmd_echo_pat = re.compile(
        r"^\s*(?:[^\s]+(?:\([^)\r\n]+\))?[>#]\s*)?"
        r"(?:show\s+running-config|show\s+startup-config|terminal\s+length\s+0)\s*$",
        re.IGNORECASE,
    )
    prompt_only_pat = re.compile(
        r"^\s*[A-Za-z0-9_.-]+(?:\([^)\r\n]+\))?[>#]\s*$"
    )

    cleaned: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if stripped.startswith("!Command:"):
            continue
        if stripped.startswith("!Running configuration"):
            continue
        if stripped.startswith("!Time:"):
            continue
        if cmd_echo_pat.match(line):
            continue
        if stripped.startswith("Building configuration"):
            continue
        if stripped.startswith("Connected to domain"):
            continue
        if stripped.startswith("Escape character is"):
            continue
        if stripped == "--More--":
            continue
        if prompt_only_pat.match(line):
            continue
        cleaned.append(line)

    while cleaned and not cleaned[0].strip():
        cleaned = cleaned[1:]
    while cleaned and not cleaned[-1].strip():
        cleaned = cleaned[:-1]

    normalized = "\n".join(cleaned)
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"

    # Prepend CML-style preamble: echo-based set_boot.py + EEM applet + credentials.
    # .format() substitutes {hostname} and converts {{}} to {} for the Python script.
    preamble = n9kv_config_preamble.format(hostname=node_name or "switch")
    normalized = preamble + "\n" + normalized
    return normalized


def format_injection_diagnostics(inject_ok: bool, diag: dict[str, Any]) -> str:
    """Render compact bootflash injection diagnostics for callback logs."""
    if not diag:
        return ""

    parts: list[str] = [f"ok={inject_ok}"]
    bytes_written = diag.get("bytes")
    if bytes_written is not None:
        parts.append(f"bytes={bytes_written}")

    partition = diag.get("resolved_partition")
    if partition:
        parts.append(f"partition={partition}")

    fs_type = diag.get("fs_type")
    if fs_type:
        parts.append(f"fs={fs_type}")

    requested = diag.get("requested_config_path")
    if requested:
        parts.append(f"requested={requested}")

    written_paths = diag.get("written_paths")
    if isinstance(written_paths, list) and written_paths:
        parts.append(f"written={','.join(str(p) for p in written_paths)}")
    else:
        targets = diag.get("write_targets")
        if isinstance(targets, list) and targets:
            parts.append(f"targets={','.join(str(p) for p in targets)}")

    error = diag.get("error")
    if error:
        parts.append(f"error={error}")

    exception = diag.get("exception")
    if exception:
        parts.append(f"exception={exception}")

    return " ".join(parts)
