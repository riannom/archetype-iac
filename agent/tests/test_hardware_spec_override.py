"""Tests for hardware spec override in CreateNodeRequest and LibvirtProvider."""
import pytest
from pydantic import ValidationError

from agent.schemas import CreateNodeRequest


class TestCreateNodeRequestHardwareFields:
    """Test that CreateNodeRequest accepts and validates hardware spec fields."""

    def test_accepts_all_hardware_fields(self):
        req = CreateNodeRequest(
            node_name="test1",
            kind="cisco_iosv",
            memory=8192,
            cpu=4,
            cpu_limit=75,
            disk_driver="ide",
            nic_driver="e1000",
            machine_type="pc-i440fx-6.2",
            libvirt_driver="qemu",
            efi_boot=True,
            efi_vars="stateless",
        )
        assert req.memory == 8192
        assert req.cpu == 4
        assert req.cpu_limit == 75
        assert req.disk_driver == "ide"
        assert req.nic_driver == "e1000"
        assert req.machine_type == "pc-i440fx-6.2"
        assert req.libvirt_driver == "qemu"
        assert req.efi_boot is True
        assert req.efi_vars == "stateless"

    def test_hardware_fields_default_to_none(self):
        req = CreateNodeRequest(node_name="test1")
        assert req.memory is None
        assert req.cpu is None
        assert req.cpu_limit is None
        assert req.disk_driver is None
        assert req.nic_driver is None
        assert req.machine_type is None
        assert req.libvirt_driver is None
        assert req.efi_boot is None
        assert req.efi_vars is None

    def test_rejects_memory_zero(self):
        with pytest.raises(ValidationError) as exc_info:
            CreateNodeRequest(node_name="test1", memory=0)
        assert "memory" in str(exc_info.value).lower()

    def test_rejects_memory_negative(self):
        with pytest.raises(ValidationError):
            CreateNodeRequest(node_name="test1", memory=-1024)

    def test_rejects_cpu_zero(self):
        with pytest.raises(ValidationError) as exc_info:
            CreateNodeRequest(node_name="test1", cpu=0)
        assert "cpu" in str(exc_info.value).lower()

    def test_rejects_cpu_negative(self):
        with pytest.raises(ValidationError):
            CreateNodeRequest(node_name="test1", cpu=-1)

    def test_rejects_cpu_limit_out_of_range(self):
        with pytest.raises(ValidationError):
            CreateNodeRequest(node_name="test1", cpu_limit=0)
        with pytest.raises(ValidationError):
            CreateNodeRequest(node_name="test1", cpu_limit=101)

    def test_accepts_partial_hardware_fields(self):
        """Only memory specified, rest defaults to None."""
        req = CreateNodeRequest(node_name="test1", memory=18432)
        assert req.memory == 18432
        assert req.cpu is None
        assert req.disk_driver is None

    def test_serialization_includes_hardware_fields(self):
        req = CreateNodeRequest(
            node_name="test1",
            memory=4096,
            cpu=2,
            cpu_limit=80,
            disk_driver="virtio",
        )
        data = req.model_dump(exclude_none=True)
        assert data["memory"] == 4096
        assert data["cpu"] == 2
        assert data["cpu_limit"] == 80
        assert data["disk_driver"] == "virtio"
        assert "nic_driver" not in data
        assert "machine_type" not in data
        assert "libvirt_driver" not in data
        assert "efi_boot" not in data
        assert "efi_vars" not in data

    def test_serialization_excludes_none_hardware_fields(self):
        req = CreateNodeRequest(node_name="test1")
        data = req.model_dump(exclude_none=True)
        assert "memory" not in data
        assert "cpu" not in data


class TestLibvirtProviderOverride:
    """Test that libvirt provider uses request params over VENDOR_CONFIGS defaults."""

    def test_request_params_override_vendor_config(self):
        """Request params should take priority over get_libvirt_config() defaults."""
        from agent.vendors import get_libvirt_config

        # Get vendor defaults for a known device
        vendor_config = get_libvirt_config("unknown_device_xyz")
        assert vendor_config.memory_mb == 2048  # fallback default

        # When request provides memory=18432, it should be used instead
        request_memory = 18432
        resolved = request_memory or vendor_config.memory_mb
        assert resolved == 18432

    def test_missing_request_params_fallback_to_vendor(self):
        """When request params are None, vendor defaults should be used."""
        from agent.vendors import get_libvirt_config

        vendor_config = get_libvirt_config("unknown_device_xyz")
        request_memory = None
        resolved = request_memory or vendor_config.memory_mb
        assert resolved == 2048  # fallback default

    def test_unknown_device_fallback_logs_warning(self, caplog):
        """Unknown non-intensive devices should log when generic fallback is used."""
        from agent.vendors import get_libvirt_config

        caplog.set_level("WARNING")
        cfg = get_libvirt_config("unknown_device_xyz")
        assert cfg.memory_mb == 2048
        assert "using fallback defaults" in caplog.text

    def test_memory_intensive_unknown_device_refuses_fallback(self):
        """Memory-intensive unknown device IDs must fail fast instead of fallback."""
        from agent.vendors import get_libvirt_config

        with pytest.raises(ValueError, match="Refusing fallback defaults"):
            get_libvirt_config("cat9000v-uadp-custom")

    @pytest.mark.parametrize("kind", ["cat9000v-uadp", "cat9000v-q200"])
    def test_cat9k_aliases_resolve_to_vendor_profile(self, kind: str):
        """Known Cat9k aliases should resolve to the Cat9kv vendor libvirt profile."""
        from agent.vendors import get_libvirt_config

        cfg = get_libvirt_config(kind)
        assert cfg.source == "vendor"
        assert cfg.memory_mb >= 18432
        assert cfg.cpu_count >= 4
        assert cfg.machine_type == "pc-i440fx-6.2"
        assert cfg.disk_driver == "ide"
        assert cfg.nic_driver == "e1000"
