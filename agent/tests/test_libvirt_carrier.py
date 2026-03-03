"""Unit tests for VM carrier propagation (domif-setlink) and monitored port cache."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.providers.libvirt as libvirt_module


def _make_provider() -> libvirt_module.LibvirtProvider:
    """Create a minimal LibvirtProvider without libvirt/Docker dependencies."""
    p = libvirt_module.LibvirtProvider.__new__(libvirt_module.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._conn = None
    p._uri = "qemu:///system"
    p._vm_port_cache = {}
    return p


def _mac(domain_name: str, index: int) -> str:
    """Reproduce _generate_mac_address for expected-value assertions."""
    h = hashlib.md5(f"{domain_name}:{index}".encode(), usedforsecurity=False).digest()
    return f"52:54:00:{h[0]:02x}:{h[1]:02x}:{h[2]:02x}"


# ---------------------------------------------------------------------------
# _resolve_data_interface_mac_sync
# ---------------------------------------------------------------------------

class TestResolveDataInterfaceMac:
    """Verify MAC offset accounting for management and reserved NICs."""

    def _setup_domain(self, provider, lab_id, node_name, kind, has_mgmt=True, reserved_nics=0):
        """Wire up mocks so _resolve_data_interface_mac_sync can resolve offsets."""
        domain = MagicMock()
        mock_conn = MagicMock()
        mock_conn.lookupByName.return_value = domain
        mock_conn.isAlive.return_value = True
        provider._conn = mock_conn
        provider._get_domain_kind = MagicMock(return_value=kind)
        provider._domain_has_dedicated_mgmt_interface = MagicMock(return_value=has_mgmt)
        # Vendor config mock
        mock_config = MagicMock()
        mock_config.management_interface = "eth0" if has_mgmt else None
        mock_config.reserved_nics = reserved_nics
        return domain, mock_config

    def test_basic_mac_no_offsets(self):
        """Interface 0 with no mgmt/reserved NICs → mac_index 0."""
        p = _make_provider()
        domain, config = self._setup_domain(p, "lab1", "r1", "generic", has_mgmt=False)
        with patch.object(libvirt_module, "get_vendor_config", return_value=config):
            mac = p._resolve_data_interface_mac_sync("lab1", "r1", 0)
        domain_name = p._domain_name("lab1", "r1")
        assert mac == _mac(domain_name, 0)

    def test_mgmt_offset(self):
        """Dedicated management NIC shifts mac_index by +1."""
        p = _make_provider()
        domain, config = self._setup_domain(p, "lab1", "r1", "iosv", has_mgmt=True)
        with patch.object(libvirt_module, "get_vendor_config", return_value=config):
            mac = p._resolve_data_interface_mac_sync("lab1", "r1", 0)
        domain_name = p._domain_name("lab1", "r1")
        # mgmt +1, reserved 0 → mac_index = 1
        assert mac == _mac(domain_name, 1)

    def test_reserved_nics_offset(self):
        """Reserved NICs add to mac_index."""
        p = _make_provider()
        domain, config = self._setup_domain(p, "lab1", "r1", "xrv9k", has_mgmt=False, reserved_nics=2)
        with patch.object(libvirt_module, "get_vendor_config", return_value=config):
            mac = p._resolve_data_interface_mac_sync("lab1", "r1", 0)
        domain_name = p._domain_name("lab1", "r1")
        # no mgmt, reserved +2 → mac_index = 2
        assert mac == _mac(domain_name, 2)

    def test_mgmt_plus_reserved(self):
        """XRv9k with dedicated mgmt: +1 mgmt + 2 reserved = +3 offset."""
        p = _make_provider()
        domain, config = self._setup_domain(p, "lab1", "r1", "xrv9k", has_mgmt=True, reserved_nics=2)
        with patch.object(libvirt_module, "get_vendor_config", return_value=config):
            mac = p._resolve_data_interface_mac_sync("lab1", "r1", 0)
        domain_name = p._domain_name("lab1", "r1")
        # mgmt +1, reserved +2 → mac_index = 3
        assert mac == _mac(domain_name, 3)

    def test_second_interface_offset(self):
        """Interface index 1 with mgmt → mac_index = 2."""
        p = _make_provider()
        domain, config = self._setup_domain(p, "lab1", "r1", "iosv", has_mgmt=True)
        with patch.object(libvirt_module, "get_vendor_config", return_value=config):
            mac = p._resolve_data_interface_mac_sync("lab1", "r1", 1)
        domain_name = p._domain_name("lab1", "r1")
        assert mac == _mac(domain_name, 2)


# ---------------------------------------------------------------------------
# set_vm_link_state
# ---------------------------------------------------------------------------

class TestSetVmLinkState:
    """Verify virsh domif-setlink invocation and error handling."""

    @pytest.mark.asyncio
    async def test_success_up(self):
        p = _make_provider()
        p._run_libvirt = AsyncMock(return_value="52:54:00:aa:bb:cc")
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            ok, err = await p.set_vm_link_state("lab1", "r1", 0, "up")

        assert ok is True
        assert err is None
        # Verify virsh was called with correct args
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "virsh"
        assert "-c" in call_args
        assert "qemu:///system" in call_args
        assert "domif-setlink" in call_args
        assert "52:54:00:aa:bb:cc" in call_args
        assert "up" in call_args

    @pytest.mark.asyncio
    async def test_success_down(self):
        p = _make_provider()
        p._run_libvirt = AsyncMock(return_value="52:54:00:aa:bb:cc")
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, err = await p.set_vm_link_state("lab1", "r1", 0, "down")

        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_mac_resolution_failure(self):
        p = _make_provider()
        p._run_libvirt = AsyncMock(side_effect=RuntimeError("libvirt down"))

        ok, err = await p.set_vm_link_state("lab1", "r1", 0, "up")

        assert ok is False
        assert "MAC resolution failed" in err

    @pytest.mark.asyncio
    async def test_virsh_failure(self):
        p = _make_provider()
        p._run_libvirt = AsyncMock(return_value="52:54:00:aa:bb:cc")
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error: domain not found"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, err = await p.set_vm_link_state("lab1", "r1", 0, "up")

        assert ok is False
        assert "virsh domif-setlink failed" in err

    @pytest.mark.asyncio
    async def test_uses_provider_uri(self):
        """Verify virsh is called with the provider's connection URI."""
        p = _make_provider()
        p._uri = "qemu+ssh://remote/system"
        p._run_libvirt = AsyncMock(return_value="52:54:00:aa:bb:cc")
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await p.set_vm_link_state("lab1", "r1", 0, "up")

        call_args = mock_exec.call_args[0]
        assert "qemu+ssh://remote/system" in call_args


# ---------------------------------------------------------------------------
# _build_vm_monitored_ports_sync
# ---------------------------------------------------------------------------

class TestBuildVmMonitoredPorts:
    """Verify batch OVS MAC matching builds correct MonitoredPort entries."""

    def _make_ovs_json(self, interfaces: list[tuple[str, str]]) -> str:
        """Build OVS JSON output for ``list Interface`` with name, mac_in_use."""
        import json
        return json.dumps({
            "data": [[name, mac] for name, mac in interfaces],
        })

    def test_empty_allocations(self):
        """No VMs deployed → empty result."""
        p = _make_provider()
        p._vlan_allocations = {}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"data": []}')
            result = p._build_vm_monitored_ports_sync()
        assert result == {}

    def test_single_vm_single_interface(self):
        """One VM with one interface matches by guest MAC."""
        p = _make_provider()
        p._vlan_allocations = {"lab1": {"r1": [2000]}}

        # Mock conn for _resolve_data_interface_mac_sync
        domain = MagicMock()
        p._conn = MagicMock()
        p._conn.lookupByName.return_value = domain
        p._get_domain_kind = MagicMock(return_value=None)
        p._domain_has_dedicated_mgmt_interface = MagicMock(return_value=False)

        domain_name = p._domain_name("lab1", "r1")
        expected_mac = _mac(domain_name, 0)

        ovs_json = self._make_ovs_json([
            ("vnet0", expected_mac),
            ("some-other-port", "aa:bb:cc:dd:ee:ff"),
        ])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ovs_json)
            result = p._build_vm_monitored_ports_sync()

        assert "vnet0" in result
        port = result["vnet0"]
        assert port.container_name == domain_name
        assert port.interface_name == "eth1"
        assert port.lab_id == "lab1"

    def test_multi_vm_batch_matching(self):
        """Multiple VMs — each interface matched from single OVS batch."""
        p = _make_provider()
        p._vlan_allocations = {
            "lab1": {"r1": [2000, 2001], "r2": [2002]},
        }

        domain = MagicMock()
        p._conn = MagicMock()
        p._conn.lookupByName.return_value = domain
        p._get_domain_kind = MagicMock(return_value=None)
        p._domain_has_dedicated_mgmt_interface = MagicMock(return_value=False)

        r1_domain = p._domain_name("lab1", "r1")
        r2_domain = p._domain_name("lab1", "r2")

        ovs_interfaces = [
            ("vnet0", _mac(r1_domain, 0)),
            ("vnet1", _mac(r1_domain, 1)),
            ("vnet2", _mac(r2_domain, 0)),
        ]
        ovs_json = self._make_ovs_json(ovs_interfaces)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ovs_json)
            result = p._build_vm_monitored_ports_sync()

        assert len(result) == 3
        assert result["vnet0"].interface_name == "eth1"
        assert result["vnet1"].interface_name == "eth2"
        assert result["vnet2"].interface_name == "eth1"

    def test_tap_mac_fallback(self):
        """Match on tap MAC (fe: prefix) when guest MAC doesn't match."""
        p = _make_provider()
        p._vlan_allocations = {"lab1": {"r1": [2000]}}

        domain = MagicMock()
        p._conn = MagicMock()
        p._conn.lookupByName.return_value = domain
        p._get_domain_kind = MagicMock(return_value=None)
        p._domain_has_dedicated_mgmt_interface = MagicMock(return_value=False)

        domain_name = p._domain_name("lab1", "r1")
        guest_mac = _mac(domain_name, 0)
        tap_mac = "fe" + guest_mac[2:]

        ovs_json = self._make_ovs_json([("tap0", tap_mac)])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ovs_json)
            result = p._build_vm_monitored_ports_sync()

        assert "tap0" in result

    def test_ovs_failure_returns_empty(self):
        """OVS batch query failure → empty dict (no crash)."""
        p = _make_provider()
        p._vlan_allocations = {"lab1": {"r1": [2000]}}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = p._build_vm_monitored_ports_sync()

        assert result == {}


# ---------------------------------------------------------------------------
# get_vm_monitored_ports / refresh
# ---------------------------------------------------------------------------

class TestVmPortCache:

    def test_get_returns_cache(self):
        p = _make_provider()
        p._vm_port_cache = {"vnet0": "dummy"}
        assert p.get_vm_monitored_ports() == {"vnet0": "dummy"}

    @pytest.mark.asyncio
    async def test_refresh_updates_cache(self):
        p = _make_provider()
        p._vm_port_cache = {}
        fake_ports = {"vnet0": "port_obj"}
        p._run_libvirt = AsyncMock(return_value=fake_ports)

        await p.refresh_vm_monitored_ports()

        assert p._vm_port_cache == fake_ports
