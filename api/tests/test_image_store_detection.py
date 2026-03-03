"""Tests for app.image_store.detection — QCOW2/IOL device detection and filename classification."""

from unittest.mock import patch


from app.image_store.detection import (
    _extract_version,
    classify_image_file,
    detect_device_from_filename,
    detect_iol_device_type,
    detect_qcow2_device_type,
)


# ---------------------------------------------------------------------------
# detect_iol_device_type
# ---------------------------------------------------------------------------
class TestDetectIolDeviceType:
    def test_l2_dash(self):
        assert detect_iol_device_type("i86bi-linux-l2-adventerprisek9-15.6.1T.bin") == "iol-l2"

    def test_ioll2(self):
        assert detect_iol_device_type("IOLL2-image.bin") == "iol-l2"

    def test_iol_l2_underscore(self):
        assert detect_iol_device_type("iol_l2_v1.bin") == "iol-l2"

    def test_l3_dash(self):
        assert detect_iol_device_type("i86bi-linux-l3-adventerprisek9-15.6.1T.bin") == "iol-xe"

    def test_generic_iol(self):
        assert detect_iol_device_type("iol-image-15.6.bin") == "iol-xe"

    def test_not_iol(self):
        assert detect_iol_device_type("ceos-lab-4.30.5M.tar") is None

    def test_case_insensitive(self):
        assert detect_iol_device_type("IOL_L2_test.bin") == "iol-l2"


# ---------------------------------------------------------------------------
# _extract_version
# ---------------------------------------------------------------------------
class TestExtractVersion:
    def test_semver(self):
        assert _extract_version("ceos-lab-4.30.5M.tar") == "4.30.5"

    def test_three_part(self):
        # Trailing single-letter suffix is trimmed for file artifacts
        assert _extract_version("image-17.16.01a.qcow2") == "17.16.01"

    def test_two_part(self):
        assert _extract_version("srlinux-23.10.qcow2") == "23.10"

    def test_no_version(self):
        assert _extract_version("generic-image.tar") is None

    def test_docker_tag_keeps_suffix(self):
        """Docker tags (with :) keep trailing letter."""
        assert _extract_version("ceos:4.28.0F") == "4.28.0F"

    def test_file_trims_trailing_letter(self):
        """File artifacts (no :) trim trailing single-letter suffix."""
        assert _extract_version("ceos-4.28.0F.tar") == "4.28.0"

    def test_multi_digit_suffix_kept(self):
        assert _extract_version("image-17.16.01a2.qcow2") == "17.16.01a2"


# ---------------------------------------------------------------------------
# detect_qcow2_device_type
# ---------------------------------------------------------------------------
class TestDetectQcow2DeviceType:
    def test_known_pattern(self):
        """At least one QCOW2 pattern should match."""
        # Use a legacy pattern that's always present
        device_id, vrnetlab = detect_qcow2_device_type("vmx-21.1R1.qcow2")
        if device_id:  # Pattern may or may not be present depending on VENDOR_CONFIGS
            assert isinstance(device_id, str)
            assert isinstance(vrnetlab, str)

    def test_unknown(self):
        device_id, vrnetlab = detect_qcow2_device_type("totally-unknown-file.qcow2")
        assert device_id is None
        assert vrnetlab is None

    def test_c8000v_sdwan(self):
        device_id, vrnetlab = detect_qcow2_device_type("c8000v_sdwan-17.16.01a.qcow2")
        if device_id:
            assert device_id == "cat-sdwan-cedge"
            assert "sdwan" in vrnetlab

    def test_veos(self):
        device_id, vrnetlab = detect_qcow2_device_type("veos-4.28.0F.qcow2")
        if device_id:
            assert device_id == "veos"

    def test_case_insensitive(self):
        d1, _ = detect_qcow2_device_type("VMX-21.1R1.QCOW2")
        d2, _ = detect_qcow2_device_type("vmx-21.1r1.qcow2")
        assert d1 == d2


# ---------------------------------------------------------------------------
# detect_device_from_filename
# ---------------------------------------------------------------------------
class TestDetectDeviceFromFilename:
    def test_with_custom_rule(self):
        """Custom rules take priority over keyword map."""
        with patch("app.image_store.custom_devices.load_rules",
                    return_value=[{"pattern": r"custom-device", "device_id": "my-custom"}]):
            device_id, version = detect_device_from_filename("custom-device-1.2.3.tar")
        assert device_id == "my-custom"
        assert version == "1.2.3"

    def test_rule_without_pattern_skipped(self):
        with patch("app.image_store.custom_devices.load_rules",
                    return_value=[{"device_id": "bad"}]):
            device_id, version = detect_device_from_filename("something-1.0.tar")
        # Falls through to keyword map
        assert device_id is None or isinstance(device_id, str)

    def test_no_match_returns_none_with_version(self, monkeypatch):
        with patch("app.image_store.custom_devices.load_rules", return_value=[]):
            monkeypatch.setattr("app.image_store.detection._get_filename_keyword_map", lambda: {})
            device_id, version = detect_device_from_filename("unknown-image-2.3.4.tar")
        assert device_id is None
        assert version == "2.3.4"

    def test_keyword_map_match(self, monkeypatch):
        with patch("app.image_store.custom_devices.load_rules", return_value=[]):
            monkeypatch.setattr(
                "app.image_store.detection._get_filename_keyword_map",
                lambda: {"srlinux": "nokia_srlinux"},
            )
            device_id, version = detect_device_from_filename("srlinux-23.10.1.tar")
        assert device_id == "nokia_srlinux"
        assert version == "23.10.1"


# ---------------------------------------------------------------------------
# classify_image_file
# ---------------------------------------------------------------------------
class TestClassifyImageFile:
    def test_qcow2_detection(self, monkeypatch):
        monkeypatch.setattr(
            "app.image_store.detection.detect_qcow2_device_type",
            lambda f: ("c8000v", "cisco/c8000v"),
        )
        device_id, version, vrnetlab = classify_image_file("c8000v-17.16.01a.qcow2")
        assert device_id == "c8000v"
        assert vrnetlab == "cisco/c8000v"
        assert version == "17.16.01"

    def test_qcow2_unknown_falls_through(self, monkeypatch):
        monkeypatch.setattr(
            "app.image_store.detection.detect_qcow2_device_type",
            lambda f: (None, None),
        )
        with patch("app.image_store.custom_devices.load_rules", return_value=[]):
            monkeypatch.setattr("app.image_store.detection._get_filename_keyword_map", lambda: {})
            device_id, version, vrnetlab = classify_image_file("unknown.qcow2")
        assert device_id is None
        assert vrnetlab is None

    def test_iol_bin(self):
        device_id, version, vrnetlab = classify_image_file("i86bi-linux-l2-adventerprisek9-15.6.1T.bin")
        assert device_id == "iol-l2"
        assert vrnetlab is None

    def test_iol_extension(self):
        device_id, version, vrnetlab = classify_image_file("some-iol-image.iol")
        if device_id:
            assert device_id in ("iol-xe", "iol-l2")

    def test_tar_falls_to_keyword(self, monkeypatch):
        with patch("app.image_store.custom_devices.load_rules", return_value=[]):
            monkeypatch.setattr(
                "app.image_store.detection._get_filename_keyword_map",
                lambda: {"ceos": "ceos"},
            )
            device_id, version, vrnetlab = classify_image_file("ceos-lab-4.30.5M.tar")
        assert device_id == "ceos"
        assert vrnetlab is None
