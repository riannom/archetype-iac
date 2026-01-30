"""Tests for ISO module Pydantic models."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.iso.models import (
    ImageImportProgress,
    ISOFormat,
    ISOManifest,
    ISOSession,
    ParsedImage,
    ParsedNodeDefinition,
)


class TestISOFormat:
    """Tests for ISOFormat enum."""

    def test_virl2_format(self):
        """Test VIRL2 format value."""
        assert ISOFormat.VIRL2.value == "virl2"

    def test_unknown_format(self):
        """Test unknown format value."""
        assert ISOFormat.UNKNOWN.value == "unknown"


class TestParsedNodeDefinition:
    """Tests for ParsedNodeDefinition model."""

    def test_minimal_node_definition(self):
        """Test creating a node definition with minimal fields."""
        node_def = ParsedNodeDefinition(
            id="test-node",
            label="Test Node",
        )
        assert node_def.id == "test-node"
        assert node_def.label == "Test Node"
        # Check defaults
        assert node_def.description == ""
        assert node_def.nature == "router"
        assert node_def.ram_mb == 2048
        assert node_def.cpus == 1
        assert node_def.interfaces == []
        assert node_def.boot_timeout == 300

    def test_full_node_definition(self):
        """Test creating a node definition with all fields."""
        node_def = ParsedNodeDefinition(
            id="ftdv",
            label="FTDv",
            description="Firepower Threat Defense Virtual",
            nature="firewall",
            vendor="Cisco",
            icon="firewall",
            ram_mb=8192,
            cpus=4,
            cpu_limit=80,
            interfaces=["GigabitEthernet0/0", "GigabitEthernet0/1"],
            interface_count_default=8,
            interface_naming_pattern="GigabitEthernet",
            has_loopback=True,
            boot_timeout=600,
            boot_completed_patterns=["FTD startup complete"],
            provisioning_driver="fxos",
            provisioning_media_type="iso",
            libvirt_driver="kvm",
            disk_driver="virtio",
            nic_driver="e1000",
            raw_yaml={"id": "ftdv"},
        )
        assert node_def.id == "ftdv"
        assert node_def.nature == "firewall"
        assert node_def.ram_mb == 8192
        assert node_def.cpus == 4
        assert len(node_def.interfaces) == 2
        assert node_def.has_loopback is True
        assert node_def.provisioning_driver == "fxos"

    def test_port_naming_property_with_interfaces(self):
        """Test port_naming property extracts pattern from interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["GigabitEthernet0/0", "GigabitEthernet0/1"],
        )
        assert node_def.port_naming == "GigabitEthernet"

    def test_port_naming_property_ethernet(self):
        """Test port_naming property for Ethernet interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["Ethernet1", "Ethernet2"],
        )
        assert node_def.port_naming == "Ethernet"

    def test_port_naming_property_management(self):
        """Test port_naming property for Management interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["Management0", "Management1"],
        )
        assert node_def.port_naming == "Management"

    def test_port_naming_property_ge(self):
        """Test port_naming property for ge- interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["ge-0/0/0", "ge-0/0/1"],
        )
        assert node_def.port_naming == "ge-"

    def test_port_naming_property_xe(self):
        """Test port_naming property for xe- interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["xe-0/0/0", "xe-0/0/1"],
        )
        assert node_def.port_naming == "xe-"

    def test_port_naming_property_eth(self):
        """Test port_naming property for eth interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["eth0", "eth1"],
        )
        assert node_def.port_naming == "eth"

    def test_port_naming_property_empty_interfaces(self):
        """Test port_naming property with empty interfaces list."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=[],
        )
        assert node_def.port_naming == "eth"

    def test_port_naming_property_unknown_pattern(self):
        """Test port_naming property with unknown pattern falls back to eth."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["custom0", "custom1"],
        )
        assert node_def.port_naming == "eth"

    def test_port_start_index_property(self):
        """Test port_start_index property extracts number from interface."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["GigabitEthernet0/0", "GigabitEthernet0/1"],
        )
        assert node_def.port_start_index == 0

    def test_port_start_index_property_nonzero(self):
        """Test port_start_index property with non-zero start."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["eth1", "eth2"],
        )
        assert node_def.port_start_index == 1

    def test_port_start_index_property_empty_interfaces(self):
        """Test port_start_index property with empty interfaces."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=[],
        )
        assert node_def.port_start_index == 0

    def test_port_start_index_property_no_number(self):
        """Test port_start_index property when interface has no number."""
        node_def = ParsedNodeDefinition(
            id="test",
            label="Test",
            interfaces=["management"],
        )
        assert node_def.port_start_index == 0


class TestParsedImage:
    """Tests for ParsedImage model."""

    def test_minimal_image(self):
        """Test creating an image with minimal fields."""
        image = ParsedImage(
            id="test-image",
            node_definition_id="test-node",
            disk_image_filename="test.qcow2",
        )
        assert image.id == "test-image"
        assert image.node_definition_id == "test-node"
        assert image.disk_image_filename == "test.qcow2"
        # Check defaults
        assert image.label == ""
        assert image.version == ""
        assert image.size_bytes == 0
        assert image.image_type == "qcow2"

    def test_full_image(self):
        """Test creating an image with all fields."""
        image = ParsedImage(
            id="cat-sdwan-edge-17-16-01a",
            node_definition_id="cat-sdwan-edge",
            label="Catalyst SD-WAN Edge 17.16.01a",
            description="Catalyst SD-WAN Edge image",
            version="17.16.01a",
            disk_image_filename="c8000v-universalk9.17.16.01a.qcow2",
            disk_image_path="virl-base-images/cat-sdwan-edge/c8000v-universalk9.17.16.01a.qcow2",
            size_bytes=1073741824,
            image_type="qcow2",
            raw_yaml={"id": "cat-sdwan-edge-17-16-01a"},
        )
        assert image.id == "cat-sdwan-edge-17-16-01a"
        assert image.version == "17.16.01a"
        assert image.size_bytes == 1073741824

    def test_is_container_property_qcow2(self):
        """Test is_container property for qcow2 image."""
        image = ParsedImage(
            id="test",
            node_definition_id="test",
            disk_image_filename="test.qcow2",
        )
        assert image.is_container is False

    def test_is_container_property_tar_gz(self):
        """Test is_container property for tar.gz image."""
        image = ParsedImage(
            id="test",
            node_definition_id="test",
            disk_image_filename="alpine.tar.gz",
        )
        assert image.is_container is True

    def test_is_container_property_tar(self):
        """Test is_container property for tar image."""
        image = ParsedImage(
            id="test",
            node_definition_id="test",
            disk_image_filename="ubuntu.tar",
        )
        assert image.is_container is True

    def test_is_container_property_tar_xz(self):
        """Test is_container property for tar.xz image."""
        image = ParsedImage(
            id="test",
            node_definition_id="test",
            disk_image_filename="debian.tar.xz",
        )
        assert image.is_container is True


class TestISOManifest:
    """Tests for ISOManifest model."""

    def test_minimal_manifest(self):
        """Test creating a manifest with minimal fields."""
        manifest = ISOManifest(iso_path="/path/to/test.iso")
        assert manifest.iso_path == "/path/to/test.iso"
        assert manifest.format == ISOFormat.UNKNOWN
        assert manifest.size_bytes == 0
        assert manifest.node_definitions == []
        assert manifest.images == []
        assert manifest.parse_errors == []

    def test_full_manifest(self):
        """Test creating a manifest with all fields."""
        node_def = ParsedNodeDefinition(id="ftdv", label="FTDv")
        image = ParsedImage(
            id="ftdv-7.4",
            node_definition_id="ftdv",
            disk_image_filename="ftdv.qcow2",
        )

        manifest = ISOManifest(
            iso_path="/path/to/cml.iso",
            format=ISOFormat.VIRL2,
            size_bytes=5368709120,
            node_definitions=[node_def],
            images=[image],
            parse_errors=["Warning: missing description"],
        )
        assert manifest.format == ISOFormat.VIRL2
        assert len(manifest.node_definitions) == 1
        assert len(manifest.images) == 1
        assert len(manifest.parse_errors) == 1

    def test_get_node_definition_found(self):
        """Test get_node_definition when node exists."""
        node_def = ParsedNodeDefinition(id="ftdv", label="FTDv")
        manifest = ISOManifest(
            iso_path="/test.iso",
            node_definitions=[node_def],
        )

        result = manifest.get_node_definition("ftdv")
        assert result is not None
        assert result.id == "ftdv"

    def test_get_node_definition_not_found(self):
        """Test get_node_definition when node doesn't exist."""
        node_def = ParsedNodeDefinition(id="ftdv", label="FTDv")
        manifest = ISOManifest(
            iso_path="/test.iso",
            node_definitions=[node_def],
        )

        result = manifest.get_node_definition("nonexistent")
        assert result is None

    def test_get_node_definition_empty_list(self):
        """Test get_node_definition with empty node definitions."""
        manifest = ISOManifest(iso_path="/test.iso")
        result = manifest.get_node_definition("anything")
        assert result is None

    def test_get_images_for_node_found(self):
        """Test get_images_for_node when images exist."""
        image1 = ParsedImage(
            id="ftdv-7.4",
            node_definition_id="ftdv",
            disk_image_filename="ftdv-7.4.qcow2",
        )
        image2 = ParsedImage(
            id="ftdv-7.3",
            node_definition_id="ftdv",
            disk_image_filename="ftdv-7.3.qcow2",
        )
        image3 = ParsedImage(
            id="asav-9.18",
            node_definition_id="asav",
            disk_image_filename="asav.qcow2",
        )

        manifest = ISOManifest(
            iso_path="/test.iso",
            images=[image1, image2, image3],
        )

        result = manifest.get_images_for_node("ftdv")
        assert len(result) == 2
        assert all(img.node_definition_id == "ftdv" for img in result)

    def test_get_images_for_node_not_found(self):
        """Test get_images_for_node when no images exist."""
        image = ParsedImage(
            id="ftdv-7.4",
            node_definition_id="ftdv",
            disk_image_filename="ftdv.qcow2",
        )
        manifest = ISOManifest(
            iso_path="/test.iso",
            images=[image],
        )

        result = manifest.get_images_for_node("nonexistent")
        assert result == []

    def test_get_images_for_node_empty_list(self):
        """Test get_images_for_node with empty images list."""
        manifest = ISOManifest(iso_path="/test.iso")
        result = manifest.get_images_for_node("anything")
        assert result == []


class TestImageImportProgress:
    """Tests for ImageImportProgress model."""

    def test_minimal_progress(self):
        """Test creating progress with minimal fields."""
        progress = ImageImportProgress(image_id="test-image")
        assert progress.image_id == "test-image"
        assert progress.status == "pending"
        assert progress.progress_percent == 0
        assert progress.bytes_extracted == 0
        assert progress.total_bytes == 0
        assert progress.error_message is None
        assert progress.started_at is None
        assert progress.completed_at is None

    def test_full_progress(self):
        """Test creating progress with all fields."""
        now = datetime.now(timezone.utc)
        progress = ImageImportProgress(
            image_id="test-image",
            status="completed",
            progress_percent=100,
            bytes_extracted=1073741824,
            total_bytes=1073741824,
            error_message=None,
            started_at=now,
            completed_at=now,
        )
        assert progress.status == "completed"
        assert progress.progress_percent == 100

    def test_progress_with_error(self):
        """Test creating progress with error."""
        progress = ImageImportProgress(
            image_id="test-image",
            status="failed",
            error_message="Extraction failed: disk full",
        )
        assert progress.status == "failed"
        assert progress.error_message == "Extraction failed: disk full"


class TestISOSession:
    """Tests for ISOSession model."""

    def test_minimal_session(self):
        """Test creating a session with minimal fields."""
        session = ISOSession(
            id="session-123",
            iso_path="/path/to/test.iso",
        )
        assert session.id == "session-123"
        assert session.iso_path == "/path/to/test.iso"
        assert session.manifest is None
        assert session.selected_images == []
        assert session.create_devices is True
        assert session.status == "pending"
        assert session.progress_percent == 0
        assert session.image_progress == {}

    def test_full_session(self):
        """Test creating a session with all fields."""
        manifest = ISOManifest(iso_path="/test.iso", format=ISOFormat.VIRL2)
        progress = ImageImportProgress(image_id="img1", status="completed")

        session = ISOSession(
            id="session-456",
            iso_path="/path/to/cml.iso",
            manifest=manifest,
            selected_images=["img1", "img2"],
            create_devices=False,
            status="importing",
            progress_percent=50,
            error_message=None,
            image_progress={"img1": progress},
        )
        assert session.manifest is not None
        assert len(session.selected_images) == 2
        assert session.create_devices is False
        assert session.status == "importing"

    def test_update_image_progress_new_image(self):
        """Test update_image_progress creates new progress entry."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )

        session.update_image_progress("img1", "extracting", 25)

        assert "img1" in session.image_progress
        assert session.image_progress["img1"].status == "extracting"
        assert session.image_progress["img1"].progress_percent == 25
        assert session.image_progress["img1"].started_at is not None

    def test_update_image_progress_existing_image(self):
        """Test update_image_progress updates existing entry."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )

        session.update_image_progress("img1", "extracting", 25)
        original_started = session.image_progress["img1"].started_at

        session.update_image_progress("img1", "extracting", 75)

        assert session.image_progress["img1"].progress_percent == 75
        # started_at should not change
        assert session.image_progress["img1"].started_at == original_started

    def test_update_image_progress_completed(self):
        """Test update_image_progress sets completed_at for completed status."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )

        session.update_image_progress("img1", "extracting", 50)
        session.update_image_progress("img1", "completed", 100)

        assert session.image_progress["img1"].completed_at is not None

    def test_update_image_progress_failed(self):
        """Test update_image_progress sets completed_at and error for failed status."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )

        session.update_image_progress(
            "img1", "failed", 50, error_message="Extraction failed"
        )

        assert session.image_progress["img1"].status == "failed"
        assert session.image_progress["img1"].error_message == "Extraction failed"
        assert session.image_progress["img1"].completed_at is not None

    def test_calculate_overall_progress_empty(self):
        """Test calculate_overall_progress with no images."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )

        assert session.calculate_overall_progress() == 0

    def test_calculate_overall_progress_single_image(self):
        """Test calculate_overall_progress with single image."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )
        session.update_image_progress("img1", "extracting", 50)

        assert session.calculate_overall_progress() == 50

    def test_calculate_overall_progress_multiple_images(self):
        """Test calculate_overall_progress with multiple images."""
        session = ISOSession(
            id="session-123",
            iso_path="/test.iso",
        )
        session.update_image_progress("img1", "completed", 100)
        session.update_image_progress("img2", "extracting", 50)
        session.update_image_progress("img3", "pending", 0)

        # (100 + 50 + 0) / 3 = 50
        assert session.calculate_overall_progress() == 50
