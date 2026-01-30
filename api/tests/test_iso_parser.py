"""Tests for ISO parser interface and registry."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.iso.extractor import ISOExtractor
from app.iso.models import ISOFormat, ISOManifest
from app.iso.parser import ISOParser, ParserRegistry


class MockParser(ISOParser):
    """Mock parser for testing."""

    def __init__(self, can_parse_result: bool = True, format_value: ISOFormat = ISOFormat.VIRL2):
        self._can_parse_result = can_parse_result
        self._format_value = format_value

    @property
    def format(self) -> ISOFormat:
        return self._format_value

    def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
        return self._can_parse_result

    async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
        return ISOManifest(iso_path=str(iso_path), format=self._format_value)


class TestISOParser:
    """Tests for ISOParser abstract base class."""

    def test_parser_is_abstract(self):
        """Test that ISOParser cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ISOParser()

    def test_mock_parser_implements_interface(self):
        """Test that mock parser correctly implements interface."""
        parser = MockParser()

        assert parser.format == ISOFormat.VIRL2
        assert parser.can_parse(Path("/test.iso"), []) is True

    @pytest.mark.asyncio
    async def test_mock_parser_parse(self):
        """Test that mock parser parse method works."""
        parser = MockParser()
        extractor = MagicMock(spec=ISOExtractor)

        result = await parser.parse(Path("/test.iso"), extractor)

        assert isinstance(result, ISOManifest)
        assert result.format == ISOFormat.VIRL2


class TestParserRegistry:
    """Tests for ParserRegistry class."""

    def setup_method(self):
        """Clear registry before each test."""
        # Save original parsers
        self._original_parsers = ParserRegistry._parsers.copy()
        # Clear the registry
        ParserRegistry._parsers.clear()

    def teardown_method(self):
        """Restore registry after each test."""
        ParserRegistry._parsers = self._original_parsers

    def test_register_parser(self):
        """Test registering a parser."""
        # Register using decorator style
        @ParserRegistry.register
        class TestParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return True

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path))

        assert TestParser in ParserRegistry._parsers

    def test_register_multiple_parsers(self):
        """Test registering multiple parsers."""
        ParserRegistry.register(type("Parser1", (ISOParser,), {
            "format": property(lambda self: ISOFormat.VIRL2),
            "can_parse": lambda self, p, f: False,
            "parse": AsyncMock(return_value=ISOManifest(iso_path="/test.iso")),
        }))
        ParserRegistry.register(type("Parser2", (ISOParser,), {
            "format": property(lambda self: ISOFormat.UNKNOWN),
            "can_parse": lambda self, p, f: True,
            "parse": AsyncMock(return_value=ISOManifest(iso_path="/test.iso")),
        }))

        assert len(ParserRegistry._parsers) == 2

    def test_get_parser_found(self):
        """Test get_parser finds a matching parser."""

        class MatchingParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return "node-definitions/" in " ".join(file_list)

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path), format=ISOFormat.VIRL2)

        ParserRegistry.register(MatchingParser)

        file_list = ["node-definitions/test.yaml", "virl-base-images/test/"]
        parser = ParserRegistry.get_parser(Path("/test.iso"), file_list)

        assert parser is not None
        assert isinstance(parser, MatchingParser)
        assert parser.format == ISOFormat.VIRL2

    def test_get_parser_not_found(self):
        """Test get_parser returns None when no parser matches."""

        class NonMatchingParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return False  # Never matches

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path))

        ParserRegistry.register(NonMatchingParser)

        parser = ParserRegistry.get_parser(Path("/test.iso"), ["random.txt"])

        assert parser is None

    def test_get_parser_empty_registry(self):
        """Test get_parser with empty registry."""
        parser = ParserRegistry.get_parser(Path("/test.iso"), ["file.txt"])

        assert parser is None

    def test_get_parser_first_match_wins(self):
        """Test that first matching parser is returned."""

        class FirstParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return True

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path), format=ISOFormat.VIRL2)

        class SecondParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.UNKNOWN

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return True  # Also matches

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path), format=ISOFormat.UNKNOWN)

        ParserRegistry.register(FirstParser)
        ParserRegistry.register(SecondParser)

        parser = ParserRegistry.get_parser(Path("/test.iso"), ["any.txt"])

        assert parser is not None
        # First registered parser should be returned
        assert isinstance(parser, FirstParser)
        assert parser.format == ISOFormat.VIRL2

    def test_list_formats(self):
        """Test list_formats returns all registered parser formats."""

        class VIRL2Parser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return False

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path))

        class UnknownParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.UNKNOWN

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return False

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path))

        ParserRegistry.register(VIRL2Parser)
        ParserRegistry.register(UnknownParser)

        formats = ParserRegistry.list_formats()

        assert len(formats) == 2
        assert ISOFormat.VIRL2 in formats
        assert ISOFormat.UNKNOWN in formats

    def test_list_formats_empty_registry(self):
        """Test list_formats with empty registry."""
        formats = ParserRegistry.list_formats()

        assert formats == []

    def test_register_returns_parser_class(self):
        """Test that register returns the parser class (decorator pattern)."""

        class TestParser(ISOParser):
            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return False

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path))

        result = ParserRegistry.register(TestParser)

        assert result is TestParser

    def test_get_parser_creates_new_instance(self):
        """Test that get_parser creates a new parser instance."""

        class StatefulParser(ISOParser):
            instance_count = 0

            def __init__(self):
                StatefulParser.instance_count += 1
                self.instance_id = StatefulParser.instance_count

            @property
            def format(self) -> ISOFormat:
                return ISOFormat.VIRL2

            def can_parse(self, iso_path: Path, file_list: list[str]) -> bool:
                return True

            async def parse(self, iso_path: Path, extractor: ISOExtractor) -> ISOManifest:
                return ISOManifest(iso_path=str(iso_path))

        ParserRegistry.register(StatefulParser)

        parser1 = ParserRegistry.get_parser(Path("/test1.iso"), ["test"])
        parser2 = ParserRegistry.get_parser(Path("/test2.iso"), ["test"])

        # Each call should create a new instance
        assert parser1 is not None
        assert parser2 is not None
        assert parser1.instance_id != parser2.instance_id
