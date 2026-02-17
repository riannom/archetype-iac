"""Tests for IOL NVRAM parser and config extraction."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestParseIolNvram:
    """Tests for the _parse_iol_nvram function."""

    def test_returns_none_for_empty_data(self):
        """Should return None for empty bytes."""
        from agent.providers.docker import _parse_iol_nvram

        assert _parse_iol_nvram(b"") is None

    def test_returns_none_for_too_small(self):
        """Should return None for data smaller than minimum header."""
        from agent.providers.docker import _parse_iol_nvram

        assert _parse_iol_nvram(b"\x00" * 32) is None

    def test_returns_none_for_no_config_markers(self):
        """Should return None when no IOS config markers found."""
        from agent.providers.docker import _parse_iol_nvram

        # 100 bytes of binary noise with no config patterns
        data = bytes(range(100))
        assert _parse_iol_nvram(data) is None

    def test_extracts_simple_config(self):
        """Should extract config text after binary header."""
        from agent.providers.docker import _parse_iol_nvram

        # Simulate NVRAM: binary header + config text + null padding
        header = b"\x00" * 76
        config = b"\nhostname Router1\n!\ninterface Ethernet0/0\n no shutdown\n!\nend"
        padding = b"\x00" * 100

        data = header + config + padding
        result = _parse_iol_nvram(data)

        assert result is not None
        assert "hostname Router1" in result
        assert "interface Ethernet0/0" in result
        assert result.endswith("end")

    def test_extracts_config_with_version_marker(self):
        """Should find config starting with 'version' line."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\xff" * 80
        config = b"\nversion 15.6\nhostname TestRouter\n!\nend"
        data = header + config + b"\x00" * 50

        result = _parse_iol_nvram(data)

        assert result is not None
        assert "version 15.6" in result
        assert "hostname TestRouter" in result

    def test_extracts_config_with_service_marker(self):
        """Should find config starting with 'service' or 'no service' line."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\xab" * 100
        config = b"\nno service pad\nservice timestamps\nhostname R1\n!\nend"
        data = header + config + b"\x00" * 50

        result = _parse_iol_nvram(data)

        assert result is not None
        assert "no service pad" in result

    def test_trims_at_null_byte(self):
        """Should stop extraction at null byte (binary data after config)."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\x00" * 80
        config = b"\nhostname R1\n!\nend"
        binary_after = b"\x00\xff\xfe\xfd" * 100

        data = header + config + binary_after
        result = _parse_iol_nvram(data)

        assert result is not None
        assert "hostname R1" in result
        assert "\xff" not in result

    def test_trims_to_last_end_statement(self):
        """Should trim config to the last 'end' statement."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\x00" * 80
        config = b"\nhostname R1\n!\nend\nsome trailing garbage before null"
        data = header + config + b"\x00" * 50

        result = _parse_iol_nvram(data)

        assert result is not None
        assert result.endswith("end")
        assert "trailing garbage" not in result

    def test_handles_large_config(self):
        """Should handle large configs without issues."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\x00" * 80
        # Generate a large config
        lines = ["\nhostname BigRouter"]
        for i in range(100):
            lines.append(f"\ninterface Ethernet0/{i}")
            lines.append(f"\n ip address 10.0.{i}.1 255.255.255.0")
            lines.append("\n no shutdown")
            lines.append("\n!")
        lines.append("\nend")
        config = "".join(lines).encode("ascii")

        data = header + config + b"\x00" * 100
        result = _parse_iol_nvram(data)

        assert result is not None
        assert "hostname BigRouter" in result
        assert "interface Ethernet0/99" in result

    def test_returns_none_for_tiny_config(self):
        """Should return None when extracted text is too small (<10 chars)."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\x00" * 80
        config = b"\n!\n!\n"  # Just delimiters, no real config
        data = header + config + b"\x00" * 50

        result = _parse_iol_nvram(data)

        assert result is None

    def test_handles_binary_in_config_gracefully(self):
        """Should ignore non-ASCII bytes in config section."""
        from agent.providers.docker import _parse_iol_nvram

        header = b"\x00" * 80
        config = b"\nhostname R1\n\x80\x81!\nend"
        data = header + config + b"\x00" * 50

        result = _parse_iol_nvram(data)

        assert result is not None
        assert "hostname R1" in result


class TestExtractConfigViaNvram:
    """Tests for DockerProvider._extract_config_via_nvram method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_nvram_file(self, tmp_path):
        """Should return None when NVRAM file doesn't exist."""
        from agent.providers.docker import DockerProvider

        provider = MagicMock(spec=DockerProvider)
        provider._extract_config_via_nvram = DockerProvider._extract_config_via_nvram.__get__(provider)

        result = await provider._extract_config_via_nvram(
            "archetype-lab1-router1", tmp_path
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_extracts_from_nvram_file(self, tmp_path):
        """Should extract config from NVRAM file in workspace."""
        from agent.providers.docker import DockerProvider

        provider = MagicMock(spec=DockerProvider)
        provider._extract_config_via_nvram = DockerProvider._extract_config_via_nvram.__get__(provider)

        # Create NVRAM file in expected path
        nvram_dir = tmp_path / "configs" / "router1" / "iol-data"
        nvram_dir.mkdir(parents=True)
        nvram_file = nvram_dir / "nvram_00001"

        # Write fake NVRAM with config
        header = b"\x00" * 80
        config = b"\nhostname Router1\n!\ninterface Ethernet0/0\n no shutdown\n!\nend"
        nvram_file.write_bytes(header + config + b"\x00" * 50)

        result = await provider._extract_config_via_nvram(
            "archetype-lab1-router1", tmp_path
        )

        assert result is not None
        assert "hostname Router1" in result

    @pytest.mark.asyncio
    async def test_returns_none_for_small_nvram(self, tmp_path):
        """Should return None for NVRAM file that's too small."""
        from agent.providers.docker import DockerProvider

        provider = MagicMock(spec=DockerProvider)
        provider._extract_config_via_nvram = DockerProvider._extract_config_via_nvram.__get__(provider)

        nvram_dir = tmp_path / "configs" / "router1" / "iol-data"
        nvram_dir.mkdir(parents=True)
        nvram_file = nvram_dir / "nvram_00001"
        nvram_file.write_bytes(b"\x00" * 32)

        result = await provider._extract_config_via_nvram(
            "archetype-lab1-router1", tmp_path
        )

        assert result is None
