"""Unit tests for cJunOS-specific Docker provider functionality.

These tests verify:
1. Config directory creation for cJunOS nodes
2. Startup config injection from topology YAML
3. Startup config injection from extracted config
4. No default config when no source exists
5. Topology config takes priority over extracted config
6. is_cjunos_kind() resolves all aliases
7. Vendor config assertions (hierarchical command, bind mount)
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile

from agent.providers.docker import (
    DockerProvider,
    TopologyNode,
    ParsedTopology,
)
from agent.vendors import (
    get_config_by_device,
    is_cjunos_kind,
    VENDOR_CONFIGS,
)


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
                    p._docker = mock_docker_client
                    return p


# --- Tests for is_cjunos_kind ---

class TestIsCjunosKind:
    """Tests for is_cjunos_kind() alias resolution."""

    def test_canonical_name(self):
        assert is_cjunos_kind("juniper_cjunos") is True

    def test_alias_cjunos(self):
        assert is_cjunos_kind("cjunos") is True

    def test_alias_cjunosevolved(self):
        assert is_cjunos_kind("cjunosevolved") is True

    def test_alias_cjunos_evolved(self):
        assert is_cjunos_kind("cjunos-evolved") is True

    def test_alias_cjunos_underscore(self):
        assert is_cjunos_kind("cjunos_evolved") is True

    def test_non_cjunos_kind(self):
        assert is_cjunos_kind("ceos") is False

    def test_unknown_kind(self):
        assert is_cjunos_kind("unknown_device") is False

    def test_case_insensitive(self):
        assert is_cjunos_kind("CJUNOS") is True
        assert is_cjunos_kind("Juniper_cJunos") is True


# --- Tests for vendor config ---

class TestCjunosVendorConfig:
    """Tests for cJunOS vendor configuration."""

    def test_extraction_command_hierarchical(self):
        """Verify extraction uses hierarchical format (not display set)."""
        config = get_config_by_device("juniper_cjunos")
        assert config is not None
        assert config.config_extract_command == "cli -c 'show configuration'"
        assert "display set" not in config.config_extract_command

    def test_bind_mount_includes_config(self):
        """Verify /config bind mount is present."""
        config = get_config_by_device("juniper_cjunos")
        assert config is not None
        config_bind = "{workspace}/configs/{node}/config:/config"
        assert config_bind in config.binds

    def test_bind_mount_includes_kvm(self):
        """Verify /dev/kvm bind mount is still present."""
        config = get_config_by_device("juniper_cjunos")
        assert config is not None
        assert "/dev/kvm:/dev/kvm" in config.binds


# --- Tests for _setup_cjunos_directories ---

class TestSetupCjunosDirectories:
    """Tests for cJunOS directory setup and config injection."""

    @pytest.mark.asyncio
    async def test_config_directory_created(self, provider):
        """Verify config directory is created."""
        topology = ParsedTopology(
            name="test",
            nodes={"junos_1": TopologyNode(name="junos_1", kind="juniper_cjunos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            config_dir = workspace / "configs" / "junos_1" / "config"
            assert config_dir.exists()
            assert config_dir.is_dir()

    @pytest.mark.asyncio
    async def test_startup_config_from_topology(self, provider):
        """Verify startup config is written from topology YAML."""
        topo_config = "system { host-name junos-test; }"
        topology = ParsedTopology(
            name="test",
            nodes={
                "junos_1": TopologyNode(
                    name="junos_1",
                    kind="juniper_cjunos",
                    startup_config=topo_config,
                ),
            },
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            cfg_path = workspace / "configs" / "junos_1" / "config" / "startup-config.cfg"
            assert cfg_path.exists()
            assert cfg_path.read_text() == topo_config

    @pytest.mark.asyncio
    async def test_startup_config_from_extracted(self, provider):
        """Verify previously extracted config is copied into config dir."""
        topology = ParsedTopology(
            name="test",
            nodes={"junos_1": TopologyNode(name="junos_1", kind="juniper_cjunos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            # Pre-create extracted config
            extracted_dir = workspace / "configs" / "junos_1"
            extracted_dir.mkdir(parents=True)
            extracted_path = extracted_dir / "startup-config"
            extracted_path.write_text("system { host-name extracted; }")

            await provider._ensure_directories(topology, workspace, use_thread=False)

            cfg_path = workspace / "configs" / "junos_1" / "config" / "startup-config.cfg"
            assert cfg_path.exists()
            assert cfg_path.read_text() == "system { host-name extracted; }"

    @pytest.mark.asyncio
    async def test_no_default_config_when_no_source(self, provider):
        """Verify no startup-config.cfg is created when no source exists."""
        topology = ParsedTopology(
            name="test",
            nodes={"junos_1": TopologyNode(name="junos_1", kind="juniper_cjunos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            cfg_path = workspace / "configs" / "junos_1" / "config" / "startup-config.cfg"
            assert not cfg_path.exists()

    @pytest.mark.asyncio
    async def test_topology_config_takes_priority(self, provider):
        """Verify topology YAML config takes priority over extracted config."""
        topo_config = "system { host-name from-topology; }"
        topology = ParsedTopology(
            name="test",
            nodes={
                "junos_1": TopologyNode(
                    name="junos_1",
                    kind="juniper_cjunos",
                    startup_config=topo_config,
                ),
            },
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            # Pre-create extracted config
            extracted_dir = workspace / "configs" / "junos_1"
            extracted_dir.mkdir(parents=True)
            (extracted_dir / "startup-config").write_text("system { host-name extracted; }")

            await provider._ensure_directories(topology, workspace, use_thread=False)

            cfg_path = workspace / "configs" / "junos_1" / "config" / "startup-config.cfg"
            assert cfg_path.read_text() == topo_config

    @pytest.mark.asyncio
    async def test_cjunos_alias_triggers_setup(self, provider):
        """Verify cJunOS alias 'cjunos' triggers directory setup."""
        topology = ParsedTopology(
            name="test",
            nodes={"junos_1": TopologyNode(name="junos_1", kind="cjunos")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            config_dir = workspace / "configs" / "junos_1" / "config"
            assert config_dir.exists()

    @pytest.mark.asyncio
    async def test_non_cjunos_no_config_dir(self, provider):
        """Verify non-cJunOS nodes don't get a config directory."""
        topology = ParsedTopology(
            name="test",
            nodes={"linux_1": TopologyNode(name="linux_1", kind="linux")},
            links=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            await provider._ensure_directories(topology, workspace, use_thread=False)

            config_dir = workspace / "configs" / "linux_1" / "config"
            assert not config_dir.exists()
