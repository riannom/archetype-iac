"""ISO parsing and import module for vendor images.

This module provides functionality to:
- Parse vendor ISO images (like Cisco RefPlat)
- Extract node definitions and image metadata
- Import qcow2/tar.gz images into the image store
- Map imported images to device types
"""

from app.iso.models import (
    ISOFormat,
    ISOManifest,
    ISOSession,
    ImageImportProgress,
    ParsedImage,
    ParsedNodeDefinition,
)
from app.iso.parser import ISOParser, ParserRegistry
from app.iso.extractor import ISOExtractor
from app.iso.virl2_parser import VIRL2Parser

__all__ = [
    "ISOFormat",
    "ISOManifest",
    "ISOSession",
    "ImageImportProgress",
    "ParsedImage",
    "ParsedNodeDefinition",
    "ISOParser",
    "ParserRegistry",
    "ISOExtractor",
    "VIRL2Parser",
]
