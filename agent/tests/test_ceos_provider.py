"""Unit tests for cEOS-specific Docker provider functionality.

These tests verify:
1. Interface counting from topology links
2. Container config uses if-wait.sh wrapper for cEOS
3. Minimal startup-config uses display_name for hostname
4. if-wait.sh script is created with correct permissions
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import tempfile
import os

from agent.providers.docker import (
    DockerProvider,
    TopologyNode,
    TopologyLink,
    ParsedTopology,
    IF_WAIT_SCRIPT,
)
from agent.schemas import DeployNode, DeployTopology
from agent.vendors import get_config_by_device


# --- Fixtures ---

@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client."""
    client = MagicMock()
    client.networks = MagicMock()
    client.containers = MagicMock()
    return client


@pytest.fixture
def provider(mock_docker_client):
    """Create a DockerProvider with mocked Docker client."""
    with patch("agent.providers.docker.docker.from_env", return_value=mock_docker_client):
        with patch("agent.providers.docker.get_local_manager"):
            with patch("agent.providers.docker.get_ovs_manager"):
                with patch("agent.providers.docker.get_docker_ovs_plugin"):
                    p = DockerProvider()
                    # docker is accessed via _docker attribute internally
                    p._docker = mock_docker_client
                    return p


@pytest.fixture
def sample_topology():
    """Create a sample topology with cEOS nodes and links."""
    nodes = {
        "eos_1": TopologyNode(
            name="eos_1",
            kind="ceos",
            display_name="EOS-1",
            image="ceos:latest",
        ),
        "eos_2": TopologyNode(
            name="eos_2",
            kind="ceos",
            display_name="EOS-2",
            image="ceos:latest",
        ),
        "eos_3": TopologyNode(
            name="eos_3",
            kind="ceos",
            display_name=None,  # Test fallback to node_name
            image="ceos:latest",
        ),
    }
    links = [
        TopologyLink(endpoints=["eos_1:eth1", "eos_2:eth1"]),
        TopologyLink(endpoints=["eos_1:eth2", "eos_3:eth1"]),
        TopologyLink(endpoints=["eos_2:eth2", "eos_3:eth2"]),
    ]
    return ParsedTopology(name="test-topology", nodes=nodes, links=links)


# --- Tests for _count_node_interfaces ---

class TestCountNodeInterfaces:
    """Tests for counting interfaces per node from topology."""

    def test_count_interfaces_single_link(self, provider):
        """Node with one interface."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[TopologyLink(endpoints=["eos_1:eth1", "other:eth1"])],
        )
        count = provider._count_node_interfaces("eos_1", topology)
        assert count == 1

    def test_count_interfaces_multiple_links(self, provider, sample_topology):
        """Node with multiple interfaces."""
        # eos_1 has eth1 and eth2
        count = provider._count_node_interfaces("eos_1", sample_topology)
        assert count == 2

        # eos_2 has eth1 and eth2
        count = provider._count_node_interfaces("eos_2", sample_topology)
        assert count == 2

        # eos_3 has eth1 and eth2
        count = provider._count_node_interfaces("eos_3", sample_topology)
        assert count == 2

    def test_count_interfaces_no_links(self, provider):
        """Node with no interfaces."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[],
        )
        count = provider._count_node_interfaces("eos_1", topology)
        assert count == 0

    def test_count_interfaces_node_not_in_links(self, provider):
        """Node exists but has no links."""
        topology = ParsedTopology(name="test",
            nodes={
                "eos_1": TopologyNode(name="eos_1", kind="ceos"),
                "eos_2": TopologyNode(name="eos_2", kind="ceos"),
            },
            links=[TopologyLink(endpoints=["eos_1:eth1", "eos_1:eth2"])],  # Self-link
        )
        count = provider._count_node_interfaces("eos_2", topology)
        assert count == 0

    def test_count_interfaces_unique_only(self, provider):
        """Same interface used in multiple links counted once."""
        # This shouldn't happen in practice, but test the set behavior
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[
                TopologyLink(endpoints=["eos_1:eth1", "other:eth1"]),
                TopologyLink(endpoints=["eos_1:eth1", "other:eth2"]),  # eth1 again
            ],
        )
        count = provider._count_node_interfaces("eos_1", topology)
        assert count == 1  # eth1 counted only once


# --- Tests for cEOS container config ---

class TestCeosContainerConfig:
    """Tests for cEOS-specific container configuration."""

    def test_ceos_uses_ifwait_wrapper(self, provider):
        """Verify cEOS containers use bash wrapper with if-wait.sh."""
        node = TopologyNode(
            name="eos_1",
            kind="ceos",
            display_name="EOS-1",
            image="ceos:latest",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = provider._create_container_config(
                node, "test-lab", workspace, interface_count=2
            )

            assert config["entrypoint"] == ["/bin/bash", "-c"]
            assert "/mnt/flash/if-wait.sh" in config["command"][0]
            assert "exec /sbin/init" in config["command"][0]
            assert config["environment"]["CLAB_INTFS"] == "2"

    def test_ceos_without_interfaces_uses_direct_init(self, provider):
        """Verify cEOS with 0 interfaces uses /sbin/init directly."""
        node = TopologyNode(
            name="eos_1",
            kind="ceos",
            display_name="EOS-1",
            image="ceos:latest",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = provider._create_container_config(
                node, "test-lab", workspace, interface_count=0
            )

            # With 0 interfaces, should use direct /sbin/init
            assert config["entrypoint"] == ["/sbin/init"]
            assert "CLAB_INTFS" not in config["environment"]

    def test_non_ceos_ignores_interface_count(self, provider):
        """Verify non-cEOS nodes don't use if-wait wrapper."""
        node = TopologyNode(
            name="linux_1",
            kind="linux",
            image="alpine:latest",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = provider._create_container_config(
                node, "test-lab", workspace, interface_count=5
            )

            # Linux nodes don't need if-wait
            assert config.get("entrypoint") != ["/bin/bash", "-c"]
            assert "CLAB_INTFS" not in config.get("environment", {})

    def test_ceos_environment_variables(self, provider):
        """Verify cEOS has required environment variables."""
        node = TopologyNode(
            name="eos_1",
            kind="ceos",
            image="ceos:latest",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = provider._create_container_config(
                node, "test-lab", workspace, interface_count=1
            )

            env = config["environment"]
            assert env.get("CEOS") == "1"
            assert env.get("EOS_PLATFORM") == "ceoslab"
            assert env.get("INTFTYPE") == "eth"
            assert env.get("MGMT_INTF") == "eth0"

    def test_json_topology_defaults_interface_count_for_ceos(self, provider):
        """Verify JSON topology defaults interface_count for cEOS when missing."""
        config = get_config_by_device("ceos")
        assert config and config.max_ports > 0

        topology = DeployTopology(
            nodes=[
                DeployNode(
                    name="eos_1",
                    display_name="EOS-1",
                    kind="ceos",
                    image="ceos:latest",
                    interface_count=None,
                ),
            ],
            links=[],
        )

        parsed = provider._topology_from_json(topology)
        assert parsed.nodes["eos_1"].interface_count == config.max_ports

    def test_required_interfaces_uses_vendor_defaults(self, provider):
        """Ensure required interface count is based on vendor defaults + buffer."""
        config = get_config_by_device("ceos")
        assert config and config.max_ports > 0

        topology = DeployTopology(
            nodes=[
                DeployNode(
                    name="eos_1",
                    display_name="EOS-1",
                    kind="ceos",
                    image="ceos:latest",
                    interface_count=None,
                ),
            ],
            links=[],
        )

        parsed = provider._topology_from_json(topology)
        required = provider._calculate_required_interfaces(parsed)
        assert required == config.max_ports + 4



# --- Tests for startup-config hostname ---

class TestStartupConfigHostname:
    """Tests for minimal startup-config generation."""

    @pytest.mark.asyncio
    async def test_startup_config_uses_display_name(self, provider, sample_topology):
        """Verify minimal startup-config uses display_name for hostname."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(sample_topology, workspace, use_thread=False)

            # Check eos_1 (has display_name="EOS-1")
            config_path = workspace / "configs" / "eos_1" / "flash" / "startup-config"
            assert config_path.exists()
            content = config_path.read_text()
            assert "hostname EOS-1" in content

            # Check eos_2 (has display_name="EOS-2")
            config_path = workspace / "configs" / "eos_2" / "flash" / "startup-config"
            content = config_path.read_text()
            assert "hostname EOS-2" in content

    @pytest.mark.asyncio
    async def test_startup_config_falls_back_to_node_name(self, provider, sample_topology):
        """Verify fallback to node_name when display_name is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(sample_topology, workspace, use_thread=False)

            # Check eos_3 (display_name=None, should use node_name)
            config_path = workspace / "configs" / "eos_3" / "flash" / "startup-config"
            content = config_path.read_text()
            assert "hostname eos_3" in content

    @pytest.mark.asyncio
    async def test_existing_startup_config_not_overwritten(self, provider):
        """Verify existing startup-config is not overwritten."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos", display_name="EOS-1")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            # Pre-create startup-config
            flash_dir = workspace / "configs" / "eos_1" / "flash"
            flash_dir.mkdir(parents=True)
            config_path = flash_dir / "startup-config"
            config_path.write_text("hostname EXISTING\n")

            await provider._ensure_directories(topology, workspace, use_thread=False)

            # Should not be overwritten
            content = config_path.read_text()
            assert "hostname EXISTING" in content
            assert "hostname EOS-1" not in content


# --- Tests for if-wait.sh script creation ---

class TestIfWaitScript:
    """Tests for if-wait.sh script creation."""

    @pytest.mark.asyncio
    async def test_ifwait_script_created(self, provider):
        """Verify if-wait.sh is created in flash directory."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            script_path = workspace / "configs" / "eos_1" / "flash" / "if-wait.sh"
            assert script_path.exists()

    @pytest.mark.asyncio
    async def test_ifwait_script_executable(self, provider):
        """Verify if-wait.sh has executable permission."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            script_path = workspace / "configs" / "eos_1" / "flash" / "if-wait.sh"
            mode = script_path.stat().st_mode
            assert mode & 0o111  # At least one execute bit set

    @pytest.mark.asyncio
    async def test_ifwait_script_content(self, provider):
        """Verify if-wait.sh content matches IF_WAIT_SCRIPT."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            script_path = workspace / "configs" / "eos_1" / "flash" / "if-wait.sh"
            content = script_path.read_text()
            assert content == IF_WAIT_SCRIPT

    @pytest.mark.asyncio
    async def test_ifwait_not_created_for_non_ceos(self, provider):
        """Verify if-wait.sh is not created for non-cEOS nodes."""
        topology = ParsedTopology(name="test",
            nodes={"linux_1": TopologyNode(name="linux_1", kind="linux")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            # Linux nodes don't get flash directory
            flash_dir = workspace / "configs" / "linux_1" / "flash"
            assert not flash_dir.exists()


# --- Tests for zerotouch-config ---

class TestZerotouchConfig:
    """Tests for zerotouch-config creation."""

    @pytest.mark.asyncio
    async def test_zerotouch_config_created(self, provider):
        """Verify zerotouch-config is created to disable ZTP."""
        topology = ParsedTopology(name="test",
            nodes={"eos_1": TopologyNode(name="eos_1", kind="ceos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            zt_path = workspace / "configs" / "eos_1" / "flash" / "zerotouch-config"
            assert zt_path.exists()
            assert "DISABLE=True" in zt_path.read_text()
