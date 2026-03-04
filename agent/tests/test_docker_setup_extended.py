"""Comprehensive tests for agent.providers.docker_setup module.

Tests cover:
1. setup_ceos_directories - cEOS directory creation, startup config injection, zerotouch
2. setup_cjunos_directories - cJunOS directory creation, startup config injection
3. validate_images - Docker image and file-based image validation
4. create_container_config - container configuration building
5. calculate_required_interfaces - interface count calculation from topology
6. count_node_interfaces - per-node interface counting
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.providers.docker import (
    ParsedTopology,
    TopologyLink,
    TopologyNode,
)
from agent.providers.docker_setup import (
    LABEL_LAB_ID,
    LABEL_NODE_DISPLAY_NAME,
    LABEL_NODE_INTERFACE_COUNT,
    LABEL_NODE_KIND,
    LABEL_NODE_NAME,
    LABEL_NODE_READINESS_PATTERN,
    LABEL_NODE_READINESS_PROBE,
    LABEL_NODE_READINESS_TIMEOUT,
    LABEL_PROVIDER,
    calculate_required_interfaces,
    count_node_interfaces,
    create_container_config,
    setup_ceos_directories,
    setup_cjunos_directories,
    validate_images,
)
from agent.vendors import ContainerRuntimeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(name: str = "r1", kind: str = "linux", **kwargs) -> TopologyNode:
    """Create a TopologyNode with sane defaults."""
    return TopologyNode(name=name, kind=kind, **kwargs)


def _make_topology(
    nodes: dict[str, TopologyNode] | None = None,
    links: list[TopologyLink] | None = None,
    name: str = "test",
) -> ParsedTopology:
    """Create a ParsedTopology with sane defaults."""
    return ParsedTopology(
        name=name,
        nodes=nodes or {},
        links=links or [],
    )


def _make_runtime_config(**overrides) -> ContainerRuntimeConfig:
    """Create a ContainerRuntimeConfig with sane defaults."""
    defaults = dict(
        image="alpine:latest",
        environment={"FOO": "bar"},
        capabilities=[],
        privileged=False,
        binds=[],
        entrypoint=None,
        cmd=None,
        network_mode="none",
        sysctls={},
        hostname="r1",
        memory_mb=512,
        cpu_count=1,
    )
    defaults.update(overrides)
    return ContainerRuntimeConfig(**defaults)


def _name_func(lab_id: str, node_name: str) -> str:
    """Deterministic container name generator for tests."""
    return f"archetype-{lab_id}-{node_name}"


# ===========================================================================
# setup_ceos_directories
# ===========================================================================

class TestSetupCeosDirectories:
    """Tests for cEOS directory and config setup."""

    def test_flash_directory_created(self, tmp_path: Path):
        """Flash directory is created under workspace/configs/node/flash."""
        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)

        flash_dir = tmp_path / "configs" / "sw1" / "flash"
        assert flash_dir.is_dir()

    def test_systemd_env_file_created(self, tmp_path: Path):
        """Systemd ceos-env.conf is created with correct content."""
        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)

        env_file = tmp_path / "configs" / "sw1" / "systemd" / "ceos-env.conf"
        assert env_file.exists()
        content = env_file.read_text()
        assert "[Manager]" in content
        assert "EOS_PLATFORM=ceoslab" in content
        assert "INTFTYPE=eth" in content
        assert "CEOS_NOZEROTOUCH=1" in content

    def test_startup_config_from_topology(self, tmp_path: Path):
        """Topology startup_config is written to flash/startup-config."""
        config_text = "hostname topology-router\n"
        node = _make_node("sw1", kind="ceos", startup_config=config_text)
        setup_ceos_directories("sw1", node, tmp_path)

        cfg = tmp_path / "configs" / "sw1" / "flash" / "startup-config"
        assert cfg.exists()
        assert cfg.read_text() == config_text

    def test_startup_config_from_extracted(self, tmp_path: Path):
        """Previously extracted config is copied when no topology config."""
        # Pre-create extracted config in the expected location
        extracted_dir = tmp_path / "configs" / "sw1"
        extracted_dir.mkdir(parents=True)
        (extracted_dir / "startup-config").write_text("hostname extracted\n")

        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)

        cfg = tmp_path / "configs" / "sw1" / "flash" / "startup-config"
        assert cfg.exists()
        assert cfg.read_text() == "hostname extracted\n"

    def test_topology_config_takes_priority_over_extracted(self, tmp_path: Path):
        """Topology startup_config wins over previously extracted config."""
        extracted_dir = tmp_path / "configs" / "sw1"
        extracted_dir.mkdir(parents=True)
        (extracted_dir / "startup-config").write_text("hostname extracted\n")

        topo_config = "hostname from-topology\n"
        node = _make_node("sw1", kind="ceos", startup_config=topo_config)
        setup_ceos_directories("sw1", node, tmp_path)

        cfg = tmp_path / "configs" / "sw1" / "flash" / "startup-config"
        assert cfg.read_text() == topo_config

    def test_minimal_config_generated_when_no_source(self, tmp_path: Path):
        """A minimal startup-config is generated when no source exists."""
        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)

        cfg = tmp_path / "configs" / "sw1" / "flash" / "startup-config"
        assert cfg.exists()
        content = cfg.read_text()
        assert "hostname sw1" in content
        assert "no aaa root" in content
        assert "IPTABLES_CLEANUP" in content

    def test_minimal_config_uses_display_name(self, tmp_path: Path):
        """Minimal config uses display_name for hostname when available."""
        node = _make_node("sw1", kind="ceos", display_name="CoreSwitch")
        setup_ceos_directories("sw1", node, tmp_path)

        cfg = tmp_path / "configs" / "sw1" / "flash" / "startup-config"
        content = cfg.read_text()
        assert "hostname CoreSwitch" in content

    def test_zerotouch_config_created(self, tmp_path: Path):
        """zerotouch-config is created to disable ZTP."""
        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)

        zt = tmp_path / "configs" / "sw1" / "flash" / "zerotouch-config"
        assert zt.exists()
        assert "DISABLE=True" in zt.read_text()

    def test_zerotouch_not_overwritten(self, tmp_path: Path):
        """Existing zerotouch-config is not overwritten."""
        flash_dir = tmp_path / "configs" / "sw1" / "flash"
        flash_dir.mkdir(parents=True)
        zt = flash_dir / "zerotouch-config"
        zt.write_text("CUSTOM_CONTENT\n")

        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)
        assert zt.read_text() == "CUSTOM_CONTENT\n"

    @patch("agent.providers.docker_setup._get_if_wait_script", return_value="#!/bin/sh\necho test\n")
    def test_if_wait_script_created_executable(self, _mock_script, tmp_path: Path):
        """if-wait.sh is created and marked executable."""
        node = _make_node("sw1", kind="ceos")
        setup_ceos_directories("sw1", node, tmp_path)

        script = tmp_path / "configs" / "sw1" / "flash" / "if-wait.sh"
        assert script.exists()
        assert script.read_text() == "#!/bin/sh\necho test\n"
        assert os.access(script, os.X_OK)


# ===========================================================================
# setup_cjunos_directories
# ===========================================================================

class TestSetupCjunosDirectories:
    """Tests for cJunOS directory and config setup."""

    def test_config_directory_created(self, tmp_path: Path):
        """Config directory is created under workspace/configs/node/config."""
        node = _make_node("junos1", kind="juniper_cjunos")
        setup_cjunos_directories("junos1", node, tmp_path)

        config_dir = tmp_path / "configs" / "junos1" / "config"
        assert config_dir.is_dir()

    def test_startup_config_from_topology(self, tmp_path: Path):
        """Topology startup_config is written to config/startup-config.cfg."""
        cfg_text = "system { host-name topo-test; }"
        node = _make_node("junos1", kind="juniper_cjunos", startup_config=cfg_text)
        setup_cjunos_directories("junos1", node, tmp_path)

        cfg = tmp_path / "configs" / "junos1" / "config" / "startup-config.cfg"
        assert cfg.exists()
        assert cfg.read_text() == cfg_text

    def test_startup_config_from_extracted(self, tmp_path: Path):
        """Extracted config is copied when no topology config."""
        extracted_dir = tmp_path / "configs" / "junos1"
        extracted_dir.mkdir(parents=True)
        (extracted_dir / "startup-config").write_text("system { host-name extracted; }")

        node = _make_node("junos1", kind="juniper_cjunos")
        setup_cjunos_directories("junos1", node, tmp_path)

        cfg = tmp_path / "configs" / "junos1" / "config" / "startup-config.cfg"
        assert cfg.exists()
        assert cfg.read_text() == "system { host-name extracted; }"

    def test_no_config_when_no_source(self, tmp_path: Path):
        """No startup-config.cfg is created when no source exists."""
        node = _make_node("junos1", kind="juniper_cjunos")
        setup_cjunos_directories("junos1", node, tmp_path)

        cfg = tmp_path / "configs" / "junos1" / "config" / "startup-config.cfg"
        assert not cfg.exists()

    def test_topology_config_priority(self, tmp_path: Path):
        """Topology config takes priority over extracted config."""
        extracted_dir = tmp_path / "configs" / "junos1"
        extracted_dir.mkdir(parents=True)
        (extracted_dir / "startup-config").write_text("system { host-name extracted; }")

        topo_config = "system { host-name from-topology; }"
        node = _make_node("junos1", kind="juniper_cjunos", startup_config=topo_config)
        setup_cjunos_directories("junos1", node, tmp_path)

        cfg = tmp_path / "configs" / "junos1" / "config" / "startup-config.cfg"
        assert cfg.read_text() == topo_config


# ===========================================================================
# validate_images
# ===========================================================================

class TestValidateImages:
    """Tests for image validation."""

    def test_docker_image_found(self):
        """No missing images when Docker image exists."""
        client = MagicMock()
        client.images.get.return_value = MagicMock()

        node = _make_node("r1", kind="linux", image="alpine:latest")
        topology = _make_topology(nodes={"r1": node})

        missing = validate_images(topology, client)
        assert missing == []
        client.images.get.assert_called_once_with("alpine:latest")

    def test_docker_image_not_found(self):
        """Missing image reported when ImageNotFound is raised."""
        from docker.errors import ImageNotFound

        client = MagicMock()
        client.images.get.side_effect = ImageNotFound("not found")

        node = _make_node("r1", kind="linux", image="missing:tag")
        topology = _make_topology(nodes={"r1": node})

        missing = validate_images(topology, client)
        assert missing == [("r1", "missing:tag")]

    def test_docker_api_error_no_missing(self):
        """APIError is logged but node is NOT reported as missing."""
        from docker.errors import APIError

        client = MagicMock()
        client.images.get.side_effect = APIError("api error")

        node = _make_node("r1", kind="linux", image="some:image")
        topology = _make_topology(nodes={"r1": node})

        missing = validate_images(topology, client)
        assert missing == []

    def test_file_based_image_exists(self, tmp_path: Path):
        """File-based images (qcow2) are checked on filesystem."""
        img_path = str(tmp_path / "disk.qcow2")
        Path(img_path).write_text("fake image")

        client = MagicMock()
        node = _make_node("vm1", kind="linux", image=img_path)
        topology = _make_topology(nodes={"vm1": node})

        missing = validate_images(topology, client)
        assert missing == []
        # Docker should NOT be queried for file-based images
        client.images.get.assert_not_called()

    def test_file_based_image_missing(self):
        """Missing file-based image is reported."""
        client = MagicMock()
        node = _make_node("vm1", kind="linux", image="/nonexistent/disk.qcow2")
        topology = _make_topology(nodes={"vm1": node})

        missing = validate_images(topology, client)
        assert missing == [("vm1", "/nonexistent/disk.qcow2")]

    def test_absolute_path_image_checked_on_filesystem(self, tmp_path: Path):
        """Images starting with '/' are treated as file-based."""
        img_path = str(tmp_path / "image.bin")
        Path(img_path).write_text("data")

        client = MagicMock()
        node = _make_node("n1", kind="linux", image=img_path)
        topology = _make_topology(nodes={"n1": node})

        missing = validate_images(topology, client)
        assert missing == []
        client.images.get.assert_not_called()

    def test_img_extension_checked_on_filesystem(self):
        """Images ending in .img are treated as file-based."""
        client = MagicMock()
        node = _make_node("n1", kind="linux", image="/var/images/disk.img")
        topology = _make_topology(nodes={"n1": node})

        missing = validate_images(topology, client)
        assert missing == [("n1", "/var/images/disk.img")]
        client.images.get.assert_not_called()

    def test_iol_extension_checked_on_filesystem(self):
        """Images ending in .iol are treated as file-based."""
        client = MagicMock()
        node = _make_node("n1", kind="linux", image="/opt/iol/ios.iol")
        topology = _make_topology(nodes={"n1": node})

        missing = validate_images(topology, client)
        assert missing == [("n1", "/opt/iol/ios.iol")]
        client.images.get.assert_not_called()

    def test_node_without_image_uses_default(self):
        """Node without explicit image uses vendor default_image."""
        client = MagicMock()
        client.images.get.return_value = MagicMock()

        # linux kind has a default image in vendor configs
        node = _make_node("r1", kind="linux", image=None)
        topology = _make_topology(nodes={"r1": node})

        missing = validate_images(topology, client)
        # Whether missing or not depends on vendor default, but no crash
        assert isinstance(missing, list)

    def test_multiple_nodes_multiple_missing(self):
        """Multiple missing images are all reported."""
        from docker.errors import ImageNotFound

        client = MagicMock()
        client.images.get.side_effect = ImageNotFound("nope")

        nodes = {
            "r1": _make_node("r1", kind="linux", image="img1"),
            "r2": _make_node("r2", kind="linux", image="img2"),
        }
        topology = _make_topology(nodes=nodes)

        missing = validate_images(topology, client)
        assert len(missing) == 2
        assert ("r1", "img1") in missing
        assert ("r2", "img2") in missing


# ===========================================================================
# create_container_config
# ===========================================================================

class TestCreateContainerConfig:
    """Tests for container configuration building."""

    @patch("agent.providers.docker_setup.get_container_config")
    def test_basic_labels(self, mock_get_config):
        """Core labels are always set."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=4, provider_name="docker",
            container_name_func=_name_func,
        )

        assert cfg["labels"][LABEL_LAB_ID] == "lab1"
        assert cfg["labels"][LABEL_NODE_NAME] == "r1"
        assert cfg["labels"][LABEL_NODE_KIND] == "linux"
        assert cfg["labels"][LABEL_PROVIDER] == "docker"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_interface_count_label(self, mock_get_config):
        """Interface count label is set when count > 0."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=8, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["labels"][LABEL_NODE_INTERFACE_COUNT] == "8"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_interface_count_label_zero(self, mock_get_config):
        """Interface count label is NOT set when count is 0."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=0, provider_name="docker",
            container_name_func=_name_func,
        )
        assert LABEL_NODE_INTERFACE_COUNT not in cfg["labels"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_display_name_label(self, mock_get_config):
        """Display name label is set when display_name is provided."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux", display_name="Router-1")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["labels"][LABEL_NODE_DISPLAY_NAME] == "Router-1"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_no_display_name_label_when_absent(self, mock_get_config):
        """Display name label is NOT set when display_name is None."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert LABEL_NODE_DISPLAY_NAME not in cfg["labels"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_readiness_labels(self, mock_get_config):
        """Readiness probe, pattern, and timeout labels are set."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node(
            "r1", kind="linux",
            readiness_probe="log_pattern",
            readiness_pattern="login:",
            readiness_timeout=300,
        )

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["labels"][LABEL_NODE_READINESS_PROBE] == "log_pattern"
        assert cfg["labels"][LABEL_NODE_READINESS_PATTERN] == "login:"
        assert cfg["labels"][LABEL_NODE_READINESS_TIMEOUT] == "300"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_readiness_timeout_zero_not_set(self, mock_get_config):
        """Readiness timeout label is NOT set when timeout is 0."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux", readiness_timeout=0)

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert LABEL_NODE_READINESS_TIMEOUT not in cfg["labels"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_env_merge(self, mock_get_config):
        """Node env vars are merged on top of vendor defaults."""
        mock_get_config.return_value = _make_runtime_config(
            environment={"VENDOR_VAR": "default", "SHARED": "vendor_val"},
        )
        node = _make_node("r1", kind="linux", env={"SHARED": "node_val", "EXTRA": "1"})

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["environment"]["VENDOR_VAR"] == "default"
        assert cfg["environment"]["SHARED"] == "node_val"
        assert cfg["environment"]["EXTRA"] == "1"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_container_name(self, mock_get_config):
        """Container name is generated by the container_name_func."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab42", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["name"] == "archetype-lab42-r1"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_volume_binds_parsing(self, mock_get_config):
        """Volume binds are parsed into Docker volumes format."""
        mock_get_config.return_value = _make_runtime_config(
            binds=["/host/path:/container/path"],
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert "/host/path" in cfg["volumes"]
        assert cfg["volumes"]["/host/path"]["bind"] == "/container/path"
        assert cfg["volumes"]["/host/path"]["mode"] == "rw"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_volume_binds_readonly(self, mock_get_config):
        """Read-only volume binds are parsed correctly."""
        mock_get_config.return_value = _make_runtime_config(
            binds=["/host:/container:ro"],
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["volumes"]["/host"]["bind"] == "/container"
        assert cfg["volumes"]["/host"]["mode"] == "ro"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_node_binds_appended(self, mock_get_config):
        """Node-specific binds are appended to vendor binds."""
        mock_get_config.return_value = _make_runtime_config(
            binds=["/vendor:/vpath"],
        )
        node = _make_node("r1", kind="linux", binds=["/node:/npath"])

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert "/vendor" in cfg["volumes"]
        assert "/node" in cfg["volumes"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_capabilities_set(self, mock_get_config):
        """Capabilities are set from runtime config."""
        mock_get_config.return_value = _make_runtime_config(
            capabilities=["NET_ADMIN", "SYS_ADMIN"],
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["cap_add"] == ["NET_ADMIN", "SYS_ADMIN"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_no_capabilities_when_empty(self, mock_get_config):
        """cap_add key is absent when capabilities list is empty."""
        mock_get_config.return_value = _make_runtime_config(capabilities=[])
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert "cap_add" not in cfg

    @patch("agent.providers.docker_setup.get_container_config")
    def test_privileged_mode(self, mock_get_config):
        """Privileged mode is set when runtime config requires it."""
        mock_get_config.return_value = _make_runtime_config(privileged=True)
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["privileged"] is True

    @patch("agent.providers.docker_setup.get_container_config")
    def test_not_privileged_by_default(self, mock_get_config):
        """Privileged key is absent when not required."""
        mock_get_config.return_value = _make_runtime_config(privileged=False)
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert "privileged" not in cfg

    @patch("agent.providers.docker_setup.get_container_config")
    def test_sysctls_set(self, mock_get_config):
        """Sysctls from runtime config are passed through."""
        mock_get_config.return_value = _make_runtime_config(
            sysctls={"net.ipv4.ip_forward": "1"},
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["sysctls"] == {"net.ipv4.ip_forward": "1"}

    @patch("agent.providers.docker_setup.get_container_config")
    def test_ceos_entrypoint_with_if_wait(self, mock_get_config):
        """cEOS nodes get if-wait.sh entrypoint wrapper and CLAB_INTFS env."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("sw1", kind="ceos")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=4, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["entrypoint"] == ["/bin/bash", "-c"]
        assert cfg["command"] == ["/mnt/flash/if-wait.sh ; exec /sbin/init"]
        assert cfg["environment"]["CLAB_INTFS"] == "4"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_ceos_zero_interfaces_no_if_wait(self, mock_get_config):
        """cEOS with 0 interfaces does NOT get if-wait wrapper."""
        mock_get_config.return_value = _make_runtime_config(entrypoint="/sbin/init")
        node = _make_node("sw1", kind="ceos")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=0, provider_name="docker",
            container_name_func=_name_func,
        )
        # Should fall through to the entrypoint from runtime config
        assert cfg["entrypoint"] == ["/sbin/init"]
        assert "CLAB_INTFS" not in cfg["environment"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_entrypoint_string_wrapped_in_list(self, mock_get_config):
        """String entrypoint is wrapped in a list."""
        mock_get_config.return_value = _make_runtime_config(entrypoint="/sbin/init")
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["entrypoint"] == ["/sbin/init"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_entrypoint_list_passed_through(self, mock_get_config):
        """List entrypoint is passed through unchanged."""
        mock_get_config.return_value = _make_runtime_config(
            entrypoint=["/bin/bash", "-c", "echo hi"],
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["entrypoint"] == ["/bin/bash", "-c", "echo hi"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_cmd_used_when_no_entrypoint(self, mock_get_config):
        """Command from runtime config is used when no entrypoint override."""
        mock_get_config.return_value = _make_runtime_config(
            entrypoint=None, cmd=["bash"],
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["command"] == ["bash"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_sleep_infinity_fallback(self, mock_get_config):
        """Sleep infinity is used as fallback when no entrypoint or cmd."""
        mock_get_config.return_value = _make_runtime_config(
            entrypoint=None, cmd=None,
        )
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["command"] == ["sleep", "infinity"]

    @patch("agent.providers.docker_setup.get_container_config")
    def test_cpu_limit_applied(self, mock_get_config):
        """CPU limit is converted to nano_cpus."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux", cpu=2, cpu_limit=50)

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        # 2 CPUs * 50% = 1.0 CPU = 1_000_000_000 nano_cpus
        assert cfg["nano_cpus"] == 1_000_000_000

    @patch("agent.providers.docker_setup.get_container_config")
    def test_cpu_limit_clamped_to_100(self, mock_get_config):
        """CPU limit is clamped to max 100%."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux", cpu=1, cpu_limit=200)

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        # Clamped to 100%: 1 CPU * 100% = 1_000_000_000
        assert cfg["nano_cpus"] == 1_000_000_000

    @patch("agent.providers.docker_setup.get_container_config")
    def test_cpu_limit_clamped_to_1(self, mock_get_config):
        """CPU limit is clamped to min 1%."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux", cpu=1, cpu_limit=-5)

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        # Clamped to 1%: 1 CPU * 1% = 10_000_000
        assert cfg["nano_cpus"] == 10_000_000

    @patch("agent.providers.docker_setup.get_container_config")
    def test_no_cpu_limit_no_nano_cpus(self, mock_get_config):
        """No nano_cpus key when cpu_limit is None."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert "nano_cpus" not in cfg

    @patch("agent.providers.docker_setup.get_container_config")
    def test_detach_tty_stdin(self, mock_get_config):
        """Container is always detached with tty and stdin_open."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["detach"] is True
        assert cfg["tty"] is True
        assert cfg["stdin_open"] is True

    @patch("agent.providers.docker_setup.get_container_config")
    def test_cgroupns_host(self, mock_get_config):
        """cgroupns is always set to 'host'."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["cgroupns"] == "host"

    @patch("agent.providers.docker_setup.get_container_config")
    def test_restart_policy_no(self, mock_get_config):
        """Restart policy is always 'no'."""
        mock_get_config.return_value = _make_runtime_config()
        node = _make_node("r1", kind="linux")

        cfg = create_container_config(
            node=node, lab_id="lab1", workspace=Path("/ws"),
            interface_count=2, provider_name="docker",
            container_name_func=_name_func,
        )
        assert cfg["restart_policy"] == {"Name": "no"}


# ===========================================================================
# calculate_required_interfaces
# ===========================================================================

class TestCalculateRequiredInterfaces:
    """Tests for topology-wide interface count calculation."""

    def test_empty_topology(self):
        """Empty topology returns minimum buffer of 4."""
        topology = _make_topology()
        assert calculate_required_interfaces(topology) == 4

    def test_from_node_interface_count(self):
        """Uses node interface_count when set."""
        node = _make_node("r1", kind="linux", interface_count=10)
        topology = _make_topology(nodes={"r1": node})

        result = calculate_required_interfaces(topology)
        assert result == 14  # 10 + 4 buffer

    def test_from_link_endpoints(self):
        """Parses interface indices from link endpoints."""
        links = [
            TopologyLink(endpoints=["r1:eth5", "r2:eth3"]),
            TopologyLink(endpoints=["r1:eth8", "r3:eth1"]),
        ]
        topology = _make_topology(links=links)

        result = calculate_required_interfaces(topology)
        assert result == 12  # max(8) + 4 buffer

    def test_node_count_higher_than_links(self):
        """Node interface_count takes precedence when higher than link indices."""
        node = _make_node("r1", kind="linux", interface_count=20)
        links = [TopologyLink(endpoints=["r1:eth5", "r2:eth3"])]
        topology = _make_topology(nodes={"r1": node}, links=links)

        result = calculate_required_interfaces(topology)
        assert result == 24  # 20 + 4 buffer

    def test_non_numeric_interface_suffix(self):
        """Endpoints without numeric suffix do not affect the count."""
        links = [TopologyLink(endpoints=["r1:mgmt", "r2:mgmt"])]
        topology = _make_topology(links=links)

        result = calculate_required_interfaces(topology)
        assert result == 4  # minimum buffer only

    def test_endpoint_without_colon(self):
        """Endpoints without colon separator are skipped."""
        links = [TopologyLink(endpoints=["r1_eth1", "r2_eth2"])]
        topology = _make_topology(links=links)

        result = calculate_required_interfaces(topology)
        assert result == 4


# ===========================================================================
# count_node_interfaces
# ===========================================================================

class TestCountNodeInterfaces:
    """Tests for per-node interface counting."""

    def test_node_with_interface_count(self):
        """Returns node.interface_count when explicitly set."""
        node = _make_node("r1", kind="linux", interface_count=6)
        topology = _make_topology(nodes={"r1": node})

        assert count_node_interfaces("r1", topology) == 6

    def test_node_without_interface_count(self):
        """Counts from link endpoints when interface_count not set."""
        node = _make_node("r1", kind="linux")
        links = [
            TopologyLink(endpoints=["r1:eth3", "r2:eth1"]),
            TopologyLink(endpoints=["r1:eth7", "r3:eth2"]),
        ]
        topology = _make_topology(nodes={"r1": node}, links=links)

        assert count_node_interfaces("r1", topology) == 7

    def test_node_not_in_any_links(self):
        """Returns 0 when the node has no links."""
        node = _make_node("r1", kind="linux")
        topology = _make_topology(nodes={"r1": node})

        assert count_node_interfaces("r1", topology) == 0

    def test_unknown_node(self):
        """Returns 0 for a node not in the topology."""
        topology = _make_topology()
        assert count_node_interfaces("unknown", topology) == 0

    def test_only_counts_target_node(self):
        """Only counts interfaces belonging to the specified node."""
        nodes = {
            "r1": _make_node("r1", kind="linux"),
            "r2": _make_node("r2", kind="linux"),
        }
        links = [
            TopologyLink(endpoints=["r1:eth2", "r2:eth10"]),
        ]
        topology = _make_topology(nodes=nodes, links=links)

        assert count_node_interfaces("r1", topology) == 2
        assert count_node_interfaces("r2", topology) == 10
