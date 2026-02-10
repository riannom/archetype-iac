"""ISO extraction utilities using 7z."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExtractionProgress:
    """Progress information for file extraction."""
    filename: str
    bytes_extracted: int
    total_bytes: int
    percent: int


class ISOExtractor:
    """Extract files from ISO images using 7z.

    Uses 7z (p7zip) for extraction, which doesn't require root
    and handles large ISOs efficiently.
    """

    def __init__(self, iso_path: Path):
        """Initialize extractor for an ISO file.

        Args:
            iso_path: Path to the ISO file
        """
        self.iso_path = iso_path
        self._file_list: list[dict] | None = None
        self._temp_dir: Path | None = None

    async def list_files(self) -> list[dict]:
        """List all files in the ISO with metadata.

        Returns:
            List of dicts with 'name', 'size', 'is_dir' keys
        """
        if self._file_list is not None:
            return self._file_list

        cmd = ["7z", "l", "-slt", str(self.iso_path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "7z listing failed"
            raise RuntimeError(f"Failed to list ISO contents: {error_msg}")

        # Parse 7z -slt output
        self._file_list = []
        current_entry: dict = {}

        for line in stdout.decode().split("\n"):
            line = line.strip()
            if not line:
                if current_entry and "Path" in current_entry:
                    self._file_list.append({
                        "name": current_entry.get("Path", ""),
                        "size": int(current_entry.get("Size", 0)),
                        "is_dir": current_entry.get("Attributes", "").startswith("D"),
                    })
                current_entry = {}
            elif "=" in line:
                key, _, value = line.partition(" = ")
                current_entry[key.strip()] = value.strip()

        # Don't forget last entry
        if current_entry and "Path" in current_entry:
            self._file_list.append({
                "name": current_entry.get("Path", ""),
                "size": int(current_entry.get("Size", 0)),
                "is_dir": current_entry.get("Attributes", "").startswith("D"),
            })

        return self._file_list

    async def get_file_names(self) -> list[str]:
        """Get just the file names (not dirs) in the ISO."""
        files = await self.list_files()
        return [f["name"] for f in files if not f["is_dir"]]

    async def read_file(self, file_path: str) -> bytes:
        """Read a file from the ISO into memory.

        Args:
            file_path: Path within the ISO

        Returns:
            File contents as bytes
        """
        cmd = ["7z", "x", "-so", str(self.iso_path), file_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "7z extraction failed"
            raise RuntimeError(f"Failed to read {file_path}: {error_msg}")

        return stdout

    async def read_text_file(self, file_path: str, encoding: str = "utf-8") -> str:
        """Read a text file from the ISO.

        Args:
            file_path: Path within the ISO
            encoding: Text encoding (default utf-8)

        Returns:
            File contents as string
        """
        content = await self.read_file(file_path)
        return content.decode(encoding)

    async def extract_file(
        self,
        file_path: str,
        dest_path: Path,
        progress_callback: Optional[Callable[[ExtractionProgress], None]] = None,
        timeout_seconds: int = 1800,
    ) -> Path:
        """Extract a single file from the ISO to disk.

        Args:
            file_path: Path within the ISO
            dest_path: Destination path on disk
            progress_callback: Optional callback for progress updates
            timeout_seconds: Extraction timeout (default 30 min)

        Returns:
            Path to extracted file
        """
        # Get file size for progress tracking
        files = await self.list_files()
        file_info = next((f for f in files if f["name"] == file_path), None)
        total_bytes = file_info["size"] if file_info else 0

        # Create parent directory
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Extract to temp file first, then move
        temp_fd, temp_path = tempfile.mkstemp(suffix=".tmp", dir=dest_path.parent)
        os.close(temp_fd)

        try:
            # Use 7z to extract directly to stdout, pipe to file
            cmd = ["7z", "x", "-so", str(self.iso_path), file_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            bytes_written = 0
            chunk_size = 1024 * 1024  # 1MB chunks

            with open(temp_path, "wb") as f:
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            proc.stdout.read(chunk_size),
                            timeout=60,  # 60s per chunk
                        )
                    except asyncio.TimeoutError:
                        proc.kill()
                        raise TimeoutError(f"Extraction stalled for {file_path}")

                    if not chunk:
                        break

                    f.write(chunk)
                    bytes_written += len(chunk)

                    if progress_callback and total_bytes > 0:
                        percent = min(99, int(bytes_written / total_bytes * 100))
                        progress_callback(ExtractionProgress(
                            filename=file_path,
                            bytes_extracted=bytes_written,
                            total_bytes=total_bytes,
                            percent=percent,
                        ))

            # Wait for process to complete
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else "7z extraction failed"
                raise RuntimeError(f"Failed to extract {file_path}: {error_msg}")

            # Move temp file to final destination
            shutil.move(temp_path, dest_path)

            if progress_callback:
                progress_callback(ExtractionProgress(
                    filename=file_path,
                    bytes_extracted=total_bytes,
                    total_bytes=total_bytes,
                    percent=100,
                ))

            return dest_path

        except Exception:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    async def extract_files(
        self,
        file_paths: list[str],
        dest_dir: Path,
        progress_callback: Optional[Callable[[str, ExtractionProgress], None]] = None,
        timeout_seconds: int = 1800,
    ) -> dict[str, Path]:
        """Extract multiple files from the ISO.

        Args:
            file_paths: List of paths within the ISO
            dest_dir: Destination directory
            progress_callback: Callback with (file_path, progress) args
            timeout_seconds: Per-file timeout

        Returns:
            Dict mapping ISO paths to extracted file paths
        """
        results = {}

        for file_path in file_paths:
            dest_file = dest_dir / Path(file_path).name

            def file_progress(p: ExtractionProgress):
                if progress_callback:
                    progress_callback(file_path, p)

            results[file_path] = await self.extract_file(
                file_path,
                dest_file,
                progress_callback=file_progress,
                timeout_seconds=timeout_seconds,
            )

        return results

    def get_temp_dir(self) -> Path:
        """Get or create a temporary directory for extractions."""
        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="iso_extract_"))
        return self._temp_dir

    def cleanup(self):
        """Clean up any temporary files."""
        if self._temp_dir and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


async def check_7z_available() -> bool:
    """Check if 7z is available on the system."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "7z", "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False
