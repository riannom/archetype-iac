"""Tests for agent/version.py — VERSION file, git tag, commit SHA resolution."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent import version


# ---------------------------------------------------------------------------
# get_version()
# ---------------------------------------------------------------------------


def test_get_version_from_version_file() -> None:
    """VERSION file is the primary source for version."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = True
    mock_vf.read_text.return_value = "2.5.0\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        result = version.get_version()
        assert result == "2.5.0"


def test_get_version_empty_file_falls_to_git() -> None:
    """Empty VERSION file triggers git fallback."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = True
    mock_vf.read_text.return_value = "   \n"

    mock_git = MagicMock()
    mock_git.returncode = 0
    mock_git.stdout = "v1.0.0\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        with patch("agent.version.subprocess.run", return_value=mock_git):
            result = version.get_version()
            assert result == "1.0.0"


def test_get_version_git_tag_strips_v_prefix() -> None:
    """Git tag 'v1.2.3' should return '1.2.3'."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = False

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "v1.2.3\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_version()
            assert result == "1.2.3"


def test_get_version_git_tag_no_prefix() -> None:
    """Git tag '2.0.0' without v prefix works."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = False

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "2.0.0\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_version()
            assert result == "2.0.0"


def test_get_version_git_failure_returns_default() -> None:
    """When both VERSION file and git fail, return '0.0.0'."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = False

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        with patch("agent.version.subprocess.run", side_effect=FileNotFoundError):
            result = version.get_version()
            assert result == "0.0.0"


def test_get_version_git_nonzero_returncode() -> None:
    """Non-zero git returncode falls through to default."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = False

    mock_result = MagicMock()
    mock_result.returncode = 128
    mock_result.stdout = ""

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_version()
            assert result == "0.0.0"


def test_get_version_version_file_read_error() -> None:
    """Exception reading VERSION file falls through to git."""
    mock_vf = MagicMock()
    mock_vf.exists.return_value = True
    mock_vf.read_text.side_effect = PermissionError("denied")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "v5.0.0\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_vf)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_version()
            assert result == "5.0.0"


# ---------------------------------------------------------------------------
# get_commit()
# ---------------------------------------------------------------------------


def test_get_commit_from_env(monkeypatch) -> None:
    """ARCHETYPE_GIT_SHA env var is the primary source."""
    monkeypatch.setenv("ARCHETYPE_GIT_SHA", "abc123def")
    assert version.get_commit() == "abc123def"


def test_get_commit_env_whitespace(monkeypatch) -> None:
    """Env var is stripped of whitespace."""
    monkeypatch.setenv("ARCHETYPE_GIT_SHA", "  abc123  ")
    assert version.get_commit() == "abc123"


def test_get_commit_from_git_sha_file(monkeypatch) -> None:
    """GIT_SHA file is the secondary source."""
    monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)

    mock_sha_file = MagicMock()
    mock_sha_file.exists.return_value = True
    mock_sha_file.read_text.return_value = "deadbeef1234\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_sha_file)
        result = version.get_commit()
        assert result == "deadbeef1234"


def test_get_commit_empty_env_falls_through(monkeypatch) -> None:
    """Empty env var falls through to file."""
    monkeypatch.setenv("ARCHETYPE_GIT_SHA", "")

    mock_sha_file = MagicMock()
    mock_sha_file.exists.return_value = True
    mock_sha_file.read_text.return_value = "cafebabe\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_sha_file)
        result = version.get_commit()
        assert result == "cafebabe"


def test_get_commit_git_rev_parse_fallback(monkeypatch) -> None:
    """git rev-parse HEAD is the tertiary source."""
    monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)

    mock_sha_file = MagicMock()
    mock_sha_file.exists.return_value = False

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "1234567890abcdef\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_sha_file)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_commit()
            assert result == "1234567890abcdef"


def test_get_commit_total_failure_returns_unknown(monkeypatch) -> None:
    """When all sources fail, return 'unknown'."""
    monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)

    mock_sha_file = MagicMock()
    mock_sha_file.exists.return_value = False

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_sha_file)
        with patch("agent.version.subprocess.run", side_effect=FileNotFoundError):
            result = version.get_commit()
            assert result == "unknown"


def test_get_commit_git_sha_file_read_error(monkeypatch) -> None:
    """Exception reading GIT_SHA file falls through to git."""
    monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)

    mock_sha_file = MagicMock()
    mock_sha_file.exists.return_value = True
    mock_sha_file.read_text.side_effect = PermissionError("denied")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "fallbacksha\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_sha_file)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_commit()
            assert result == "fallbacksha"


def test_get_commit_empty_git_sha_file(monkeypatch) -> None:
    """Empty GIT_SHA file falls through to git."""
    monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)

    mock_sha_file = MagicMock()
    mock_sha_file.exists.return_value = True
    mock_sha_file.read_text.return_value = "   \n"

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "gitsha123\n"

    with patch("agent.version.Path") as MockPath:
        MockPath.return_value.parent.__truediv__ = MagicMock(return_value=mock_sha_file)
        with patch("agent.version.subprocess.run", return_value=mock_result):
            result = version.get_commit()
            assert result == "gitsha123"


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------


def test_module_level_version_cached() -> None:
    """__version__ is set at import time."""
    assert isinstance(version.__version__, str)
    assert len(version.__version__) > 0
