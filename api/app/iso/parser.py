"""Base ISO parser interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Type

from app.iso.models import ISOFormat, ISOManifest


class ISOParser(ABC):
    """Abstract base class for ISO parsers.

    Each parser implementation handles a specific ISO format
    (e.g., VIRL2, EVE-NG, GNS3).
    """

    @property
    @abstractmethod
    def format(self) -> ISOFormat:
        """Return the ISO format this parser handles."""
        pass

    @abstractmethod
    def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
        """Check if this parser can handle the given ISO.

        Args:
            iso_path: Path to the ISO file
            file_list: List of files in the ISO (from 7z listing)

        Returns:
            True if this parser can handle the ISO
        """
        pass

    @abstractmethod
    async def parse(self, iso_path: Path, extractor: "ISOExtractor") -> ISOManifest:
        """Parse the ISO and return a manifest.

        Args:
            iso_path: Path to the ISO file
            extractor: ISOExtractor instance for reading files

        Returns:
            Parsed ISOManifest
        """
        pass


class ParserRegistry:
    """Registry of ISO parsers.

    Parsers are tried in order of registration until one
    reports that it can handle the ISO.
    """

    _parsers: list[Type[ISOParser]] = []

    @classmethod
    def register(cls, parser_class: Type[ISOParser]) -> Type[ISOParser]:
        """Register a parser class.

        Can be used as a decorator:
            @ParserRegistry.register
            class MyParser(ISOParser):
                ...
        """
        cls._parsers.append(parser_class)
        return parser_class

    @classmethod
    def get_parser(cls, iso_path: Path, file_list: list[str]) -> Optional[ISOParser]:
        """Find a parser that can handle the given ISO.

        Args:
            iso_path: Path to the ISO file
            file_list: List of files in the ISO

        Returns:
            Parser instance or None if no parser can handle the ISO
        """
        for parser_class in cls._parsers:
            parser = parser_class()
            if parser.can_parse(iso_path, file_list):
                return parser
        return None

    @classmethod
    def list_formats(cls) -> list[ISOFormat]:
        """List all supported formats."""
        return [p().format for p in cls._parsers]


# Import for type hints
from app.iso.extractor import ISOExtractor  # noqa: E402
