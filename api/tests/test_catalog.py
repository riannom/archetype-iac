"""Tests for catalog module."""
from __future__ import annotations

from unittest.mock import patch


from app.catalog import _parse_devices_table, list_devices, list_images


class TestParseDevicesTable:
    """Tests for _parse_devices_table function."""

    def test_parses_simple_table(self):
        """Test parsing a simple device table."""
        output = """Device     Description     Support
------     -----------     -------
ceos       Arista cEOS     full
linux      Generic Linux   full
iosv       Cisco IOSv      partial
"""
        devices = _parse_devices_table(output)

        assert len(devices) == 3
        assert devices[0]["id"] == "ceos"
        assert devices[1]["id"] == "linux"
        assert devices[2]["id"] == "iosv"

    def test_parses_device_with_support(self):
        """Test parsing device with support column (3+ columns needed for support)."""
        # The parser expects at least 3 columns to extract support
        output = """Device     Description     Support
------     -----------     -------
ceos       Arista cEOS     full
"""
        devices = _parse_devices_table(output)

        assert len(devices) == 1
        assert devices[0]["id"] == "ceos"
        assert devices[0]["support"] == "full"

    def test_handles_empty_output(self):
        """Test handling empty output."""
        devices = _parse_devices_table("")
        assert devices == []

    def test_handles_header_only(self):
        """Test handling output with only header."""
        output = """Device     Support
------     -------
"""
        devices = _parse_devices_table(output)
        assert devices == []

    def test_handles_whitespace_lines(self):
        """Test handling output with whitespace lines."""
        output = """Device     Support
------     -------

ceos       full

linux      full

"""
        devices = _parse_devices_table(output)
        assert len(devices) == 2

    def test_ignores_lines_before_header(self):
        """Test that lines before header are ignored."""
        output = """Some preamble text
Another line
Device     Support
------     -------
ceos       full
"""
        devices = _parse_devices_table(output)
        assert len(devices) == 1
        assert devices[0]["id"] == "ceos"


class TestListDevices:
    """Tests for list_devices function."""

    @patch("app.catalog.run_netlab_command")
    def test_successful_list(self, mock_run):
        """Test successful device listing."""
        mock_run.return_value = (
            0,
            """Device     Support
------     -------
ceos       full
linux      full
""",
            "",
        )

        result = list_devices()

        assert "devices" in result
        assert len(result["devices"]) == 2
        assert result["devices"][0]["id"] == "ceos"

    @patch("app.catalog.run_netlab_command")
    def test_command_failure(self, mock_run):
        """Test handling command failure."""
        mock_run.return_value = (1, "", "netlab: command not found")

        result = list_devices()

        assert "error" in result
        assert "not found" in result["error"]

    @patch("app.catalog.run_netlab_command")
    def test_returns_raw_output(self, mock_run):
        """Test that raw output is included."""
        raw_output = "Device     Support\n------     -------\nceos       full\n"
        mock_run.return_value = (0, raw_output, "")

        result = list_devices()

        assert "raw" in result
        assert result["raw"] == raw_output


class TestListImages:
    """Tests for list_images function."""

    @patch("app.catalog.run_netlab_command")
    def test_successful_list(self, mock_run):
        """Test successful image listing."""
        yaml_output = """ceos:
  clab: ceos:4.28.0
linux:
  clab: alpine:latest
"""
        mock_run.return_value = (0, yaml_output, "")

        result = list_images()

        assert "images" in result
        assert "ceos" in result["images"]
        assert result["images"]["ceos"]["clab"] == "ceos:4.28.0"

    @patch("app.catalog.run_netlab_command")
    def test_command_failure(self, mock_run):
        """Test handling command failure."""
        mock_run.return_value = (1, "", "netlab: command not found")

        result = list_images()

        assert "error" in result

    @patch("app.catalog.run_netlab_command")
    def test_empty_output(self, mock_run):
        """Test handling empty YAML output."""
        mock_run.return_value = (0, "", "")

        result = list_images()

        assert "images" in result
        assert result["images"] == {}

    @patch("app.catalog.run_netlab_command")
    def test_returns_raw_output(self, mock_run):
        """Test that raw output is included."""
        raw_output = "ceos:\n  clab: ceos:4.28.0\n"
        mock_run.return_value = (0, raw_output, "")

        result = list_images()

        assert "raw" in result
        assert result["raw"] == raw_output
