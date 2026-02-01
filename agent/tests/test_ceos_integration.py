"""Integration tests for cEOS container deployment.

These tests require Docker and actually deploy cEOS containers.
Mark with @pytest.mark.integration to skip in CI without Docker.

Tests verify:
1. cEOS boots with correct platform detection (Ark.getPlatform() == "ceoslab")
2. CLI is accessible after boot
3. Multiple cEOS nodes all boot successfully
4. Deploy completes without blocking/timeout
"""

import asyncio
import pytest
import subprocess
import time
import json
import httpx
from typing import Generator

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


# --- Configuration ---

AGENT_URL = "http://localhost:8001"
CEOS_IMAGE = "ceos:latest"
TEST_LAB_ID = "test-ceos-integration"
DEPLOY_TIMEOUT = 180  # seconds
BOOT_TIMEOUT = 120  # seconds for cEOS to boot


# --- Fixtures ---

@pytest.fixture(scope="module")
def check_prerequisites():
    """Check that Docker and cEOS image are available."""
    # Check Docker
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("Docker not available")

    # Check cEOS image
    result = subprocess.run(
        ["docker", "images", "-q", CEOS_IMAGE],
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        pytest.skip(f"cEOS image '{CEOS_IMAGE}' not available")

    # Check agent is running
    try:
        response = httpx.get(f"{AGENT_URL}/health", timeout=5)
        if response.status_code != 200:
            pytest.skip("Agent not running")
    except httpx.RequestError:
        pytest.skip("Agent not reachable")


@pytest.fixture
def cleanup_lab():
    """Cleanup test lab containers before and after test."""
    def _cleanup():
        # Remove any existing test containers
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=archetype.lab_id={TEST_LAB_ID}"],
            capture_output=True,
            text=True,
        )
        container_ids = result.stdout.strip().split()
        if container_ids and container_ids[0]:
            subprocess.run(["docker", "rm", "-f"] + container_ids, capture_output=True)

        # Remove test networks
        result = subprocess.run(
            ["docker", "network", "ls", "-q", "--filter", f"name={TEST_LAB_ID}"],
            capture_output=True,
            text=True,
        )
        network_ids = result.stdout.strip().split()
        if network_ids and network_ids[0]:
            for net_id in network_ids:
                subprocess.run(["docker", "network", "rm", net_id], capture_output=True)

    _cleanup()
    yield
    _cleanup()


# --- Helper Functions ---

def deploy_lab(topology: dict, timeout: float = DEPLOY_TIMEOUT) -> dict:
    """Deploy a lab via the agent API."""
    request = {
        "job_id": f"test-{int(time.time())}",
        "lab_id": TEST_LAB_ID,
        "topology": topology,
        "provider": "docker",
    }

    response = httpx.post(
        f"{AGENT_URL}/jobs/deploy",
        json=request,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def wait_for_container_ready(container_name: str, timeout: float = BOOT_TIMEOUT) -> bool:
    """Wait for a container to be ready (running and healthy)."""
    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            ["docker", "inspect", container_name, "--format", "{{.State.Status}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "running" in result.stdout.lower():
            return True
        time.sleep(2)
    return False


def exec_in_container(container_name: str, cmd: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """Execute a command in a container."""
    result = subprocess.run(
        ["docker", "exec", container_name] + cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def get_platform(container_name: str) -> str | None:
    """Get Ark.getPlatform() result from a cEOS container."""
    code, stdout, stderr = exec_in_container(
        container_name,
        ["python3", "-c", "import Ark; print(Ark.getPlatform())"],
    )
    if code == 0:
        return stdout.strip()
    return None


def test_cli(container_name: str) -> bool:
    """Test if CLI is accessible in a cEOS container."""
    code, stdout, stderr = exec_in_container(
        container_name,
        ["Cli", "-c", "show version"],
        timeout=60,
    )
    return code == 0 and "Arista" in stdout


# --- Tests ---

class TestCeosPlatformDetection:
    """Tests for cEOS platform detection fix."""

    def test_single_ceos_platform_detection(self, check_prerequisites, cleanup_lab):
        """Deploy single cEOS and verify Ark.getPlatform() returns 'ceoslab'."""
        topology = {
            "nodes": [
                {"name": "eos_1", "display_name": "EOS-1", "kind": "ceos", "image": CEOS_IMAGE}
            ],
            "links": [],
        }

        result = deploy_lab(topology)
        assert result["status"] == "completed", f"Deploy failed: {result}"

        container_name = f"archetype-{TEST_LAB_ID[:20]}-eos_1"

        # Wait for container to be running
        assert wait_for_container_ready(container_name), "Container did not start"

        # Wait additional time for cEOS to boot
        time.sleep(30)

        # Check platform detection
        platform = get_platform(container_name)
        assert platform == "ceoslab", f"Expected platform=ceoslab, got {platform}"

    def test_ceos_cli_accessible(self, check_prerequisites, cleanup_lab):
        """Verify CLI is accessible after cEOS boot."""
        topology = {
            "nodes": [
                {"name": "eos_1", "display_name": "EOS-1", "kind": "ceos", "image": CEOS_IMAGE}
            ],
            "links": [],
        }

        result = deploy_lab(topology)
        assert result["status"] == "completed"

        container_name = f"archetype-{TEST_LAB_ID[:20]}-eos_1"
        assert wait_for_container_ready(container_name)

        # Wait for full boot
        time.sleep(60)

        # Test CLI
        assert test_cli(container_name), "CLI not accessible"


class TestMultipleCeosNodes:
    """Tests for multiple cEOS nodes booting correctly."""

    def test_three_ceos_nodes_platform_detection(self, check_prerequisites, cleanup_lab):
        """Verify all 3 cEOS nodes boot with correct platform."""
        topology = {
            "nodes": [
                {"name": "eos_1", "display_name": "EOS-1", "kind": "ceos", "image": CEOS_IMAGE},
                {"name": "eos_2", "display_name": "EOS-2", "kind": "ceos", "image": CEOS_IMAGE},
                {"name": "eos_3", "display_name": "EOS-3", "kind": "ceos", "image": CEOS_IMAGE},
            ],
            "links": [
                {"source_node": "eos_1", "source_interface": "eth1",
                 "target_node": "eos_2", "target_interface": "eth1"},
                {"source_node": "eos_2", "source_interface": "eth2",
                 "target_node": "eos_3", "target_interface": "eth1"},
            ],
        }

        result = deploy_lab(topology, timeout=300)
        assert result["status"] == "completed", f"Deploy failed: {result}"

        # Wait for all containers
        for node in ["eos_1", "eos_2", "eos_3"]:
            container_name = f"archetype-{TEST_LAB_ID[:20]}-{node}"
            assert wait_for_container_ready(container_name), f"{node} did not start"

        # Wait for boot
        time.sleep(90)

        # Check platform on all nodes
        for node in ["eos_1", "eos_2", "eos_3"]:
            container_name = f"archetype-{TEST_LAB_ID[:20]}-{node}"
            platform = get_platform(container_name)
            assert platform == "ceoslab", f"{node}: expected platform=ceoslab, got {platform}"

    def test_three_ceos_nodes_cli_accessible(self, check_prerequisites, cleanup_lab):
        """Verify CLI works on all 3 cEOS nodes."""
        topology = {
            "nodes": [
                {"name": "eos_1", "display_name": "EOS-1", "kind": "ceos", "image": CEOS_IMAGE},
                {"name": "eos_2", "display_name": "EOS-2", "kind": "ceos", "image": CEOS_IMAGE},
                {"name": "eos_3", "display_name": "EOS-3", "kind": "ceos", "image": CEOS_IMAGE},
            ],
            "links": [],
        }

        result = deploy_lab(topology, timeout=300)
        assert result["status"] == "completed"

        # Wait for all containers and boot
        time.sleep(120)

        # Check CLI on all nodes
        for node in ["eos_1", "eos_2", "eos_3"]:
            container_name = f"archetype-{TEST_LAB_ID[:20]}-{node}"
            assert test_cli(container_name), f"{node}: CLI not accessible"


class TestDeployPerformance:
    """Tests for deploy not blocking."""

    def test_deploy_completes_within_timeout(self, check_prerequisites, cleanup_lab):
        """Verify deploy doesn't block indefinitely."""
        topology = {
            "nodes": [
                {"name": "eos_1", "display_name": "EOS-1", "kind": "ceos", "image": CEOS_IMAGE},
                {"name": "eos_2", "display_name": "EOS-2", "kind": "ceos", "image": CEOS_IMAGE},
            ],
            "links": [
                {"source_node": "eos_1", "source_interface": "eth1",
                 "target_node": "eos_2", "target_interface": "eth1"},
            ],
        }

        start = time.time()
        result = deploy_lab(topology, timeout=DEPLOY_TIMEOUT)
        elapsed = time.time() - start

        assert result["status"] == "completed", f"Deploy failed: {result}"
        # Deploy should complete (containers created) relatively quickly
        # The timeout is for readiness which takes longer
        assert elapsed < DEPLOY_TIMEOUT, f"Deploy took too long: {elapsed}s"

    @pytest.mark.asyncio
    async def test_deploy_async_not_blocking(self, check_prerequisites, cleanup_lab):
        """Verify deploy doesn't block the event loop."""
        topology = {
            "nodes": [
                {"name": "eos_1", "kind": "ceos", "image": CEOS_IMAGE},
            ],
            "links": [],
        }

        async def deploy_async():
            async with httpx.AsyncClient() as client:
                request = {
                    "job_id": f"test-async-{int(time.time())}",
                    "lab_id": TEST_LAB_ID,
                    "topology": topology,
                    "provider": "docker",
                }
                response = await client.post(
                    f"{AGENT_URL}/jobs/deploy",
                    json=request,
                    timeout=DEPLOY_TIMEOUT,
                )
                return response.json()

        # Should complete without asyncio.TimeoutError
        result = await asyncio.wait_for(deploy_async(), timeout=DEPLOY_TIMEOUT)
        assert result["status"] == "completed"


class TestCeosHostname:
    """Tests for cEOS hostname configuration."""

    def test_hostname_uses_display_name(self, check_prerequisites, cleanup_lab):
        """Verify hostname is set from display_name."""
        topology = {
            "nodes": [
                {"name": "eos_1", "display_name": "MySwitch", "kind": "ceos", "image": CEOS_IMAGE},
            ],
            "links": [],
        }

        result = deploy_lab(topology)
        assert result["status"] == "completed"

        container_name = f"archetype-{TEST_LAB_ID[:20]}-eos_1"
        assert wait_for_container_ready(container_name)
        time.sleep(60)

        # Check hostname via CLI
        code, stdout, stderr = exec_in_container(
            container_name,
            ["Cli", "-c", "show hostname"],
        )
        assert code == 0
        assert "MySwitch" in stdout, f"Expected hostname MySwitch, got: {stdout}"
