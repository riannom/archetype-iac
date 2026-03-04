"""Coverage tests for interface_config.py host helpers, MTU functions, and detection."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.network import interface_config as ic


# ---------------------------------------------------------------------------
# _is_in_container
# ---------------------------------------------------------------------------


class TestIsInContainer:
    def test_detects_dockerenv(self, monkeypatch):
        with patch.object(Path, "exists", return_value=True):
            assert ic._is_in_container() is True

    def test_detects_cgroup_docker(self, monkeypatch):
        with patch.object(Path, "exists", side_effect=lambda self=None: False):
            # Override per-path behavior
            original_exists = Path.exists

            def _fake_exists(self):
                if str(self) == "/.dockerenv":
                    return False
                if str(self) == "/proc/1/cgroup":
                    return True
                return False

            with patch.object(Path, "exists", _fake_exists):
                with patch.object(Path, "read_text", return_value="12:devices:/docker/abc123"):
                    assert ic._is_in_container() is True

    def test_detects_container_env_var(self, monkeypatch):
        monkeypatch.setenv("container", "docker")
        with patch.object(Path, "exists", return_value=False):
            assert ic._is_in_container() is True

    def test_not_in_container(self, monkeypatch):
        monkeypatch.delenv("container", raising=False)
        with patch.object(Path, "exists", return_value=False):
            assert ic._is_in_container() is False


# ---------------------------------------------------------------------------
# _run_on_host
# ---------------------------------------------------------------------------


class TestRunOnHost:
    def test_direct_when_not_in_container(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            with patch("agent.network.interface_config.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
                result = ic._run_on_host(["echo", "test"])
                assert result.returncode == 0
                mock_run.assert_called_once_with(
                    ["echo", "test"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

    def test_nsenter_when_in_container(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
                result = ic._run_on_host(["ip", "link"])
                assert result.returncode == 0
                args = mock_run.call_args[0][0]
                assert args[:5] == ["nsenter", "-t", "1", "-m", "--"]
                assert args[5:] == ["ip", "link"]


# ---------------------------------------------------------------------------
# _host_path_exists
# ---------------------------------------------------------------------------


class TestHostPathExists:
    def test_native_path_check(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            with patch.object(Path, "exists", return_value=True):
                assert ic._host_path_exists("/etc/hosts") is True

    def test_nsenter_path_check(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0)
                assert ic._host_path_exists("/etc/hosts") is True

                mock_run.return_value = SimpleNamespace(returncode=1)
                assert ic._host_path_exists("/nonexistent") is False


# ---------------------------------------------------------------------------
# _host_glob
# ---------------------------------------------------------------------------


class TestHostGlob:
    def test_native_glob(self, tmp_path: Path):
        (tmp_path / "a.yaml").write_text("x")
        (tmp_path / "b.yaml").write_text("y")
        (tmp_path / "c.txt").write_text("z")

        with patch("agent.network.interface_config._is_in_container", return_value=False):
            result = ic._host_glob(str(tmp_path), "*.yaml")
            assert len(result) == 2

    def test_nsenter_glob(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(
                    returncode=0,
                    stdout="/etc/netplan/01-cfg.yaml\n/etc/netplan/02-cfg.yaml\n",
                )
                result = ic._host_glob("/etc/netplan", "*.yaml")
                assert len(result) == 2

    def test_nsenter_glob_empty(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="")
                result = ic._host_glob("/etc/netplan", "*.yaml")
                assert result == []


# ---------------------------------------------------------------------------
# _host_read_file
# ---------------------------------------------------------------------------


class TestHostReadFile:
    def test_native_read(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            assert ic._host_read_file(str(f)) == "hello"

    def test_native_read_missing(self, tmp_path: Path):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            assert ic._host_read_file(str(tmp_path / "missing.txt")) is None

    def test_nsenter_read(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stdout="content")
                assert ic._host_read_file("/etc/test") == "content"

    def test_nsenter_read_failure(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="")
                assert ic._host_read_file("/etc/missing") is None


# ---------------------------------------------------------------------------
# _host_write_file
# ---------------------------------------------------------------------------


class TestHostWriteFile:
    def test_native_write(self, tmp_path: Path):
        f = tmp_path / "out.txt"
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            ok, err = ic._host_write_file(str(f), "data")
            assert ok is True
            assert err is None
            assert f.read_text() == "data"

    def test_native_write_error(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            ok, err = ic._host_write_file("/proc/nonexistent/impossible", "data")
            assert ok is False
            assert err is not None

    def test_nsenter_write_success(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                ok, err = ic._host_write_file("/tmp/test", "content")
                assert ok is True

    def test_nsenter_write_failure(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=1, stderr="permission denied")
                ok, err = ic._host_write_file("/root/test", "content")
                assert ok is False
                assert "permission denied" in err


# ---------------------------------------------------------------------------
# _host_mkdir
# ---------------------------------------------------------------------------


class TestHostMkdir:
    def test_native_mkdir(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "c"
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            ok, err = ic._host_mkdir(str(target))
            assert ok is True
            assert target.exists()

    def test_nsenter_mkdir_success(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                ok, err = ic._host_mkdir("/tmp/new_dir")
                assert ok is True

    def test_nsenter_mkdir_failure(self):
        with patch("agent.network.interface_config._is_in_container", return_value=True):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=1, stderr="read-only filesystem")
                ok, err = ic._host_mkdir("/readonly/dir")
                assert ok is False
                assert "read-only" in err


# ---------------------------------------------------------------------------
# detect_network_manager
# ---------------------------------------------------------------------------


class TestDetectNetworkManager:
    def test_detects_networkmanager(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stdout="running\n")
                assert ic.detect_network_manager() == "networkmanager"

    def test_detects_netplan(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            # nmcli fails
            def _side_effect(cmd, timeout=10):
                if cmd[0] == "nmcli":
                    return SimpleNamespace(returncode=1, stdout="")
                if cmd[0] == "which":
                    return SimpleNamespace(returncode=0, stdout="/usr/sbin/netplan\n")
                return SimpleNamespace(returncode=1, stdout="")

            with patch("agent.network.interface_config._run_on_host", side_effect=_side_effect):
                with patch("agent.network.interface_config._host_glob", return_value=["/etc/netplan/01.yaml"]):
                    assert ic.detect_network_manager() == "netplan"

    def test_detects_systemd_networkd(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            def _side_effect(cmd, timeout=10):
                if cmd[0] == "nmcli":
                    return SimpleNamespace(returncode=1, stdout="")
                if cmd[0] == "systemctl":
                    return SimpleNamespace(returncode=0, stdout="active\n")
                return SimpleNamespace(returncode=1, stdout="")

            with patch("agent.network.interface_config._run_on_host", side_effect=_side_effect):
                with patch("agent.network.interface_config._host_glob", return_value=[]):
                    assert ic.detect_network_manager() == "systemd-networkd"

    def test_unknown_when_nothing_detected(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            with patch("agent.network.interface_config._run_on_host") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=1, stdout="")
                with patch("agent.network.interface_config._host_glob", return_value=[]):
                    assert ic.detect_network_manager() == "unknown"

    def test_exception_in_nmcli_falls_through(self):
        with patch("agent.network.interface_config._is_in_container", return_value=False):
            def _side_effect(cmd, timeout=10):
                if cmd[0] == "nmcli":
                    raise FileNotFoundError("nmcli not found")
                return SimpleNamespace(returncode=1, stdout="")

            with patch("agent.network.interface_config._run_on_host", side_effect=_side_effect):
                with patch("agent.network.interface_config._host_glob", return_value=[]):
                    assert ic.detect_network_manager() == "unknown"


# ---------------------------------------------------------------------------
# set_mtu_persistent_* variants
# ---------------------------------------------------------------------------


class TestSetMtuNmcli:
    def test_nmcli_success(self):
        with patch("agent.network.interface_config._run_on_host") as mock_run:
            # list connections, modify, activate
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout="Wired connection 1:eth0\n"),
                SimpleNamespace(returncode=0, stderr=""),
                SimpleNamespace(returncode=0, stderr=""),
            ]
            ok, err = asyncio.run(ic.set_mtu_persistent_networkmanager("eth0", 9000))
            assert ok is True

    def test_nmcli_no_connection(self):
        with patch("agent.network.interface_config._run_on_host") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout="Wired:ens192\n")
            ok, err = asyncio.run(ic.set_mtu_persistent_networkmanager("eth0", 9000))
            assert ok is False
            assert "No NetworkManager connection" in err

    def test_nmcli_list_failure(self):
        with patch("agent.network.interface_config._run_on_host") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=1, stderr="not running")
            ok, err = asyncio.run(ic.set_mtu_persistent_networkmanager("eth0", 9000))
            assert ok is False

    def test_nmcli_timeout(self):
        with patch("agent.network.interface_config._run_on_host", side_effect=subprocess.TimeoutExpired("nmcli", 10)):
            ok, err = asyncio.run(ic.set_mtu_persistent_networkmanager("eth0", 9000))
            assert ok is False
            assert "timed out" in err


class TestSetMtuNetplan:
    def test_netplan_creates_new_config(self):
        with patch("agent.network.interface_config._host_glob", return_value=[]):
            with patch("agent.network.interface_config._host_read_file", return_value=None):
                with patch("agent.network.interface_config._host_write_file", return_value=(True, None)):
                    with patch("agent.network.interface_config._run_on_host") as mock_run:
                        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                        ok, err = asyncio.run(ic.set_mtu_persistent_netplan("eth0", 9000))
                        assert ok is True

    def test_netplan_updates_existing_config(self):
        yaml_content = (
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      dhcp4: true\n"
        )
        with patch("agent.network.interface_config._host_glob", return_value=["/etc/netplan/01.yaml"]):
            with patch("agent.network.interface_config._host_read_file", return_value=yaml_content):
                with patch("agent.network.interface_config._host_write_file", return_value=(True, None)):
                    with patch("agent.network.interface_config._run_on_host") as mock_run:
                        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                        ok, err = asyncio.run(ic.set_mtu_persistent_netplan("eth0", 9000))
                        assert ok is True

    def test_netplan_apply_failure(self):
        with patch("agent.network.interface_config._host_glob", return_value=[]):
            with patch("agent.network.interface_config._host_write_file", return_value=(True, None)):
                with patch("agent.network.interface_config._run_on_host") as mock_run:
                    mock_run.return_value = SimpleNamespace(returncode=1, stderr="netplan error")
                    ok, err = asyncio.run(ic.set_mtu_persistent_netplan("eth0", 9000))
                    assert ok is False
                    assert "netplan" in err

    def test_netplan_write_failure(self):
        with patch("agent.network.interface_config._host_glob", return_value=[]):
            with patch("agent.network.interface_config._host_write_file", return_value=(False, "permission denied")):
                ok, err = asyncio.run(ic.set_mtu_persistent_netplan("eth0", 9000))
                assert ok is False


class TestSetMtuSystemdNetworkd:
    def test_creates_new_config(self):
        with patch("agent.network.interface_config._host_mkdir", return_value=(True, None)):
            with patch("agent.network.interface_config._host_glob", return_value=[]):
                with patch("agent.network.interface_config._host_write_file", return_value=(True, None)):
                    with patch("agent.network.interface_config._run_on_host") as mock_run:
                        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                        ok, err = asyncio.run(
                            ic.set_mtu_persistent_systemd_networkd("eth0", 9000)
                        )
                        assert ok is True

    def test_updates_existing_with_link_section(self):
        existing = "[Match]\nName=eth0\n\n[Link]\nMTUBytes=1500\n"
        with patch("agent.network.interface_config._host_mkdir", return_value=(True, None)):
            with patch("agent.network.interface_config._host_glob", return_value=["/etc/systemd/network/10-eth0.network"]):
                with patch("agent.network.interface_config._host_read_file", return_value=existing):
                    with patch("agent.network.interface_config._host_write_file", return_value=(True, None)) as mock_write:
                        with patch("agent.network.interface_config._run_on_host") as mock_run:
                            mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                            ok, err = asyncio.run(
                                ic.set_mtu_persistent_systemd_networkd("eth0", 9000)
                            )
                            assert ok is True
                            written_content = mock_write.call_args[0][1]
                            assert "MTUBytes=9000" in written_content
                            assert "MTUBytes=1500" not in written_content

    def test_adds_link_section_when_missing(self):
        existing = "[Match]\nName=eth0\n\n[Network]\nDHCP=yes\n"
        with patch("agent.network.interface_config._host_mkdir", return_value=(True, None)):
            with patch("agent.network.interface_config._host_glob", return_value=["/etc/systemd/network/10-eth0.network"]):
                with patch("agent.network.interface_config._host_read_file", return_value=existing):
                    with patch("agent.network.interface_config._host_write_file", return_value=(True, None)) as mock_write:
                        with patch("agent.network.interface_config._run_on_host") as mock_run:
                            mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
                            ok, err = asyncio.run(
                                ic.set_mtu_persistent_systemd_networkd("eth0", 9000)
                            )
                            assert ok is True
                            written_content = mock_write.call_args[0][1]
                            assert "[Link]\nMTUBytes=9000" in written_content

    def test_reload_fails_tries_restart(self):
        with patch("agent.network.interface_config._host_mkdir", return_value=(True, None)):
            with patch("agent.network.interface_config._host_glob", return_value=[]):
                with patch("agent.network.interface_config._host_write_file", return_value=(True, None)):
                    with patch("agent.network.interface_config._run_on_host") as mock_run:
                        # reload fails, restart succeeds
                        mock_run.side_effect = [
                            SimpleNamespace(returncode=1, stderr="reload failed"),
                            SimpleNamespace(returncode=0, stderr=""),
                        ]
                        ok, err = asyncio.run(
                            ic.set_mtu_persistent_systemd_networkd("eth0", 9000)
                        )
                        assert ok is True

    def test_both_reload_and_restart_fail(self):
        with patch("agent.network.interface_config._host_mkdir", return_value=(True, None)):
            with patch("agent.network.interface_config._host_glob", return_value=[]):
                with patch("agent.network.interface_config._host_write_file", return_value=(True, None)):
                    with patch("agent.network.interface_config._run_on_host") as mock_run:
                        mock_run.return_value = SimpleNamespace(returncode=1, stderr="error")
                        ok, err = asyncio.run(
                            ic.set_mtu_persistent_systemd_networkd("eth0", 9000)
                        )
                        assert ok is False

    def test_mkdir_failure(self):
        with patch("agent.network.interface_config._host_mkdir", return_value=(False, "permission denied")):
            ok, err = asyncio.run(
                ic.set_mtu_persistent_systemd_networkd("eth0", 9000)
            )
            assert ok is False
            assert "networkd directory" in err


class TestSetMtuIproute2:
    def test_runtime_mtu_success(self):
        with patch("agent.network.interface_config.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
            ok, err = asyncio.run(ic.set_mtu_runtime("eth0", 9000))
            assert ok is True

    def test_runtime_mtu_failure(self):
        with patch("agent.network.interface_config.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=2, stderr="RTNETLINK: No such device")
            ok, err = asyncio.run(ic.set_mtu_runtime("eth0", 9000))
            assert ok is False
            assert "No such device" in err

    def test_runtime_mtu_timeout(self):
        with patch(
            "agent.network.interface_config.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ip", 10),
        ):
            ok, err = asyncio.run(ic.set_mtu_runtime("eth0", 9000))
            assert ok is False
            assert "timed out" in err


# ---------------------------------------------------------------------------
# set_mtu_persistent dispatcher
# ---------------------------------------------------------------------------


class TestSetMtuPersistent:
    def test_dispatches_to_networkmanager(self):
        from unittest.mock import AsyncMock as AM
        import asyncio as aio

        async def _test():
            mock_fn = AM(return_value=(True, None))
            with patch(
                "agent.network.interface_config.set_mtu_persistent_networkmanager",
                mock_fn,
            ):
                return await ic.set_mtu_persistent("eth0", 9000, "networkmanager")

        ok, err = aio.run(_test())
        assert ok is True

    def test_unknown_manager(self):
        ok, err = asyncio.run(ic.set_mtu_persistent("eth0", 9000, "unknown"))
        assert ok is False
        assert "Unknown network manager" in err


# ---------------------------------------------------------------------------
# get_interface_max_mtu
# ---------------------------------------------------------------------------


class TestGetInterfaceMaxMtu:
    def test_returns_max_mtu(self):
        ip_output = json.dumps([{"max_mtu": 9216}])
        with patch("agent.network.interface_config.subprocess.run") as mock_run:
            # ethtool call, then ip call
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout=""),
                SimpleNamespace(returncode=0, stdout=ip_output),
            ]
            assert ic.get_interface_max_mtu("eth0") == 9216

    def test_returns_none_on_failure(self):
        with patch(
            "agent.network.interface_config.subprocess.run",
            side_effect=Exception("nope"),
        ):
            assert ic.get_interface_max_mtu("eth0") is None

    def test_returns_none_when_not_available(self):
        ip_output = json.dumps([{}])
        with patch("agent.network.interface_config.subprocess.run") as mock_run:
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout=""),
                SimpleNamespace(returncode=0, stdout=ip_output),
            ]
            assert ic.get_interface_max_mtu("eth0") is None


# ---------------------------------------------------------------------------
# get_default_route_interface
# ---------------------------------------------------------------------------


class TestGetDefaultRouteInterface:
    def test_returns_interface(self):
        routes = json.dumps([{"dev": "eth0", "gateway": "10.0.0.1"}])
        with patch("agent.network.interface_config.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout=routes)
            assert ic.get_default_route_interface() == "eth0"

    def test_returns_none_on_empty(self):
        with patch("agent.network.interface_config.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout="[]")
            assert ic.get_default_route_interface() is None

    def test_returns_none_on_error(self):
        with patch("agent.network.interface_config.subprocess.run", side_effect=Exception("fail")):
            assert ic.get_default_route_interface() is None


# ---------------------------------------------------------------------------
# is_physical_interface
# ---------------------------------------------------------------------------


class TestIsPhysicalInterface:
    def test_virtual_prefix_rejected(self):
        for prefix in ("lo", "docker0", "veth123", "br-abc", "virbr0", "vxlan1", "tap0"):
            assert ic.is_physical_interface(prefix) is False

    def test_virtual_device_path(self, tmp_path: Path):
        with patch.object(Path, "exists") as mock_exists:
            # First call: virtual path exists
            # Second call: device path check
            mock_exists.side_effect = [True]
            assert ic.is_physical_interface("ens192") is False

    def test_physical_device(self):
        with patch.object(Path, "exists") as mock_exists:
            # virtual path doesn't exist, device symlink exists
            mock_exists.side_effect = [False, True]
            assert ic.is_physical_interface("ens192") is True

    def test_no_device_symlink(self):
        with patch.object(Path, "exists") as mock_exists:
            # virtual path doesn't exist, device symlink doesn't exist
            mock_exists.side_effect = [False, False]
            assert ic.is_physical_interface("ens192") is False
