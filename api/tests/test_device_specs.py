"""Tests for device hardware spec resolution in DeviceService."""
from unittest.mock import patch
from dataclasses import dataclass, field

from app.services.device_service import DeviceService


# Minimal VendorConfig-like mock for testing
@dataclass
class MockVendorConfig:
    kind: str = "cisco_iosv"
    memory: int = 512
    cpu: int = 1
    max_ports: int = 32
    port_naming: str = "eth"
    disk_driver: str = "ide"
    nic_driver: str = "e1000"
    machine_type: str = "pc-i440fx-6.2"
    readiness_probe: str | None = None
    readiness_pattern: str | None = None
    readiness_timeout: int | None = None
    supported_image_kinds: list[str] = field(default_factory=lambda: ["qcow2"])
    efi_boot: bool = False
    efi_vars: str = ""
    data_volume_gb: int = 0


class TestResolveHardwareSpecs:
    """Test DeviceService.resolve_hardware_specs()."""

    def setup_method(self):
        self.service = DeviceService()

    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_iosv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_builtin_device_specs(self, mock_config, mock_kind, mock_custom, mock_override):
        """Built-in device returns vendor config specs."""
        mock_config.return_value = MockVendorConfig()
        specs = self.service.resolve_hardware_specs("iosv")
        assert specs["memory"] == 512
        assert specs["cpu"] == 1
        assert specs["disk_driver"] == "ide"
        assert specs["nic_driver"] == "e1000"
        assert specs["machine_type"] == "pc-i440fx-6.2"
        assert specs["libvirt_driver"] == "kvm"

    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device")
    @patch("app.services.device_service.get_kind_for_device", return_value="unknown")
    @patch("app.services.device_service._get_config_by_kind", return_value=None)
    def test_custom_device_specs(self, mock_config, mock_kind, mock_custom, mock_override):
        """Custom device returns specs from custom_devices.json."""
        mock_custom.return_value = {
            "id": "cat9000v-uadp",
            "memory": 18432,
            "cpu": 4,
            "diskDriver": "ide",
            "nicDriver": "e1000",
            "machineType": "pc-i440fx-6.2",
        }
        specs = self.service.resolve_hardware_specs("cat9000v-uadp")
        assert specs["memory"] == 18432
        assert specs["cpu"] == 4
        assert specs["disk_driver"] == "ide"
        assert specs["nic_driver"] == "e1000"
        assert specs["machine_type"] == "pc-i440fx-6.2"

    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_iosv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_per_node_overrides_take_priority(self, mock_config, mock_kind, mock_custom, mock_override):
        """Per-node config_json overrides device defaults."""
        mock_config.return_value = MockVendorConfig(memory=512, cpu=1)
        node_config = {"memory": 2048, "cpu": 2}
        specs = self.service.resolve_hardware_specs("iosv", node_config)
        assert specs["memory"] == 2048
        assert specs["cpu"] == 2
        # Non-overridden fields keep vendor defaults
        assert specs["disk_driver"] == "ide"

    @patch("app.services.device_service.get_device_override")
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_iosv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_device_override_layer(self, mock_config, mock_kind, mock_custom, mock_override):
        """Device overrides (device_overrides.json) sit between vendor and per-node."""
        mock_config.return_value = MockVendorConfig(memory=512, cpu=1)
        mock_override.return_value = {"memory": 4096}
        specs = self.service.resolve_hardware_specs("iosv")
        assert specs["memory"] == 4096  # Override wins over vendor
        assert specs["cpu"] == 1  # Vendor default preserved

    @patch("app.services.device_service.get_device_override")
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_iosv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_full_priority_chain(self, mock_config, mock_kind, mock_custom, mock_override):
        """Per-node > device override > vendor config."""
        mock_config.return_value = MockVendorConfig(memory=512, cpu=1, disk_driver="ide")
        mock_override.return_value = {"memory": 4096, "cpu": 2}
        node_config = {"memory": 8192}  # Override only memory
        specs = self.service.resolve_hardware_specs("iosv", node_config)
        assert specs["memory"] == 8192  # Per-node wins
        assert specs["cpu"] == 2  # Device override wins over vendor
        assert specs["disk_driver"] == "ide"  # Vendor default

    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="unknown")
    @patch("app.services.device_service._get_config_by_kind", return_value=None)
    def test_unknown_device_returns_empty(self, mock_config, mock_kind, mock_custom, mock_override):
        """Unknown device with no custom definition returns empty dict."""
        specs = self.service.resolve_hardware_specs("totally_unknown_device")
        assert specs == {}

    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="unknown")
    @patch("app.services.device_service._get_config_by_kind", return_value=None)
    def test_unknown_device_with_node_config(self, mock_config, mock_kind, mock_custom, mock_override):
        """Unknown device with per-node config returns node config values."""
        node_config = {"memory": 16384, "cpu": 4}
        specs = self.service.resolve_hardware_specs("unknown", node_config)
        assert specs["memory"] == 16384
        assert specs["cpu"] == 4

    @patch("app.services.device_service.get_image_runtime_metadata")
    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_n9kv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_image_metadata_overrides_vendor_defaults(
        self,
        mock_config,
        mock_kind,
        mock_custom,
        mock_override,
        mock_image_meta,
    ):
        """Imported image metadata should override vendor registry defaults."""
        mock_config.return_value = MockVendorConfig(
            memory=8192,
            cpu=2,
            disk_driver="virtio",
            nic_driver="virtio",
        )
        mock_image_meta.return_value = {
            "memory": 12288,
            "cpu": 4,
            "cpu_limit": 80,
            "disk_driver": "sata",
            "nic_driver": "e1000",
            "libvirt_driver": "qemu",
            "readiness_probe": "log_pattern",
            "readiness_pattern": "Press RETURN",
            "readiness_timeout": 480,
        }
        specs = self.service.resolve_hardware_specs(
            "cisco_n9kv",
            None,
            "/var/lib/archetype/images/n9kv.qcow2",
        )
        assert specs["memory"] == 12288
        assert specs["cpu"] == 4
        assert specs["cpu_limit"] == 80
        assert specs["disk_driver"] == "sata"
        assert specs["nic_driver"] == "e1000"
        assert specs["libvirt_driver"] == "qemu"
        assert specs["readiness_probe"] == "log_pattern"
        assert specs["readiness_pattern"] == "Press RETURN"
        assert specs["readiness_timeout"] == 480

    @patch("app.services.device_service.get_image_runtime_metadata")
    @patch("app.services.device_service.get_device_override")
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_n9kv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_override_layers_still_win_over_image_metadata(
        self,
        mock_config,
        mock_kind,
        mock_custom,
        mock_override,
        mock_image_meta,
    ):
        """Device overrides and node overrides remain higher priority than image metadata."""
        mock_config.return_value = MockVendorConfig(memory=8192, cpu=2)
        mock_image_meta.return_value = {"memory": 12288, "cpu": 4, "cpu_limit": 80}
        mock_override.return_value = {"memory": 16384}
        node_config = {"memory": 24576, "cpu_limit": 70}
        specs = self.service.resolve_hardware_specs(
            "cisco_n9kv",
            node_config,
            "/var/lib/archetype/images/n9kv.qcow2",
        )
        assert specs["memory"] == 24576
        assert specs["cpu"] == 4
        assert specs["cpu_limit"] == 70

    @patch("app.services.device_service.get_image_runtime_metadata")
    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_n9kv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_vendor_probe_none_blocks_image_readiness_override(
        self,
        mock_config,
        mock_kind,
        mock_custom,
        mock_override,
        mock_image_meta,
    ):
        """When vendor sets readiness_probe='none', image metadata must not override it."""
        mock_config.return_value = MockVendorConfig(
            kind="cisco_n9kv",
            memory=12288,
            cpu=2,
            readiness_probe="none",
            readiness_pattern=None,
            readiness_timeout=600,
        )
        mock_image_meta.return_value = {
            "memory": 16384,
            "readiness_probe": "log_pattern",
            "readiness_pattern": "There is no admin password|User Access Verification",
            "readiness_timeout": 480,
        }
        specs = self.service.resolve_hardware_specs(
            "cisco_n9kv",
            None,
            "/var/lib/archetype/images/n9kv.qcow2",
        )
        # Memory should be overridden by image metadata
        assert specs["memory"] == 16384
        # Readiness probe/pattern must stay as vendor "none" — not overridden
        assert specs["readiness_probe"] == "none"
        assert specs["readiness_pattern"] is None
        # Readiness timeout is NOT protected (only probe + pattern are)
        assert specs["readiness_timeout"] == 480

    @patch("app.services.device_service.get_image_runtime_metadata")
    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_iosv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_vendor_probe_non_none_allows_image_readiness_override(
        self,
        mock_config,
        mock_kind,
        mock_custom,
        mock_override,
        mock_image_meta,
    ):
        """When vendor has readiness_probe != 'none', image metadata can override it."""
        mock_config.return_value = MockVendorConfig(
            readiness_probe="log_pattern",
            readiness_pattern="original_pattern",
            readiness_timeout=300,
        )
        mock_image_meta.return_value = {
            "readiness_probe": "log_pattern",
            "readiness_pattern": "new_pattern_from_image",
            "readiness_timeout": 600,
        }
        specs = self.service.resolve_hardware_specs(
            "cisco_iosv",
            None,
            "/var/lib/archetype/images/iosv.qcow2",
        )
        assert specs["readiness_probe"] == "log_pattern"
        assert specs["readiness_pattern"] == "new_pattern_from_image"
        assert specs["readiness_timeout"] == 600

    @patch("app.services.device_service.get_image_runtime_metadata")
    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="unknown")
    @patch("app.services.device_service._get_config_by_kind", return_value=None)
    def test_custom_device_allows_image_readiness_fields(
        self,
        mock_config,
        mock_kind,
        mock_custom,
        mock_override,
        mock_image_meta,
    ):
        """Custom devices (no vendor config) should still get image readiness metadata."""
        mock_custom.return_value = {"id": "custom_device", "memory": 4096, "cpu": 2}
        mock_image_meta.return_value = {
            "readiness_probe": "log_pattern",
            "readiness_pattern": "custom_boot_done",
        }
        specs = self.service.resolve_hardware_specs(
            "custom_device",
            None,
            "/var/lib/archetype/images/custom.qcow2",
        )
        assert specs["readiness_probe"] == "log_pattern"
        assert specs["readiness_pattern"] == "custom_boot_done"

    @patch("app.services.device_service.get_image_runtime_metadata")
    @patch("app.services.device_service.get_device_override", return_value=None)
    @patch("app.services.device_service.find_custom_device", return_value=None)
    @patch("app.services.device_service.get_kind_for_device", return_value="cisco_n9kv")
    @patch("app.services.device_service._get_config_by_kind")
    def test_per_node_override_can_still_set_readiness_on_probe_none_vendor(
        self,
        mock_config,
        mock_kind,
        mock_custom,
        mock_override,
        mock_image_meta,
    ):
        """Per-node config can override readiness even when vendor is 'none' (explicit user intent)."""
        mock_config.return_value = MockVendorConfig(
            kind="cisco_n9kv",
            readiness_probe="none",
            readiness_pattern=None,
        )
        mock_image_meta.return_value = {
            "readiness_probe": "log_pattern",
            "readiness_pattern": "stale_pattern",
        }
        node_config = {
            "readiness_probe": "log_pattern",
            "readiness_pattern": "user_chosen_pattern",
        }
        specs = self.service.resolve_hardware_specs(
            "cisco_n9kv",
            node_config,
            "/var/lib/archetype/images/n9kv.qcow2",
        )
        # Per-node override (Layer 3) should win — explicit user intent
        assert specs["readiness_probe"] == "log_pattern"
        assert specs["readiness_pattern"] == "user_chosen_pattern"
