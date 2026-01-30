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

    def test_clab_supports_node_actions(self):
        """Test that clab provider supports node actions."""
        assert supports_node_actions("clab") is True

    def test_libvirt_does_not_support_node_actions(self):
        """Test that libvirt provider does not support node actions."""
        assert supports_node_actions("libvirt") is False

    def test_unknown_provider_does_not_support_node_actions(self):
        """Test that unknown provider does not support node actions."""
        assert supports_node_actions("unknown") is False
        assert supports_node_actions("") is False


class TestSupportedNodeActions:
    """Tests for supported_node_actions function."""

    def test_clab_supported_actions(self):
        """Test clab provider supported actions."""
        actions = supported_node_actions("clab")
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
    """Tests for node_action_command function."""

    def test_clab_start_command(self):
        """Test clab start command generation."""
        commands = node_action_command("clab", "test-lab", "start", "r1")

        assert len(commands) == 2  # create command + deploy command

        # First command should be netlab create
        assert "netlab" in commands[0]
        assert "create" in commands[0]

        # Second command should be clab deploy with node filter
        assert "clab" in commands[1]
        assert "deploy" in commands[1]
        assert "--node-filter" in commands[1]
        assert "r1" in commands[1]

    def test_clab_stop_command(self):
        """Test clab stop command generation."""
        commands = node_action_command("clab", "test-lab", "stop", "r1")

        assert len(commands) == 2

        # Second command should be clab destroy with node filter
        assert "clab" in commands[1]
        assert "destroy" in commands[1]
        assert "--node-filter" in commands[1]
        assert "r1" in commands[1]

    def test_clab_unsupported_action(self):
        """Test clab with unsupported action raises error."""
        with pytest.raises(ProviderActionError):
            node_action_command("clab", "test-lab", "restart", "r1")

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
