"""Tests for providers module."""
from __future__ import annotations

import pytest

from app.providers import (
    ProviderActionError,
    node_action_command,
    supported_node_actions,
    supports_node_actions,
)


class TestSupportsNodeActions:
    """Tests for supports_node_actions function."""

    def test_docker_supports_node_actions(self):
        """Test that docker provider supports node actions."""
        assert supports_node_actions("docker") is True

    def test_libvirt_does_not_support_node_actions(self):
        """Test that libvirt provider does not support node actions."""
        assert supports_node_actions("libvirt") is False

    def test_unknown_provider_does_not_support_node_actions(self):
        """Test that unknown provider does not support node actions."""
        assert supports_node_actions("unknown") is False
        assert supports_node_actions("") is False


class TestSupportedNodeActions:
    """Tests for supported_node_actions function."""

    def test_docker_supported_actions(self):
        """Test docker provider supported actions."""
        actions = supported_node_actions("docker")
        assert "start" in actions
        assert "stop" in actions

    def test_libvirt_no_supported_actions(self):
        """Test libvirt provider has no supported actions."""
        actions = supported_node_actions("libvirt")
        assert len(actions) == 0

    def test_unknown_provider_no_supported_actions(self):
        """Test unknown provider has no supported actions."""
        actions = supported_node_actions("unknown")
        assert len(actions) == 0


class TestNodeActionCommand:
    """Tests for node_action_command function.

    Note: DockerProvider handles node actions via the agent API, not CLI commands.
    These tests verify that the function correctly raises ProviderActionError
    since CLI-based node actions are not supported for Docker provider.
    """

    def test_docker_start_raises_error(self):
        """Test docker start raises error (handled by agent API instead)."""
        with pytest.raises(ProviderActionError, match="agent API"):
            node_action_command("docker", "test-lab", "start", "r1")

    def test_docker_stop_raises_error(self):
        """Test docker stop raises error (handled by agent API instead)."""
        with pytest.raises(ProviderActionError, match="agent API"):
            node_action_command("docker", "test-lab", "stop", "r1")

    def test_libvirt_raises_error(self):
        """Test libvirt provider raises error for any action."""
        with pytest.raises(ProviderActionError):
            node_action_command("libvirt", "test-lab", "start", "r1")

    def test_unknown_provider_raises_error(self):
        """Test unknown provider raises error."""
        with pytest.raises(ProviderActionError):
            node_action_command("unknown", "test-lab", "start", "r1")


class TestProviderActionError:
    """Tests for ProviderActionError exception."""

    def test_error_is_value_error(self):
        """Test that ProviderActionError is a ValueError."""
        error = ProviderActionError("test error")
        assert isinstance(error, ValueError)

    def test_error_message(self):
        """Test error message is preserved."""
        error = ProviderActionError("Custom error message")
        assert str(error) == "Custom error message"
