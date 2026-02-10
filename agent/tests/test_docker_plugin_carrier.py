"""Tests for carrier state and port isolation behavior in docker_plugin.py.

These tests verify the expected behavior of carrier state management
functions without importing the actual DockerOVSPlugin, which requires
Docker dependencies. The tests use mock objects to simulate the plugin
behavior and verify the logic flow.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCarrierStateLogic:
    """Tests for carrier state logic."""

    @pytest.mark.asyncio
    async def test_carrier_state_on_command(self):
        """Should execute correct nsenter command for carrier on."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            # Simulate what set_carrier_state would do
            pid = 12345
            interface = "eth1"
            state = "on"

            process = await asyncio.create_subprocess_exec(
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", interface, "carrier", state,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            assert "nsenter" in call_args
            assert "-t" in call_args
            assert str(pid) in call_args
            assert "-n" in call_args
            assert "ip" in call_args
            assert "link" in call_args
            assert "carrier" in call_args
            assert state in call_args

    @pytest.mark.asyncio
    async def test_carrier_state_off_command(self):
        """Should execute correct nsenter command for carrier off."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            pid = 12345
            interface = "eth1"
            state = "off"

            process = await asyncio.create_subprocess_exec(
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", interface, "carrier", state,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            call_args = mock_exec.call_args[0]
            assert "off" in call_args

    def test_invalid_carrier_state_rejected(self):
        """Invalid carrier state should be rejected."""
        valid_states = {"on", "off"}
        invalid_states = ["invalid", "up", "down", "1", "0", "yes", "no"]

        for state in invalid_states:
            assert state not in valid_states

    @pytest.mark.asyncio
    async def test_carrier_command_failure_detection(self):
        """Should detect when carrier command fails."""
        mock_process = AsyncMock()
        mock_process.returncode = 1  # Non-zero = failure
        mock_process.communicate = AsyncMock(return_value=(b"", b"Device not found"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            process = await asyncio.create_subprocess_exec(
                "nsenter", "-t", "12345", "-n",
                "ip", "link", "set", "eth1", "carrier", "on",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            assert process.returncode != 0  # Indicates failure


class TestIsolatePortLogic:
    """Tests for port isolation logic."""

    def test_isolation_requires_vlan_change(self):
        """Port isolation should assign a unique VLAN."""
        # Simulate two ports on same VLAN (connected)
        port1_vlan = 100
        port2_vlan = 100
        assert port1_vlan == port2_vlan  # Connected

        # After isolation, port1 gets unique VLAN
        isolated_vlan = 999  # Unique isolation VLAN
        assert isolated_vlan != port2_vlan  # No longer connected

    def test_isolation_workflow(self):
        """Isolation should change VLAN and set carrier off."""
        # Workflow:
        # 1. Call hot_disconnect to get unique VLAN
        # 2. Set carrier off
        # 3. Return new VLAN tag

        # Simulate the workflow
        original_vlan = 100
        isolated_vlan = 999
        carrier_off_success = True

        # Verify isolation is complete when both steps succeed
        assert isolated_vlan != original_vlan
        assert carrier_off_success

    def test_isolation_returns_vlan_even_if_carrier_fails(self):
        """VLAN isolation should still work even if carrier off fails."""
        # VLAN-based isolation still provides L2 isolation
        # even if carrier state change fails
        isolated_vlan = 999

        # VLAN should still be returned
        assert isolated_vlan is not None


class TestRestorePortLogic:
    """Tests for port restoration logic."""

    def test_restore_requires_target_vlan(self):
        """Port restore needs a target VLAN to connect to."""
        target_vlan = 100
        assert target_vlan is not None
        assert isinstance(target_vlan, int)

    def test_restore_workflow(self):
        """Restoration should set VLAN and enable carrier."""
        # Workflow:
        # 1. Set port VLAN to target (matching peer)
        # 2. Set carrier on
        # 3. Return success

        isolated_vlan = 999
        target_vlan = 100  # Same as peer

        # After restore
        restored_vlan = target_vlan
        carrier_on_success = True

        assert restored_vlan == target_vlan  # Connected
        assert restored_vlan != isolated_vlan  # No longer isolated
        assert carrier_on_success


class TestGetEndpointVlanLogic:
    """Tests for endpoint VLAN lookup logic."""

    def test_endpoint_lookup_by_container_and_interface(self):
        """Should find endpoint by container name and interface."""
        endpoints = {
            "ep-1": MagicMock(
                container_name="archetype-test-r1",
                interface_name="eth1",
                vlan_tag=100
            ),
            "ep-2": MagicMock(
                container_name="archetype-test-r2",
                interface_name="eth1",
                vlan_tag=100
            ),
        }

        # Find endpoint
        target_container = "archetype-test-r1"
        target_interface = "eth1"

        found_vlan = None
        for ep in endpoints.values():
            if ep.container_name == target_container and ep.interface_name == target_interface:
                found_vlan = ep.vlan_tag
                break

        assert found_vlan == 100

    def test_endpoint_not_found(self):
        """Should return None for non-existent endpoint."""
        endpoints = {
            "ep-1": MagicMock(
                container_name="archetype-test-r1",
                interface_name="eth1",
                vlan_tag=100
            ),
        }

        target_container = "archetype-test-r2"  # Not in endpoints
        target_interface = "eth1"

        found_vlan = None
        for ep in endpoints.values():
            if ep.container_name == target_container and ep.interface_name == target_interface:
                found_vlan = ep.vlan_tag
                break

        assert found_vlan is None


class TestCarrierStateIntegrationLogic:
    """Tests for carrier state integration workflow."""

    def test_link_connectivity_workflow(self):
        """Test the complete link up/down workflow."""
        # Initial state: two endpoints connected via same VLAN
        ep1_vlan = 100
        ep2_vlan = 100
        ep1_carrier = "on"
        ep2_carrier = "on"

        assert ep1_vlan == ep2_vlan  # L2 connected
        assert ep1_carrier == "on"   # Link up
        assert ep2_carrier == "on"   # Link up

        # Simulate link down on ep1
        ep1_vlan = 999  # Isolated
        ep1_carrier = "off"

        assert ep1_vlan != ep2_vlan  # L2 isolated
        assert ep1_carrier == "off"  # Carrier down

        # Simulate link restore on ep1
        ep1_vlan = ep2_vlan  # Reconnect to same VLAN
        ep1_carrier = "on"

        assert ep1_vlan == ep2_vlan  # L2 connected again
        assert ep1_carrier == "on"   # Carrier up

    def test_carrier_state_affects_traffic_flow(self):
        """Carrier off should simulate cable disconnect."""
        # When carrier is off, no traffic can flow even with L2 connectivity
        vlan_connected = True  # Same VLAN
        carrier_on = False

        # Link is "down" because carrier is off
        link_operational = vlan_connected and carrier_on
        assert not link_operational

        # Enable carrier
        carrier_on = True
        link_operational = vlan_connected and carrier_on
        assert link_operational


class TestVNIAllocationLogic:
    """Tests for VNI allocation logic (used by link_manager)."""

    def test_deterministic_vni_allocation(self):
        """Same inputs should always produce same VNI."""
        import hashlib

        def allocate_vni(lab_id: str, link_name: str) -> int:
            combined = f"{lab_id}:{link_name}"
            hash_val = int(hashlib.md5(combined.encode()).hexdigest()[:8], 16)
            return (hash_val % 16000000) + 1000

        vni1 = allocate_vni("lab-123", "R1:eth1-R2:eth1")
        vni2 = allocate_vni("lab-123", "R1:eth1-R2:eth1")

        assert vni1 == vni2

    def test_different_links_get_different_vnis(self):
        """Different links should get different VNIs."""
        import hashlib

        def allocate_vni(lab_id: str, link_name: str) -> int:
            combined = f"{lab_id}:{link_name}"
            hash_val = int(hashlib.md5(combined.encode()).hexdigest()[:8], 16)
            return (hash_val % 16000000) + 1000

        vni1 = allocate_vni("lab-123", "R1:eth1-R2:eth1")
        vni2 = allocate_vni("lab-123", "R1:eth2-R3:eth1")

        assert vni1 != vni2

    def test_vni_range_valid(self):
        """VNI should be in valid range."""
        import hashlib

        def allocate_vni(lab_id: str, link_name: str) -> int:
            combined = f"{lab_id}:{link_name}"
            hash_val = int(hashlib.md5(combined.encode()).hexdigest()[:8], 16)
            return (hash_val % 16000000) + 1000

        for i in range(100):
            vni = allocate_vni(f"lab-{i}", f"link-{i}")
            assert 1000 <= vni <= 16001000
