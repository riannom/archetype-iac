"""Tests for ISO device type mapping."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from app.iso.mapper import (
    NATURE_TO_CATEGORY,
    NATURE_TO_ICON,
    VIRL2_TO_VENDOR_MAP,
    _generate_tags,
    create_device_config_from_node_def,
    get_image_device_mapping,
    map_node_definition_to_device,
)
from app.iso.models import ParsedImage, ParsedNodeDefinition


class TestVIRL2ToVendorMap:
    """Tests for VIRL2_TO_VENDOR_MAP constant."""

    def test_sdwan_mappings(self):
        """Test SD-WAN device mappings."""
        assert VIRL2_TO_VENDOR_MAP["cat-sdwan-edge"] == "c8000v"
        assert VIRL2_TO_VENDOR_MAP["cat-sdwan-controller"] == "cat-sdwan-controller"
        assert VIRL2_TO_VENDOR_MAP["cat-sdwan-manager"] == "cat-sdwan-manager"

    def test_security_mappings(self):
        """Test security device mappings."""
        assert VIRL2_TO_VENDOR_MAP["ftdv"] == "ftdv"
        assert VIRL2_TO_VENDOR_MAP["fmcv"] == "fmcv"
        assert VIRL2_TO_VENDOR_MAP["asav"] == "cisco_asav"

    def test_router_mappings(self):
        """Test router device mappings."""
        assert VIRL2_TO_VENDOR_MAP["iosv"] == "cisco_iosv"
        assert VIRL2_TO_VENDOR_MAP["iosvl2"] == "iosvl2"
        assert VIRL2_TO_VENDOR_MAP["cat8000v"] == "c8000v"
        assert VIRL2_TO_VENDOR_MAP["csr1000v"] == "cisco_csr1000v"
        assert VIRL2_TO_VENDOR_MAP["iosxrv9000"] == "cisco_iosxr"
        assert VIRL2_TO_VENDOR_MAP["nxos"] == "cisco_n9kv"
        assert VIRL2_TO_VENDOR_MAP["nxosv9000"] == "cisco_n9kv"

    def test_iol_mappings(self):
        """Test IOL device mappings."""
        assert VIRL2_TO_VENDOR_MAP["iol-xe"] == "iol-xe"
        assert VIRL2_TO_VENDOR_MAP["iol-xe-serial-4eth"] == "iol-xe"
        assert VIRL2_TO_VENDOR_MAP["iol"] == "iol-xe"
        assert VIRL2_TO_VENDOR_MAP["iol-l2"] == "iol-l2"
        assert VIRL2_TO_VENDOR_MAP["ioll2-xe"] == "iol-l2"

    def test_linux_mappings(self):
        """Test Linux/container device mappings."""
        assert VIRL2_TO_VENDOR_MAP["alpine"] == "alpine"
        assert VIRL2_TO_VENDOR_MAP["ubuntu"] == "linux"
        assert VIRL2_TO_VENDOR_MAP["server"] == "linux"
        assert VIRL2_TO_VENDOR_MAP["tcl"] == "tcl"


class TestNatureToIcon:
    """Tests for NATURE_TO_ICON constant."""

    def test_router_icon(self):
        """Test router icon mapping."""
        assert NATURE_TO_ICON["router"] == "fa-arrows-to-dot"

    def test_switch_icon(self):
        """Test switch icon mapping."""
        assert NATURE_TO_ICON["switch"] == "fa-arrows-left-right-to-line"

    def test_firewall_icon(self):
        """Test firewall icon mapping."""
        assert NATURE_TO_ICON["firewall"] == "fa-shield-halved"

    def test_server_icon(self):
        """Test server icon mapping."""
        assert NATURE_TO_ICON["server"] == "fa-server"

    def test_wireless_icon(self):
        """Test wireless icon mapping."""
        assert NATURE_TO_ICON["wireless"] == "fa-wifi"

    def test_container_icon(self):
        """Test container icon mapping."""
        assert NATURE_TO_ICON["container"] == "fa-box"


class TestNatureToCategory:
    """Tests for NATURE_TO_CATEGORY constant."""

    def test_router_category(self):
        """Test router category mapping."""
        assert NATURE_TO_CATEGORY["router"] == ("Network", "Routers")

    def test_switch_category(self):
        """Test switch category mapping."""
        assert NATURE_TO_CATEGORY["switch"] == ("Network", "Switches")

    def test_firewall_category(self):
        """Test firewall category mapping."""
        assert NATURE_TO_CATEGORY["firewall"] == ("Security", None)

    def test_server_category(self):
        """Test server category mapping."""
        assert NATURE_TO_CATEGORY["server"] == ("Compute", None)

    def test_wireless_category(self):
        """Test wireless category mapping."""
        assert NATURE_TO_CATEGORY["wireless"] == ("Network", "Wireless")

    def test_container_category(self):
        """Test container category mapping."""
        assert NATURE_TO_CATEGORY["container"] == ("Compute", None)


class TestMapNodeDefinitionToDevice:
    """Tests for map_node_definition_to_device function."""

    def test_direct_mapping_found(self):
        """Test direct mapping when node ID is in VIRL2_TO_VENDOR_MAP."""
        node_def = ParsedNodeDefinition(id="ftdv", label="FTDv")
        result = map_node_definition_to_device(node_def)
        assert result == "ftdv"

    def test_normalized_mapping_with_underscore(self):
        """Test mapping with underscore to hyphen normalization."""
        node_def = ParsedNodeDefinition(id="cat_sdwan_edge", label="Catalyst SD-WAN Edge")
        result = map_node_definition_to_device(node_def)
        assert result == "c8000v"

    def test_normalized_mapping_uppercase(self):
        """Test mapping with uppercase to lowercase normalization."""
        node_def = ParsedNodeDefinition(id="FTDV", label="FTDv")
        result = map_node_definition_to_device(node_def)
        assert result == "ftdv"

    def test_no_mapping_found(self):
        """Test when no mapping exists."""
        node_def = ParsedNodeDefinition(id="unknown-device", label="Unknown")

        # Mock vendor registry to not have the device
        with patch.dict("sys.modules", {"agent.vendors": MagicMock()}):
            with patch("app.iso.mapper.VENDOR_CONFIGS", {}, create=True):
                result = map_node_definition_to_device(node_def)

        # Without vendor registry import working, should return None
        assert result is None

    def test_vendor_registry_direct_match(self):
        """Test mapping via vendor registry direct match.

        Note: Testing actual vendor registry integration requires the agent module
        to be available. This test verifies the function handles the ImportError
        gracefully when the agent module is not available.
        """
        node_def = ParsedNodeDefinition(id="unknown_custom_device", label="Custom Device")

        # The function should handle ImportError gracefully
        # and return None when no mapping is found
        result = map_node_definition_to_device(node_def)

        # Will return None since this is not in VIRL2_TO_VENDOR_MAP
        # and agent module import may fail
        assert result is None

    def test_import_error_handled(self):
        """Test that ImportError from vendor registry is handled."""
        node_def = ParsedNodeDefinition(id="unknown-device", label="Unknown")

        # The function should not raise even if imports fail
        result = map_node_definition_to_device(node_def)
        assert result is None


class TestCreateDeviceConfigFromNodeDef:
    """Tests for create_device_config_from_node_def function."""

    def test_basic_config(self):
        """Test creating a basic device config."""
        node_def = ParsedNodeDefinition(
            id="test-device",
            label="Test Device",
            nature="router",
            vendor="TestVendor",
        )

        config = create_device_config_from_node_def(node_def)

        assert config["id"] == "test-device"
        assert config["name"] == "Test Device"
        assert config["type"] == "router"
        assert config["vendor"] == "TestVendor"
        assert config["category"] == "Network"
        assert config["icon"] == "fa-arrows-to-dot"
        assert config["isActive"] is True
        assert config["importedFromISO"] is True
        assert config["isoNodeDefinitionId"] == "test-device"

    def test_resource_properties(self):
        """Test resource properties in config."""
        node_def = ParsedNodeDefinition(
            id="ftdv",
            label="FTDv",
            ram_mb=8192,
            cpus=4,
            interfaces=["GigabitEthernet0/0", "GigabitEthernet0/1"],
            interface_count_default=8,
            interface_naming_pattern="GigabitEthernet",
        )

        config = create_device_config_from_node_def(node_def)

        assert config["memory"] == 8192
        assert config["cpu"] == 4
        assert config["maxPorts"] == 2  # len(interfaces)
        assert config["portNaming"] == "GigabitEthernet"

    def test_resource_properties_cat9k_enforces_minimums(self):
        """Test Cat9k variants are normalized to safe hardware minimums."""
        node_def = ParsedNodeDefinition(
            id="cat9000v-uadp",
            label="Cat9000v UADP",
            ram_mb=12288,
            cpus=2,
        )

        config = create_device_config_from_node_def(node_def)

        assert config["memory"] == 18432
        assert config["cpu"] == 4

    def test_resource_properties_no_interfaces(self):
        """Test resource properties when no interfaces defined."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=[],
            interface_count_default=8,
        )

        config = create_device_config_from_node_def(node_def)

        # Should use interface_count_default when interfaces list is empty
        assert config["maxPorts"] == 8

    def test_boot_properties_with_patterns(self):
        """Test boot properties with completion patterns."""
        node_def = ParsedNodeDefinition(
            id="ftdv",
            label="FTDv",
            boot_timeout=600,
            boot_completed_patterns=["System startup complete", "FTD ready"],
        )

        config = create_device_config_from_node_def(node_def)

        assert config["readinessProbe"] == "log_pattern"
        # Patterns are escaped and joined with |
        assert "System" in config["readinessPattern"]
        assert "startup" in config["readinessPattern"]
        assert "complete" in config["readinessPattern"]
        assert "FTD" in config["readinessPattern"]
        assert "ready" in config["readinessPattern"]
        assert config["readinessTimeout"] == 600

    def test_boot_properties_no_patterns(self):
        """Test boot properties without completion patterns."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            boot_completed_patterns=[],
        )

        config = create_device_config_from_node_def(node_def)

        assert config["readinessProbe"] == "none"
        assert config["readinessPattern"] is None

    def test_vm_specific_properties(self):
        """Test VM-specific properties."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            libvirt_driver="kvm",
            disk_driver="virtio",
            nic_driver="e1000",
            machine_type="pc-q35-6.2",
            efi_boot=True,
            efi_vars="stateless",
        )

        config = create_device_config_from_node_def(node_def)

        assert config["libvirtDriver"] == "kvm"
        assert config["diskDriver"] == "virtio"
        assert config["nicDriver"] == "e1000"
        assert config["machineType"] == "pc-q35-6.2"
        assert config["efiBoot"] is True
        assert config["efiVars"] == "stateless"

    def test_firewall_nature_category(self):
        """Test category mapping for firewall nature."""
        node_def = ParsedNodeDefinition(
            id="ftdv",
            label="FTDv",
            nature="firewall",
        )

        config = create_device_config_from_node_def(node_def)

        assert config["category"] == "Security"
        assert config["icon"] == "fa-shield-halved"

    def test_switch_nature_category(self):
        """Test category mapping for switch nature."""
        node_def = ParsedNodeDefinition(
            id="iosvl2",
            label="IOSvL2",
            nature="switch",
        )

        config = create_device_config_from_node_def(node_def)

        assert config["category"] == "Network"
        assert config["icon"] == "fa-arrows-left-right-to-line"

    def test_unknown_nature_defaults(self):
        """Test defaults for unknown nature."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            nature="unknown_nature",
        )

        config = create_device_config_from_node_def(node_def)

        # Should use defaults for unknown nature
        assert config["category"] == "Compute"
        assert config["icon"] == "fa-box"

    def test_vendor_defaults_to_cisco(self):
        """Test vendor defaults to Cisco when not specified."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            vendor="",
        )

        config = create_device_config_from_node_def(node_def)

        assert config["vendor"] == "Cisco"


class TestGenerateTags:
    """Tests for _generate_tags function."""

    def test_nature_tag(self):
        """Test nature is added as tag."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            nature="router",
        )

        tags = _generate_tags(node_def)

        assert "router" in tags

    def test_vendor_tag(self):
        """Test vendor is added as lowercase tag."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            vendor="Cisco",
        )

        tags = _generate_tags(node_def)

        assert "cisco" in tags

    def test_no_vendor_tag_when_empty(self):
        """Test no vendor tag when vendor is empty."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            vendor="",
        )

        tags = _generate_tags(node_def)

        # Should not have empty string as tag
        assert "" not in tags

    def test_sdwan_keyword_in_description(self):
        """Test SD-WAN keyword extracted from description."""
        node_def = ParsedNodeDefinition(
            id="c8000v",
            label="C8000v",
            description="Cisco Catalyst SD-WAN Edge Router",
        )

        tags = _generate_tags(node_def)

        assert "sdwan" in tags

    def test_firewall_keyword_in_description(self):
        """Test firewall keyword extracted from description."""
        node_def = ParsedNodeDefinition(
            id="ftdv",
            label="FTDv",
            description="Firepower Threat Defense Virtual Firewall",
        )

        tags = _generate_tags(node_def)

        assert "firewall" in tags

    def test_keyword_in_id(self):
        """Test keywords extracted from node ID."""
        node_def = ParsedNodeDefinition(
            id="cat-sdwan-controller",
            label="SD-WAN Controller",
            description="Controller for SD-WAN",
        )

        tags = _generate_tags(node_def)

        assert "sdwan" in tags
        assert "controller" in tags

    def test_multiple_keywords(self):
        """Test multiple keywords extracted."""
        node_def = ParsedNodeDefinition(
            id="cat-sdwan-edge",
            label="Catalyst SD-WAN Edge",
            description="Catalyst SD-WAN Edge with VPN and Security features",
            vendor="Cisco",
        )

        tags = _generate_tags(node_def)

        # Should have unique tags
        assert len(tags) == len(set(tags))
        assert "cisco" in tags
        assert "sdwan" in tags
        assert "vpn" in tags
        assert "security" in tags
        assert "edge" in tags

    def test_hyphen_removed_from_keywords(self):
        """Test hyphens are removed from keywords."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            description="SD-WAN device",
        )

        tags = _generate_tags(node_def)

        # 'sd-wan' should become 'sdwan'
        assert "sdwan" in tags
        assert "sd-wan" not in tags


class TestGetImageDeviceMapping:
    """Tests for get_image_device_mapping function."""

    def test_mapping_with_existing_device(self):
        """Test mapping when device exists in vendor registry."""
        image = ParsedImage(
            id="ftdv-7.4",
            node_definition_id="ftdv",
            disk_image_filename="ftdv.qcow2",
        )
        node_def = ParsedNodeDefinition(id="ftdv", label="FTDv")

        device_id, new_config = get_image_device_mapping(image, [node_def])

        # ftdv maps to 'ftdv' in VIRL2_TO_VENDOR_MAP
        assert device_id == "ftdv"
        assert new_config is None  # No new config needed

    def test_mapping_with_new_device(self):
        """Test mapping when device doesn't exist."""
        image = ParsedImage(
            id="custom-device-1.0",
            node_definition_id="custom-device",
            disk_image_filename="custom.qcow2",
        )
        node_def = ParsedNodeDefinition(
            id="custom-device",
            label="Custom Device",
            nature="router",
            vendor="CustomVendor",
        )

        device_id, new_config = get_image_device_mapping(image, [node_def])

        assert device_id == "custom-device"
        assert new_config is not None
        assert new_config["id"] == "custom-device"
        assert new_config["name"] == "Custom Device"
        assert new_config["vendor"] == "CustomVendor"

    def test_mapping_no_node_definition(self):
        """Test mapping when no node definition found."""
        image = ParsedImage(
            id="orphan-image",
            node_definition_id="missing-node-def",
            disk_image_filename="orphan.qcow2",
        )

        device_id, new_config = get_image_device_mapping(image, [])

        # Should return node_definition_id and no config
        assert device_id == "missing-node-def"
        assert new_config is None

    def test_mapping_node_def_not_in_list(self):
        """Test mapping when node definition ID doesn't match any in list."""
        image = ParsedImage(
            id="test-image",
            node_definition_id="target-node",
            disk_image_filename="test.qcow2",
        )
        other_node_def = ParsedNodeDefinition(id="other-node", label="Other Node")

        device_id, new_config = get_image_device_mapping(image, [other_node_def])

        assert device_id == "target-node"
        assert new_config is None

    def test_mapping_sdwan_edge_to_c8000v(self):
        """Test SD-WAN edge maps to c8000v."""
        image = ParsedImage(
            id="cat-sdwan-edge-17.16",
            node_definition_id="cat-sdwan-edge",
            disk_image_filename="c8000v.qcow2",
        )
        node_def = ParsedNodeDefinition(
            id="cat-sdwan-edge",
            label="Catalyst SD-WAN Edge",
        )

        device_id, new_config = get_image_device_mapping(image, [node_def])

        assert device_id == "c8000v"
        assert new_config is None

    def test_mapping_linux_devices(self):
        """Test Linux-based devices map to expected draggable IDs."""
        expected = {
            "alpine": "alpine",
            "ubuntu": "linux",
            "server": "linux",
        }
        for virl_id, expected_device in expected.items():
            image = ParsedImage(
                id=f"{virl_id}-latest",
                node_definition_id=virl_id,
                disk_image_filename=f"{virl_id}.tar.gz",
            )
            node_def = ParsedNodeDefinition(id=virl_id, label=virl_id.capitalize())

            device_id, new_config = get_image_device_mapping(image, [node_def])

            assert device_id == expected_device, f"Expected '{expected_device}' for {virl_id}"
            assert new_config is None
