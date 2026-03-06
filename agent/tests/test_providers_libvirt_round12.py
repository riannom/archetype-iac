"""Round 12 deep-path tests for agent/providers/libvirt.py.

Targets under-tested methods: stale VM recovery, domain XML metadata,
boot state detection, error handling in destroy/remove flows, base image
resolution, console ownership, and domain introspection helpers.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import agent.providers.libvirt as libvirt_mod
from agent.providers.base import NodeStatus


# ---------------------------------------------------------------------------
# Helper: create a LibvirtProvider without __init__ (no libvirt dependency)
# ---------------------------------------------------------------------------

def _make_provider() -> libvirt_mod.LibvirtProvider:
    p = libvirt_mod.LibvirtProvider.__new__(libvirt_mod.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._n9kv_loader_recovery_attempts = {}
    p._n9kv_loader_recovery_last_at = {}
    p._n9kv_poap_skip_attempted = set()
    p._n9kv_admin_password_completed = set()
    p._n9kv_panic_recovery_attempts = {}
    p._n9kv_panic_recovery_last_at = {}
    p._n9kv_panic_last_log_size = {}
    p._conn = None
    p._uri = "qemu:///system"
    p._vm_port_cache = {}
    return p


# ===========================================================================
# 1. _recover_stale_network: merge persisted + discovered VLANs, keep only
#    nodes with live domains, discard stale ones
# ===========================================================================

class TestRecoverStaleNetwork:
    """Tests for _recover_stale_network — VLAN recovery from persisted state
    and live domain introspection."""

    def _setup_provider_with_domains(
        self,
        monkeypatch,
        persisted_allocs: dict,
        discovered: dict,
        domain_names: list[str],
    ):
        """Wire up a provider with controlled persisted/discovered VLAN data."""
        provider = _make_provider()

        # _load_vlan_allocations: populate from persisted
        def fake_load(lab_id, workspace):
            provider._vlan_allocations[lab_id] = dict(persisted_allocs)
            return True

        provider._load_vlan_allocations = MagicMock(side_effect=fake_load)
        provider._discover_vlan_allocations_from_domains = MagicMock(return_value=discovered)
        provider._save_vlan_allocations = MagicMock()

        # Build fake domains
        class _FakeDomain:
            def __init__(self, name):
                self._name = name
            def name(self):
                return self._name

        domains = [_FakeDomain(n) for n in domain_names]
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            listAllDomains=lambda _flags: domains,
        )
        # Ensure _lab_prefix returns known prefix
        monkeypatch.setattr(
            libvirt_mod, "sanitize_id", lambda v, max_len=20: v[:max_len],
        )
        return provider

    def test_merges_persisted_and_discovered_keeps_valid(self, monkeypatch, tmp_path):
        """Discovered VLANs override persisted; only nodes with live domains are kept."""
        provider = self._setup_provider_with_domains(
            monkeypatch,
            persisted_allocs={"r1": [100], "r2": [200]},
            discovered={"r1": [150]},            # overrides persisted r1
            domain_names=["arch-lab1-r1"],        # only r1 has a live domain
        )

        recovered = provider._recover_stale_network("lab1", tmp_path)

        # r1 kept (live domain), r2 discarded (no domain)
        assert "r1" in recovered
        assert recovered["r1"] == [150]  # discovered takes precedence
        assert "r2" not in recovered
        provider._save_vlan_allocations.assert_called_once()

    def test_empty_when_no_domains_exist(self, monkeypatch, tmp_path):
        """Returns empty dict when no lab domains exist."""
        provider = self._setup_provider_with_domains(
            monkeypatch,
            persisted_allocs={"r1": [100]},
            discovered={},
            domain_names=[],  # no domains
        )

        recovered = provider._recover_stale_network("lab1", tmp_path)
        assert recovered == {}

    def test_returns_empty_on_exception(self, monkeypatch, tmp_path):
        """Catches exceptions from listAllDomains and returns empty."""
        provider = _make_provider()
        provider._load_vlan_allocations = MagicMock()
        provider._discover_vlan_allocations_from_domains = MagicMock(return_value={})
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            listAllDomains=MagicMock(side_effect=RuntimeError("connection reset")),
        )
        monkeypatch.setattr(
            libvirt_mod, "sanitize_id", lambda v, max_len=20: v[:max_len],
        )

        recovered = provider._recover_stale_network("lab1", tmp_path)
        assert recovered == {}


# ===========================================================================
# 2. _node_precheck_sync: stale domain teardown, running domain short-circuit
# ===========================================================================

class TestNodePrecheckSync:
    """Tests for _node_precheck_sync — the pre-deploy domain cleanup routine."""

    def test_already_running_returns_uuid(self, monkeypatch):
        """If domain is already running, returns (True, uuid, status)."""
        provider = _make_provider()
        provider._recover_stale_network = MagicMock()
        provider._undefine_domain = MagicMock()

        fake_domain = SimpleNamespace(
            UUIDString=lambda: "abcd1234-5678-9012",
        )
        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            libvirtError=type("libvirtError", (Exception,), {}),
        ))
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=lambda _name: fake_domain,
        )
        provider._get_domain_status = MagicMock(return_value=NodeStatus.RUNNING)

        workspace = Path("/tmp/ws")
        disks_dir = Path("/tmp/ws/disks")

        already_running, uuid_short, status = provider._node_precheck_sync(
            "lab1", "r1", "arch-lab1-r1", workspace, disks_dir,
        )

        assert already_running is True
        assert uuid_short == "abcd1234-567"
        assert status == NodeStatus.RUNNING
        provider._undefine_domain.assert_not_called()

    def test_stale_shutoff_domain_is_cleaned_up(self, monkeypatch, tmp_path):
        """Stale (non-running) domain is undefined, disks removed, VLANs cleared."""
        provider = _make_provider()
        provider._recover_stale_network = MagicMock()
        provider._clear_vm_post_boot_commands_cache = MagicMock()
        provider._teardown_n9kv_poap_network = MagicMock()
        provider._save_vlan_allocations = MagicMock()
        provider._vlan_allocations = {"lab1": {"r1": [100, 101]}}

        fake_domain = SimpleNamespace(UUIDString=lambda: "1234")

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            libvirtError=FakeLibvirtError,
        ))
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=lambda _name: fake_domain,
        )
        provider._get_domain_status = MagicMock(return_value=NodeStatus.STOPPED)
        provider._undefine_domain = MagicMock()

        # Create fake disk files
        disks_dir = tmp_path / "disks"
        disks_dir.mkdir()
        overlay = disks_dir / "r1.qcow2"
        overlay.write_bytes(b"fake")
        data_disk = disks_dir / "r1-data.qcow2"
        data_disk.write_bytes(b"fake")

        result = provider._node_precheck_sync(
            "lab1", "r1", "arch-lab1-r1", tmp_path, disks_dir,
        )

        assert result == (False, None, None)
        provider._undefine_domain.assert_called_once_with(fake_domain, "arch-lab1-r1")
        provider._clear_vm_post_boot_commands_cache.assert_called_once()
        assert not overlay.exists()
        assert not data_disk.exists()
        assert "r1" not in provider._vlan_allocations.get("lab1", {})

    def test_domain_not_found_returns_proceed(self, monkeypatch):
        """When lookupByName raises libvirtError, returns (False, None, None)."""
        provider = _make_provider()
        provider._recover_stale_network = MagicMock()

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            libvirtError=FakeLibvirtError,
        ))
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=MagicMock(side_effect=FakeLibvirtError("not found")),
        )

        result = provider._node_precheck_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp"), Path("/tmp/disks"),
        )
        assert result == (False, None, None)


# ===========================================================================
# 3. _get_domain_metadata_values: XML metadata extraction
# ===========================================================================

class TestGetDomainMetadataValues:
    """Tests for _get_domain_metadata_values — extracts kind/readiness from XML."""

    def test_extracts_kind_and_readiness_fields(self):
        provider = _make_provider()
        xml = """<domain>
          <metadata>
            <archetype xmlns="urn:archetype">
              <kind>cisco_iosv</kind>
              <readiness_probe>log_pattern</readiness_probe>
              <readiness_pattern>login:</readiness_pattern>
              <readiness_timeout>300</readiness_timeout>
            </archetype>
          </metadata>
        </domain>"""
        domain = SimpleNamespace(XMLDesc=lambda: xml)
        values = provider._get_domain_metadata_values(domain)
        assert values == {
            "kind": "cisco_iosv",
            "readiness_probe": "log_pattern",
            "readiness_pattern": "login:",
            "readiness_timeout": "300",
        }

    def test_returns_empty_when_no_metadata(self):
        provider = _make_provider()
        xml = "<domain><name>test</name></domain>"
        domain = SimpleNamespace(XMLDesc=lambda: xml)
        assert provider._get_domain_metadata_values(domain) == {}

    def test_skips_empty_text_values(self):
        provider = _make_provider()
        xml = """<domain>
          <metadata>
            <archetype xmlns="urn:archetype">
              <kind>  </kind>
              <readiness_probe>tcp_port</readiness_probe>
            </archetype>
          </metadata>
        </domain>"""
        domain = SimpleNamespace(XMLDesc=lambda: xml)
        values = provider._get_domain_metadata_values(domain)
        # kind has only whitespace -> skipped
        assert "kind" not in values
        assert values["readiness_probe"] == "tcp_port"


# ===========================================================================
# 4. _get_domain_readiness_overrides: parses timeout as int, skips bad values
# ===========================================================================

class TestGetDomainReadinessOverrides:
    def test_parses_valid_timeout(self):
        provider = _make_provider()
        xml = """<domain><metadata>
          <a xmlns="urn:archetype">
            <readiness_probe>log_pattern</readiness_probe>
            <readiness_timeout>600</readiness_timeout>
          </a>
        </metadata></domain>"""
        domain = SimpleNamespace(XMLDesc=lambda: xml)
        overrides = provider._get_domain_readiness_overrides(domain)
        assert overrides == {"readiness_probe": "log_pattern", "readiness_timeout": 600}

    def test_skips_invalid_timeout(self):
        provider = _make_provider()
        xml = """<domain><metadata>
          <a xmlns="urn:archetype">
            <readiness_timeout>not_a_number</readiness_timeout>
          </a>
        </metadata></domain>"""
        domain = SimpleNamespace(XMLDesc=lambda: xml)
        overrides = provider._get_domain_readiness_overrides(domain)
        assert "readiness_timeout" not in overrides

    def test_skips_zero_or_negative_timeout(self):
        provider = _make_provider()
        xml = """<domain><metadata>
          <a xmlns="urn:archetype"><readiness_timeout>0</readiness_timeout></a>
        </metadata></domain>"""
        domain = SimpleNamespace(XMLDesc=lambda: xml)
        assert "readiness_timeout" not in provider._get_domain_readiness_overrides(domain)


# ===========================================================================
# 5. _verify_backing_image: SHA256 checks, page cache drop, corruption error
# ===========================================================================

class TestVerifyBackingImage:
    def test_no_expected_sha_is_noop(self, tmp_path):
        provider = _make_provider()
        f = tmp_path / "image.qcow2"
        f.write_bytes(b"data")
        # Should not raise
        provider._verify_backing_image(str(f), None)

    def test_matching_sha_passes(self, tmp_path):
        provider = _make_provider()
        data = b"test image content"
        f = tmp_path / "image.qcow2"
        f.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        provider._verify_backing_image(str(f), sha)

    def test_mismatch_with_page_cache_recovery(self, tmp_path):
        """If first hash mismatches but second (after cache drop) matches, no error."""
        provider = _make_provider()
        data = b"good content"
        f = tmp_path / "image.qcow2"
        f.write_bytes(data)
        correct_sha = hashlib.sha256(data).hexdigest()

        # First call returns wrong hash, second returns correct
        call_count = [0]
        real_compute = provider._compute_file_sha256

        def fake_compute(path):
            call_count[0] += 1
            if call_count[0] == 1:
                return "0" * 64
            return real_compute(path)

        provider._compute_file_sha256 = fake_compute
        original_open = open

        def patched_open(path, *args, **kwargs):
            if str(path) == "/proc/sys/vm/drop_caches":
                m = MagicMock()
                m.__enter__ = lambda s: MagicMock()
                m.__exit__ = lambda *x: None
                return m
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=patched_open):
            # Should not raise — second compute returns the correct hash
            provider._verify_backing_image(str(f), correct_sha)
        assert call_count[0] == 2

    def test_permanent_mismatch_raises(self, tmp_path):
        """If SHA never matches (even after cache drop), raises RuntimeError."""
        provider = _make_provider()
        f = tmp_path / "image.qcow2"
        f.write_bytes(b"some content")

        wrong_sha = "a" * 64

        # Patch only the drop_caches open to avoid permission error
        original_open = open

        def patched_open(path, *args, **kwargs):
            if str(path) == "/proc/sys/vm/drop_caches":
                m = MagicMock()
                m.__enter__ = lambda s: MagicMock()
                m.__exit__ = lambda *x: None
                return m
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=patched_open):
            with pytest.raises(RuntimeError, match="integrity check failed"):
                provider._verify_backing_image(str(f), wrong_sha)


# ===========================================================================
# 6. _get_base_image: resolution from absolute path, qcow2 store, partial match
# ===========================================================================

class TestGetBaseImage:
    def test_none_image_returns_none(self):
        provider = _make_provider()
        assert provider._get_base_image({}) is None
        assert provider._get_base_image({"image": ""}) is None

    def test_absolute_path_existing(self, tmp_path):
        provider = _make_provider()
        img = tmp_path / "test.qcow2"
        img.write_bytes(b"qcow2")
        assert provider._get_base_image({"image": str(img)}) == str(img)

    def test_absolute_path_missing(self):
        provider = _make_provider()
        assert provider._get_base_image({"image": "/nonexistent/path.qcow2"}) is None

    def test_relative_exact_match(self, tmp_path, monkeypatch):
        provider = _make_provider()
        monkeypatch.setattr(libvirt_mod.settings, "qcow2_store_path", str(tmp_path), raising=False)
        img = tmp_path / "router.qcow2"
        img.write_bytes(b"data")
        assert provider._get_base_image({"image": "router.qcow2"}) == str(img)

    def test_relative_with_auto_extension(self, tmp_path, monkeypatch):
        provider = _make_provider()
        monkeypatch.setattr(libvirt_mod.settings, "qcow2_store_path", str(tmp_path), raising=False)
        img = tmp_path / "switch.qcow2"
        img.write_bytes(b"data")
        assert provider._get_base_image({"image": "switch"}) == str(img)

    def test_partial_name_match(self, tmp_path, monkeypatch):
        provider = _make_provider()
        monkeypatch.setattr(libvirt_mod.settings, "qcow2_store_path", str(tmp_path), raising=False)
        img = tmp_path / "cisco-iosv-l2-17.12.qcow2"
        img.write_bytes(b"data")
        # "iosv" is a partial match for the filename
        result = provider._get_base_image({"image": "iosv"})
        assert result == str(img)


# ===========================================================================
# 7. _domain_has_dedicated_mgmt_interface: XML introspection
# ===========================================================================

class TestDomainHasDedicatedMgmtInterface:
    def test_true_when_network_interface_present(self):
        provider = _make_provider()
        xml = """<domain><devices>
          <interface type='network'><source network='default'/></interface>
          <interface type='bridge'><source bridge='arch-ovs'/></interface>
        </devices></domain>"""
        domain = SimpleNamespace(XMLDesc=lambda _flags: xml)
        assert provider._domain_has_dedicated_mgmt_interface(domain) is True

    def test_false_when_only_bridge_interfaces(self):
        provider = _make_provider()
        xml = """<domain><devices>
          <interface type='bridge'><source bridge='arch-ovs'/></interface>
        </devices></domain>"""
        domain = SimpleNamespace(XMLDesc=lambda _flags: xml)
        assert provider._domain_has_dedicated_mgmt_interface(domain) is False

    def test_false_when_no_interfaces(self):
        provider = _make_provider()
        xml = "<domain><devices></devices></domain>"
        domain = SimpleNamespace(XMLDesc=lambda _flags: xml)
        assert provider._domain_has_dedicated_mgmt_interface(domain) is False

    def test_false_on_xml_parse_error(self):
        provider = _make_provider()
        domain = SimpleNamespace(XMLDesc=MagicMock(side_effect=RuntimeError("boom")))
        assert provider._domain_has_dedicated_mgmt_interface(domain) is False


# ===========================================================================
# 8. _mark_post_boot_console_ownership_pending: console control state
# ===========================================================================

class TestMarkPostBootConsoleOwnership:
    def test_no_kind_is_noop(self):
        provider = _make_provider()
        provider._set_vm_console_control_state = MagicMock()
        provider._clear_vm_console_control_state = MagicMock()
        provider._mark_post_boot_console_ownership_pending("arch-lab1-r1", None)
        provider._set_vm_console_control_state.assert_not_called()
        provider._clear_vm_console_control_state.assert_not_called()

    def test_kind_without_post_boot_commands_clears(self, monkeypatch):
        provider = _make_provider()
        provider._clear_vm_console_control_state = MagicMock()
        provider._set_vm_console_control_state = MagicMock()
        # Vendor config with no post_boot_commands
        mock_config = SimpleNamespace(post_boot_commands=None)
        monkeypatch.setattr(libvirt_mod, "get_vendor_config", lambda _k: mock_config)
        monkeypatch.setattr(libvirt_mod, "get_kind_for_device", lambda k: k)

        provider._mark_post_boot_console_ownership_pending("arch-lab1-r1", "iosv")
        provider._clear_vm_console_control_state.assert_called_once()
        provider._set_vm_console_control_state.assert_not_called()

    def test_kind_with_post_boot_commands_sets_read_only(self, monkeypatch):
        provider = _make_provider()
        provider._clear_vm_console_control_state = MagicMock()
        provider._set_vm_console_control_state = MagicMock()
        mock_config = SimpleNamespace(post_boot_commands=["cmd1", "cmd2"])
        monkeypatch.setattr(libvirt_mod, "get_vendor_config", lambda _k: mock_config)
        monkeypatch.setattr(libvirt_mod, "get_kind_for_device", lambda k: k)

        provider._mark_post_boot_console_ownership_pending("arch-lab1-r1", "ceos")
        provider._set_vm_console_control_state.assert_called_once()
        call_kwargs = provider._set_vm_console_control_state.call_args[1]
        assert call_kwargs["state"] == "read_only"

    def test_n9kv_with_boot_modifications_disabled_clears(self, monkeypatch):
        provider = _make_provider()
        provider._clear_vm_console_control_state = MagicMock()
        provider._set_vm_console_control_state = MagicMock()
        monkeypatch.setattr(libvirt_mod, "get_kind_for_device", lambda k: "cisco_n9kv")
        monkeypatch.setattr(
            libvirt_mod.settings, "n9kv_boot_modifications_enabled", False, raising=False
        )

        provider._mark_post_boot_console_ownership_pending("arch-lab1-n9k", "n9kv")
        provider._clear_vm_console_control_state.assert_called_once()
        provider._set_vm_console_control_state.assert_not_called()


# ===========================================================================
# 9. _clear_vm_post_boot_commands_cache: clears all N9Kv lifecycle state
# ===========================================================================

class TestClearVmPostBootCommandsCache:
    def test_clears_all_n9kv_state(self, monkeypatch):
        provider = _make_provider()
        domain_name = "arch-lab1-n9k"

        # Populate all per-VM state dicts
        provider._n9kv_loader_recovery_attempts[domain_name] = 3
        provider._n9kv_loader_recovery_last_at[domain_name] = 100.0
        provider._n9kv_poap_skip_attempted.add(domain_name)
        provider._n9kv_admin_password_completed.add(domain_name)
        provider._n9kv_panic_recovery_attempts[domain_name] = 1
        provider._n9kv_panic_recovery_last_at[domain_name] = 50.0
        provider._n9kv_panic_last_log_size[domain_name] = 1024

        # Mock the imports that _clear_vm_post_boot_commands_cache uses
        monkeypatch.setattr(
            libvirt_mod.LibvirtProvider,
            "_clear_vm_console_control_state",
            staticmethod(lambda _dn: None),
        )

        with patch("agent.providers.libvirt.logger"):
            # Mock the dynamic import
            with patch.dict("sys.modules", {
                "agent.console_extractor": MagicMock(
                    clear_vm_post_boot_cache=MagicMock()
                )
            }):
                provider._clear_vm_post_boot_commands_cache(domain_name)

        # All per-VM state should be cleared
        assert domain_name not in provider._n9kv_loader_recovery_attempts
        assert domain_name not in provider._n9kv_loader_recovery_last_at
        assert domain_name not in provider._n9kv_poap_skip_attempted
        assert domain_name not in provider._n9kv_admin_password_completed
        assert domain_name not in provider._n9kv_panic_recovery_attempts
        assert domain_name not in provider._n9kv_panic_recovery_last_at
        assert domain_name not in provider._n9kv_panic_last_log_size


# ===========================================================================
# 10. _destroy_sync: lab-level destroy, disk + serial log cleanup
# ===========================================================================

class TestDestroySync:
    def test_destroys_running_and_inactive_domains(self, tmp_path, monkeypatch):
        provider = _make_provider()
        provider._vlan_allocations = {"lab1": {"r1": [100]}}
        provider._next_vlan = {"lab1": 200}
        provider._clear_vm_post_boot_commands_cache = MagicMock()
        provider._teardown_n9kv_poap_network = MagicMock()
        provider._remove_vlan_file = MagicMock()

        # Fake libvirt constants
        VIR_DOMAIN_RUNNING = 1
        VIR_DOMAIN_SHUTOFF = 5

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            VIR_CONNECT_LIST_DOMAINS_ACTIVE=1,
            VIR_CONNECT_LIST_DOMAINS_INACTIVE=2,
            VIR_DOMAIN_RUNNING=VIR_DOMAIN_RUNNING,
            libvirtError=FakeLibvirtError,
        ))
        monkeypatch.setattr(
            libvirt_mod, "sanitize_id", lambda v, max_len=20: v[:max_len],
        )

        running_domain = SimpleNamespace(
            name=lambda: "arch-lab1-r1",
            state=lambda: (VIR_DOMAIN_RUNNING, 0),
            destroy=MagicMock(),
        )
        stopped_domain = SimpleNamespace(
            name=lambda: "arch-lab1-sw1",
            state=lambda: (VIR_DOMAIN_SHUTOFF, 0),
        )
        other_domain = SimpleNamespace(
            name=lambda: "other-vm",
            state=lambda: (VIR_DOMAIN_RUNNING, 0),
        )

        provider._undefine_domain = MagicMock()

        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            listAllDomains=lambda flags: {
                1: [running_domain],
                2: [stopped_domain, other_domain],
            }.get(flags, []),
        )

        # Create disks and serial logs
        disks_dir = tmp_path / "disks"
        disks_dir.mkdir()
        (disks_dir / "r1.qcow2").write_bytes(b"disk1")
        serial_dir = tmp_path / "serial-logs"
        serial_dir.mkdir()
        (serial_dir / "arch-lab1-r1.log").write_text("log data")

        destroyed, errors, fatal = provider._destroy_sync("lab1", tmp_path)

        assert fatal is None
        assert destroyed == 2  # r1 and sw1
        assert len(errors) == 0
        # Running domain was destroy()'d, other wasn't
        running_domain.destroy.assert_called_once()
        # Disks cleaned up
        assert not (disks_dir / "r1.qcow2").exists()
        # Serial logs cleaned up
        assert not serial_dir.exists()
        # VLAN state cleaned up
        assert "lab1" not in provider._vlan_allocations
        assert "lab1" not in provider._next_vlan
        provider._remove_vlan_file.assert_called_once()

    def test_fatal_exception_returns_error(self, tmp_path, monkeypatch):
        """Top-level exception in _destroy_sync returns as fatal."""
        provider = _make_provider()

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            VIR_CONNECT_LIST_DOMAINS_ACTIVE=1,
            VIR_CONNECT_LIST_DOMAINS_INACTIVE=2,
        ))
        monkeypatch.setattr(
            libvirt_mod, "sanitize_id", lambda v, max_len=20: v[:max_len],
        )

        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            listAllDomains=MagicMock(side_effect=RuntimeError("libvirt dead")),
        )

        destroyed, errors, fatal = provider._destroy_sync("lab1", tmp_path)
        assert destroyed == 0
        assert "libvirt dead" in fatal


# ===========================================================================
# 11. _deploy_node_define_start_sync: define + start domain
# ===========================================================================

class TestDeployNodeDefineStartSync:
    def test_defines_starts_and_returns_uuid(self):
        provider = _make_provider()
        provider._clear_vm_post_boot_commands_cache = MagicMock()
        provider._mark_post_boot_console_ownership_pending = MagicMock()

        fake_domain = SimpleNamespace(
            create=MagicMock(),
            UUIDString=lambda: "abcdef12-3456-7890",
        )
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            defineXML=MagicMock(return_value=fake_domain),
        )

        uuid = provider._deploy_node_define_start_sync("arch-lab1-r1", "<domain/>", "iosv")

        assert uuid == "abcdef12-345"
        fake_domain.create.assert_called_once()
        provider._clear_vm_post_boot_commands_cache.assert_called_once_with("arch-lab1-r1")
        provider._mark_post_boot_console_ownership_pending.assert_called_once_with(
            "arch-lab1-r1", "iosv",
        )

    def test_raises_when_define_returns_none(self):
        provider = _make_provider()
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            defineXML=MagicMock(return_value=None),
        )

        with pytest.raises(RuntimeError, match="Failed to define"):
            provider._deploy_node_define_start_sync("arch-lab1-r1", "<domain/>", None)


# ===========================================================================
# 12. _start_node_sync: domain start with error paths
# ===========================================================================

class TestStartNodeSync:
    def test_already_running(self, monkeypatch):
        provider = _make_provider()

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            VIR_DOMAIN_RUNNING=1,
            libvirtError=FakeLibvirtError,
        ))

        fake_domain = SimpleNamespace(
            state=lambda: (1, 0),
            create=MagicMock(),
        )
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=lambda _n: fake_domain,
        )

        status, kind, error = provider._start_node_sync("arch-lab1-r1")
        assert status == "already_running"
        fake_domain.create.assert_not_called()

    def test_successful_start(self, monkeypatch):
        provider = _make_provider()
        provider._clear_vm_post_boot_commands_cache = MagicMock()
        provider._mark_post_boot_console_ownership_pending = MagicMock()
        provider._get_domain_kind = MagicMock(return_value="iosv")

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            VIR_DOMAIN_RUNNING=1,
            libvirtError=FakeLibvirtError,
        ))

        fake_domain = SimpleNamespace(
            state=lambda: (5, 0),  # SHUTOFF
            create=MagicMock(),
        )
        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=lambda _n: fake_domain,
        )

        status, kind, error = provider._start_node_sync("arch-lab1-r1")
        assert status == "started"
        assert kind == "iosv"
        assert error is None

    def test_libvirt_error_clears_console_state(self, monkeypatch):
        provider = _make_provider()
        provider._clear_vm_console_control_state = MagicMock()

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            VIR_DOMAIN_RUNNING=1,
            libvirtError=FakeLibvirtError,
        ))

        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=MagicMock(side_effect=FakeLibvirtError("permission denied")),
        )

        status, kind, error = provider._start_node_sync("arch-lab1-r1")
        assert status == "error"
        assert "Libvirt error" in error
        provider._clear_vm_console_control_state.assert_called_once()

    def test_generic_error_clears_console_state(self, monkeypatch):
        provider = _make_provider()
        provider._clear_vm_console_control_state = MagicMock()

        class FakeLibvirtError(Exception):
            pass

        monkeypatch.setattr(libvirt_mod, "libvirt", SimpleNamespace(
            VIR_DOMAIN_RUNNING=1,
            libvirtError=FakeLibvirtError,
        ))

        provider._conn = SimpleNamespace(
            isAlive=lambda: True,
            lookupByName=MagicMock(side_effect=ValueError("bad value")),
        )

        status, kind, error = provider._start_node_sync("arch-lab1-r1")
        assert status == "error"
        assert error == "bad value"
        provider._clear_vm_console_control_state.assert_called_once()
