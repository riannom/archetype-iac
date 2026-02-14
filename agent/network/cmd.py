"""Shared async command utilities for agent network modules."""

import asyncio


async def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    """Run a shell command asynchronously.

    Args:
        cmd: Command and arguments as list

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def ovs_vsctl(*args: str) -> tuple[int, str, str]:
    """Run ovs-vsctl command.

    Args:
        args: Arguments to ovs-vsctl

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    return await run_cmd(["ovs-vsctl", *args])


async def ip_link_exists(name: str) -> bool:
    """Check if a network interface exists."""
    code, _, _ = await run_cmd(["ip", "link", "show", name])
    return code == 0
