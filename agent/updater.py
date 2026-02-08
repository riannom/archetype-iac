"""Agent software update logic.

This module handles self-update functionality for the agent, supporting
different deployment modes (systemd, docker) with appropriate update strategies.
Includes rollback support via a sentinel file mechanism.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ROLLBACK_SENTINEL = Path("/tmp/archetype-update-rollback.json")


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


def is_commit_sha(ref: str) -> bool:
    """Check if a string looks like a git commit SHA (7-40 hex chars)."""
    return bool(re.fullmatch(r'[0-9a-f]{7,40}', ref.lower()))


# --- Rollback Sentinel ---

def _save_rollback_info(previous_ref: str, target_ref: str) -> None:
    """Save rollback info before performing update checkout."""
    try:
        ROLLBACK_SENTINEL.write_text(json.dumps({
            "previous_ref": previous_ref,
            "target_ref": target_ref,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        logger.info(f"Saved rollback sentinel: {previous_ref} -> {target_ref}")
    except Exception as e:
        logger.warning(f"Failed to save rollback sentinel: {e}")


def _clear_rollback_info() -> None:
    """Remove rollback sentinel (update succeeded)."""
    try:
        if ROLLBACK_SENTINEL.exists():
            ROLLBACK_SENTINEL.unlink()
            logger.info("Cleared rollback sentinel")
    except Exception as e:
        logger.warning(f"Failed to clear rollback sentinel: {e}")


def check_and_rollback() -> None:
    """Check for rollback sentinel on startup and roll back if needed.

    Called during agent startup. If a sentinel exists:
    - If current HEAD matches the target_ref -> update succeeded, clear sentinel
    - If current HEAD doesn't match -> update failed, roll back to previous_ref
    """
    if not ROLLBACK_SENTINEL.exists():
        return

    try:
        data = json.loads(ROLLBACK_SENTINEL.read_text())
        previous_ref = data.get("previous_ref")
        target_ref = data.get("target_ref")

        if not previous_ref or not target_ref:
            logger.warning("Rollback sentinel has missing fields, clearing")
            _clear_rollback_info()
            return

        # Check current HEAD
        root = get_agent_root()
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=root,
        )
        if result.returncode != 0:
            logger.warning("Cannot determine current HEAD for rollback check")
            _clear_rollback_info()
            return

        current_sha = result.stdout.strip()

        # Resolve target_ref to SHA for comparison
        target_result = subprocess.run(
            ["git", "rev-parse", "--verify", target_ref],
            capture_output=True, text=True, timeout=10, cwd=root,
        )
        target_sha = target_result.stdout.strip() if target_result.returncode == 0 else ""

        if current_sha == target_sha or current_sha.startswith(target_ref):
            # Update succeeded - agent is running the target version
            logger.info(f"Update to {target_ref} confirmed, clearing rollback sentinel")
            _clear_rollback_info()
        else:
            # Update may have failed - roll back
            logger.warning(
                f"Current HEAD {current_sha[:8]} doesn't match target {target_ref}, "
                f"rolling back to {previous_ref}"
            )
            rollback_result = subprocess.run(
                ["git", "checkout", previous_ref],
                capture_output=True, text=True, timeout=30, cwd=root,
            )
            if rollback_result.returncode == 0:
                logger.info(f"Rolled back to {previous_ref}")
            else:
                logger.error(f"Rollback failed: {rollback_result.stderr}")
            _clear_rollback_info()

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Corrupted rollback sentinel: {e}")
        _clear_rollback_info()
    except Exception as e:
        logger.error(f"Rollback check failed: {e}")
        _clear_rollback_info()


# --- Progress Reporting ---

async def report_progress(
    client: httpx.AsyncClient,
    callback_url: str,
    job_id: str,
    agent_id: str,
    status: str,
    progress_percent: int,
    error_message: str | None = None,
) -> None:
    """Report update progress to the controller.

    Args:
        client: Reusable httpx client
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
        await client.post(callback_url, json=payload)
    except Exception as e:
        logger.warning(f"Failed to report progress: {e}")


# --- Update Logic ---

async def perform_systemd_update(
    job_id: str,
    agent_id: str,
    target_version: str,
    callback_url: str,
) -> bool:
    """Perform update for systemd-managed agent.

    Update flow:
    1. Report "downloading" status
    2. git fetch origin && git checkout <version_tag or commit SHA>
    3. Report "installing" status
    4. pip install -r requirements.txt
    5. Save rollback sentinel
    6. Report "restarting" status
    7. systemctl restart archetype-agent
    8. After restart, agent re-registers with new version

    Args:
        job_id: Update job ID
        agent_id: This agent's ID
        target_version: Version to update to (git tag, branch, or commit SHA)
        callback_url: URL for progress updates

    Returns:
        True if update initiated successfully, False on error
    """
    root = get_agent_root()

    def _run_subprocess(cmd, cwd, timeout):
        """Run subprocess synchronously (to be wrapped in asyncio.to_thread)."""
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Capture current HEAD for rollback
            head_result = await asyncio.to_thread(
                _run_subprocess,
                ["git", "rev-parse", "HEAD"],
                root, 10,
            )
            previous_ref = head_result.stdout.strip() if head_result.returncode == 0 else "HEAD"

            # Step 1: Downloading (git fetch)
            logger.info(f"Update {job_id}: starting git fetch")
            await report_progress(client, callback_url, job_id, agent_id, "downloading", 10)

            result = await asyncio.to_thread(
                _run_subprocess,
                ["git", "fetch", "origin", "--tags", "--force"],
                root,
                60,
            )
            if result.returncode != 0:
                logger.error(f"Update {job_id}: git fetch failed: {result.stderr}")
                await report_progress(
                    client, callback_url, job_id, agent_id, "failed", 0,
                    f"git fetch failed: {result.stderr}"
                )
                return False

            logger.info(f"Update {job_id}: git fetch complete")
            await report_progress(client, callback_url, job_id, agent_id, "downloading", 30)

            # Resolve the target version to a checkout ref
            # Support: "latest" (origin/main), commit SHAs, tags (v0.3.7),
            # version strings (0.3.7), branches (main)
            checkout_ref = None

            if target_version.lower() == "latest":
                # "latest" means the tip of the default branch
                checkout_ref = "origin/main"
                logger.info(f"Update {job_id}: resolved 'latest' to origin/main")
            elif is_commit_sha(target_version):
                # Direct commit SHA - verify it exists
                result = await asyncio.to_thread(
                    _run_subprocess,
                    ["git", "rev-parse", "--verify", target_version],
                    root, 10,
                )
                if result.returncode == 0:
                    checkout_ref = target_version
                    logger.info(f"Update {job_id}: resolved commit SHA {target_version[:8]}")
            else:
                # Try tag/branch formats
                for ref in [
                    f"v{target_version}",
                    target_version,
                    f"origin/v{target_version}",
                    f"origin/{target_version}",
                ]:
                    result = await asyncio.to_thread(
                        _run_subprocess,
                        ["git", "rev-parse", "--verify", ref],
                        root, 10,
                    )
                    if result.returncode == 0:
                        checkout_ref = ref
                        logger.info(f"Update {job_id}: resolved ref '{ref}'")
                        break

            if not checkout_ref:
                logger.error(f"Update {job_id}: version {target_version} not found")
                await report_progress(
                    client, callback_url, job_id, agent_id, "failed", 0,
                    f"Version {target_version} not found"
                )
                return False

            # Save rollback info before checkout
            _save_rollback_info(previous_ref, checkout_ref)

            result = await asyncio.to_thread(
                _run_subprocess,
                ["git", "checkout", checkout_ref],
                root,
                30,
            )
            if result.returncode != 0:
                logger.error(f"Update {job_id}: git checkout failed: {result.stderr}")
                await report_progress(
                    client, callback_url, job_id, agent_id, "failed", 0,
                    f"git checkout failed: {result.stderr}"
                )
                _clear_rollback_info()
                return False

            logger.info(f"Update {job_id}: checked out {checkout_ref}")
            await report_progress(client, callback_url, job_id, agent_id, "downloading", 50)

            # Step 2: Installing dependencies
            logger.info(f"Update {job_id}: installing dependencies")
            await report_progress(client, callback_url, job_id, agent_id, "installing", 60)

            # Find the correct pip/python
            python_exe = sys.executable
            result = await asyncio.to_thread(
                _run_subprocess,
                [python_exe, "-m", "pip", "install", "-r", "requirements.txt"],
                root / "agent",
                300,  # 5 min for pip install
            )
            if result.returncode != 0:
                logger.error(f"Update {job_id}: pip install failed: {result.stderr}")
                await report_progress(
                    client, callback_url, job_id, agent_id, "failed", 0,
                    f"pip install failed: {result.stderr}"
                )
                return False

            logger.info(f"Update {job_id}: dependencies installed")
            await report_progress(client, callback_url, job_id, agent_id, "installing", 80)

            # Step 3: Restart the service
            logger.info(f"Update {job_id}: scheduling restart")
            await report_progress(client, callback_url, job_id, agent_id, "restarting", 90)

        except subprocess.TimeoutExpired as e:
            logger.error(f"Update {job_id}: command timed out: {e.cmd}")
            await report_progress(
                client, callback_url, job_id, agent_id, "failed", 0,
                f"Command timed out: {e.cmd}"
            )
            return False
        except Exception as e:
            logger.error(f"Update {job_id}: error: {e}")
            await report_progress(
                client, callback_url, job_id, agent_id, "failed", 0,
                f"Update error: {str(e)}"
            )
            return False

    # Schedule the restart after a short delay to allow the response to be sent
    # The restart will kill this process, so we won't report completion here
    # The new agent instance will re-register with the new version
    asyncio.create_task(_delayed_restart())

    return True


async def _delayed_restart():
    """Restart the agent service after a short delay."""
    await asyncio.sleep(2)  # Allow time for response to be sent

    try:
        # Use systemctl to restart - this will terminate this process
        logger.info("Restarting archetype-agent service")
        subprocess.Popen(
            ["systemctl", "restart", "archetype-agent"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error(f"Failed to restart service: {e}")


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
    async with httpx.AsyncClient(timeout=10.0) as client:
        await report_progress(
            client, callback_url, job_id, agent_id, "failed", 0,
            "Docker deployment detected. Update must be performed by restarting "
            "the container with the new image version. Use: "
            f"docker pull archetype-agent:{target_version} && docker-compose up -d"
        )
    return False
