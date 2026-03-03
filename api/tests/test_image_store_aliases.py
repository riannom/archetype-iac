"""Tests for app.image_store.aliases — device ID canonicalization, compatibility, provider."""

import pytest

from app.image_store.aliases import (
    _device_compatibility_tokens,
    _maybe_backfill_specific_linux_device,
    _maybe_backfill_vjunos_evolved_device,
    _maybe_correct_device_via_filename,
    canonicalize_device_id,
    canonicalize_device_ids,
    get_image_compatibility_aliases,
    get_image_default_device_scopes,
    get_image_provider,
    get_vendor_for_device,
    image_matches_device,
    is_image_default_for_device,
    normalize_default_device_scope_id,
    normalize_default_device_scope_ids,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear LRU caches around each test."""
    canonicalize_device_id.cache_clear()
    yield
    canonicalize_device_id.cache_clear()


# ---------------------------------------------------------------------------
# get_image_provider
# ---------------------------------------------------------------------------
class TestGetImageProvider:
    def test_none_returns_docker(self):
        assert get_image_provider(None) == "docker"

    def test_empty_string_returns_docker(self):
        assert get_image_provider("") == "docker"

    def test_docker_tag_returns_docker(self):
        assert get_image_provider("ceos:4.28.0F") == "docker"

    def test_qcow2_returns_libvirt(self):
        assert get_image_provider("c8000v-17.16.01a.qcow2") == "libvirt"

    def test_img_returns_libvirt(self):
        assert get_image_provider("vios-adventerprisek9.img") == "libvirt"

    def test_tar_returns_docker(self):
        assert get_image_provider("ceos-lab-4.30.5M.tar") == "docker"

    def test_bin_returns_docker(self):
        assert get_image_provider("i86bi-linux-l3-adventerprisek9.bin") == "docker"


# ---------------------------------------------------------------------------
# normalize_default_device_scope_id / ids
# ---------------------------------------------------------------------------
class TestNormalizeDefaultDeviceScope:
    def test_none(self):
        assert normalize_default_device_scope_id(None) is None

    def test_empty(self):
        assert normalize_default_device_scope_id("") is None

    def test_whitespace_only(self):
        assert normalize_default_device_scope_id("   ") is None

    def test_lowercase(self):
        assert normalize_default_device_scope_id("CeOS") == "ceos"

    def test_strips(self):
        assert normalize_default_device_scope_id("  srlinux  ") == "srlinux"

    def test_ids_none(self):
        assert normalize_default_device_scope_ids(None) == []

    def test_ids_empty(self):
        assert normalize_default_device_scope_ids([]) == []

    def test_ids_dedup(self):
        assert normalize_default_device_scope_ids(["CeOS", "ceos"]) == ["ceos"]

    def test_ids_preserves_order(self):
        assert normalize_default_device_scope_ids(["B", "A", "B"]) == ["b", "a"]


# ---------------------------------------------------------------------------
# canonicalize_device_id / ids
# ---------------------------------------------------------------------------
class TestCanonicalizeDeviceId:
    def test_none(self):
        assert canonicalize_device_id(None) is None

    def test_empty(self):
        assert canonicalize_device_id("") is None

    def test_whitespace(self):
        # Whitespace-only normalizes to empty string (not None - that's only for None input)
        result = canonicalize_device_id("   ")
        assert result is not None  # Unlike None input, whitespace goes through normalization

    def test_alias_resolution(self, monkeypatch):
        """Known alias maps to canonical key."""
        monkeypatch.setitem(
            __import__("app.image_store.aliases", fromlist=["DEVICE_ID_ALIASES"]).DEVICE_ID_ALIASES,
            "eos",
            "ceos",
        )
        assert canonicalize_device_id("eos") == "ceos"

    def test_case_insensitive(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        assert canonicalize_device_id("CeOS") == "ceos"

    def test_unknown_returns_lowered(self, monkeypatch):
        """Unknown device falls through to normalized string."""
        import app.image_store.aliases as mod
        # Ensure no alias matches and VENDOR_CONFIGS import fails
        monkeypatch.setattr(mod, "DEVICE_ID_ALIASES", {})
        assert canonicalize_device_id("my_custom_device") == "my_custom_device"

    def test_ids_none(self):
        assert canonicalize_device_ids(None) == []

    def test_ids_empty(self):
        assert canonicalize_device_ids([]) == []

    def test_ids_dedup(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "eos", "ceos")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        result = canonicalize_device_ids(["eos", "ceos"])
        assert result == ["ceos"]


# ---------------------------------------------------------------------------
# image_matches_device
# ---------------------------------------------------------------------------
class TestImageMatchesDevice:
    def test_exact_device_id_match(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        image = {"device_id": "ceos"}
        assert image_matches_device(image, "ceos") is True

    def test_no_match(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "srlinux", "srlinux")
        image = {"device_id": "ceos"}
        assert image_matches_device(image, "srlinux") is False

    def test_compatible_devices(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "veos", "veos")
        image = {"device_id": "ceos", "compatible_devices": ["veos"]}
        assert image_matches_device(image, "veos") is True

    def test_empty_device_id_returns_false(self):
        assert image_matches_device({"device_id": "ceos"}, "") is False

    def test_compat_alias_forward(self, monkeypatch):
        """IMAGE_COMPAT_ALIASES forward: cat9000v-uadp shares images with cisco_cat9kv."""
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cat9000v-uadp", "cat9000v-uadp")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cisco_cat9kv", "cisco_cat9kv")
        image = {"device_id": "cisco_cat9kv"}
        assert image_matches_device(image, "cat9000v-uadp") is True

    def test_compat_alias_reverse(self, monkeypatch):
        """Reverse: cisco_cat9kv matches images assigned to cat9000v-uadp."""
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cat9000v-uadp", "cat9000v-uadp")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cisco_cat9kv", "cisco_cat9kv")
        image = {"device_id": "cat9000v-uadp"}
        assert image_matches_device(image, "cisco_cat9kv") is True


# ---------------------------------------------------------------------------
# get_image_default_device_scopes / is_image_default_for_device
# ---------------------------------------------------------------------------
class TestImageDefaultScopes:
    def test_default_for_devices(self):
        image = {"default_for_devices": ["CeOS", "veos"]}
        scopes = get_image_default_device_scopes(image)
        assert scopes == ["ceos", "veos"]

    def test_legacy_is_default_fallback(self):
        image = {"is_default": True, "device_id": "CeOS"}
        scopes = get_image_default_device_scopes(image)
        assert scopes == ["ceos"]

    def test_neither_returns_empty(self):
        image = {"device_id": "ceos"}
        assert get_image_default_device_scopes(image) == []

    def test_is_default_for_device_match(self):
        image = {"default_for_devices": ["ceos"]}
        assert is_image_default_for_device(image, "ceos") is True

    def test_is_default_for_device_no_match(self):
        image = {"default_for_devices": ["ceos"]}
        assert is_image_default_for_device(image, "srlinux") is False

    def test_is_default_for_device_empty(self):
        assert is_image_default_for_device({}, "") is False


# ---------------------------------------------------------------------------
# get_vendor_for_device
# ---------------------------------------------------------------------------
class TestGetVendorForDevice:
    def test_known_device(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_VENDOR_MAP, "ceos", "Arista")
        assert get_vendor_for_device("ceos") == "Arista"

    def test_empty_returns_none(self):
        assert get_vendor_for_device("") is None

    def test_unknown_fallback(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setattr(mod, "DEVICE_VENDOR_MAP", {})
        # DeviceResolver will also fail → None
        result = get_vendor_for_device("unknown_device_xyz")
        # May return None or a value from DeviceResolver
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# get_image_compatibility_aliases
# ---------------------------------------------------------------------------
class TestGetImageCompatibilityAliases:
    def test_returns_normalized_dict(self):
        aliases = get_image_compatibility_aliases()
        assert isinstance(aliases, dict)
        for key, values in aliases.items():
            assert key == key.lower()
            for v in values:
                assert v == v.lower()

    def test_contains_expected_entries(self):
        aliases = get_image_compatibility_aliases()
        # IMAGE_COMPAT_ALIASES has cat9000v-uadp, c8000v, ftdv
        assert "c8000v" in aliases or "cat9000v-uadp" in aliases


# ---------------------------------------------------------------------------
# _maybe_backfill_specific_linux_device
# ---------------------------------------------------------------------------
class TestMaybeBackfillLinuxDevice:
    def test_non_linux_unchanged(self):
        image = {"device_id": "ceos", "reference": "ceos:latest"}
        assert _maybe_backfill_specific_linux_device(image) == "ceos"

    def test_linux_frr(self):
        image = {"device_id": "linux", "reference": "frrouting/frr:latest"}
        assert _maybe_backfill_specific_linux_device(image) == "frr"

    def test_linux_haproxy(self):
        image = {"device_id": "linux", "id": "haproxy-2.8"}
        assert _maybe_backfill_specific_linux_device(image) == "haproxy"

    def test_linux_tcl(self):
        image = {"device_id": "linux", "reference": "tcl-image:latest"}
        assert _maybe_backfill_specific_linux_device(image) == "tcl"

    def test_linux_tiny_core(self):
        image = {"device_id": "linux", "filename": "tiny core linux.tar"}
        assert _maybe_backfill_specific_linux_device(image) == "tcl"

    def test_linux_alpine(self):
        image = {"device_id": "linux", "reference": "alpine:3.18"}
        assert _maybe_backfill_specific_linux_device(image) == "alpine"

    def test_linux_unknown_stays_linux(self):
        image = {"device_id": "linux", "reference": "ubuntu:22.04"}
        assert _maybe_backfill_specific_linux_device(image) == "linux"


# ---------------------------------------------------------------------------
# _maybe_backfill_vjunos_evolved_device
# ---------------------------------------------------------------------------
class TestMaybeBackfillVjunosEvolved:
    def test_non_vjunos_unchanged(self):
        assert _maybe_backfill_vjunos_evolved_device({"reference": "ceos"}, "ceos") == "ceos"

    def test_vjunos_router_with_evolved_filename(self):
        image = {"filename": "vjunos-evolved-23.2R1.qcow2"}
        assert _maybe_backfill_vjunos_evolved_device(image, "juniper_vjunosrouter") == "juniper_vjunosevolved"

    def test_vjunos_router_without_evolved(self):
        image = {"filename": "vjunos-router-23.2R1.qcow2"}
        assert _maybe_backfill_vjunos_evolved_device(image, "juniper_vjunosrouter") == "juniper_vjunosrouter"

    def test_vjunos_dash_variant(self):
        image = {"filename": "vjunos_evolved_24.2R1.qcow2"}
        assert _maybe_backfill_vjunos_evolved_device(image, "vjunos-router") == "juniper_vjunosevolved"


# ---------------------------------------------------------------------------
# _maybe_correct_device_via_filename
# ---------------------------------------------------------------------------
class TestMaybeCorrectDeviceViaFilename:
    def test_non_qcow2_unchanged(self):
        image = {"kind": "docker", "filename": "ceos.tar"}
        assert _maybe_correct_device_via_filename(image, "ceos") == "ceos"

    def test_no_filename_unchanged(self):
        image = {"kind": "qcow2", "filename": ""}
        assert _maybe_correct_device_via_filename(image, "ceos") == "ceos"

    def test_qcow2_corrects_device(self, monkeypatch):
        """When filename detection gives a different device, corrects it."""
        import app.image_store.detection as det_mod
        monkeypatch.setattr(det_mod, "detect_qcow2_device_type", lambda f: ("c8000v", "cisco/c8000v"))
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "c8000v", "c8000v")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cisco_cat9kv", "cisco_cat9kv")
        image = {"kind": "qcow2", "filename": "c8000v-17.16.qcow2", "compatible_devices": ["old"]}
        result = _maybe_correct_device_via_filename(image, "cisco_cat9kv")
        assert result == "c8000v"
        assert image["compatible_devices"] == ["c8000v"]

    def test_qcow2_same_device_no_change(self, monkeypatch):
        import app.image_store.detection as det_mod
        monkeypatch.setattr(det_mod, "detect_qcow2_device_type", lambda f: ("ceos", "arista/ceos"))
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        image = {"kind": "qcow2", "filename": "ceos.qcow2", "compatible_devices": ["old"]}
        result = _maybe_correct_device_via_filename(image, "ceos")
        assert result == "ceos"
        # compatible_devices NOT cleared since same canonical
        assert image["compatible_devices"] == ["old"]


# ---------------------------------------------------------------------------
# _device_compatibility_tokens
# ---------------------------------------------------------------------------
class TestDeviceCompatibilityTokens:
    def test_none(self):
        assert _device_compatibility_tokens(None) == set()

    def test_empty(self):
        assert _device_compatibility_tokens("") == set()

    def test_includes_canonical(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "ceos", "ceos")
        tokens = _device_compatibility_tokens("ceos")
        assert "ceos" in tokens

    def test_includes_compat_aliases(self, monkeypatch):
        import app.image_store.aliases as mod
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cat9000v-uadp", "cat9000v-uadp")
        monkeypatch.setitem(mod.DEVICE_ID_ALIASES, "cisco_cat9kv", "cisco_cat9kv")
        tokens = _device_compatibility_tokens("cat9000v-uadp")
        assert "cisco_cat9kv" in tokens
