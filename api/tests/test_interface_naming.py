"""Tests for the centralized interface naming module."""
from __future__ import annotations

import pytest

from agent.vendors import VENDOR_CONFIGS
from app.services.interface_naming import (
    normalize_interface,
    denormalize_interface,
    _resolve_port_naming,
    _build_normalize_regex,
)


# ---------------------------------------------------------------------------
# Test data: (device_type, vendor_name, linux_name)
# Covers every portNaming pattern in the vendor catalog.
#
# Docker reserves eth0 for management. Data ports start at eth1.
# Formula: eth{vendor_index - port_start_index + 1}
# ---------------------------------------------------------------------------
NORMALIZE_CASES = [
    # Arista cEOS: Ethernet, start=1 → eth{1-1+1}=eth1
    ("ceos", "Ethernet1", "eth1"),
    ("ceos", "Ethernet16", "eth16"),
    # Nokia SR Linux: e1-, start=1
    ("nokia_srlinux", "e1-1", "eth1"),
    ("nokia_srlinux", "e1-4", "eth4"),
    # Cumulus/CVX: swp, start=1
    ("cvx", "swp1", "eth1"),
    ("cvx", "swp12", "eth12"),
    # SONiC: Ethernet, start=0 → eth{0-0+1}=eth1
    ("sonic-vs", "Ethernet0", "eth1"),
    ("sonic-vs", "Ethernet3", "eth4"),
    # Cisco IOS-XR: GigabitEthernet0/0/0/{index}, start=0 → eth{0-0+1}=eth1
    ("cisco_iosxr", "GigabitEthernet0/0/0/0", "eth1"),
    ("cisco_iosxr", "GigabitEthernet0/0/0/3", "eth4"),
    # Cisco XRd: GigabitEthernet0/0/0/{index}, start=0
    ("cisco_xrd", "GigabitEthernet0/0/0/0", "eth1"),
    # Cisco IOSv: GigabitEthernet0/{index}, start=0
    ("cisco_iosv", "GigabitEthernet0/0", "eth1"),
    ("cisco_iosv", "GigabitEthernet0/2", "eth3"),
    # Cisco CSR1000v / C8000v: GigabitEthernet, start=1
    ("cisco_csr1000v", "GigabitEthernet1", "eth1"),
    ("c8000v", "GigabitEthernet1", "eth1"),
    # Cisco Cat9800: GigabitEthernet, start=1
    ("cat9800", "GigabitEthernet1", "eth1"),
    # Cisco Cat9000v aliases: GigabitEthernet1/0/{index}, start=1
    ("cat9000v-uadp", "GigabitEthernet1/0/1", "eth1"),
    ("cat9000v-q200", "GigabitEthernet1/0/8", "eth8"),
    # Juniper vSRX3: ge-0/0/, start=0 → eth{0-0+1}=eth1
    ("juniper_vsrx3", "ge-0/0/0", "eth1"),
    ("juniper_vsrx3", "ge-0/0/3", "eth4"),
    # Juniper vJunos Switch: ge-0/0/, start=0
    ("juniper_vjunosswitch", "ge-0/0/0", "eth1"),
    # Juniper vJunos Router: ge-0/0/, start=0
    ("juniper_vjunosrouter", "ge-0/0/0", "eth1"),
    # Juniper vJunos Evolved: ge-0/0/, start=0
    ("juniper_vjunosevolved", "ge-0/0/0", "eth1"),
    # Juniper cJunos: et-0/0/, start=0
    ("juniper_cjunos", "et-0/0/0", "eth1"),
    ("juniper_cjunos", "et-0/0/5", "eth6"),
    # Cisco Nexus 9000v: Ethernet1/, start=1
    ("cisco_n9kv", "Ethernet1/1", "eth1"),
    ("cisco_n9kv", "Ethernet1/8", "eth8"),
    # F5 BIG-IP: 1., start=1
    ("f5_bigip", "1.1", "eth1"),
    ("f5_bigip", "1.4", "eth4"),
    # Citrix ADC: 0/, start=1
    ("citrix_adc", "0/1", "eth1"),
    ("citrix_adc", "0/3", "eth3"),
    # Cisco ASAv: GigabitEthernet0/, start=0
    ("cisco_asav", "GigabitEthernet0/0", "eth1"),
    ("cisco_asav", "GigabitEthernet0/3", "eth4"),
    # Fortinet FortiGate: port, start=1
    ("fortinet_fortigate", "port1", "eth1"),
    ("fortinet_fortigate", "port4", "eth4"),
    # Palo Alto VM-Series: ethernet1/, start=1
    ("paloalto_vmseries", "ethernet1/1", "eth1"),
    ("paloalto_vmseries", "ethernet1/5", "eth5"),
    # VyOS: eth, start=0 (identity transform — already eth{N})
    ("vyos", "eth0", "eth0"),
    ("vyos", "eth3", "eth3"),
    # Linux: eth, start=0 (identity transform)
    ("linux", "eth0", "eth0"),
    ("linux", "eth1", "eth1"),
    # FRR: eth, start=0 (identity transform)
    ("frr", "eth0", "eth0"),
    # Windows: Ethernet, start=0
    ("windows", "Ethernet0", "eth1"),
    ("windows", "Ethernet3", "eth4"),
    # Cisco SD-WAN vEdge: ge0/, start=0
    ("cat-sdwan-vedge", "ge0/0", "eth1"),
    ("cat-sdwan-vedge", "ge0/2", "eth3"),
    # Cisco FTDv: GigabitEthernet0/, start=0
    ("ftdv", "GigabitEthernet0/0", "eth1"),
    ("ftdv", "GigabitEthernet0/2", "eth3"),
    # Juniper cRPD: eth, start=0 (identity)
    ("juniper_crpd", "eth0", "eth0"),
]


class TestNormalizeInterface:
    """Tests for normalize_interface()."""

    @pytest.mark.parametrize("device_type,vendor_name,expected", NORMALIZE_CASES)
    def test_device_aware_normalize(self, device_type, vendor_name, expected):
        assert normalize_interface(vendor_name, device_type) == expected

    def test_already_normalized(self):
        assert normalize_interface("eth1", "ceos") == "eth1"
        assert normalize_interface("eth0", "cisco_iosxr") == "eth0"
        assert normalize_interface("eth16", None) == "eth16"

    def test_already_normalized_case_insensitive(self):
        assert normalize_interface("ETH1") == "eth1"
        assert normalize_interface("Eth3", "ceos") == "eth3"

    def test_empty_and_none(self):
        assert normalize_interface("") == ""
        assert normalize_interface("", "ceos") == ""

    def test_unrecognized_passthrough(self):
        assert normalize_interface("loopback0", "ceos") == "loopback0"
        assert normalize_interface("mgmt0", "cisco_n9kv") == "mgmt0"

    def test_fallback_no_device_type(self):
        """When device_type is None, common patterns should still work.

        Juniper patterns (ge-, xe-, et-) are treated as 0-indexed in fallback
        and get +1 offset for Docker management interface.
        Other patterns are ambiguous without device_type and use raw index.
        """
        assert normalize_interface("Ethernet1") == "eth1"
        assert normalize_interface("GigabitEthernet0") == "eth0"
        assert normalize_interface("ge-0/0/3") == "eth4"       # 0-indexed +1
        assert normalize_interface("xe-0/0/5") == "eth6"       # 0-indexed +1
        assert normalize_interface("et-0/0/0") == "eth1"       # 0-indexed +1
        assert normalize_interface("GigabitEthernet0/0/0/3") == "eth3"

    def test_case_insensitivity_vendor_names(self):
        """Vendor names should be case-insensitive."""
        assert normalize_interface("ethernet1", "ceos") == "eth1"
        assert normalize_interface("ETHERNET1", "ceos") == "eth1"
        assert normalize_interface("gigabitethernet0/0/0/0", "cisco_iosxr") == "eth1"


class TestDenormalizeInterface:
    """Tests for denormalize_interface()."""

    @pytest.mark.parametrize("device_type,expected_vendor,eth_name", [
        (dt, vn, en) for dt, vn, en in NORMALIZE_CASES
        # Exclude identity-transform devices (port_naming == "eth")
        if dt not in ("vyos", "linux", "frr", "juniper_crpd",
                       "haproxy", "cat-sdwan-controller",
                       "cat-sdwan-manager", "cat-sdwan-validator", "fmcv")
    ])
    def test_device_aware_denormalize(self, device_type, expected_vendor, eth_name):
        assert denormalize_interface(eth_name, device_type) == expected_vendor

    def test_eth_naming_devices_passthrough(self):
        """Devices with port_naming='eth' should return eth{N} unchanged."""
        assert denormalize_interface("eth0", "linux") == "eth0"
        assert denormalize_interface("eth1", "vyos") == "eth1"
        assert denormalize_interface("eth2", "frr") == "eth2"

    def test_no_device_type(self):
        assert denormalize_interface("eth1", None) == "eth1"
        assert denormalize_interface("eth0") == "eth0"

    def test_empty_and_none(self):
        assert denormalize_interface("", "ceos") == ""
        assert denormalize_interface("", None) == ""

    def test_non_eth_passthrough(self):
        """Non eth{N} inputs should pass through unchanged."""
        assert denormalize_interface("loopback0", "ceos") == "loopback0"
        assert denormalize_interface("mgmt0", "ceos") == "mgmt0"

    def test_eth0_management_passthrough(self):
        """eth0 is Docker management — never denormalize to vendor data port."""
        assert denormalize_interface("eth0", "juniper_cjunos") == "eth0"
        assert denormalize_interface("eth0", "juniper_vsrx3") == "eth0"
        assert denormalize_interface("eth0", "sonic_vs") == "eth0"
        assert denormalize_interface("eth0", "ceos") == "eth0"
        assert denormalize_interface("eth0", "cisco_n9kv") == "eth0"


class TestRoundTrip:
    """Verify normalize → denormalize round-trips for all device types."""

    @pytest.mark.parametrize("device_type,vendor_name,linux_name", [
        (dt, vn, en) for dt, vn, en in NORMALIZE_CASES
        # Exclude identity transforms — they trivially round-trip
        if dt not in ("vyos", "linux", "frr", "juniper_crpd",
                       "haproxy", "cat-sdwan-controller",
                       "cat-sdwan-manager", "cat-sdwan-validator", "fmcv")
    ])
    def test_round_trip(self, device_type, vendor_name, linux_name):
        """normalize(vendor) → eth, then denormalize(eth) → vendor."""
        normalized = normalize_interface(vendor_name, device_type)
        assert normalized == linux_name
        denormalized = denormalize_interface(normalized, device_type)
        assert denormalized == vendor_name


class TestResolvePortNaming:
    """Tests for the internal _resolve_port_naming helper."""

    def test_known_device(self):
        naming, start = _resolve_port_naming("ceos")
        assert naming == "Ethernet"
        assert start == 1

    def test_unknown_device_defaults(self):
        naming, start = _resolve_port_naming("nonexistent_device_xyz")
        # Falls back to "eth" / 0 when no vendor config found
        assert naming == "eth"
        assert start == 0

    def test_custom_device_override(self, monkeypatch):
        """Custom device portNaming should override vendor catalog."""
        monkeypatch.setattr(
            "app.services.interface_naming.find_custom_device",
            lambda d: {"portNaming": "CustomPort", "portStartIndex": 5} if d == "my_custom" else None,
        )
        monkeypatch.setattr(
            "app.services.interface_naming.get_device_override",
            lambda d: None,
        )
        naming, start = _resolve_port_naming("my_custom")
        assert naming == "CustomPort"
        assert start == 5

    def test_device_override(self, monkeypatch):
        """device_overrides.json portNaming should take final precedence."""
        monkeypatch.setattr(
            "app.services.interface_naming.get_device_override",
            lambda d: {"portNaming": "OverriddenEth", "portStartIndex": 2} if d == "ceos" else None,
        )
        naming, start = _resolve_port_naming("ceos")
        assert naming == "OverriddenEth"
        assert start == 2


class TestIosxrBugFix:
    """Regression test: the old vendor_to_linux_interface had a known bug
    where GigabitEthernet0/0/0/3 matched the wrong regex pattern first.

    The centralized normalize_interface fixes this by using device-aware patterns.
    """

    def test_iosxr_gigabit_ethernet(self):
        """GigabitEthernet0/0/0/3 should normalize correctly with device_type."""
        # With device_type, uses the device-aware pattern (0-indexed → +1)
        assert normalize_interface("GigabitEthernet0/0/0/3", "cisco_iosxr") == "eth4"

    def test_iosxr_fallback(self):
        """Even without device_type, the fallback should get this right."""
        # Fallback uses raw index for GigE patterns (ambiguous without device_type)
        assert normalize_interface("GigabitEthernet0/0/0/3") == "eth3"


# ---------------------------------------------------------------------------
# Parametrized vendor registry validation
# ---------------------------------------------------------------------------

# Build test params from every VendorConfig in the catalog.
_VENDOR_REGISTRY_PARAMS = [
    pytest.param(key, cfg.port_naming, cfg.port_start_index, id=key)
    for key, cfg in VENDOR_CONFIGS.items()
]


class TestVendorRegistryRoundTrip:
    """Validate that every VendorConfig entry produces a working regex
    and round-trips correctly through normalize → denormalize."""

    @pytest.mark.parametrize("key,port_naming,port_start_index", _VENDOR_REGISTRY_PARAMS)
    def test_regex_builds(self, key, port_naming, port_start_index):
        """_build_normalize_regex should return a valid pattern (or None for 'eth')."""
        regex = _build_normalize_regex(port_naming)
        if port_naming == "eth":
            assert regex is None, f"{key}: 'eth' naming should return None"
        else:
            assert regex is not None, f"{key}: regex should not be None for {port_naming!r}"

    @pytest.mark.parametrize("key,port_naming,port_start_index", _VENDOR_REGISTRY_PARAMS)
    def test_round_trip_eth1(self, key, port_naming, port_start_index):
        """normalize(denormalize('eth1', key), key) should equal 'eth1'."""
        vendor_name = denormalize_interface("eth1", key)
        normalized = normalize_interface(vendor_name, key)
        assert normalized == "eth1", (
            f"{key}: round-trip failed: eth1 → {vendor_name!r} → {normalized!r}"
        )

    @pytest.mark.parametrize("key,port_naming,port_start_index", _VENDOR_REGISTRY_PARAMS)
    def test_round_trip_eth4(self, key, port_naming, port_start_index):
        """normalize(denormalize('eth4', key), key) should equal 'eth4'."""
        vendor_name = denormalize_interface("eth4", key)
        normalized = normalize_interface(vendor_name, key)
        assert normalized == "eth4", (
            f"{key}: round-trip failed: eth4 → {vendor_name!r} → {normalized!r}"
        )
