"""Extended tests for agent/main.py utility functions — full coverage.

Covers edge cases and branches not exercised by test_main_lifespan_coverage.py:
- _parse_driver_status: int/bool coercion, single-element items, empty inner lists
- _classify_docker_snapshotter_mode: empty-string driver_type, overlayfs+containerd,
  non-overlay driver with non-snapshotter type
- _parse_metrics_allowlist: single host IP (/32), duplicate entries, cache behavior,
  trailing commas, IPv6 host
- _client_host_from_request: whitespace-only forwarded-for, multi-hop with spaces
- _is_metrics_client_allowed: second CIDR match, whitespace-padded client, mixed v4/v6
"""

from __future__ import annotations

import ipaddress
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure agent root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.main import (
    _classify_docker_snapshotter_mode,
    _client_host_from_request,
    _is_metrics_client_allowed,
    _parse_driver_status,
    _parse_metrics_allowlist,
)


# ---------------------------------------------------------------------------
# _parse_driver_status — additional edge cases
# ---------------------------------------------------------------------------


class TestParseDriverStatusEdgeCases:
    """Edge cases for _parse_driver_status coercion and filtering."""

    def test_integer_keys_and_values_coerced_to_str(self):
        """Non-string keys/values are coerced via str()."""
        result = _parse_driver_status([[42, True], [None, 3.14]])
        assert result == {"42": "True", "None": "3.14"}

    def test_single_element_items_skipped(self):
        """Items with length != 2 are silently skipped."""
        result = _parse_driver_status([["only_one"], ["k", "v"]])
        assert result == {"k": "v"}

    def test_empty_inner_lists_skipped(self):
        """Empty sub-lists are skipped."""
        result = _parse_driver_status([[], ["a", "b"]])
        assert result == {"a": "b"}

    def test_dict_input_returns_empty(self):
        """A dict (not a list) returns empty dict."""
        assert _parse_driver_status({"key": "value"}) == {}

    def test_nested_list_pairs_with_extra_nesting(self):
        """Only top-level items are inspected; deeply nested data is skipped."""
        result = _parse_driver_status([[["nested"], "val"]])
        # The key is ['nested'] coerced to str
        assert result == {"['nested']": "val"}


# ---------------------------------------------------------------------------
# _classify_docker_snapshotter_mode — additional branches
# ---------------------------------------------------------------------------


class TestClassifySnapshotterModeEdgeCases:
    """Edge cases for _classify_docker_snapshotter_mode."""

    def test_empty_string_driver_type_is_legacy(self):
        """Empty string is falsy -> legacy."""
        assert _classify_docker_snapshotter_mode("overlay2", "") == "legacy"

    def test_overlayfs_with_containerd_snapshotter(self):
        """overlayfs driver + containerd snapshotter type -> containerd."""
        assert _classify_docker_snapshotter_mode(
            "overlayfs", "io.containerd.snapshotter.v1.native"
        ) == "containerd"

    def test_non_overlay_driver_with_non_snapshotter_type(self):
        """btrfs + some type without 'snapshotter' -> unknown (not legacy)."""
        assert _classify_docker_snapshotter_mode(
            "btrfs", "io.containerd.content.v1"
        ) == "unknown"

    def test_overlay2_with_snapshotter_in_type_but_not_containerd(self):
        """overlay2 + type containing 'snapshotter' but not containerd prefix -> unknown."""
        # driver_type has "snapshotter" so the third branch doesn't match
        # but it also doesn't match containerd prefix -> falls to unknown
        assert _classify_docker_snapshotter_mode(
            "overlay2", "some.snapshotter.custom"
        ) == "unknown"

    def test_devicemapper_driver_no_type(self):
        """devicemapper with no driver-type -> legacy."""
        assert _classify_docker_snapshotter_mode("devicemapper", None) == "legacy"

    def test_containerd_snapshotter_v1_substring_match(self):
        """Any driver_type containing the containerd prefix returns containerd."""
        assert _classify_docker_snapshotter_mode(
            "zfs", "prefix.io.containerd.snapshotter.v1.suffix"
        ) == "containerd"


# ---------------------------------------------------------------------------
# _parse_metrics_allowlist — additional edge cases
# ---------------------------------------------------------------------------


class TestParseMetricsAllowlistEdgeCases:
    """Edge cases for _parse_metrics_allowlist."""

    def setup_method(self):
        _parse_metrics_allowlist.cache_clear()

    def test_single_host_ip_parsed_as_network(self):
        """A bare IP like '10.0.0.1' parses as /32 network."""
        networks, literals = _parse_metrics_allowlist("10.0.0.1")
        assert len(networks) == 1
        assert networks[0] == ipaddress.ip_network("10.0.0.1/32")
        assert literals == frozenset()

    def test_duplicate_entries_preserved_in_networks(self):
        """Duplicate CIDRs produce duplicate network objects."""
        networks, literals = _parse_metrics_allowlist("10.0.0.0/8, 10.0.0.0/8")
        assert len(networks) == 2

    def test_trailing_commas_produce_no_extras(self):
        """Trailing/leading commas don't create spurious entries."""
        networks, literals = _parse_metrics_allowlist(",10.0.0.0/8,,")
        assert len(networks) == 1
        assert literals == frozenset()

    def test_ipv6_host_parsed_as_network(self):
        """Bare IPv6 address '::1' parses as /128."""
        networks, literals = _parse_metrics_allowlist("::1")
        assert len(networks) == 1
        assert networks[0] == ipaddress.ip_network("::1/128")

    def test_cache_info_reports_hits(self):
        """lru_cache should track hit/miss statistics."""
        _parse_metrics_allowlist.cache_clear()
        _parse_metrics_allowlist("192.168.0.0/16")
        _parse_metrics_allowlist("192.168.0.0/16")
        info = _parse_metrics_allowlist.cache_info()
        assert info.hits >= 1
        assert info.misses >= 1

    def test_non_strict_network_parsing(self):
        """Host bits set should still parse (strict=False)."""
        networks, literals = _parse_metrics_allowlist("10.1.2.3/8")
        assert len(networks) == 1
        assert networks[0] == ipaddress.ip_network("10.0.0.0/8")


# ---------------------------------------------------------------------------
# _client_host_from_request — additional edge cases
# ---------------------------------------------------------------------------


class TestClientHostFromRequestEdgeCases:
    """Edge cases for _client_host_from_request."""

    def test_whitespace_only_forwarded_for_returns_none(self):
        """Whitespace-only X-Forwarded-For should return None."""
        req = MagicMock()
        req.client = None
        req.headers = {"x-forwarded-for": "   "}
        # After strip, first_hop is empty
        assert _client_host_from_request(req) is None

    def test_multi_hop_with_spaces(self):
        """Multi-hop with spaces around commas returns trimmed first hop."""
        req = MagicMock()
        req.client = None
        req.headers = {"x-forwarded-for": "  10.0.0.1 , 10.0.0.2 , 10.0.0.3 "}
        assert _client_host_from_request(req) == "10.0.0.1"

    def test_client_host_preferred_over_forwarded(self):
        """Direct client.host takes precedence over X-Forwarded-For."""
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "192.168.1.1"
        req.headers = {"x-forwarded-for": "10.0.0.1"}
        assert _client_host_from_request(req) == "192.168.1.1"


# ---------------------------------------------------------------------------
# _is_metrics_client_allowed — additional edge cases
# ---------------------------------------------------------------------------


class TestIsMetricsClientAllowedEdgeCases:
    """Edge cases for _is_metrics_client_allowed."""

    def setup_method(self):
        _parse_metrics_allowlist.cache_clear()

    def test_second_cidr_matches(self, monkeypatch):
        """Client matches the second CIDR in a multi-CIDR allowlist."""
        from agent.config import settings

        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8, 172.16.0.0/12")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("172.16.5.5") is True

    def test_whitespace_padded_client(self, monkeypatch):
        """Client host with whitespace is stripped before comparison."""
        from agent.config import settings

        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("  10.1.2.3  ") is True

    def test_literal_host_no_cidr_match(self, monkeypatch):
        """Literal hostname matches but IP doesn't match any CIDR."""
        from agent.config import settings

        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "myhost, 10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("myhost") is True
        assert _is_metrics_client_allowed("otherhost") is False

    def test_none_allowlist_allows_all(self, monkeypatch):
        """None metrics_allowed_cidrs allows all clients."""
        from agent.config import settings

        monkeypatch.setattr(settings, "metrics_allowed_cidrs", None)
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("anything") is True

    def test_ipv6_in_ipv4_cidr_denied(self, monkeypatch):
        """IPv6 client should not match IPv4 CIDR (version mismatch)."""
        from agent.config import settings

        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("::1") is False
