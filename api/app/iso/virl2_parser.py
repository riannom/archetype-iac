"""Parser for Cisco VIRL2/CML2 format ISOs."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from app.iso.extractor import ISOExtractor
from app.iso.models import (
    ISOFormat,
    ISOManifest,
    ParsedImage,
    ParsedNodeDefinition,
)
from app.iso.parser import ISOParser, ParserRegistry

logger = logging.getLogger(__name__)


@ParserRegistry.register
class VIRL2Parser(ISOParser):
    """Parser for Cisco VIRL2/CML2 format ISOs.

    VIRL2 format structure:
    - /node-definitions/*.yaml - Device type definitions
    - /virl-base-images/*/ - Image directories with:
        - *.yaml - Image metadata
        - *.qcow2 or *.tar.gz - Disk image or container archive
    """

    @property
    def format(self) -> ISOFormat:
        return ISOFormat.VIRL2

    def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
        """Check for VIRL2 format markers.

        VIRL2 ISOs have:
        - node-definitions/ directory
        - virl-base-images/ directory
        """
        has_node_defs = any("node-definitions/" in f for f in file_list)
        has_base_images = any("virl-base-images/" in f for f in file_list)
        return has_node_defs and has_base_images

    async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
        """Parse a VIRL2 format ISO."""
        manifest = ISOManifest(
            iso_path=str(iso_path),
            format=ISOFormat.VIRL2,
            size_bytes=iso_path.stat().st_size,
        )

        file_list = await extractor.get_file_names()

        # Parse node definitions
        node_def_files = [f for f in file_list if f.startswith("node-definitions/") and f.endswith(".yaml")]
        for node_def_file in node_def_files:
            try:
                content = await extractor.read_text_file(node_def_file)
                node_def = self._parse_node_definition(content, node_def_file)
                if node_def:
                    manifest.node_definitions.append(node_def)
            except Exception as e:
                logger.warning(f"Failed to parse node definition {node_def_file}: {e}")
                manifest.parse_errors.append(f"Node definition {node_def_file}: {e}")

        # Parse images
        image_yaml_files = [
            f for f in file_list
            if f.startswith("virl-base-images/") and f.endswith(".yaml")
        ]
        for image_yaml_file in image_yaml_files:
            try:
                content = await extractor.read_text_file(image_yaml_file)
                image = self._parse_image(content, image_yaml_file, file_list)
                if image:
                    manifest.images.append(image)
            except Exception as e:
                logger.warning(f"Failed to parse image definition {image_yaml_file}: {e}")
                manifest.parse_errors.append(f"Image definition {image_yaml_file}: {e}")

        return manifest

    def _parse_node_definition(self, yaml_content: str, file_path: str) -> ParsedNodeDefinition | None:
        """Parse a node definition YAML file."""
        try:
            data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            logger.warning(f"Invalid YAML in {file_path}: {e}")
            return None

        if not data or not isinstance(data, dict):
            return None

        node_id = data.get("id", "")
        if not node_id:
            return None

        # Extract UI section
        ui = data.get("ui", {})
        general = data.get("general", {})
        device = data.get("device", {})
        interfaces = device.get("interfaces", {})
        sim = data.get("sim", {})
        linux_native = sim.get("linux_native", {})
        boot = data.get("boot", {})

        # Build interface list
        physical_interfaces = interfaces.get("physical", [])

        # Extract interface naming pattern
        interface_naming = "eth"
        if physical_interfaces:
            first_iface = physical_interfaces[0]
            for pattern in ["GigabitEthernet", "Ethernet", "Management", "ge-", "xe-", "eth"]:
                if first_iface.startswith(pattern):
                    interface_naming = pattern
                    break

        return ParsedNodeDefinition(
            id=node_id,
            label=ui.get("label", general.get("description", node_id)),
            description=ui.get("description", general.get("description", "")),
            nature=general.get("nature", "router"),
            vendor=ui.get("group", ""),
            icon=ui.get("icon", "router"),
            ram_mb=linux_native.get("ram", 2048),
            cpus=linux_native.get("cpus", 1),
            cpu_limit=linux_native.get("cpu_limit", 100),
            interfaces=physical_interfaces,
            interface_count_default=interfaces.get("default_count", 4),
            interface_naming_pattern=interface_naming,
            has_loopback=interfaces.get("has_loopback_zero", False),
            boot_timeout=boot.get("timeout", 300),
            boot_completed_patterns=boot.get("completed", []),
            provisioning_driver=data.get("configuration", {}).get("generator", {}).get("driver"),
            provisioning_media_type=data.get("configuration", {}).get("provisioning", {}).get("media_type"),
            libvirt_driver=linux_native.get("libvirt_domain_driver", "kvm"),
            disk_driver=linux_native.get("disk_driver", "virtio"),
            nic_driver=linux_native.get("nic_driver", "virtio"),
            raw_yaml=data,
        )

    def _parse_image(
        self, yaml_content: str, yaml_path: str, file_list: list[str]
    ) -> ParsedImage | None:
        """Parse an image definition YAML file."""
        try:
            data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            logger.warning(f"Invalid YAML in {yaml_path}: {e}")
            return None

        if not data or not isinstance(data, dict):
            return None

        image_id = data.get("id", "")
        node_def_id = data.get("node_definition_id", "")
        disk_image = data.get("disk_image", "")

        if not image_id or not node_def_id or not disk_image:
            return None

        # Find the actual disk image file in the same directory
        image_dir = str(Path(yaml_path).parent)
        disk_image_path = ""
        disk_image_size = 0

        # Look for the disk image file
        for f in file_list:
            if f.startswith(image_dir + "/") and (
                f.endswith(".qcow2") or f.endswith(".tar.gz") or f.endswith(".tar")
            ):
                disk_image_path = f
                break

        # If not found by directory, try exact name match
        if not disk_image_path:
            for f in file_list:
                if f.endswith("/" + disk_image) or f == disk_image:
                    disk_image_path = f
                    break

        # Determine image type
        if disk_image.endswith(".qcow2"):
            image_type = "qcow2"
        elif disk_image.endswith((".tar.gz", ".tar", ".tar.xz")):
            image_type = "docker"
        else:
            image_type = "unknown"

        # Extract version from image ID or filename
        version = self._extract_version(image_id) or self._extract_version(disk_image)

        return ParsedImage(
            id=image_id,
            node_definition_id=node_def_id,
            label=data.get("label", image_id),
            description=data.get("description", ""),
            version=version or "",
            disk_image_filename=disk_image,
            disk_image_path=disk_image_path,
            size_bytes=disk_image_size,  # Will be populated during extraction
            image_type=image_type,
            raw_yaml=data,
        )

    def _extract_version(self, text: str) -> str | None:
        """Extract version string from text."""
        # Common version patterns
        patterns = [
            r"(\d+\.\d+\.\d+[A-Za-z]*)",  # 17.16.01a
            r"(\d+-\d+-\d+)",  # 20-16-1
            r"(\d+\.\d+)",  # 7.7
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None
