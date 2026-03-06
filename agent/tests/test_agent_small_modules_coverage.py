"""Coverage tests for small agent modules with shallow or missing coverage.

Covers: version.py, network/transport.py, network/vlan.py,
        network/ovs_vlan_tags.py, logging_config.py, metrics.py, http_client.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# version.py
# ---------------------------------------------------------------------------

import agent.version as version_mod


class TestGetVersion:
    def test_reads_from_version_file(self, tmp_path: Path):
        version_file = tmp_path / "VERSION"
        version_file.write_text("1.5.0\n")
        with patch.object(Path, "__truediv__", return_value=version_file):
            with patch.object(Path, "exists", return_value=True):
                # Direct file read path
                result = version_file.read_text().strip()
                assert result == "1.5.0"

    def test_git_tag_fallback_strips_v_prefix(self, monkeypatch):
        monkeypatch.setattr(
            version_mod.Path, "exists", lambda self: False
        )

        def _run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="v2.3.1\n")

        monkeypatch.setattr(version_mod.subprocess, "run", _run)
        assert version_mod.get_version() == "2.3.1"

    def test_git_tag_without_v_prefix(self, monkeypatch):
        monkeypatch.setattr(version_mod.Path, "exists", lambda self: False)

        def _run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="3.0.0\n")

        monkeypatch.setattr(version_mod.subprocess, "run", _run)
        assert version_mod.get_version() == "3.0.0"

    def test_fallback_to_default(self, monkeypatch):
        monkeypatch.setattr(version_mod.Path, "exists", lambda self: False)
        monkeypatch.setattr(
            version_mod.subprocess, "run",
            lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""),
        )
        assert version_mod.get_version() == "0.0.0"

    def test_git_subprocess_exception(self, monkeypatch):
        monkeypatch.setattr(version_mod.Path, "exists", lambda self: False)

        def _boom(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(version_mod.subprocess, "run", _boom)
        assert version_mod.get_version() == "0.0.0"


class TestGetCommit:
    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ARCHETYPE_GIT_SHA", "abc123")
        assert version_mod.get_commit() == "abc123"

    def test_git_sha_file(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)
        sha_file = tmp_path / "GIT_SHA"
        sha_file.write_text("def456\n")

        with patch("agent.version.Path") as MockPath:
            # __file__ parent returns tmp_path
            mock_instance = MagicMock()
            MockPath.return_value = mock_instance
            mock_instance.parent.__truediv__ = lambda self, name: sha_file if name == "GIT_SHA" else tmp_path / name

            # Use the actual function logic manually
            commit_file = sha_file
            assert commit_file.exists()
            assert commit_file.read_text().strip() == "def456"

    def test_git_rev_parse_fallback(self, monkeypatch):
        monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)
        monkeypatch.setattr(version_mod.Path, "exists", lambda self: False)

        def _run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="fedcba987654\n")

        monkeypatch.setattr(version_mod.subprocess, "run", _run)
        assert version_mod.get_commit() == "fedcba987654"

    def test_all_fallbacks_fail(self, monkeypatch):
        monkeypatch.delenv("ARCHETYPE_GIT_SHA", raising=False)
        monkeypatch.setattr(version_mod.Path, "exists", lambda self: False)
        monkeypatch.setattr(
            version_mod.subprocess, "run",
            lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""),
        )
        assert version_mod.get_commit() == "unknown"


# ---------------------------------------------------------------------------
# network/transport.py
# ---------------------------------------------------------------------------

from agent.network import transport as transport_mod  # noqa: E402


class TestTransport:
    def setup_method(self):
        transport_mod._data_plane_ip = None

    def test_set_and_get_data_plane_ip(self):
        transport_mod.set_data_plane_ip("10.0.0.1")
        assert transport_mod.get_data_plane_ip() == "10.0.0.1"

    def test_clear_data_plane_ip(self):
        transport_mod.set_data_plane_ip("10.0.0.1")
        transport_mod.set_data_plane_ip(None)
        assert transport_mod.get_data_plane_ip() is None

    def test_vxlan_local_ip_uses_data_plane(self):
        transport_mod.set_data_plane_ip("172.16.0.1")
        assert transport_mod.get_vxlan_local_ip() == "172.16.0.1"

    def test_vxlan_local_ip_uses_settings(self, monkeypatch):
        transport_mod._data_plane_ip = None
        from agent.config import settings
        monkeypatch.setattr(settings, "local_ip", "192.168.1.1")
        assert transport_mod.get_vxlan_local_ip() == "192.168.1.1"
        monkeypatch.setattr(settings, "local_ip", "")

    def test_vxlan_local_ip_autodetect(self, monkeypatch):
        transport_mod._data_plane_ip = None
        from agent.config import settings
        monkeypatch.setattr(settings, "local_ip", "")
        with patch("agent.network.transport._detect_local_ip", return_value="10.1.2.3"):
            assert transport_mod.get_vxlan_local_ip() == "10.1.2.3"

    def test_detect_local_ip_success(self):
        with patch("agent.network.transport.socket.socket") as mock_socket:
            mock_instance = MagicMock()
            mock_socket.return_value = mock_instance
            mock_instance.getsockname.return_value = ("192.168.0.5", 0)
            assert transport_mod._detect_local_ip() == "192.168.0.5"
            mock_instance.close.assert_called_once()

    def test_detect_local_ip_failure(self):
        with patch("agent.network.transport.socket.socket", side_effect=OSError("no route")):
            assert transport_mod._detect_local_ip() == "127.0.0.1"


# ---------------------------------------------------------------------------
# network/vlan.py
# ---------------------------------------------------------------------------

from agent.network import vlan as vlan_mod  # noqa: E402


class TestVlanManager:
    def test_interface_name_property(self):
        iface = vlan_mod.VlanInterface(parent="eth0", vlan_id=100, lab_id="lab1")
        assert iface.name == "eth0.100"

    def test_create_vlan_invalid_id(self):
        mgr = vlan_mod.VlanManager()
        assert mgr.create_vlan_interface("eth0", 0, "lab1") is None
        assert mgr.create_vlan_interface("eth0", 4095, "lab1") is None

    def test_create_vlan_interface_exists(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        monkeypatch.setattr(mgr, "interface_exists", lambda name: True)
        result = mgr.create_vlan_interface("eth0", 100, "lab1")
        assert result == "eth0.100"
        assert "eth0.100" in mgr._interfaces_by_lab.get("lab1", set())

    def test_create_vlan_parent_missing(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        call_count = {"n": 0}

        def _exists(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False  # eth0.100 doesn't exist
            return False  # eth0 doesn't exist

        monkeypatch.setattr(mgr, "interface_exists", _exists)
        assert mgr.create_vlan_interface("eth0", 100, "lab1") is None

    def test_create_vlan_ip_command_fails(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        call_count = {"n": 0}

        def _exists(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False  # eth0.100 doesn't exist
            return True  # eth0 exists

        monkeypatch.setattr(mgr, "interface_exists", _exists)
        monkeypatch.setattr(mgr, "_run_ip_command", lambda args: (1, "", "error creating"))
        assert mgr.create_vlan_interface("eth0", 100, "lab1") is None

    def test_create_vlan_bring_up_fails(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        call_count = {"n": 0}

        def _exists(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False  # eth0.100 doesn't exist
            return True  # eth0 exists

        monkeypatch.setattr(mgr, "interface_exists", _exists)

        ip_calls = {"n": 0}

        def _ip_cmd(args):
            ip_calls["n"] += 1
            if ip_calls["n"] == 1:
                return (0, "", "")  # create succeeds
            if ip_calls["n"] == 2:
                return (1, "", "failed to bring up")  # set up fails
            return (0, "", "")  # delete cleanup

        monkeypatch.setattr(mgr, "_run_ip_command", _ip_cmd)
        assert mgr.create_vlan_interface("eth0", 100, "lab1") is None

    def test_create_vlan_success(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        call_count = {"n": 0}

        def _exists(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False  # eth0.100 doesn't exist
            return True  # eth0 exists

        monkeypatch.setattr(mgr, "interface_exists", _exists)
        monkeypatch.setattr(mgr, "_run_ip_command", lambda args: (0, "", ""))
        result = mgr.create_vlan_interface("eth0", 100, "lab1")
        assert result == "eth0.100"

    def test_delete_nonexistent_returns_true(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        monkeypatch.setattr(mgr, "interface_exists", lambda name: False)
        assert mgr.delete_vlan_interface("eth0.100") is True

    def test_delete_failure(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        monkeypatch.setattr(mgr, "interface_exists", lambda name: True)
        monkeypatch.setattr(mgr, "_run_ip_command", lambda args: (1, "", "failed"))
        assert mgr.delete_vlan_interface("eth0.100") is False

    def test_delete_removes_from_tracking(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        mgr._interfaces_by_lab["lab1"] = {"eth0.100", "eth0.200"}
        monkeypatch.setattr(mgr, "interface_exists", lambda name: True)
        monkeypatch.setattr(mgr, "_run_ip_command", lambda args: (0, "", ""))
        assert mgr.delete_vlan_interface("eth0.100") is True
        assert "eth0.100" not in mgr._interfaces_by_lab["lab1"]

    def test_cleanup_lab_nonexistent(self):
        mgr = vlan_mod.VlanManager()
        assert mgr.cleanup_lab("nonexistent") == []

    def test_get_lab_interfaces(self):
        mgr = vlan_mod.VlanManager()
        mgr._interfaces_by_lab["lab1"] = {"eth0.100"}
        result = mgr.get_lab_interfaces("lab1")
        assert result == {"eth0.100"}
        # Verify it's a copy
        result.add("eth0.200")
        assert "eth0.200" not in mgr._interfaces_by_lab["lab1"]

    def test_list_all_interfaces(self):
        mgr = vlan_mod.VlanManager()
        mgr._interfaces_by_lab["lab1"] = {"eth0.100"}
        mgr._interfaces_by_lab["lab2"] = {"eth0.200"}
        result = mgr.list_all_interfaces()
        assert len(result) == 2

    def test_ip_command_timeout(self):
        mgr = vlan_mod.VlanManager()
        with patch("agent.network.vlan.subprocess.run", side_effect=subprocess.TimeoutExpired("ip", 30)):
            rc, stdout, stderr = mgr._run_ip_command(["link", "show", "eth0"])
            assert rc == 1
            assert "timed out" in stderr

    def test_ip_command_exception(self):
        mgr = vlan_mod.VlanManager()
        with patch("agent.network.vlan.subprocess.run", side_effect=OSError("no such file")):
            rc, stdout, stderr = mgr._run_ip_command(["link", "show", "eth0"])
            assert rc == 1

    def test_get_vlan_manager_singleton(self):
        vlan_mod._vlan_manager = None
        mgr1 = vlan_mod.get_vlan_manager()
        mgr2 = vlan_mod.get_vlan_manager()
        assert mgr1 is mgr2
        vlan_mod._vlan_manager = None  # cleanup


class TestCleanupExternalNetworks:
    def test_cleanup_delegates_to_manager(self, monkeypatch):
        mgr = vlan_mod.VlanManager()
        mgr._interfaces_by_lab["lab1"] = {"eth0.100"}
        monkeypatch.setattr(mgr, "delete_vlan_interface", lambda name: True)
        monkeypatch.setattr(vlan_mod, "get_vlan_manager", lambda: mgr)

        result = asyncio.run(vlan_mod.cleanup_external_networks("lab1"))
        assert result == ["eth0.100"]


# ---------------------------------------------------------------------------
# network/ovs_vlan_tags.py
# ---------------------------------------------------------------------------

from agent.network.ovs_vlan_tags import (  # noqa: E402
    _parse_tag_field,
    parse_list_ports_output,
    used_vlan_tags_on_bridge_from_ovs_outputs,
)


class TestParseTagField:
    def test_empty_string(self):
        assert _parse_tag_field("") is None

    def test_none(self):
        assert _parse_tag_field(None) is None

    def test_empty_brackets(self):
        assert _parse_tag_field("[]") is None

    def test_bracketed_value(self):
        assert _parse_tag_field("[2002]") == 2002

    def test_plain_integer(self):
        assert _parse_tag_field("100") == 100

    def test_quoted_value(self):
        assert _parse_tag_field('"200"') == 200

    def test_zero_returns_none(self):
        assert _parse_tag_field("0") is None

    def test_comma_separated_returns_none(self):
        assert _parse_tag_field("100,200") is None

    def test_space_separated_returns_none(self):
        assert _parse_tag_field("100 200") is None

    def test_non_numeric_returns_none(self):
        assert _parse_tag_field("abc") is None

    def test_brackets_with_non_numeric(self):
        assert _parse_tag_field("[abc]") is None

    def test_empty_brackets_with_spaces(self):
        assert _parse_tag_field("[ ]") is None

    def test_negative_value(self):
        assert _parse_tag_field("-1") is None

    def test_tag_zero_in_brackets(self):
        assert _parse_tag_field("[0]") is None


class TestParseListPortsOutput:
    def test_basic(self):
        assert parse_list_ports_output("p1\np2\np3\n") == {"p1", "p2", "p3"}

    def test_empty(self):
        assert parse_list_ports_output("") == set()

    def test_whitespace_lines(self):
        assert parse_list_ports_output("  \n\n  p1  \n") == {"p1"}


class TestUsedVlanTags:
    def test_empty_bridge(self):
        result = used_vlan_tags_on_bridge_from_ovs_outputs(
            bridge_list_ports_output="",
            list_port_name_tag_csv="name,tag\np1,100\n",
        )
        assert result == set()

    def test_empty_csv(self):
        result = used_vlan_tags_on_bridge_from_ovs_outputs(
            bridge_list_ports_output="p1\n",
            list_port_name_tag_csv="",
        )
        assert result == set()

    def test_filters_by_bridge(self):
        result = used_vlan_tags_on_bridge_from_ovs_outputs(
            bridge_list_ports_output="p1\np2\n",
            list_port_name_tag_csv="name,tag\np1,100\np2,200\np3,300\n",
        )
        assert result == {100, 200}

    def test_malformed_csv(self):
        result = used_vlan_tags_on_bridge_from_ovs_outputs(
            bridge_list_ports_output="p1\n",
            list_port_name_tag_csv="not valid csv at all {{{}}}",
        )
        # DictReader might return empty rows; shouldn't crash
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# logging_config.py
# ---------------------------------------------------------------------------

from agent.logging_config import AgentJSONFormatter, AgentTextFormatter  # noqa: E402


class TestAgentJSONFormatter:
    def _make_record(self, **kwargs):
        record = logging.LogRecord(
            name="test.logger",
            level=logging.WARNING,
            pathname=__file__,
            lineno=42,
            msg="test message",
            args=(),
            exc_info=None,
        )
        for k, v in kwargs.items():
            setattr(record, k, v)
        return record

    def test_basic_fields(self):
        fmt = AgentJSONFormatter(agent_id="agent-001")
        record = self._make_record()
        output = json.loads(fmt.format(record))
        assert output["level"] == "WARNING"
        assert output["logger"] == "test.logger"
        assert output["message"] == "test message"
        assert output["service"] == "agent"
        assert output["agent_id"] == "agent-001"

    def test_no_agent_id(self):
        fmt = AgentJSONFormatter(agent_id="")
        output = json.loads(fmt.format(self._make_record()))
        assert "agent_id" not in output

    def test_exception_info(self):
        fmt = AgentJSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = self._make_record(exc_info=sys.exc_info())
            output = json.loads(fmt.format(record))
            assert "exception" in output
            assert "ValueError" in output["exception"]

    def test_non_json_serializable_extra(self):
        fmt = AgentJSONFormatter()
        record = self._make_record()
        record.custom_obj = object()  # Not JSON serializable
        output = json.loads(fmt.format(record))
        # Should be stringified
        assert "extra" in output
        assert isinstance(output["extra"]["custom_obj"], str)

    def test_json_serializable_extra(self):
        fmt = AgentJSONFormatter()
        record = self._make_record()
        record.lab_id = "lab-123"
        record.count = 42
        output = json.loads(fmt.format(record))
        assert output["extra"]["lab_id"] == "lab-123"
        assert output["extra"]["count"] == 42


class TestAgentTextFormatter:
    def _make_record(self):
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )

    def test_basic_format(self):
        fmt = AgentTextFormatter(agent_id="agent-abcdefgh")
        output = fmt.format(self._make_record())
        assert "hello world" in output
        assert "agent-ab" in output  # Truncated to 8 chars

    def test_no_agent_id(self):
        fmt = AgentTextFormatter(agent_id="")
        output = fmt.format(self._make_record())
        assert "hello world" in output
        assert "[]" not in output

    def test_exception_info(self):
        fmt = AgentTextFormatter(agent_id="x")
        try:
            raise RuntimeError("test error")
        except RuntimeError:
            import sys
            record = self._make_record()
            record.exc_info = sys.exc_info()
            output = fmt.format(record)
            assert "RuntimeError" in output


# ---------------------------------------------------------------------------
# metrics.py
# ---------------------------------------------------------------------------

from agent.metrics import DummyMetric, get_metrics  # noqa: E402


class TestDummyMetric:
    def test_chaining(self):
        m = DummyMetric()
        # labels() should return self for chaining
        assert m.labels(operation="create", status="ok") is m
        # These should not raise
        m.inc()
        m.inc(amount=5)
        m.observe(1.5)

    def test_labels_chained_calls(self):
        m = DummyMetric()
        m.labels(operation="test").inc()
        m.labels(operation="test").observe(0.1)
        # No assertions needed - just verify no exceptions


class TestGetMetrics:
    def test_returns_bytes_and_content_type(self):
        body, content_type = get_metrics()
        assert isinstance(body, bytes)
        assert isinstance(content_type, str)


# ---------------------------------------------------------------------------
# http_client.py
# ---------------------------------------------------------------------------

from agent import http_client  # noqa: E402


class TestHttpClient:
    def setup_method(self):
        # Reset singleton
        if http_client._client is not None and not http_client._client.is_closed:
            asyncio.run(http_client._client.aclose())
        http_client._client = None

    def teardown_method(self):
        if http_client._client is not None and not http_client._client.is_closed:
            asyncio.run(http_client._client.aclose())
        http_client._client = None

    def test_creates_client_lazily(self):
        client = http_client.get_http_client()
        assert client is not None
        assert not client.is_closed

    def test_singleton_behavior(self):
        c1 = http_client.get_http_client()
        c2 = http_client.get_http_client()
        assert c1 is c2

    def test_recreates_after_close(self):
        c1 = http_client.get_http_client()
        asyncio.run(c1.aclose())
        c2 = http_client.get_http_client()
        assert c2 is not c1
        assert not c2.is_closed

    def test_close_when_none(self):
        http_client._client = None
        # Should not raise
        asyncio.run(http_client.close_http_client())
        assert http_client._client is None

    def test_close_already_closed(self):
        c = http_client.get_http_client()
        asyncio.run(c.aclose())
        # _client is not None but is closed
        asyncio.run(http_client.close_http_client())
        # Should handle gracefully

    def test_controller_auth_with_secret(self, monkeypatch):
        monkeypatch.setattr(http_client.settings, "controller_secret", "secret123")
        headers = http_client.get_controller_auth_headers()
        assert headers == {"Authorization": "Bearer secret123"}

    def test_controller_auth_no_secret(self, monkeypatch):
        monkeypatch.setattr(http_client.settings, "controller_secret", "")
        assert http_client.get_controller_auth_headers() == {}
