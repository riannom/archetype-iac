"""Tests for app.services.topology_resolution — image, kind, and port resolution."""

from types import SimpleNamespace
from unittest.mock import MagicMock


from app.services.topology_resolution import (
    NodePlacementInfo,
    TopologyAnalysisResult,
    resolve_device_kind,
    resolve_effective_max_ports,
    resolve_node_image,
)


# ---------------------------------------------------------------------------
# resolve_node_image
# ---------------------------------------------------------------------------
class TestResolveNodeImage:
    def test_explicit_image_wins(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.find_image_reference",
            lambda d, v: "manifest:latest",
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_default_image",
            lambda k: "vendor:default",
        )
        result = resolve_node_image("ceos", "ceos", explicit_image="my:image")
        assert result == "my:image"

    def test_manifest_image_second(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.find_image_reference",
            lambda d, v: "manifest:4.30",
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_default_image",
            lambda k: "vendor:default",
        )
        result = resolve_node_image("ceos", "ceos")
        assert result == "manifest:4.30"

    def test_vendor_default_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.find_image_reference",
            lambda d, v: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_default_image",
            lambda k: "ceos:4.28.0F",
        )
        result = resolve_node_image("ceos", "ceos")
        assert result == "ceos:4.28.0F"

    def test_no_image_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.find_image_reference",
            lambda d, v: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_default_image",
            lambda k: None,
        )
        result = resolve_node_image("unknown", "unknown")
        assert result is None

    def test_uses_device_for_manifest(self, monkeypatch):
        calls = []

        def capture_find(device, version):
            calls.append(("find", device, version))
            return None

        monkeypatch.setattr(
            "app.services.topology_resolution.find_image_reference",
            capture_find,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_default_image",
            lambda k: None,
        )
        resolve_node_image("ceos", "ceos", version="4.30")
        assert calls[0] == ("find", "ceos", "4.30")

    def test_null_device_uses_kind(self, monkeypatch):
        calls = []

        def capture_find(device, version):
            calls.append(device)
            return None

        monkeypatch.setattr(
            "app.services.topology_resolution.find_image_reference",
            capture_find,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_default_image",
            lambda k: None,
        )
        resolve_node_image(None, "linux")
        assert calls[0] == "linux"


# ---------------------------------------------------------------------------
# resolve_device_kind
# ---------------------------------------------------------------------------
class TestResolveDeviceKind:
    def test_none_returns_linux(self, monkeypatch):
        result = resolve_device_kind(None)
        assert result == "linux"

    def test_vendor_config_match(self, monkeypatch):
        mock_config = SimpleNamespace(kind="ceos")
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: mock_config,
        )
        result = resolve_device_kind("arista_ceos")
        assert result == "ceos"

    def test_custom_device_kind(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.find_custom_device",
            lambda d: {"kind": "ceos"},
        )
        result = resolve_device_kind("eos")
        assert result == "ceos"

    def test_custom_device_no_kind(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.find_custom_device",
            lambda d: {"id": "my-dev"},
        )
        result = resolve_device_kind("my-dev")
        assert result == "my-dev"

    def test_unknown_returns_device_id(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.find_custom_device",
            lambda d: None,
        )
        result = resolve_device_kind("totally-unknown")
        assert result == "totally-unknown"


# ---------------------------------------------------------------------------
# resolve_effective_max_ports
# ---------------------------------------------------------------------------
class TestResolveEffectiveMaxPorts:
    def test_device_service_primary(self, monkeypatch):
        mock_service = MagicMock()
        mock_service.resolve_hardware_specs.return_value = {"max_ports": 32}
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            lambda: mock_service,
        )
        result = resolve_effective_max_ports("ceos", "ceos")
        assert result == 32

    def test_vendor_config_fallback(self, monkeypatch):
        # device_service raises
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            MagicMock(side_effect=Exception("fail")),
        )
        mock_config = SimpleNamespace(max_ports=16)
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: mock_config,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_device_override",
            lambda d: None,
        )
        result = resolve_effective_max_ports("ceos", "ceos")
        assert result == 16

    def test_custom_device_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            MagicMock(side_effect=Exception("fail")),
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.find_custom_device",
            lambda d: {"maxPorts": 12},
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_device_override",
            lambda d: None,
        )
        result = resolve_effective_max_ports("my-custom", None)
        assert result == 12

    def test_override_wins(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            MagicMock(side_effect=Exception("fail")),
        )
        mock_config = SimpleNamespace(max_ports=16)
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: mock_config,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_device_override",
            lambda d: {"maxPorts": 48},
        )
        result = resolve_effective_max_ports("ceos", "ceos")
        assert result == 48

    def test_nothing_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            MagicMock(side_effect=Exception("fail")),
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_config_by_device",
            lambda d: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.find_custom_device",
            lambda d: None,
        )
        monkeypatch.setattr(
            "app.services.topology_resolution.get_device_override",
            lambda d: None,
        )
        result = resolve_effective_max_ports(None, None)
        assert result == 0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
class TestDataclasses:
    def test_node_placement_info(self):
        p = NodePlacementInfo(node_name="R1", host_id="agent-1")
        assert p.node_name == "R1"
        assert p.host_id == "agent-1"
        assert p.node_id is None

    def test_node_placement_info_with_id(self):
        p = NodePlacementInfo(node_name="R1", host_id="agent-1", node_id="abc-123")
        assert p.node_id == "abc-123"

    def test_topology_analysis_result(self):
        p = NodePlacementInfo(node_name="R1", host_id="agent-1")
        r = TopologyAnalysisResult(
            placements={"agent-1": [p]},
            cross_host_links=[],
            single_host=True,
        )
        assert r.single_host is True
        assert len(r.placements["agent-1"]) == 1
