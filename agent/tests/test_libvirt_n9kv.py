"""Tests for agent/providers/libvirt_n9kv.py — N9Kv POAP bootstrapping and management helpers.

Covers:
- Deterministic naming / addressing helpers
- POAP TFTP script staging
- Libvirt network management (ensure, teardown)
- Management network resolution logic
- N9Kv config preamble content
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


import agent.providers.libvirt_n9kv as n9kv_mod
from agent.providers.libvirt_n9kv import (
    _N9KV_CONFIG_PREAMBLE,
    ensure_libvirt_network,
    ensure_n9kv_poap_network,
    n9kv_poap_bootfile_name,
    n9kv_poap_bridge_name,
    n9kv_poap_config_url,
    n9kv_poap_network_name,
    n9kv_poap_subnet,
    n9kv_poap_tftp_root,
    node_uses_dedicated_mgmt_interface,
    resolve_management_network,
    stage_n9kv_poap_tftp_script,
    teardown_n9kv_poap_network,
)


# ---------------------------------------------------------------------------
# Deterministic naming / addressing helpers
# ---------------------------------------------------------------------------


class TestDeterministicNaming:
    """Tests for n9kv_poap_network_name, bridge_name, subnet, config_url, tftp_root."""

    def test_network_name_is_deterministic(self):
        """Same lab+node always produces the same network name."""
        name1 = n9kv_poap_network_name("lab-1", "n9kv1")
        name2 = n9kv_poap_network_name("lab-1", "n9kv1")
        assert name1 == name2
        assert name1.startswith("ap-poap-")
        assert len(name1) == len("ap-poap-") + 10

    def test_network_name_differs_per_node(self):
        """Different nodes in the same lab get different network names."""
        name1 = n9kv_poap_network_name("lab-1", "n9kv1")
        name2 = n9kv_poap_network_name("lab-1", "n9kv2")
        assert name1 != name2

    def test_bridge_name_is_within_linux_limit(self):
        """Linux bridge names must be <= 15 chars."""
        name = n9kv_poap_bridge_name("lab-1", "n9kv1")
        assert len(name) <= 15
        assert name.startswith("vpoap")

    def test_subnet_returns_valid_triple(self):
        """Subnet helper returns (gateway, dhcp_start, dhcp_end) in 10.64-127.x.0/24."""
        gw, start, end = n9kv_poap_subnet("lab-1", "n9kv1")
        parts = gw.split(".")
        assert parts[0] == "10"
        assert 64 <= int(parts[1]) <= 127
        assert parts[3] == "1"
        assert start.endswith(".10")
        assert end.endswith(".250")
        # All three share the same /24
        assert gw.rsplit(".", 1)[0] == start.rsplit(".", 1)[0]
        assert gw.rsplit(".", 1)[0] == end.rsplit(".", 1)[0]

    def test_config_url_encodes_lab_and_node(self, monkeypatch):
        """Config URL includes URL-encoded lab_id and node_name."""
        monkeypatch.setattr(n9kv_mod.settings, "agent_port", 8001, raising=False)
        url = n9kv_poap_config_url("lab/1", "node name", "10.0.0.1")
        assert "lab%2F1" in url
        assert "node%20name" in url
        assert "10.0.0.1:8001" in url
        assert url.startswith("http://")

    def test_tftp_root_under_workspace(self, monkeypatch, tmp_path):
        """TFTP root is under the workspace .poap-tftp directory."""
        monkeypatch.setattr(n9kv_mod.settings, "workspace_path", str(tmp_path), raising=False)
        root = n9kv_poap_tftp_root("lab-1", "n9kv1")
        assert str(root).startswith(str(tmp_path))
        assert ".poap-tftp" in str(root)

    def test_bootfile_name_is_script_py(self):
        """The bootfile name is always script.py."""
        assert n9kv_poap_bootfile_name() == "script.py"


# ---------------------------------------------------------------------------
# TFTP script staging
# ---------------------------------------------------------------------------


class TestStageTftpScript:
    """Tests for stage_n9kv_poap_tftp_script."""

    def test_stages_script_successfully(self, monkeypatch, tmp_path):
        """Successful staging creates file and returns (root, name)."""
        monkeypatch.setattr(n9kv_mod.settings, "workspace_path", str(tmp_path), raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "agent_port", 8001, raising=False)
        monkeypatch.setattr(n9kv_mod, "render_poap_script", lambda url: f"# script for {url}")

        result = stage_n9kv_poap_tftp_script("lab-1", "n9kv1", "10.0.0.1")
        assert result is not None
        tftp_root, script_name = result
        assert script_name == "script.py"
        script_path = tftp_root / script_name
        assert script_path.exists()
        content = script_path.read_text()
        assert "10.0.0.1" in content

    def test_returns_none_on_write_failure(self, monkeypatch, tmp_path):
        """Returns None when script write fails."""
        monkeypatch.setattr(n9kv_mod.settings, "workspace_path", str(tmp_path), raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "agent_port", 8001, raising=False)
        monkeypatch.setattr(n9kv_mod, "render_poap_script", lambda url: "# script")

        # Make the parent directory a file to cause mkdir to fail
        fake_root = n9kv_poap_tftp_root("lab-1", "n9kv1")
        fake_root.parent.mkdir(parents=True, exist_ok=True)
        # Create a file where the directory would be
        Path(str(fake_root)).parent.joinpath(fake_root.name).write_text("block")

        # Now try staging — should fail since the path is a file, not a dir
        # (or succeed if it already exists as dir; the point is graceful handling)
        # Use a more reliable approach: monkeypatch Path.mkdir to raise
        orig_mkdir = Path.mkdir

        def _raise_on_stage(self, *args, **kwargs):
            if ".poap-tftp" in str(self):
                raise PermissionError("no write")
            return orig_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _raise_on_stage)
        result = stage_n9kv_poap_tftp_script("lab-fail", "n9kv1", "10.0.0.1")
        assert result is None


# ---------------------------------------------------------------------------
# Libvirt network management
# ---------------------------------------------------------------------------


class TestEnsureLibvirtNetwork:
    """Tests for ensure_libvirt_network."""

    def test_existing_active_network_returns_true(self):
        """Active network returns True immediately."""
        network = MagicMock()
        network.isActive.return_value = 1
        conn = MagicMock()
        conn.networkLookupByName.return_value = network

        assert ensure_libvirt_network(conn, "default") is True
        network.setAutostart.assert_called_once_with(True)

    def test_existing_inactive_network_is_started(self):
        """Inactive network is started via create()."""
        network = MagicMock()
        network.isActive.return_value = 0
        conn = MagicMock()
        conn.networkLookupByName.return_value = network

        assert ensure_libvirt_network(conn, "default") is True
        network.create.assert_called_once()
        network.setAutostart.assert_called_once_with(True)

    def test_missing_network_returns_false(self):
        """Missing network (lookup raises) returns False."""
        conn = MagicMock()
        conn.networkLookupByName.side_effect = Exception("not found")

        assert ensure_libvirt_network(conn, "missing") is False

    def test_autostart_failure_is_nonfatal(self):
        """setAutostart failure does not prevent returning True."""
        network = MagicMock()
        network.isActive.return_value = 1
        network.setAutostart.side_effect = Exception("permission denied")
        conn = MagicMock()
        conn.networkLookupByName.return_value = network

        assert ensure_libvirt_network(conn, "default") is True


# ---------------------------------------------------------------------------
# POAP network (ensure / teardown)
# ---------------------------------------------------------------------------


class TestEnsureN9kvPoapNetwork:
    """Tests for ensure_n9kv_poap_network."""

    def test_creates_new_network_successfully(self, monkeypatch, tmp_path):
        """When network does not exist, creates and activates it."""
        monkeypatch.setattr(n9kv_mod.settings, "workspace_path", str(tmp_path), raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "agent_port", 8001, raising=False)
        monkeypatch.setattr(n9kv_mod, "render_poap_script", lambda url: "# script")

        conn = MagicMock()
        conn.networkLookupByName.side_effect = Exception("not found")
        network = MagicMock()
        network.isActive.return_value = 0
        conn.networkDefineXML.return_value = network

        result = ensure_n9kv_poap_network(conn, "lab-1", "n9kv1")
        assert result is not None
        conn.networkDefineXML.assert_called_once()
        network.create.assert_called_once()

    def test_existing_network_with_correct_xml_returns_name(self, monkeypatch, tmp_path):
        """Existing network with correct DHCP options is reused."""
        monkeypatch.setattr(n9kv_mod.settings, "workspace_path", str(tmp_path), raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "agent_port", 8001, raising=False)
        monkeypatch.setattr(n9kv_mod, "render_poap_script", lambda url: "# script")

        gw, _, _ = n9kv_poap_subnet("lab-1", "n9kv1")
        script_server_opt = f"dhcp-option-force=66,{gw}"
        script_name_opt = "dhcp-option-force=67,script.py"

        xml_desc = (
            f"<network><tftp root='/tmp'/>"
            f"<bootp file='script.py'/>"
            f"<dnsmasq:option value='{script_server_opt}'/>"
            f"<dnsmasq:option value='{script_name_opt}'/>"
            f"</network>"
        )

        network = MagicMock()
        network.XMLDesc.return_value = xml_desc
        network.isActive.return_value = 1
        conn = MagicMock()
        conn.networkLookupByName.return_value = network

        result = ensure_n9kv_poap_network(conn, "lab-1", "n9kv1")
        assert result == n9kv_poap_network_name("lab-1", "n9kv1")
        # Should not recreate
        conn.networkDefineXML.assert_not_called()

    def test_returns_none_when_staging_fails(self, monkeypatch, tmp_path):
        """Returns None when TFTP script staging fails."""
        monkeypatch.setattr(n9kv_mod, "stage_n9kv_poap_tftp_script", lambda *_a, **_k: None)

        conn = MagicMock()
        result = ensure_n9kv_poap_network(conn, "lab-1", "n9kv1")
        assert result is None

    def test_returns_none_when_define_fails(self, monkeypatch, tmp_path):
        """Returns None when networkDefineXML fails."""
        monkeypatch.setattr(n9kv_mod.settings, "workspace_path", str(tmp_path), raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "agent_port", 8001, raising=False)
        monkeypatch.setattr(n9kv_mod, "render_poap_script", lambda url: "# script")

        conn = MagicMock()
        conn.networkLookupByName.side_effect = Exception("not found")
        conn.networkDefineXML.side_effect = Exception("define failed")

        result = ensure_n9kv_poap_network(conn, "lab-1", "n9kv1")
        assert result is None


class TestTeardownN9kvPoapNetwork:
    """Tests for teardown_n9kv_poap_network."""

    def test_destroys_and_undefines_active_network(self):
        """Active network is destroyed then undefined."""
        network = MagicMock()
        network.isActive.return_value = 1
        conn = MagicMock()
        conn.networkLookupByName.return_value = network

        teardown_n9kv_poap_network(conn, "lab-1", "n9kv1")
        network.destroy.assert_called_once()
        network.undefine.assert_called_once()

    def test_inactive_network_only_undefined(self):
        """Inactive network is only undefined (no destroy)."""
        network = MagicMock()
        network.isActive.return_value = 0
        conn = MagicMock()
        conn.networkLookupByName.return_value = network

        teardown_n9kv_poap_network(conn, "lab-1", "n9kv1")
        network.destroy.assert_not_called()
        network.undefine.assert_called_once()

    def test_missing_network_is_noop(self):
        """Missing network (lookup raises) is a no-op."""
        conn = MagicMock()
        conn.networkLookupByName.side_effect = Exception("not found")

        teardown_n9kv_poap_network(conn, "lab-1", "n9kv1")
        # No exception raised


# ---------------------------------------------------------------------------
# Management interface / network resolution
# ---------------------------------------------------------------------------


class TestNodeUsesDedicatedMgmtInterface:
    """Tests for node_uses_dedicated_mgmt_interface."""

    def test_none_kind_returns_false(self):
        """None kind returns False."""
        assert node_uses_dedicated_mgmt_interface(None) is False

    def test_empty_kind_returns_false(self):
        """Empty string kind returns False."""
        assert node_uses_dedicated_mgmt_interface("") is False

    def test_unknown_kind_returns_false(self):
        """Unknown kind (no vendor config) returns False."""
        assert node_uses_dedicated_mgmt_interface("nonexistent_device_xyz") is False

    def test_kind_with_management_interface(self, monkeypatch):
        """Kind with management_interface in vendor config returns True."""
        config = SimpleNamespace(management_interface="mgmt0")
        monkeypatch.setattr(n9kv_mod, "get_vendor_config", lambda _kind: config)
        assert node_uses_dedicated_mgmt_interface("cisco_n9kv") is True

    def test_kind_without_management_interface(self, monkeypatch):
        """Kind without management_interface returns False."""
        config = SimpleNamespace(management_interface=None)
        monkeypatch.setattr(n9kv_mod, "get_vendor_config", lambda _kind: config)
        assert node_uses_dedicated_mgmt_interface("iosv") is False


class TestResolveManagementNetwork:
    """Tests for resolve_management_network."""

    def test_no_dedicated_mgmt_returns_false_default(self, monkeypatch):
        """Device without dedicated mgmt interface returns (False, 'default')."""
        monkeypatch.setattr(n9kv_mod, "node_uses_dedicated_mgmt_interface", lambda _kind: False)

        include, network = resolve_management_network(
            MagicMock(), "lab-1", "node1", "linux",
            canonical_kind_fn=lambda k: k,
        )
        assert include is False
        assert network == "default"

    def test_n9kv_with_poap_preboot_uses_poap_network(self, monkeypatch, tmp_path):
        """N9Kv with POAP preboot enabled gets a POAP network."""
        monkeypatch.setattr(n9kv_mod, "node_uses_dedicated_mgmt_interface", lambda _kind: True)
        monkeypatch.setattr(n9kv_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "n9kv_poap_preboot_enabled", True, raising=False)

        expected_name = n9kv_poap_network_name("lab-1", "n9kv1")
        monkeypatch.setattr(n9kv_mod, "ensure_n9kv_poap_network", lambda *_args: expected_name)

        include, network = resolve_management_network(
            MagicMock(), "lab-1", "n9kv1", "cisco_n9kv",
            canonical_kind_fn=lambda _kind: "cisco_n9kv",
        )
        assert include is True
        assert network == expected_name

    def test_n9kv_fallback_to_default_on_poap_failure(self, monkeypatch):
        """Falls back to default network when POAP network creation fails."""
        monkeypatch.setattr(n9kv_mod, "node_uses_dedicated_mgmt_interface", lambda _kind: True)
        monkeypatch.setattr(n9kv_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
        monkeypatch.setattr(n9kv_mod.settings, "n9kv_poap_preboot_enabled", True, raising=False)
        monkeypatch.setattr(n9kv_mod, "ensure_n9kv_poap_network", lambda *_args: None)
        monkeypatch.setattr(n9kv_mod, "ensure_libvirt_network", lambda *_args: True)

        include, network = resolve_management_network(
            MagicMock(), "lab-1", "n9kv1", "cisco_n9kv",
            canonical_kind_fn=lambda _kind: "cisco_n9kv",
        )
        assert include is True
        assert network == "default"

    def test_non_n9kv_with_mgmt_uses_default_network(self, monkeypatch):
        """Non-N9Kv device with mgmt interface uses default libvirt network."""
        monkeypatch.setattr(n9kv_mod, "node_uses_dedicated_mgmt_interface", lambda _kind: True)
        monkeypatch.setattr(n9kv_mod, "ensure_libvirt_network", lambda *_args: True)

        include, network = resolve_management_network(
            MagicMock(), "lab-1", "node1", "iosv",
            canonical_kind_fn=lambda _kind: "iosv",
        )
        assert include is True
        assert network == "default"

    def test_default_network_unavailable_omits_mgmt_nic(self, monkeypatch):
        """When default network unavailable, returns (False, 'default')."""
        monkeypatch.setattr(n9kv_mod, "node_uses_dedicated_mgmt_interface", lambda _kind: True)
        monkeypatch.setattr(n9kv_mod, "ensure_libvirt_network", lambda *_args: False)

        include, network = resolve_management_network(
            MagicMock(), "lab-1", "node1", "iosv",
            canonical_kind_fn=lambda _kind: "iosv",
        )
        assert include is False
        assert network == "default"


# ---------------------------------------------------------------------------
# Config preamble content
# ---------------------------------------------------------------------------


class TestConfigPreamble:
    """Tests for _N9KV_CONFIG_PREAMBLE format string."""

    def test_preamble_has_hostname_placeholder(self):
        """Preamble uses {hostname} for substitution."""
        assert "{hostname}" in _N9KV_CONFIG_PREAMBLE

    def test_preamble_renders_with_hostname(self):
        """Preamble can be formatted with a hostname."""
        rendered = _N9KV_CONFIG_PREAMBLE.format(hostname="my-switch")
        assert "hostname my-switch" in rendered
        assert "{hostname}" not in rendered

    def test_preamble_contains_eem_applet(self):
        """Preamble defines EEM applet BOOTCONFIG."""
        assert "event manager applet BOOTCONFIG" in _N9KV_CONFIG_PREAMBLE

    def test_preamble_contains_admin_credentials(self):
        """Preamble sets admin credentials."""
        assert "username admin password cisco" in _N9KV_CONFIG_PREAMBLE

    def test_preamble_creates_set_boot_script(self):
        """Preamble echoes set_boot.py to bootflash."""
        assert "set_boot.py" in _N9KV_CONFIG_PREAMBLE
        assert "echo" in _N9KV_CONFIG_PREAMBLE
