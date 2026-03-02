"""VXLAN device lifecycle functions for overlay networking.

Extracted from overlay.py to reduce file size. These standalone async functions
handle low-level VXLAN device creation, deletion, MTU discovery, and link info
reading. They accept explicit parameters rather than the OverlayManager instance.
"""

from __future__ import annotations

import asyncio
import logging

from agent.config import settings
from agent.network.cmd import (
    run_cmd as _shared_run_cmd,
    ovs_vsctl as _shared_ovs_vsctl,
    ip_link_exists as _shared_ip_link_exists,
)

logger = logging.getLogger(__name__)

# VXLAN default port
VXLAN_PORT = 4789


async def create_vxlan_device(
    name: str,
    vni: int,
    local_ip: str,
    remote_ip: str,
    bridge: str,
    vlan_tag: int | None = None,
    tenant_mtu: int = 0,
) -> None:
    """Create a Linux VXLAN device with nopmtudisc and add it to OVS.

    Uses Linux VXLAN devices instead of OVS-managed VXLAN ports so that
    nopmtudisc disables all PMTUD checking on the tunnel. This allows
    inner packets to pass through at full MTU while the kernel handles
    outer packet fragmentation transparently.

    Args:
        name: Interface name for the VXLAN device
        vni: VXLAN Network Identifier
        local_ip: Local IP for VXLAN endpoint
        remote_ip: Remote IP for VXLAN endpoint
        bridge: OVS bridge name to add the port to
        vlan_tag: Optional VLAN tag (access mode) or None (trunk mode)
        tenant_mtu: API-supplied overlay MTU (0 = use agent default)

    Raises:
        RuntimeError: If device creation fails
    """
    code, _, stderr = await _shared_run_cmd([
        "ip", "link", "add", name, "type", "vxlan",
        "id", str(vni), "local", local_ip, "remote", remote_ip,
        "dstport", str(VXLAN_PORT), "df", "unset",
    ])
    if code != 0 and "already exists" in (stderr or ""):
        logger.warning(f"VXLAN device {name} already exists, deleting stale device and retrying")
        await _shared_run_cmd(["ip", "link", "delete", name])
        code, _, stderr = await _shared_run_cmd([
            "ip", "link", "add", name, "type", "vxlan",
            "id", str(vni), "local", local_ip, "remote", remote_ip,
            "dstport", str(VXLAN_PORT), "df", "unset",
        ])
    if code != 0:
        raise RuntimeError(f"Failed to create VXLAN device {name}: {stderr}")

    vxlan_mtu = tenant_mtu if tenant_mtu > 0 else (settings.overlay_mtu if settings.overlay_mtu > 0 else 1500)
    await _shared_run_cmd(["ip", "link", "set", name, "mtu", str(vxlan_mtu)])

    # Bring device up
    await _shared_run_cmd(["ip", "link", "set", name, "up"])

    # Add to OVS bridge as a system port
    if vlan_tag is not None:
        code, _, stderr = await _shared_ovs_vsctl(
            "add-port", bridge, name, f"tag={vlan_tag}",
        )
    else:
        code, _, stderr = await _shared_ovs_vsctl(
            "add-port", bridge, name,
        )
    if code != 0:
        await _shared_run_cmd(["ip", "link", "delete", name])
        raise RuntimeError(f"Failed to add VXLAN device {name} to OVS: {stderr}")


async def delete_vxlan_device(name: str, bridge: str) -> None:
    """Remove a VXLAN device from OVS and delete the Linux interface.

    Args:
        name: Interface name of the VXLAN device
        bridge: OVS bridge name to remove the port from
    """
    await _shared_ovs_vsctl("--if-exists", "del-port", bridge, name)
    await _shared_run_cmd(["ip", "link", "delete", name])


async def discover_path_mtu(remote_ip: str, mtu_cache: dict[str, int]) -> int:
    """Discover the path MTU to a remote IP address.

    Uses ping with DF (Don't Fragment) bit set to find the maximum
    MTU that works on the path.

    Args:
        remote_ip: Target IP address to test
        mtu_cache: Dict to check/update for cached results

    Returns:
        Discovered path MTU, or 0 if discovery fails (use fallback)
    """
    if remote_ip in mtu_cache:
        cached = mtu_cache[remote_ip]
        logger.debug(f"Using cached MTU {cached} for {remote_ip}")
        return cached

    test_mtus = [9000, 4000, 1500]

    from agent.network.transport import get_data_plane_ip
    dp_ip = get_data_plane_ip()

    async def test_mtu(mtu: int) -> bool:
        payload_size = mtu - 28
        if payload_size < 0:
            return False

        try:
            ping_args = [
                "ping",
                "-M", "do",
                "-c", "1",
                "-W", "2",
                "-s", str(payload_size),
            ]
            if dp_ip:
                ping_args.extend(["-I", dp_ip])
            ping_args.append(remote_ip)

            process = await asyncio.create_subprocess_exec(
                *ping_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=5.0,
            )

            if process.returncode == 0:
                return True

            combined = stdout.decode() + stderr.decode()
            if "message too long" in combined.lower() or "frag needed" in combined.lower():
                return False

            return False

        except asyncio.TimeoutError:
            return False
        except Exception as e:
            logger.debug(f"MTU test error for {remote_ip} at {mtu}: {e}")
            return False

    discovered_mtu = 0
    for mtu in test_mtus:
        logger.debug(f"Testing MTU {mtu} to {remote_ip}")
        if await test_mtu(mtu):
            discovered_mtu = mtu
            logger.info(f"Path MTU to {remote_ip}: {mtu} bytes")
            break

    if discovered_mtu == 0:
        logger.warning(
            f"MTU discovery failed for {remote_ip}, will use fallback overlay_mtu={settings.overlay_mtu}"
        )
    else:
        mtu_cache[remote_ip] = discovered_mtu

    return discovered_mtu


async def read_vxlan_link_info(interface_name: str) -> tuple[int, str, str]:
    """Read VNI/remote/local from a Linux VXLAN device.

    Returns (vni, remote_ip, local_ip). Zero/empty values on failure.
    """
    code, link_out, _ = await _shared_run_cmd([
        "ip", "-d", "link", "show", interface_name
    ])
    if code != 0:
        return 0, "", ""

    vni = 0
    remote_ip = ""
    local_ip = ""
    parts = link_out.split()
    for i, part in enumerate(parts):
        if part == "id" and i + 1 < len(parts):
            try:
                vni = int(parts[i + 1])
            except ValueError:
                pass
        elif part == "remote" and i + 1 < len(parts):
            remote_ip = parts[i + 1]
        elif part == "local" and i + 1 < len(parts):
            local_ip = parts[i + 1]

    return vni, remote_ip, local_ip


async def ip_link_exists(name: str) -> bool:
    """Check if a network interface exists."""
    return await _shared_ip_link_exists(name)
