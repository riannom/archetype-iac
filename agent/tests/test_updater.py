"""Tests for the agent updater module.

Covers deployment mode detection, commit SHA validation, systemd/docker
update flows, progress reporting, and rollback sentinel logic.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.updater import (
    DeploymentMode,
    ROLLBACK_SENTINEL,
    _clear_rollback_info,
    _save_rollback_info,
    check_and_rollback,
    detect_deployment_mode,
    get_agent_root,
    is_commit_sha,
    perform_docker_update,
    perform_systemd_update,
    report_progress,
)


# ---------------------------------------------------------------------------
# 1. Deployment mode detection
# ---------------------------------------------------------------------------


class TestDetectDeploymentMode:
    """Tests for detect_deployment_mode()."""

    def test_docker_mode_detected(self, monkeypatch):
        """When /.dockerenv exists, mode should be DOCKER."""
        monkeypatch.setattr(
            "agent.updater._is_running_in_docker", lambda: True,
        )
        assert detect_deployment_mode() == DeploymentMode.DOCKER

    def test_systemd_mode_detected(self, monkeypatch):
        """When not Docker but systemd manages the agent, mode should be SYSTEMD."""
        monkeypatch.setattr(
            "agent.updater._is_running_in_docker", lambda: False,
        )
        monkeypatch.setattr(
            "agent.updater._is_managed_by_systemd", lambda: True,
        )
        assert detect_deployment_mode() == DeploymentMode.SYSTEMD

    def test_unknown_mode_fallback(self, monkeypatch):
        """When neither Docker nor systemd is detected, mode should be UNKNOWN."""
        monkeypatch.setattr(
            "agent.updater._is_running_in_docker", lambda: False,
        )
        monkeypatch.setattr(
            "agent.updater._is_managed_by_systemd", lambda: False,
        )
        assert detect_deployment_mode() == DeploymentMode.UNKNOWN


# ---------------------------------------------------------------------------
# 2. is_commit_sha() helper
# ---------------------------------------------------------------------------


class TestIsCommitSha:
    """Tests for is_commit_sha()."""

    def test_valid_short_sha(self):
        """7-character hex string is a valid short SHA."""
        assert is_commit_sha("abcdef0") is True

    def test_valid_full_sha(self):
        """40-character hex string is a valid full SHA."""
        sha = "a" * 40
        assert is_commit_sha(sha) is True

    def test_too_short(self):
        """6-character hex string is too short to be a SHA."""
        assert is_commit_sha("abcdef") is False

    def test_non_hex_characters(self):
        """Strings with non-hex characters are not SHAs."""
        assert is_commit_sha("abcdeg0") is False
        assert is_commit_sha("xyz1234") is False

    def test_version_string_not_sha(self):
        """Version strings like '0.3.7' should not be detected as SHAs."""
        assert is_commit_sha("0.3.7") is False
        assert is_commit_sha("v0.3.7") is False


# ---------------------------------------------------------------------------
# 3. perform_systemd_update() happy path
# ---------------------------------------------------------------------------


def _make_subprocess_result(returncode=0, stdout="", stderr=""):
    """Create a subprocess.CompletedProcess-like result."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestPerformSystemdUpdateHappyPath:
    """Tests for perform_systemd_update() successful flows."""

    @pytest.mark.asyncio
    async def test_version_tag_update(self, monkeypatch, tmp_path):
        """Update with a version tag (e.g. '0.3.7') should succeed."""
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", tmp_path / "sentinel.json")
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        call_count = 0

        async def fake_to_thread(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            cmd = args[0] if args else []
            if cmd[:2] == ["git", "rev-parse"] and "HEAD" in cmd:
                return _make_subprocess_result(stdout="abc1234deadbeef\n")
            if cmd[:2] == ["git", "fetch"]:
                return _make_subprocess_result()
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                # First call with v0.3.7 should succeed
                ref = cmd[3] if len(cmd) > 3 else ""
                if ref.startswith("v"):
                    return _make_subprocess_result(stdout="def5678\n")
                return _make_subprocess_result(returncode=1)
            if cmd[:2] == ["git", "checkout"]:
                return _make_subprocess_result()
            if "pip" in cmd:
                return _make_subprocess_result()
            return _make_subprocess_result()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        # Mock httpx.AsyncClient to avoid real HTTP calls
        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)

        # Mock asyncio.create_task to prevent actual restart
        monkeypatch.setattr(asyncio, "create_task", lambda coro: coro.close())

        result = await perform_systemd_update(
            job_id="job-1",
            agent_id="agent-1",
            target_version="0.3.7",
            callback_url="http://localhost/callback",
        )

        assert result is True
        # Progress reports should have been made
        assert mock_client.post.call_count > 0

    @pytest.mark.asyncio
    async def test_commit_sha_update(self, monkeypatch, tmp_path):
        """Update with a commit SHA should succeed."""
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", tmp_path / "sentinel.json")
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        target_sha = "abcdef1234567"

        async def fake_to_thread(fn, *args, **kwargs):
            cmd = args[0] if args else []
            if cmd[:2] == ["git", "rev-parse"] and "HEAD" in cmd:
                return _make_subprocess_result(stdout="oldsha00\n")
            if cmd[:2] == ["git", "fetch"]:
                return _make_subprocess_result()
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _make_subprocess_result(stdout=f"{target_sha}\n")
            if cmd[:2] == ["git", "checkout"]:
                return _make_subprocess_result()
            if "pip" in cmd:
                return _make_subprocess_result()
            return _make_subprocess_result()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)
        monkeypatch.setattr(asyncio, "create_task", lambda coro: coro.close())

        result = await perform_systemd_update(
            job_id="job-2",
            agent_id="agent-1",
            target_version=target_sha,
            callback_url="http://localhost/callback",
        )

        assert result is True


# ---------------------------------------------------------------------------
# 4. Error paths
# ---------------------------------------------------------------------------


class TestPerformSystemdUpdateErrors:
    """Tests for perform_systemd_update() error flows."""

    @pytest.mark.asyncio
    async def test_git_fetch_fails(self, monkeypatch, tmp_path):
        """Update should return False when git fetch fails."""
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", tmp_path / "sentinel.json")
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        async def fake_to_thread(fn, *args, **kwargs):
            cmd = args[0] if args else []
            if cmd[:2] == ["git", "rev-parse"] and "HEAD" in cmd:
                return _make_subprocess_result(stdout="abc1234\n")
            if cmd[:2] == ["git", "fetch"]:
                return _make_subprocess_result(returncode=1, stderr="fatal: could not read")
            return _make_subprocess_result()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)

        result = await perform_systemd_update(
            job_id="job-err-1",
            agent_id="agent-1",
            target_version="0.3.7",
            callback_url="http://localhost/callback",
        )

        assert result is False
        # Verify a "failed" progress report was sent
        calls = mock_client.post.call_args_list
        failed_calls = [c for c in calls if c[1].get("json", {}).get("status") == "failed"]
        assert len(failed_calls) >= 1

    @pytest.mark.asyncio
    async def test_version_not_found(self, monkeypatch, tmp_path):
        """Update should return False when target version cannot be resolved."""
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", tmp_path / "sentinel.json")
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        async def fake_to_thread(fn, *args, **kwargs):
            cmd = args[0] if args else []
            if cmd[:2] == ["git", "rev-parse"] and "HEAD" in cmd:
                return _make_subprocess_result(stdout="abc1234\n")
            if cmd[:2] == ["git", "fetch"]:
                return _make_subprocess_result()
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                # All ref formats fail
                return _make_subprocess_result(returncode=1, stderr="not found")
            return _make_subprocess_result()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)

        result = await perform_systemd_update(
            job_id="job-err-2",
            agent_id="agent-1",
            target_version="99.99.99",
            callback_url="http://localhost/callback",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_pip_install_fails(self, monkeypatch, tmp_path):
        """Update should return False when pip install fails."""
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", tmp_path / "sentinel.json")
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        async def fake_to_thread(fn, *args, **kwargs):
            cmd = args[0] if args else []
            if cmd[:2] == ["git", "rev-parse"] and "HEAD" in cmd:
                return _make_subprocess_result(stdout="abc1234\n")
            if cmd[:2] == ["git", "fetch"]:
                return _make_subprocess_result()
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _make_subprocess_result(stdout="def5678\n")
            if cmd[:2] == ["git", "checkout"]:
                return _make_subprocess_result()
            if "pip" in cmd:
                return _make_subprocess_result(returncode=1, stderr="Could not install")
            return _make_subprocess_result()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)

        result = await perform_systemd_update(
            job_id="job-err-3",
            agent_id="agent-1",
            target_version="0.3.7",
            callback_url="http://localhost/callback",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_during_update(self, monkeypatch, tmp_path):
        """Update should return False when a subprocess times out."""
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", tmp_path / "sentinel.json")
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        async def fake_to_thread(fn, *args, **kwargs):
            cmd = args[0] if args else []
            if cmd[:2] == ["git", "rev-parse"] and "HEAD" in cmd:
                return _make_subprocess_result(stdout="abc1234\n")
            if cmd[:2] == ["git", "fetch"]:
                raise subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=60)
            return _make_subprocess_result()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)

        result = await perform_systemd_update(
            job_id="job-err-4",
            agent_id="agent-1",
            target_version="0.3.7",
            callback_url="http://localhost/callback",
        )

        assert result is False


# ---------------------------------------------------------------------------
# 5. perform_docker_update()
# ---------------------------------------------------------------------------


class TestPerformDockerUpdate:
    """Tests for perform_docker_update()."""

    @pytest.mark.asyncio
    async def test_returns_false(self, monkeypatch):
        """Docker update should return False (not self-updatable)."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("agent.updater.httpx.AsyncClient", lambda **kw: mock_client)

        result = await perform_docker_update(
            job_id="job-docker",
            agent_id="agent-1",
            target_version="0.4.0",
            callback_url="http://localhost/callback",
        )

        assert result is False
        # Should have reported "failed" with guidance message
        mock_client.post.assert_called_once()
        call_payload = mock_client.post.call_args[1]["json"]
        assert call_payload["status"] == "failed"
        assert "Docker" in call_payload["error_message"]


# ---------------------------------------------------------------------------
# 6. report_progress()
# ---------------------------------------------------------------------------


class TestReportProgress:
    """Tests for report_progress()."""

    @pytest.mark.asyncio
    async def test_sends_progress_payload(self):
        """report_progress should POST the correct payload."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        await report_progress(
            client=mock_client,
            callback_url="http://localhost/progress",
            job_id="job-rp-1",
            agent_id="agent-1",
            status="downloading",
            progress_percent=42,
            error_message=None,
        )

        mock_client.post.assert_called_once_with(
            "http://localhost/progress",
            json={
                "job_id": "job-rp-1",
                "agent_id": "agent-1",
                "status": "downloading",
                "progress_percent": 42,
                "error_message": None,
            },
        )

    @pytest.mark.asyncio
    async def test_network_error_does_not_raise(self):
        """report_progress should swallow network errors gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        # Should not raise
        await report_progress(
            client=mock_client,
            callback_url="http://localhost/progress",
            job_id="job-rp-2",
            agent_id="agent-1",
            status="installing",
            progress_percent=60,
        )


# ---------------------------------------------------------------------------
# 7. Rollback sentinel
# ---------------------------------------------------------------------------


class TestRollbackSentinel:
    """Tests for rollback sentinel save/clear/check_and_rollback."""

    def test_save_creates_sentinel_file(self, monkeypatch, tmp_path):
        """_save_rollback_info should write a JSON sentinel file."""
        sentinel = tmp_path / "sentinel.json"
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)

        _save_rollback_info("abc1234", "v0.3.7")

        assert sentinel.exists()
        data = json.loads(sentinel.read_text())
        assert data["previous_ref"] == "abc1234"
        assert data["target_ref"] == "v0.3.7"
        assert "timestamp" in data

    def test_clear_removes_sentinel_file(self, monkeypatch, tmp_path):
        """_clear_rollback_info should remove the sentinel file."""
        sentinel = tmp_path / "sentinel.json"
        sentinel.write_text("{}")
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)

        _clear_rollback_info()

        assert not sentinel.exists()

    def test_clear_noop_when_no_sentinel(self, monkeypatch, tmp_path):
        """_clear_rollback_info should be a no-op when sentinel does not exist."""
        sentinel = tmp_path / "sentinel.json"
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)

        # Should not raise
        _clear_rollback_info()
        assert not sentinel.exists()

    def test_check_and_rollback_matching_target_clears(self, monkeypatch, tmp_path):
        """When HEAD matches target, sentinel should be cleared without rollback."""
        sentinel = tmp_path / "sentinel.json"
        target_sha = "deadbeef" * 5  # 40-char SHA
        sentinel.write_text(json.dumps({
            "previous_ref": "oldref123",
            "target_ref": target_sha,
            "timestamp": "2026-01-01T00:00:00Z",
        }))
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return _make_subprocess_result(stdout=f"{target_sha}\n")
            if "rev-parse" in cmd and "--verify" in cmd:
                return _make_subprocess_result(stdout=f"{target_sha}\n")
            return _make_subprocess_result()

        monkeypatch.setattr(subprocess, "run", fake_run)

        check_and_rollback()

        assert not sentinel.exists()

    def test_check_and_rollback_mismatch_rolls_back(self, monkeypatch, tmp_path):
        """When HEAD doesn't match target, should checkout previous_ref."""
        sentinel = tmp_path / "sentinel.json"
        sentinel.write_text(json.dumps({
            "previous_ref": "oldref123",
            "target_ref": "v0.3.7",
            "timestamp": "2026-01-01T00:00:00Z",
        }))
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)
        monkeypatch.setattr("agent.updater.get_agent_root", lambda: tmp_path)

        checkout_calls = []

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return _make_subprocess_result(stdout="completely_different_sha\n")
            if "rev-parse" in cmd and "--verify" in cmd:
                return _make_subprocess_result(stdout="resolved_target_sha\n")
            if cmd[:2] == ["git", "checkout"]:
                checkout_calls.append(cmd)
                return _make_subprocess_result()
            return _make_subprocess_result()

        monkeypatch.setattr(subprocess, "run", fake_run)

        check_and_rollback()

        assert not sentinel.exists()
        assert len(checkout_calls) == 1
        assert checkout_calls[0] == ["git", "checkout", "oldref123"]

    def test_check_and_rollback_no_sentinel_noop(self, monkeypatch, tmp_path):
        """When no sentinel file exists, check_and_rollback is a no-op."""
        sentinel = tmp_path / "sentinel.json"
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)

        # Should not raise or do anything
        check_and_rollback()

    def test_check_and_rollback_corrupted_file(self, monkeypatch, tmp_path):
        """A corrupted sentinel file should be cleared without crashing."""
        sentinel = tmp_path / "sentinel.json"
        sentinel.write_text("this is not valid json{{{")
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)

        # Should not raise
        check_and_rollback()

        assert not sentinel.exists()

    def test_check_and_rollback_missing_fields(self, monkeypatch, tmp_path):
        """Sentinel with missing required fields should be cleared."""
        sentinel = tmp_path / "sentinel.json"
        sentinel.write_text(json.dumps({
            "previous_ref": "abc123",
            # "target_ref" is missing
            "timestamp": "2026-01-01T00:00:00Z",
        }))
        monkeypatch.setattr("agent.updater.ROLLBACK_SENTINEL", sentinel)

        check_and_rollback()

        assert not sentinel.exists()
