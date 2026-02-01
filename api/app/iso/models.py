"""Pydantic models for ISO parsing and import."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ISOFormat(str, Enum):
    """Supported ISO image formats."""
    VIRL2 = "virl2"  # Cisco CML2/VIRL2 format
    UNKNOWN = "unknown"


class ParsedNodeDefinition(BaseModel):
    """A node definition parsed from an ISO image.

    Node definitions describe a device type and its properties,
    such as interfaces, resource requirements, and boot behavior.
    """
    id: str = Field(..., description="Node definition ID (e.g., 'ftdv')")
    label: str = Field(..., description="Display label (e.g., 'FTDv')")
    description: str = Field(default="", description="Full description")
    nature: str = Field(default="router", description="Device nature: router, switch, firewall, server")
    vendor: str = Field(default="", description="Vendor name (e.g., 'Cisco')")
    icon: str = Field(default="router", description="Icon type for UI")

    # Resource requirements
    ram_mb: int = Field(default=2048, description="RAM in megabytes")
    cpus: int = Field(default=1, description="Number of vCPUs")
    cpu_limit: int = Field(default=100, description="CPU limit percentage")

    # Interface configuration
    interfaces: list[str] = Field(default_factory=list, description="Physical interface names")
    interface_count_default: int = Field(default=4, description="Default interface count")
    interface_naming_pattern: str = Field(default="eth", description="Interface naming pattern")
    has_loopback: bool = Field(default=False, description="Whether device has loopback interfaces")

    # Boot configuration
    boot_timeout: int = Field(default=300, description="Boot timeout in seconds")
    boot_completed_patterns: list[str] = Field(default_factory=list, description="Patterns indicating boot completion")

    # Provisioning
    provisioning_driver: Optional[str] = Field(default=None, description="Configuration driver")
    provisioning_media_type: Optional[str] = Field(default=None, description="Provisioning media type (iso, etc.)")

    # VM-specific settings
    libvirt_driver: str = Field(default="kvm", description="Libvirt domain driver")
    disk_driver: str = Field(default="virtio", description="Disk driver type")
    nic_driver: str = Field(default="virtio", description="NIC driver type")

    # Original YAML content for reference
    raw_yaml: dict = Field(default_factory=dict, description="Original YAML content")

    @property
    def port_naming(self) -> str:
        """Extract port naming pattern from interface names."""
        if not self.interfaces:
            return "eth"
        # Extract common prefix from interface names
        first_iface = self.interfaces[0]
        # Common patterns: GigabitEthernet, Ethernet, Management, eth
        for pattern in ["GigabitEthernet", "Ethernet", "Management", "eth", "ge-", "xe-"]:
            if first_iface.startswith(pattern):
                return pattern
        return "eth"

    @property
    def port_start_index(self) -> int:
        """Extract starting port index from interface names."""
        if not self.interfaces:
            return 0
        first_iface = self.interfaces[0]
        # Try to extract number from first interface
        import re
        match = re.search(r"(\d+)", first_iface)
        if match:
            return int(match.group(1))
        return 0


class ParsedImage(BaseModel):
    """An image parsed from an ISO.

    Images are associated with node definitions and contain
    the actual disk image or container archive.
    """
    id: str = Field(..., description="Image ID (e.g., 'cat-sdwan-edge-17-16-01a')")
    node_definition_id: str = Field(..., description="Associated node definition ID")
    label: str = Field(default="", description="Display label")
    description: str = Field(default="", description="Image description")
    version: str = Field(default="", description="Version string")

    # File information
    disk_image_filename: str = Field(..., description="Disk image filename (qcow2 or tar.gz)")
    disk_image_path: str = Field(default="", description="Path within ISO")
    size_bytes: int = Field(default=0, description="Image size in bytes")

    # Image type
    image_type: str = Field(default="qcow2", description="Image type: qcow2, tar.gz, docker")

    # Original YAML content
    raw_yaml: dict = Field(default_factory=dict, description="Original YAML content")

    @property
    def is_container(self) -> bool:
        """Check if this is a container image (tar.gz)."""
        return self.disk_image_filename.endswith((".tar.gz", ".tar", ".tar.xz"))


class ISOManifest(BaseModel):
    """Complete manifest of an ISO image.

    Contains all parsed node definitions and images,
    along with metadata about the ISO itself.
    """
    iso_path: str = Field(..., description="Path to the ISO file")
    format: ISOFormat = Field(default=ISOFormat.UNKNOWN, description="Detected ISO format")
    size_bytes: int = Field(default=0, description="ISO file size")

    # Parsed content
    node_definitions: list[ParsedNodeDefinition] = Field(default_factory=list)
    images: list[ParsedImage] = Field(default_factory=list)

    # Parsing metadata
    parsed_at: datetime = Field(default_factory=datetime.utcnow)
    parse_errors: list[str] = Field(default_factory=list)

    def get_node_definition(self, node_def_id: str) -> Optional[ParsedNodeDefinition]:
        """Get a node definition by ID."""
        for node_def in self.node_definitions:
            if node_def.id == node_def_id:
                return node_def
        return None

    def get_images_for_node(self, node_def_id: str) -> list[ParsedImage]:
        """Get all images associated with a node definition."""
        return [img for img in self.images if img.node_definition_id == node_def_id]


class ImageImportProgress(BaseModel):
    """Progress tracking for a single image import."""
    image_id: str
    status: str = Field(default="pending", description="pending, extracting, importing, completed, failed")
    progress_percent: int = Field(default=0)
    bytes_extracted: int = Field(default=0)
    total_bytes: int = Field(default=0)
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ISOSession(BaseModel):
    """Session state for an ISO import operation.

    Tracks the overall import progress and per-image status.
    """
    id: str = Field(..., description="Session ID")
    iso_path: str = Field(..., description="Path to ISO file")
    manifest: Optional[ISOManifest] = None

    # Import configuration
    selected_images: list[str] = Field(default_factory=list, description="Image IDs selected for import")
    create_devices: bool = Field(default=True, description="Create device types for unknown definitions")

    # Overall status
    status: str = Field(default="pending", description="pending, scanning, importing, completed, failed, cancelled")
    progress_percent: int = Field(default=0)
    error_message: Optional[str] = None

    # Per-image progress
    image_progress: dict[str, ImageImportProgress] = Field(default_factory=dict)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    def update_image_progress(
        self,
        image_id: str,
        status: str,
        progress_percent: int = 0,
        error_message: Optional[str] = None,
    ):
        """Update progress for a specific image."""
        if image_id not in self.image_progress:
            self.image_progress[image_id] = ImageImportProgress(image_id=image_id)

        progress = self.image_progress[image_id]
        progress.status = status
        progress.progress_percent = progress_percent
        if error_message:
            progress.error_message = error_message
        if status == "extracting" and not progress.started_at:
            progress.started_at = datetime.now(timezone.utc)
        if status in ("completed", "failed"):
            progress.completed_at = datetime.now(timezone.utc)

        self.updated_at = datetime.now(timezone.utc)

    def calculate_overall_progress(self) -> int:
        """Calculate overall progress based on individual image progress."""
        if not self.image_progress:
            return 0
        total = sum(p.progress_percent for p in self.image_progress.values())
        return total // len(self.image_progress)
