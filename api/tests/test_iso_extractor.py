"""Tests for ISO extraction utilities."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.iso.extractor import (
    ExtractionProgress,
    ISOExtractor,
    check_7z_available,
)


class TestExtractionProgress:
    """Tests for ExtractionProgress dataclass."""

    def test_extraction_progress_creation(self):
        """Test creating ExtractionProgress instance."""
        progress = ExtractionProgress(
            filename="test.qcow2",
            bytes_extracted=512000000,
            total_bytes=1024000000,
            percent=50,
        )
        assert progress.filename == "test.qcow2"
        assert progress.bytes_extracted == 512000000
        assert progress.total_bytes == 1024000000
        assert progress.percent == 50


class TestISOExtractor:
    """Tests for ISOExtractor class."""

    def test_init(self, tmp_path):
        """Test ISOExtractor initialization."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        assert extractor.iso_path == iso_path
        assert extractor._file_list is None
        assert extractor._temp_dir is None

    @pytest.mark.asyncio
    async def test_list_files_success(self, tmp_path):
        """Test list_files with successful 7z output."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        # Mock 7z output in -slt format
        # Note: 7z includes the archive itself in the Path header, which gets parsed
        mock_output = b"""7-Zip 23.01 (x64)

Listing archive: test.iso

--
Path = test.iso
Type = Iso

----------
Path = node-definitions/ftdv.yaml
Size = 1234
Attributes = ....A

Path = virl-base-images/ftdv/ftdv.qcow2
Size = 1073741824
Attributes = ....A

Path = virl-base-images
Size = 0
Attributes = D....
"""

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(mock_output, b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            files = await extractor.list_files()

        # The parser includes the ISO path entry from the header section
        assert len(files) == 4
        # Check file entry
        yaml_file = next(f for f in files if f["name"] == "node-definitions/ftdv.yaml")
        assert yaml_file["size"] == 1234
        assert yaml_file["is_dir"] is False

        # Check directory entry
        dir_entry = next(f for f in files if f["name"] == "virl-base-images")
        assert dir_entry["is_dir"] is True

        # Check qcow2 file entry
        qcow2_file = next(f for f in files if f["name"] == "virl-base-images/ftdv/ftdv.qcow2")
        assert qcow2_file["size"] == 1073741824
        assert qcow2_file["is_dir"] is False

    @pytest.mark.asyncio
    async def test_list_files_cached(self, tmp_path):
        """Test list_files returns cached result on second call."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        mock_output = b"""Path = test.yaml
Size = 100
Attributes = ....A
"""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(mock_output, b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            # First call
            files1 = await extractor.list_files()
            # Second call should use cache
            files2 = await extractor.list_files()

        # Should only call 7z once
        assert mock_exec.call_count == 1
        assert files1 == files2

    @pytest.mark.asyncio
    async def test_list_files_failure(self, tmp_path):
        """Test list_files raises error on 7z failure."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"Error: Cannot open file"))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(RuntimeError, match="Failed to list ISO contents"):
                await extractor.list_files()

    @pytest.mark.asyncio
    async def test_get_file_names(self, tmp_path):
        """Test get_file_names returns only non-directory names."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        # Pre-populate the file list cache
        extractor._file_list = [
            {"name": "file1.yaml", "size": 100, "is_dir": False},
            {"name": "file2.qcow2", "size": 1000, "is_dir": False},
            {"name": "directory", "size": 0, "is_dir": True},
        ]

        names = await extractor.get_file_names()

        assert names == ["file1.yaml", "file2.qcow2"]
        assert "directory" not in names

    @pytest.mark.asyncio
    async def test_read_file_success(self, tmp_path):
        """Test read_file with successful extraction."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        file_content = b"id: ftdv\nlabel: FTDv\n"
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(file_content, b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            content = await extractor.read_file("node-definitions/ftdv.yaml")

        assert content == file_content

    @pytest.mark.asyncio
    async def test_read_file_failure(self, tmp_path):
        """Test read_file raises error on failure."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"File not found"))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(RuntimeError, match="Failed to read"):
                await extractor.read_file("nonexistent.yaml")

    @pytest.mark.asyncio
    async def test_read_text_file(self, tmp_path):
        """Test read_text_file decodes content."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        file_content = b"id: ftdv\nlabel: FTDv\n"
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(file_content, b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            content = await extractor.read_text_file("test.yaml")

        assert content == "id: ftdv\nlabel: FTDv\n"
        assert isinstance(content, str)

    @pytest.mark.asyncio
    async def test_read_text_file_custom_encoding(self, tmp_path):
        """Test read_text_file with custom encoding."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        file_content = "Test content".encode("latin-1")
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(file_content, b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            content = await extractor.read_text_file("test.txt", encoding="latin-1")

        assert content == "Test content"

    @pytest.mark.asyncio
    async def test_extract_file_success(self, tmp_path):
        """Test extract_file successfully extracts to destination."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()
        dest_path = tmp_path / "output" / "test.qcow2"

        extractor = ISOExtractor(iso_path)

        # Pre-populate file list for size lookup
        extractor._file_list = [
            {"name": "test.qcow2", "size": 100, "is_dir": False},
        ]

        # Mock the extraction process
        file_content = b"fake qcow2 content"

        # Create async mock for stdout.read
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[file_content, b""])

        mock_process = AsyncMock()
        mock_process.stdout = mock_stdout
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await extractor.extract_file("test.qcow2", dest_path)

        assert result == dest_path
        assert dest_path.exists()
        assert dest_path.read_bytes() == file_content

    @pytest.mark.asyncio
    async def test_extract_file_with_progress_callback(self, tmp_path):
        """Test extract_file calls progress callback."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()
        dest_path = tmp_path / "output" / "test.qcow2"

        extractor = ISOExtractor(iso_path)

        # Pre-populate file list for size lookup
        extractor._file_list = [
            {"name": "test.qcow2", "size": 100, "is_dir": False},
        ]

        progress_calls = []

        def progress_callback(progress: ExtractionProgress):
            progress_calls.append(progress)

        file_content = b"x" * 50  # 50 bytes

        # Create async mock for stdout.read
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[file_content, b""])

        mock_process = AsyncMock()
        mock_process.stdout = mock_stdout
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            await extractor.extract_file(
                "test.qcow2", dest_path, progress_callback=progress_callback
            )

        # Should have at least one progress call
        assert len(progress_calls) >= 1
        # Last call should be 100%
        assert progress_calls[-1].percent == 100

    @pytest.mark.asyncio
    async def test_extract_file_failure(self, tmp_path):
        """Test extract_file raises error on failure."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()
        dest_path = tmp_path / "output" / "test.qcow2"

        extractor = ISOExtractor(iso_path)
        extractor._file_list = [{"name": "test.qcow2", "size": 100, "is_dir": False}]

        # Create async mock for stdout.read
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        mock_process = AsyncMock()
        mock_process.stdout = mock_stdout
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"Extraction failed"))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(RuntimeError, match="Failed to extract"):
                await extractor.extract_file("test.qcow2", dest_path)

        # Temp file should be cleaned up
        assert not (tmp_path / "output").exists() or not list((tmp_path / "output").glob("*.tmp"))

    @pytest.mark.asyncio
    async def test_extract_files_multiple(self, tmp_path):
        """Test extract_files extracts multiple files."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()
        dest_dir = tmp_path / "output"

        extractor = ISOExtractor(iso_path)
        extractor._file_list = [
            {"name": "path/to/file1.yaml", "size": 50, "is_dir": False},
            {"name": "path/to/file2.yaml", "size": 60, "is_dir": False},
        ]

        # Create async mock for stdout.read
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[b"content1", b"", b"content2", b""])

        mock_process = AsyncMock()
        mock_process.stdout = mock_stdout
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            results = await extractor.extract_files(
                ["path/to/file1.yaml", "path/to/file2.yaml"],
                dest_dir,
            )

        assert len(results) == 2
        assert "path/to/file1.yaml" in results
        assert "path/to/file2.yaml" in results

    def test_get_temp_dir_creates_directory(self, tmp_path):
        """Test get_temp_dir creates and returns temp directory."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        temp_dir = extractor.get_temp_dir()

        assert temp_dir.exists()
        assert temp_dir.is_dir()
        assert "iso_extract_" in str(temp_dir)

        # Cleanup
        extractor.cleanup()

    def test_get_temp_dir_returns_same_directory(self, tmp_path):
        """Test get_temp_dir returns same directory on multiple calls."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        temp_dir1 = extractor.get_temp_dir()
        temp_dir2 = extractor.get_temp_dir()

        assert temp_dir1 == temp_dir2

        # Cleanup
        extractor.cleanup()

    def test_cleanup_removes_temp_directory(self, tmp_path):
        """Test cleanup removes the temp directory."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        temp_dir = extractor.get_temp_dir()
        # Create a file in the temp directory
        (temp_dir / "test.txt").write_text("test")

        assert temp_dir.exists()

        extractor.cleanup()

        assert not temp_dir.exists()
        assert extractor._temp_dir is None

    def test_cleanup_no_temp_dir(self, tmp_path):
        """Test cleanup does nothing if no temp dir exists."""
        iso_path = tmp_path / "test.iso"
        iso_path.touch()

        extractor = ISOExtractor(iso_path)

        # Should not raise
        extractor.cleanup()
        assert extractor._temp_dir is None


class TestCheck7zAvailable:
    """Tests for check_7z_available function."""

    @pytest.mark.asyncio
    async def test_7z_available(self):
        """Test check_7z_available returns True when 7z is available."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"7-Zip help", b""))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await check_7z_available()

        assert result is True

    @pytest.mark.asyncio
    async def test_7z_not_available_bad_return_code(self):
        """Test check_7z_available returns False on non-zero return code."""
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"Error"))

        with patch("app.iso.extractor.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await check_7z_available()

        assert result is False

    @pytest.mark.asyncio
    async def test_7z_not_found(self):
        """Test check_7z_available returns False when 7z is not found."""
        with patch(
            "app.iso.extractor.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("7z not found"),
        ):
            result = await check_7z_available()

        assert result is False
