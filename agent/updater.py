"""Agent software update logic.

This module handles self-update functionality for the agent, supporting
different deployment modes (systemd, docker) with appropriate update strategies.
"""

import asyncio
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

import httpx


class DeploymentMode(str, Enum):
    """How the agent is deployed/installed."""
    SYSTEMD = "systemd"
    DOCKER = "docker"
    UNKNOWN = "unknown"


def detect_deployment_mode() -> DeploymentMode:
    """Detect how this agent was deployed.

    Returns:
        DeploymentMode indicating systemd, docker, or unknown
    """
    # Check if running inside Docker
    if _is_running_in_docker():
        return DeploymentMode.DOCKER

    # Check if managed by systemd
    if _is_managed_by_systemd():
        return DeploymentMode.SYSTEMD

    return DeploymentMode.UNKNOWN


def _is_running_in_docker() -> bool:
    """Check if we're running inside a Docker container."""
    # Check for /.dockerenv file
    if Path("/.dockerenv").exists():
        return True

    # Check cgroup for docker
    try:
        with open("/proc/1/cgroup", "r") as f:
            return "docker" in f.read()
    except Exception:
        pass

    return False


def _is_managed_by_systemd() -> bool:
    """Check if the agent is managed by systemd."""
    # Check for INVOCATION_ID (set by systemd)
    if os.environ.get("INVOCATION_ID"):
        return True

    # Check if archetype-agent service exists
    try:
        result = subprocess.run(
            ["systemctl", "status", "archetype-agent"],
            capture_output=True,
            timeout=5,
        )
        # Service exists if return code is 0, 3 (inactive), or 4 (not loaded but unit file exists)
        return result.returncode in (0, 3, 4)
    except Exception:
        pass

    return False


def get_agent_root() -> Path:
    """Get the root directory of the agent installation.

    Returns:
        Path to the agent root directory (parent of 'agent' package)
    """
    # This file is at agent/updater.py, so parent.parent is the root
    return Path(__file__).parent.parent


async def report_progress(
    callback_url: str,
    job_id: str,
    agent_id: str,
    status: str,
    progress_percent: int,
    error_message: str | None = None,
) -> None:
    """Report update progress to the controller.

    Args:
        callback_url: URL to POST progress updates
        job_id: The update job ID
        agent_id: This agent's ID
        status: Current status (downloading, installing, restarting, completed, failed)
        progress_percent: Progress percentage (0-100)
        error_message: Error message if failed
    """
    payload = {
        "job_id": job_id,
        "agent_id": agent_id,
        "status": status,
        "progress_percent": progress_percent,
        "error_message": error_message,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(callback_url, json=payload)
    except Exception as e:
        print(f"Failed to report progress: {e}")


async def perform_systemd_update(
    job_id: str,
    agent_id: str,
    target_version: str,
    callback_url: str,
) -> bool:
    """Perform update for systemd-managed agent.

    Update flow:
    1. Report "downloading" status
    2. git fetch origin && git checkout <version_tag>
    3. Report "installing" status
    4. pip install -r requirements.txt
    5. Report "restarting" status
    6. systemctl restart archetype-agent
    7. After restart, agent re-registers with new version

    Args:
        job_id: Update job ID
        agent_id: This agent's ID
        target_version: Version to update to (git tag)
        callback_url: URL for progress updates

    Returns:
        True if update initiated successfully, False on error
    """
    root = get_agent_root()

    try:
        # Step 1: Downloading (git fetch)
        await report_progress(callback_url, job_id, agent_id, "downloading", 10)

        result = subprocess.run(
            ["git", "fetch", "origin", "--tags"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=root,
        )
        if result.returncode != 0:
            await report_progress(
                callback_url, job_id, agent_id, "failed", 0,
                f"git fetch failed: {result.stderr}"
            )
            return False

        await report_progress(callback_url, job_id, agent_id, "downloading", 30)

        # Checkout the target version (could be tag or branch)
        # Try tag format first (v0.2.0), then raw version (0.2.0)
        checkout_ref = None
        for ref in [f"v{target_version}", target_version, f"origin/v{target_version}"]:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", ref],
                capture_output=True,
                timeout=10,
                cwd=root,
            )
            if result.returncode == 0:
                checkout_ref = ref
                break

        if not checkout_ref:
            await report_progress(
                callback_url, job_id, agent_id, "failed", 0,
                f"Version {target_version} not found"
            )
            return False

        result = subprocess.run(
            ["git", "checkout", checkout_ref],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=root,
        )
        if result.returncode != 0:
            await report_progress(
                callback_url, job_id, agent_id, "failed", 0,
                f"git checkout failed: {result.stderr}"
            )
            return False

        await report_progress(callback_url, job_id, agent_id, "downloading", 50)

        # Step 2: Installing dependencies
        await report_progress(callback_url, job_id, agent_id, "installing", 60)

        # Find the correct pip/python
        python_exe = sys.executable
        result = subprocess.run(
            [python_exe, "-m", "pip", "install", "-r", "requirements.txt"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min for pip install
            cwd=root / "agent",
        )
        if result.returncode != 0:
            await report_progress(
                callback_url, job_id, agent_id, "failed", 0,
                f"pip install failed: {result.stderr}"
            )
            return False

        await report_progress(callback_url, job_id, agent_id, "installing", 80)

        # Step 3: Restart the service
        await report_progress(callback_url, job_id, agent_id, "restarting", 90)

        # Schedule the restart after a short delay to allow the response to be sent
        # The restart will kill this process, so we won't report completion here
        # The new agent instance will re-register with the new version
        asyncio.create_task(_delayed_restart())

        return True

    except subprocess.TimeoutExpired as e:
        await report_progress(
            callback_url, job_id, agent_id, "failed", 0,
            f"Command timed out: {e.cmd}"
        )
        return False
    except Exception as e:
        await report_progress(
            callback_url, job_id, agent_id, "failed", 0,
            f"Update error: {str(e)}"
        )
        return False


async def _delayed_restart():
    """Restart the agent service after a short delay."""
    await asyncio.sleep(2)  # Allow time for response to be sent

    try:
        # Use systemctl to restart - this will terminate this process
        subprocess.Popen(
            ["systemctl", "restart", "archetype-agent"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"Failed to restart service: {e}")


async def perform_docker_update(
    job_id: str,
    agent_id: str,
    target_version: str,
    callback_url: str,
) -> bool:
    """Handle update request for Docker-deployed agent.

    For Docker deployments, the update is handled externally:
    - Container orchestrator (docker-compose, kubernetes) pulls new image
    - This agent just reports that it received the update request

    The controller should handle the actual container restart/update.

    Args:
        job_id: Update job ID
        agent_id: This agent's ID
        target_version: Version to update to
        callback_url: URL for progress updates

    Returns:
        True to indicate the request was acknowledged
    """
    # For Docker, we just report back that update needs external handling
    await report_progress(
        callback_url, job_id, agent_id, "failed", 0,
        "Docker deployment detected. Update must be performed by restarting "
        "the container with the new image version. Use: "
        f"docker pull archetype-agent:{target_version} && docker-compose up -d"
    )
    return False
