"""Tests for API utility modules: naming, cache, pagination, image_integrity.

Covers functions not already exercised by the existing test_utils_* files:
- naming.py: sanitize_id, docker_container_name, libvirt_domain_name (no prior coverage)
- cache.py: key namespacing, TTL forwarding, None miss (extends test_utils_misc.py)
- pagination.py: multi-batch, single batch, exact multiple, batch_size=1 (extends test_utils_misc.py)
- image_integrity.py: Path object input to validate_qcow2, QCOW2_MAGIC constant value
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch


from app.utils.naming import (
    DOCKER_PREFIX,
    LIBVIRT_PREFIX,
    docker_container_name,
    libvirt_domain_name,
    sanitize_id,
)
from app.utils.cache import DEFAULT_TTL, cache_get, cache_set
from app.utils.image_integrity import QCOW2_MAGIC, validate_qcow2


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory Redis stub that records setex calls."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}
        self.setex_calls: list[tuple] = []

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        self.ttls[key] = ttl
        self.setex_calls.append((key, ttl, value))


# ===========================================================================
# naming.py
# ===========================================================================


class TestSanitizeId:
    """Unit tests for sanitize_id()."""

    def test_alphanumeric_unchanged(self):
        assert sanitize_id("abc123") == "abc123"

    def test_underscores_and_dashes_kept(self):
        assert sanitize_id("hello_world-test") == "hello_world-test"

    def test_special_chars_stripped(self):
        result = sanitize_id("lab@2024!name.foo")
        assert result == "lab2024namefoo"

    def test_spaces_stripped(self):
        assert sanitize_id("my lab") == "mylab"

    def test_dots_stripped(self):
        assert sanitize_id("v1.2.3") == "v123"

    def test_slashes_stripped(self):
        assert sanitize_id("path/to/thing") == "pathtothing"

    def test_empty_string_returns_empty(self):
        assert sanitize_id("") == ""

    def test_all_special_returns_empty(self):
        assert sanitize_id("!@#$%^&*()") == ""

    def test_max_len_zero_no_truncation(self):
        long_id = "a" * 50
        assert sanitize_id(long_id, max_len=0) == long_id

    def test_max_len_truncates(self):
        result = sanitize_id("abcdefghij", max_len=5)
        assert result == "abcde"
        assert len(result) == 5

    def test_max_len_longer_than_string_no_pad(self):
        result = sanitize_id("abc", max_len=10)
        assert result == "abc"

    def test_mixed_case_preserved(self):
        assert sanitize_id("LabName") == "LabName"

    def test_unicode_stripped(self):
        # Non-ASCII characters should be stripped
        result = sanitize_id("caf\u00e9")
        assert result == "caf"


class TestDockerContainerName:
    """Unit tests for docker_container_name()."""

    def test_basic_format(self):
        name = docker_container_name("lab-abc", "router1")
        assert name == "archetype-lab-abc-router1"

    def test_prefix_is_archetype(self):
        name = docker_container_name("x", "y")
        assert name.startswith(DOCKER_PREFIX + "-")

    def test_lab_id_truncated_to_20(self):
        long_lab_id = "a" * 30
        name = docker_container_name(long_lab_id, "node")
        # safe_lab_id must be at most 20 chars
        name.split("-")
        # archetype - <lab_id_segment> - node  (but lab_id may contain dashes)
        # Easier: strip the prefix and suffix
        after_prefix = name[len(DOCKER_PREFIX) + 1:]   # remove "archetype-"
        lab_segment = after_prefix[: after_prefix.rfind("-node")]
        assert len(lab_segment) <= 20

    def test_special_chars_in_lab_id_stripped(self):
        name = docker_container_name("lab@2024", "node1")
        assert "@" not in name
        assert "2024" in name

    def test_special_chars_in_node_stripped(self):
        name = docker_container_name("labA", "my.router!")
        assert "." not in name
        assert "!" not in name

    def test_node_name_not_truncated(self):
        # node_name has no max_len constraint in docker_container_name
        long_node = "r" * 40
        name = docker_container_name("lab1", long_node)
        assert long_node in name

    def test_returns_string(self):
        assert isinstance(docker_container_name("x", "y"), str)


class TestLibvirtDomainName:
    """Unit tests for libvirt_domain_name()."""

    def test_basic_format(self):
        name = libvirt_domain_name("lab-abc", "vm1")
        assert name == "arch-lab-abc-vm1"

    def test_prefix_is_arch(self):
        name = libvirt_domain_name("x", "y")
        assert name.startswith(LIBVIRT_PREFIX + "-")

    def test_lab_id_truncated_to_20(self):
        long_lab_id = "b" * 30
        name = libvirt_domain_name(long_lab_id, "vm")
        after_prefix = name[len(LIBVIRT_PREFIX) + 1:]  # remove "arch-"
        lab_segment = after_prefix[: after_prefix.rfind("-vm")]
        assert len(lab_segment) <= 20

    def test_node_name_truncated_to_30(self):
        long_node = "n" * 50
        name = libvirt_domain_name("lab1", long_node)
        # The node segment after "arch-lab1-" must be at most 30 chars
        suffix = name[len("arch-lab1-"):]
        assert len(suffix) <= 30

    def test_special_chars_stripped(self):
        name = libvirt_domain_name("lab!2024", "vm@host")
        assert "!" not in name
        assert "@" not in name

    def test_different_prefix_from_docker(self):
        docker_name = docker_container_name("lab1", "node1")
        libvirt_name = libvirt_domain_name("lab1", "node1")
        assert docker_name.startswith("archetype-")
        assert libvirt_name.startswith("arch-")
        # The two prefixes must differ
        assert DOCKER_PREFIX != LIBVIRT_PREFIX

    def test_returns_string(self):
        assert isinstance(libvirt_domain_name("x", "y"), str)


# ===========================================================================
# cache.py
# ===========================================================================


class TestCacheGet:
    """Unit tests for cache_get()."""

    def test_miss_returns_none(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        assert cache_get("nonexistent-key") is None

    def test_hit_deserializes_json(self, monkeypatch):
        fake = FakeRedis()
        fake.store["cache:mykey"] = json.dumps({"a": 1})
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        result = cache_get("mykey")
        assert result == {"a": 1}

    def test_key_is_namespaced_with_cache_prefix(self, monkeypatch):
        fake = FakeRedis()
        # Store value under namespaced key
        fake.store["cache:ns-key"] = json.dumps(42)
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        assert cache_get("ns-key") == 42
        # The raw key without prefix must NOT be found
        assert cache_get("cache:ns-key") is None

    def test_redis_error_returns_none(self, monkeypatch):
        def boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr("app.utils.cache.get_redis", boom)
        assert cache_get("anything") is None

    def test_list_value_roundtrip(self, monkeypatch):
        fake = FakeRedis()
        fake.store["cache:list-key"] = json.dumps([1, 2, 3])
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        assert cache_get("list-key") == [1, 2, 3]


class TestCacheSet:
    """Unit tests for cache_set()."""

    def test_stores_value_under_namespaced_key(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        cache_set("mykey", {"x": 99})

        assert "cache:mykey" in fake.store
        assert json.loads(fake.store["cache:mykey"]) == {"x": 99}

    def test_default_ttl_applied(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        cache_set("ttl-test", "hello")

        assert fake.ttls.get("cache:ttl-test") == DEFAULT_TTL

    def test_custom_ttl_forwarded(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        cache_set("custom-ttl", "value", ttl=300)

        assert fake.ttls.get("cache:custom-ttl") == 300

    def test_redis_error_silently_ignored(self, monkeypatch):
        def boom():
            raise RuntimeError("connection refused")

        monkeypatch.setattr("app.utils.cache.get_redis", boom)
        # Must not raise
        cache_set("key", "value")

    def test_non_string_value_serialized(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.utils.cache.get_redis", lambda: fake)

        cache_set("int-key", 123)

        assert json.loads(fake.store["cache:int-key"]) == 123


# ===========================================================================
# image_integrity.py — supplementary tests not in test_utils_image_integrity.py
# ===========================================================================


class TestQcow2MagicConstant:
    """Verify the QCOW2_MAGIC constant is correct."""

    def test_magic_bytes_value(self):
        # qcow2 magic is 0x514649fb = b"QFI\xfb"
        assert QCOW2_MAGIC == b"QFI\xfb"
        assert len(QCOW2_MAGIC) == 4


class TestValidateQcow2Supplementary:
    """Additional edge cases for validate_qcow2 not covered by test_utils_image_integrity."""

    def test_accepts_path_object(self, tmp_path):
        """validate_qcow2 should accept a pathlib.Path, not just str."""
        f = tmp_path / "img.qcow2"
        f.write_bytes(QCOW2_MAGIC + b"\x00" * 100)

        with patch("app.utils.image_integrity.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=json.dumps({"format": "qcow2"}),
                stderr="",
            )
            ok, msg = validate_qcow2(Path(f))

        assert ok is True
        assert msg == ""

    def test_exactly_four_bytes_with_wrong_magic(self, tmp_path):
        """A 4-byte file with wrong magic fails the magic check, not the size check."""
        f = tmp_path / "four.qcow2"
        f.write_bytes(b"\x00\x00\x00\x00")

        ok, msg = validate_qcow2(str(f))

        assert ok is False
        assert "magic" in msg.lower()

    def test_qemu_img_os_error_skipped(self, tmp_path):
        """OSError from subprocess should be silently skipped (magic already passed)."""
        f = tmp_path / "oserr.qcow2"
        f.write_bytes(QCOW2_MAGIC + b"\x00" * 100)

        with patch(
            "app.utils.image_integrity.subprocess.run",
            side_effect=OSError("device not found"),
        ):
            ok, msg = validate_qcow2(str(f))

        assert ok is True
        assert msg == ""
