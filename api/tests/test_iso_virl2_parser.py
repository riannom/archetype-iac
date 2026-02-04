"""Tests for VIRL2/CML2 ISO parser."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.iso.extractor import ISOExtractor
from app.iso.models import ISOFormat, ISOManifest, ParsedImage, ParsedNodeDefinition
from app.iso.parser import ParserRegistry
from app.iso.virl2_parser import VIRL2Parser


class TestVIRL2ParserRegistration:
    """Tests for VIRL2Parser registration."""

    def test_parser_is_registered(self):
        """Test that VIRL2Parser is automatically registered."""
        # The parser should be registered via @ParserRegistry.register decorator
        formats = ParserRegistry.list_formats()
        assert ISOFormat.VIRL2 in formats


class TestVIRL2ParserFormat:
    """Tests for VIRL2Parser format property."""

    def test_format_is_virl2(self):
        """Test that parser returns VIRL2 format."""
        parser = VIRL2Parser()
        assert parser.format == ISOFormat.VIRL2


class TestVIRL2ParserCanParse:
    """Tests for VIRL2Parser.can_parse method."""

    def test_can_parse_virl2_iso(self):
        """Test can_parse returns True for VIRL2 format ISOs."""
        parser = VIRL2Parser()
        file_list = [
            "node-definitions/ftdv.yaml",
            "node-definitions/iosv.yaml",
            "virl-base-images/ftdv/ftdv.yaml",
            "virl-base-images/ftdv/ftdv.qcow2",
        ]

        result = parser.can_parse(Path("/test.iso"), file_list)

        assert result is True

    def test_can_parse_missing_node_definitions(self):
        """Test can_parse returns False when node-definitions is missing."""
        parser = VIRL2Parser()
        file_list = [
            "virl-base-images/ftdv/ftdv.yaml",
            "virl-base-images/ftdv/ftdv.qcow2",
        ]

        result = parser.can_parse(Path("/test.iso"), file_list)

        assert result is False

    def test_can_parse_missing_base_images(self):
        """Test can_parse returns False when virl-base-images is missing."""
        parser = VIRL2Parser()
        file_list = [
            "node-definitions/ftdv.yaml",
            "node-definitions/iosv.yaml",
        ]

        result = parser.can_parse(Path("/test.iso"), file_list)

        assert result is False

    def test_can_parse_empty_file_list(self):
        """Test can_parse returns False for empty file list."""
        parser = VIRL2Parser()

        result = parser.can_parse(Path("/test.iso"), [])

        assert result is False

    def test_can_parse_unrelated_files(self):
        """Test can_parse returns False for non-VIRL2 ISOs."""
        parser = VIRL2Parser()
        file_list = [
            "random/file.txt",
            "another/directory/image.qcow2",
        ]

        result = parser.can_parse(Path("/test.iso"), file_list)

        assert result is False


class TestVIRL2ParserParse:
    """Tests for VIRL2Parser.parse method."""

    @pytest.mark.asyncio
    async def test_parse_empty_iso(self, tmp_path):
        """Test parsing an ISO with no node definitions or images."""
        parser = VIRL2Parser()
        iso_path = tmp_path / "test.iso"
        iso_path.write_bytes(b"fake iso")

        extractor = MagicMock(spec=ISOExtractor)
        extractor.get_file_names = AsyncMock(return_value=[])

        manifest = await parser.parse(iso_path, extractor)

        assert manifest.iso_path == str(iso_path)
        assert manifest.format == ISOFormat.VIRL2
        assert manifest.node_definitions == []
        assert manifest.images == []

    @pytest.mark.asyncio
    async def test_parse_with_node_definitions(self, tmp_path):
        """Test parsing ISO with node definitions."""
        parser = VIRL2Parser()
        iso_path = tmp_path / "test.iso"
        iso_path.write_bytes(b"fake iso")

        ftdv_yaml = """
id: ftdv
ui:
  label: FTDv
  description: Firepower Threat Defense Virtual
  group: Cisco
  icon: firewall
general:
  nature: firewall
device:
  interfaces:
    physical:
      - GigabitEthernet0/0
      - GigabitEthernet0/1
    default_count: 4
    has_loopback_zero: false
sim:
  linux_native:
    ram: 8192
    cpus: 4
    libvirt_domain_driver: kvm
    disk_driver: virtio
    nic_driver: e1000
boot:
  timeout: 600
  completed:
    - "FTD startup complete"
"""

        extractor = MagicMock(spec=ISOExtractor)
        extractor.get_file_names = AsyncMock(return_value=[
            "node-definitions/ftdv.yaml",
        ])
        extractor.read_text_file = AsyncMock(return_value=ftdv_yaml)

        manifest = await parser.parse(iso_path, extractor)

        assert len(manifest.node_definitions) == 1
        node_def = manifest.node_definitions[0]
        assert node_def.id == "ftdv"
        assert node_def.label == "FTDv"
        assert node_def.description == "Firepower Threat Defense Virtual"
        assert node_def.nature == "firewall"
        assert node_def.vendor == "Cisco"
        assert node_def.ram_mb == 8192
        assert node_def.cpus == 4
        assert len(node_def.interfaces) == 2
        assert node_def.boot_timeout == 600
        assert "FTD startup complete" in node_def.boot_completed_patterns

    @pytest.mark.asyncio
    async def test_parse_with_images(self, tmp_path):
        """Test parsing ISO with image definitions."""
        parser = VIRL2Parser()
        iso_path = tmp_path / "test.iso"
        iso_path.write_bytes(b"fake iso")

        image_yaml = """
id: ftdv-7.4.0
node_definition_id: ftdv
label: FTDv 7.4.0
description: Firepower Threat Defense 7.4.0
disk_image: ftdv-7.4.0.qcow2
"""

        extractor = MagicMock(spec=ISOExtractor)
        extractor.get_file_names = AsyncMock(return_value=[
            "virl-base-images/ftdv/ftdv-7.4.0.yaml",
            "virl-base-images/ftdv/ftdv-7.4.0.qcow2",
        ])
        extractor.read_text_file = AsyncMock(return_value=image_yaml)

        manifest = await parser.parse(iso_path, extractor)

        assert len(manifest.images) == 1
        image = manifest.images[0]
        assert image.id == "ftdv-7.4.0"
        assert image.node_definition_id == "ftdv"
        assert image.label == "FTDv 7.4.0"
        assert image.disk_image_filename == "ftdv-7.4.0.qcow2"
        assert image.image_type == "qcow2"

    @pytest.mark.asyncio
    async def test_parse_handles_yaml_errors(self, tmp_path):
        """Test parsing handles YAML errors gracefully.

        Note: The parser logs a warning for invalid YAML but doesn't add to
        parse_errors because _parse_node_definition returns None silently.
        The parse_errors list is only populated when an exception is raised
        during file reading or when _parse_node_definition raises.
        """
        parser = VIRL2Parser()
        iso_path = tmp_path / "test.iso"
        iso_path.write_bytes(b"fake iso")

        invalid_yaml = "id: test\n  invalid: yaml: content:"

        extractor = MagicMock(spec=ISOExtractor)
        extractor.get_file_names = AsyncMock(return_value=[
            "node-definitions/invalid.yaml",
        ])
        extractor.read_text_file = AsyncMock(return_value=invalid_yaml)

        manifest = await parser.parse(iso_path, extractor)

        # Should not raise, and node_definitions should be empty (invalid YAML skipped)
        assert len(manifest.node_definitions) == 0

    @pytest.mark.asyncio
    async def test_parse_handles_read_errors(self, tmp_path):
        """Test parsing handles file read errors gracefully."""
        parser = VIRL2Parser()
        iso_path = tmp_path / "test.iso"
        iso_path.write_bytes(b"fake iso")

        extractor = MagicMock(spec=ISOExtractor)
        extractor.get_file_names = AsyncMock(return_value=[
            "node-definitions/error.yaml",
        ])
        extractor.read_text_file = AsyncMock(side_effect=RuntimeError("Read failed"))

        manifest = await parser.parse(iso_path, extractor)

        # Should not raise, but should record error
        assert len(manifest.parse_errors) >= 1
        assert "error.yaml" in manifest.parse_errors[0]


class TestVIRL2ParserParseNodeDefinition:
    """Tests for VIRL2Parser._parse_node_definition method."""

    def test_parse_minimal_node_definition(self):
        """Test parsing a minimal node definition."""
        parser = VIRL2Parser()

        yaml_content = """
id: test-node
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.id == "test-node"
        # Defaults should be applied
        assert result.nature == "router"
        assert result.ram_mb == 2048
        assert result.cpus == 1

    def test_parse_full_node_definition(self):
        """Test parsing a full node definition."""
        parser = VIRL2Parser()

        yaml_content = """
id: cat-sdwan-edge
ui:
  label: Catalyst SD-WAN Edge
  description: Cisco Catalyst SD-WAN Edge Router
  group: Cisco
  icon: router
general:
  nature: router
  description: Catalyst Edge
device:
  interfaces:
    physical:
      - GigabitEthernet1
      - GigabitEthernet2
      - GigabitEthernet3
    default_count: 8
    has_loopback_zero: true
sim:
  linux_native:
    ram: 4096
    cpus: 2
    cpu_limit: 80
    libvirt_domain_driver: kvm
    disk_driver: virtio
    nic_driver: virtio
boot:
  timeout: 900
  completed:
    - "System Ready"
    - "SDWAN daemon started"
configuration:
  generator:
    driver: sdwan
  provisioning:
    media_type: iso
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.id == "cat-sdwan-edge"
        assert result.label == "Catalyst SD-WAN Edge"
        assert result.description == "Cisco Catalyst SD-WAN Edge Router"
        assert result.nature == "router"
        assert result.vendor == "Cisco"
        assert result.ram_mb == 4096
        assert result.cpus == 2
        assert result.cpu_limit == 80
        assert len(result.interfaces) == 3
        assert result.interface_count_default == 8
        assert result.has_loopback is True
        assert result.boot_timeout == 900
        assert len(result.boot_completed_patterns) == 2
        assert result.provisioning_driver == "sdwan"
        assert result.provisioning_media_type == "iso"
        assert result.libvirt_driver == "kvm"
        assert result.disk_driver == "virtio"
        assert result.nic_driver == "virtio"

    def test_parse_node_definition_invalid_yaml(self):
        """Test parsing invalid YAML returns None."""
        parser = VIRL2Parser()

        yaml_content = "invalid: yaml: content:"

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is None

    def test_parse_node_definition_empty_data(self):
        """Test parsing empty YAML returns None."""
        parser = VIRL2Parser()

        yaml_content = ""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is None

    def test_parse_node_definition_missing_id(self):
        """Test parsing node definition without ID returns None."""
        parser = VIRL2Parser()

        yaml_content = """
ui:
  label: Test Node
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is None

    def test_parse_node_definition_interface_naming_gigabitethernet(self):
        """Test interface naming detection for GigabitEthernet."""
        parser = VIRL2Parser()

        yaml_content = """
id: test
device:
  interfaces:
    physical:
      - GigabitEthernet0/0
      - GigabitEthernet0/1
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.interface_naming_pattern == "GigabitEthernet"

    def test_parse_node_definition_interface_naming_ethernet(self):
        """Test interface naming detection for Ethernet."""
        parser = VIRL2Parser()

        yaml_content = """
id: test
device:
  interfaces:
    physical:
      - Ethernet1
      - Ethernet2
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.interface_naming_pattern == "Ethernet"

    def test_parse_node_definition_interface_naming_management(self):
        """Test interface naming detection for Management."""
        parser = VIRL2Parser()

        yaml_content = """
id: test
device:
  interfaces:
    physical:
      - Management0
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.interface_naming_pattern == "Management"

    def test_parse_node_definition_interface_naming_ge(self):
        """Test interface naming detection for ge-."""
        parser = VIRL2Parser()

        yaml_content = """
id: test
device:
  interfaces:
    physical:
      - ge-0/0/0
      - ge-0/0/1
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.interface_naming_pattern == "ge-"

    def test_parse_node_definition_interface_naming_default(self):
        """Test interface naming defaults to eth."""
        parser = VIRL2Parser()

        yaml_content = """
id: test
device:
  interfaces:
    physical: []
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.interface_naming_pattern == "eth"

    def test_parse_node_definition_raw_yaml_stored(self):
        """Test that raw YAML data is stored."""
        parser = VIRL2Parser()

        yaml_content = """
id: test-node
custom_field: custom_value
"""

        result = parser._parse_node_definition(yaml_content, "test.yaml")

        assert result is not None
        assert result.raw_yaml["id"] == "test-node"
        assert result.raw_yaml["custom_field"] == "custom_value"


class TestVIRL2ParserParseImage:
    """Tests for VIRL2Parser._parse_image method."""

    def test_parse_minimal_image(self):
        """Test parsing a minimal image definition."""
        parser = VIRL2Parser()

        yaml_content = """
id: test-image
node_definition_id: test-node
disk_image: test.qcow2
"""
        file_list = ["virl-base-images/test/test-image.yaml"]

        result = parser._parse_image(yaml_content, "virl-base-images/test/test-image.yaml", file_list)

        assert result is not None
        assert result.id == "test-image"
        assert result.node_definition_id == "test-node"
        assert result.disk_image_filename == "test.qcow2"
        assert result.image_type == "qcow2"

    def test_parse_full_image(self):
        """Test parsing a full image definition."""
        parser = VIRL2Parser()

        yaml_content = """
id: ftdv-7.4.0
node_definition_id: ftdv
label: FTDv 7.4.0
description: Firepower Threat Defense Virtual 7.4.0
disk_image: Cisco_Firepower_Threat_Defense_Virtual-7.4.0.qcow2
"""
        file_list = [
            "virl-base-images/ftdv/ftdv-7.4.0.yaml",
            "virl-base-images/ftdv/Cisco_Firepower_Threat_Defense_Virtual-7.4.0.qcow2",
        ]

        result = parser._parse_image(
            yaml_content,
            "virl-base-images/ftdv/ftdv-7.4.0.yaml",
            file_list,
        )

        assert result is not None
        assert result.id == "ftdv-7.4.0"
        assert result.node_definition_id == "ftdv"
        assert result.label == "FTDv 7.4.0"
        assert result.description == "Firepower Threat Defense Virtual 7.4.0"
        assert result.disk_image_path == "virl-base-images/ftdv/Cisco_Firepower_Threat_Defense_Virtual-7.4.0.qcow2"
        assert result.image_type == "qcow2"
        assert result.version == "7.4.0"

    def test_parse_image_tar_gz(self):
        """Test parsing a container image (tar.gz)."""
        parser = VIRL2Parser()

        yaml_content = """
id: alpine-latest
node_definition_id: alpine
disk_image: alpine.tar.gz
"""
        file_list = [
            "virl-base-images/alpine/alpine-latest.yaml",
            "virl-base-images/alpine/alpine.tar.gz",
        ]

        result = parser._parse_image(
            yaml_content,
            "virl-base-images/alpine/alpine-latest.yaml",
            file_list,
        )

        assert result is not None
        assert result.image_type == "docker"
        assert result.is_container is True

    def test_parse_image_iol_bin(self):
        """Test parsing an IOL/IOL-XE binary image (.bin)."""
        parser = VIRL2Parser()

        yaml_content = """
id: iol-xe-17.16
node_definition_id: iol-xe
disk_image: i86bi_linux_l2-adventerprisek9-ms.SSA.high_iron_20230523.bin
"""
        file_list = [
            "virl-base-images/iol-xe/iol-xe-17.16.yaml",
            "virl-base-images/iol-xe/i86bi_linux_l2-adventerprisek9-ms.SSA.high_iron_20230523.bin",
        ]

        result = parser._parse_image(
            yaml_content,
            "virl-base-images/iol-xe/iol-xe-17.16.yaml",
            file_list,
        )

        assert result is not None
        assert result.id == "iol-xe-17.16"
        assert result.node_definition_id == "iol-xe"
        assert result.disk_image_filename == "i86bi_linux_l2-adventerprisek9-ms.SSA.high_iron_20230523.bin"
        assert result.disk_image_path == "virl-base-images/iol-xe/i86bi_linux_l2-adventerprisek9-ms.SSA.high_iron_20230523.bin"
        assert result.image_type == "iol"

    def test_parse_image_invalid_yaml(self):
        """Test parsing invalid YAML returns None."""
        parser = VIRL2Parser()

        yaml_content = "invalid: yaml: content:"

        result = parser._parse_image(yaml_content, "test.yaml", [])

        assert result is None

    def test_parse_image_missing_required_fields(self):
        """Test parsing image missing required fields returns None."""
        parser = VIRL2Parser()

        # Missing node_definition_id
        yaml_content = """
id: test-image
disk_image: test.qcow2
"""

        result = parser._parse_image(yaml_content, "test.yaml", [])

        assert result is None

    def test_parse_image_missing_id(self):
        """Test parsing image missing id returns None."""
        parser = VIRL2Parser()

        yaml_content = """
node_definition_id: test-node
disk_image: test.qcow2
"""

        result = parser._parse_image(yaml_content, "test.yaml", [])

        assert result is None

    def test_parse_image_missing_disk_image(self):
        """Test parsing image missing disk_image returns None."""
        parser = VIRL2Parser()

        yaml_content = """
id: test-image
node_definition_id: test-node
"""

        result = parser._parse_image(yaml_content, "test.yaml", [])

        assert result is None

    def test_parse_image_finds_disk_by_directory(self):
        """Test that disk image is found by directory match."""
        parser = VIRL2Parser()

        yaml_content = """
id: cat-sdwan-edge-17.16
node_definition_id: cat-sdwan-edge
disk_image: c8000v-universalk9.17.16.01a.qcow2
"""
        file_list = [
            "virl-base-images/cat-sdwan-edge/cat-sdwan-edge-17.16.yaml",
            "virl-base-images/cat-sdwan-edge/c8000v-universalk9.17.16.01a.qcow2",
            "virl-base-images/other/other.qcow2",
        ]

        result = parser._parse_image(
            yaml_content,
            "virl-base-images/cat-sdwan-edge/cat-sdwan-edge-17.16.yaml",
            file_list,
        )

        assert result is not None
        assert result.disk_image_path == "virl-base-images/cat-sdwan-edge/c8000v-universalk9.17.16.01a.qcow2"

    def test_parse_image_finds_disk_by_exact_name(self):
        """Test that disk image is found by exact name match."""
        parser = VIRL2Parser()

        yaml_content = """
id: test-image
node_definition_id: test
disk_image: specific-image.qcow2
"""
        file_list = [
            "virl-base-images/test/test-image.yaml",
            "some/other/path/specific-image.qcow2",
        ]

        result = parser._parse_image(
            yaml_content,
            "virl-base-images/test/test-image.yaml",
            file_list,
        )

        assert result is not None
        assert result.disk_image_path == "some/other/path/specific-image.qcow2"

    def test_parse_image_raw_yaml_stored(self):
        """Test that raw YAML data is stored."""
        parser = VIRL2Parser()

        yaml_content = """
id: test-image
node_definition_id: test-node
disk_image: test.qcow2
custom_field: custom_value
"""

        result = parser._parse_image(yaml_content, "test.yaml", [])

        assert result is not None
        assert result.raw_yaml["custom_field"] == "custom_value"


class TestVIRL2ParserExtractVersion:
    """Tests for VIRL2Parser._extract_version method."""

    def test_extract_version_major_minor_patch(self):
        """Test extracting version with major.minor.patch format."""
        parser = VIRL2Parser()

        assert parser._extract_version("ftdv-7.4.0") == "7.4.0"
        # The regex matches version with optional letter suffix
        result = parser._extract_version("image-17.16.01a.qcow2")
        assert result is not None
        assert result.startswith("17.16.01")

    def test_extract_version_with_suffix(self):
        """Test extracting version with letter suffix."""
        parser = VIRL2Parser()

        assert parser._extract_version("c8000v-17.16.01a") == "17.16.01a"

    def test_extract_version_hyphen_separated(self):
        """Test extracting version with hyphen-separated format."""
        parser = VIRL2Parser()

        result = parser._extract_version("image-20-16-1.qcow2")
        assert result is not None
        # Should match either 20-16-1 or some numeric pattern
        assert "20" in result

    def test_extract_version_major_minor(self):
        """Test extracting version with major.minor format."""
        parser = VIRL2Parser()

        assert parser._extract_version("asav-9.18") == "9.18"

    def test_extract_version_no_version(self):
        """Test extracting version when none exists."""
        parser = VIRL2Parser()

        assert parser._extract_version("alpine") is None
        assert parser._extract_version("linux-server") is None

    def test_extract_version_complex_string(self):
        """Test extracting version from complex string."""
        parser = VIRL2Parser()

        result = parser._extract_version("Cisco_Firepower_Threat_Defense_Virtual-7.4.0.qcow2")
        assert result == "7.4.0"

    def test_extract_version_multiple_versions(self):
        """Test that first version is extracted."""
        parser = VIRL2Parser()

        # Should return first match
        result = parser._extract_version("image-7.4.0-patch-1.2.3")
        assert result == "7.4.0"


class TestVIRL2ParserIntegration:
    """Integration tests for VIRL2Parser."""

    @pytest.mark.asyncio
    async def test_parse_complete_iso(self, tmp_path):
        """Test parsing a complete VIRL2-style ISO."""
        parser = VIRL2Parser()
        iso_path = tmp_path / "cml-images.iso"
        iso_path.write_bytes(b"fake iso content")

        # Create mock node definition
        ftdv_node_yaml = """
id: ftdv
ui:
  label: FTDv
  description: Firepower Threat Defense Virtual
  group: Cisco
general:
  nature: firewall
device:
  interfaces:
    physical:
      - GigabitEthernet0/0
      - GigabitEthernet0/1
    default_count: 4
sim:
  linux_native:
    ram: 8192
    cpus: 4
boot:
  timeout: 600
  completed:
    - "FTD startup complete"
"""

        # Create mock image definition
        ftdv_image_yaml = """
id: ftdv-7.4.0
node_definition_id: ftdv
label: FTDv 7.4.0
disk_image: ftdv-7.4.0.qcow2
"""

        iosv_node_yaml = """
id: iosv
ui:
  label: IOSv
  description: Cisco IOSv Router
  group: Cisco
general:
  nature: router
device:
  interfaces:
    physical:
      - GigabitEthernet0/0
      - GigabitEthernet0/1
      - GigabitEthernet0/2
      - GigabitEthernet0/3
    default_count: 4
sim:
  linux_native:
    ram: 512
    cpus: 1
"""

        iosv_image_yaml = """
id: iosv-15.9
node_definition_id: iosv
label: IOSv 15.9
disk_image: iosv-15.9.qcow2
"""

        file_list = [
            "node-definitions/ftdv.yaml",
            "node-definitions/iosv.yaml",
            "virl-base-images/ftdv/ftdv-7.4.0.yaml",
            "virl-base-images/ftdv/ftdv-7.4.0.qcow2",
            "virl-base-images/iosv/iosv-15.9.yaml",
            "virl-base-images/iosv/iosv-15.9.qcow2",
        ]

        async def mock_read_text_file(path):
            if path == "node-definitions/ftdv.yaml":
                return ftdv_node_yaml
            elif path == "node-definitions/iosv.yaml":
                return iosv_node_yaml
            elif path == "virl-base-images/ftdv/ftdv-7.4.0.yaml":
                return ftdv_image_yaml
            elif path == "virl-base-images/iosv/iosv-15.9.yaml":
                return iosv_image_yaml
            raise RuntimeError(f"Unknown file: {path}")

        extractor = MagicMock(spec=ISOExtractor)
        extractor.get_file_names = AsyncMock(return_value=file_list)
        extractor.read_text_file = mock_read_text_file

        manifest = await parser.parse(iso_path, extractor)

        # Verify manifest
        assert manifest.format == ISOFormat.VIRL2
        assert len(manifest.parse_errors) == 0

        # Verify node definitions
        assert len(manifest.node_definitions) == 2
        ftdv_def = manifest.get_node_definition("ftdv")
        assert ftdv_def is not None
        assert ftdv_def.nature == "firewall"
        assert ftdv_def.ram_mb == 8192

        iosv_def = manifest.get_node_definition("iosv")
        assert iosv_def is not None
        assert iosv_def.nature == "router"
        assert iosv_def.ram_mb == 512

        # Verify images
        assert len(manifest.images) == 2

        ftdv_images = manifest.get_images_for_node("ftdv")
        assert len(ftdv_images) == 1
        assert ftdv_images[0].version == "7.4.0"

        iosv_images = manifest.get_images_for_node("iosv")
        assert len(iosv_images) == 1
        assert iosv_images[0].version == "15.9"
