"""Coverage tests for deep branches in DockerProvider (docker.py).

Covers: _create_lab_networks, _delete_lab_networks, _extract_config_via_ssh,
_rename_container_interface, _get_container_ips, _get_container_status,
_node_from_container, _topology_from_json, discover_labs.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure agent root is importable
_AGENT_ROOT = str(Path(__file__).resolve().parents[1])
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

from agent.providers.docker import DockerProvider
from providers.base import NodeStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider() -> DockerProvider:
    """Create DockerProvider via __new__() to skip __init__ side-effects."""
    p = DockerProvider.__new__(DockerProvider)
    p._docker = MagicMock()
    p._local_network = None
    p._ovs_manager = None
    p._lab_network_locks = {}
    return p


def _make_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    """Create a mock async subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# _create_lab_networks
# ---------------------------------------------------------------------------


class TestCreateLabNetworks:
    """Tests for DockerProvider._create_lab_networks which delegates to
    create_lab_networks() in docker_networks.py."""

    @pytest.mark.asyncio
    async def test_creates_networks_via_impl(self):
        """_create_lab_networks delegates to the impl function."""
        p = _make_provider()
        mock_result = {"eth0": "lab1-eth0", "eth1": "lab1-eth1"}
        with patch(
            "agent.providers.docker._create_lab_networks_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            result = await p._create_lab_networks("lab1", max_interfaces=1)
            mock_impl.assert_called_once_with(p, "lab1", 1)
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_default_max_interfaces(self):
        """Default max_interfaces is 8."""
        p = _make_provider()
        with patch(
            "agent.providers.docker._create_lab_networks_impl",
            new_callable=AsyncMock,
            return_value={},
        ) as mock_impl:
            await p._create_lab_networks("lab2")
            mock_impl.assert_called_once_with(p, "lab2", 8)

    @pytest.mark.asyncio
    async def test_propagates_error_from_impl(self):
        """Errors from the impl function propagate to the caller."""
        p = _make_provider()
        with patch(
            "agent.providers.docker._create_lab_networks_impl",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network creation failed"),
        ):
            with pytest.raises(RuntimeError, match="network creation failed"):
                await p._create_lab_networks("labX")


# ---------------------------------------------------------------------------
# _delete_lab_networks
# ---------------------------------------------------------------------------


class TestDeleteLabNetworks:
    """Tests for DockerProvider._delete_lab_networks."""

    @pytest.mark.asyncio
    async def test_deletes_networks_via_impl(self):
        """_delete_lab_networks delegates to the impl and returns count."""
        p = _make_provider()
        with patch(
            "agent.providers.docker._delete_lab_networks_impl",
            new_callable=AsyncMock,
            return_value=5,
        ) as mock_impl:
            result = await p._delete_lab_networks("lab3")
            mock_impl.assert_called_once_with(p, "lab3")
            assert result == 5

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_networks(self):
        """Returns 0 when no matching networks exist."""
        p = _make_provider()
        with patch(
            "agent.providers.docker._delete_lab_networks_impl",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await p._delete_lab_networks("empty-lab")
            assert result == 0


# ---------------------------------------------------------------------------
# _extract_config_via_ssh
# ---------------------------------------------------------------------------


class TestExtractConfigViaSsh:
    """Tests for DockerProvider._extract_config_via_ssh."""

    @pytest.mark.asyncio
    async def test_delegates_to_extract_function(self):
        """Calls extract_config_via_ssh with correct args."""
        p = _make_provider()
        container = MagicMock()
        expected_config = "hostname router1\n"

        with patch(
            "agent.providers.docker.extract_config_via_ssh",
            new_callable=AsyncMock,
            return_value=expected_config,
        ) as mock_fn:
            result = await p._extract_config_via_ssh(
                container, "arista_ceos", "show running", "ceos1"
            )
            mock_fn.assert_called_once_with(
                container,
                "arista_ceos",
                "show running",
                "ceos1",
                p._get_container_ips,
                p._run_ssh_command,
            )
            assert result == expected_config

    @pytest.mark.asyncio
    async def test_returns_none_on_no_ips(self):
        """Returns None when the SSH extractor can't find IPs."""
        p = _make_provider()
        container = MagicMock()

        with patch(
            "agent.providers.docker.extract_config_via_ssh",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await p._extract_config_via_ssh(
                container, "nokia_srlinux", "info", "srl1"
            )
            assert result is None


# ---------------------------------------------------------------------------
# _get_container_ips
# ---------------------------------------------------------------------------


class TestGetContainerIps:
    """Tests for DockerProvider._get_container_ips."""

    def test_extracts_ips_from_networks(self):
        p = _make_provider()
        container = SimpleNamespace(
            attrs={
                "NetworkSettings": {
                    "Networks": {
                        "bridge": {"IPAddress": "172.17.0.2"},
                        "custom": {"IPAddress": "10.0.0.5"},
                    }
                }
            }
        )
        ips = p._get_container_ips(container)
        assert set(ips) == {"172.17.0.2", "10.0.0.5"}

    def test_skips_empty_ip(self):
        p = _make_provider()
        container = SimpleNamespace(
            attrs={
                "NetworkSettings": {
                    "Networks": {
                        "net1": {"IPAddress": "10.0.0.1"},
                        "net2": {"IPAddress": ""},
                        "net3": {},
                    }
                }
            }
        )
        ips = p._get_container_ips(container)
        assert ips == ["10.0.0.1"]

    def test_returns_empty_on_missing_attrs(self):
        p = _make_provider()
        container = SimpleNamespace(attrs={})
        assert p._get_container_ips(container) == []

    def test_returns_empty_on_exception(self):
        """Handles exception gracefully (attrs property raises)."""
        p = _make_provider()
        container = MagicMock()
        container.attrs = property(lambda self: (_ for _ in ()).throw(KeyError))
        container.attrs = MagicMock(side_effect=TypeError("boom"))
        # Force the .get chain to fail
        bad_container = MagicMock()
        bad_container.attrs.get.side_effect = RuntimeError("bad")
        assert p._get_container_ips(bad_container) == []


# ---------------------------------------------------------------------------
# _get_container_status
# ---------------------------------------------------------------------------


class TestGetContainerStatus:
    """Tests for DockerProvider._get_container_status."""

    @pytest.mark.parametrize(
        "docker_status,expected",
        [
            ("running", NodeStatus.RUNNING),
            ("Running", NodeStatus.RUNNING),
            ("created", NodeStatus.PENDING),
            ("exited", NodeStatus.STOPPED),
            ("dead", NodeStatus.STOPPED),
            ("paused", NodeStatus.STOPPED),
            ("restarting", NodeStatus.STARTING),
            ("removing", NodeStatus.UNKNOWN),
            ("whatever", NodeStatus.UNKNOWN),
        ],
    )
    def test_status_mapping(self, docker_status, expected):
        p = _make_provider()
        container = SimpleNamespace(status=docker_status)
        assert p._get_container_status(container) == expected


# ---------------------------------------------------------------------------
# _node_from_container
# ---------------------------------------------------------------------------


class TestNodeFromContainer:
    """Tests for DockerProvider._node_from_container."""

    def test_returns_node_info(self):
        p = _make_provider()
        container = MagicMock()
        container.labels = {"archetype.node_name": "router1"}
        container.status = "running"
        container.short_id = "abc123"
        container.image.tags = ["ceos:latest"]
        container.attrs = {
            "NetworkSettings": {"Networks": {"br": {"IPAddress": "10.0.0.1"}}}
        }
        node = p._node_from_container(container)
        assert node is not None
        assert node.name == "router1"
        assert node.status == NodeStatus.RUNNING
        assert node.container_id == "abc123"
        assert node.image == "ceos:latest"
        assert "10.0.0.1" in node.ip_addresses

    def test_returns_none_when_no_node_name_label(self):
        p = _make_provider()
        container = MagicMock()
        container.labels = {}
        assert p._node_from_container(container) is None

    def test_handles_no_image_tags(self):
        """Falls back to image id when no tags exist."""
        p = _make_provider()
        container = MagicMock()
        container.labels = {"archetype.node_name": "sw1"}
        container.status = "created"
        container.short_id = "def456"
        container.image.tags = []
        container.image.id = "sha256:abcdef1234567890"
        container.attrs = {"NetworkSettings": {"Networks": {}}}
        node = p._node_from_container(container)
        assert node is not None
        assert node.image == "sha256:abcdef1234567890"[:12]


# ---------------------------------------------------------------------------
# _rename_container_interface
# ---------------------------------------------------------------------------


class TestRenameContainerInterface:
    """Tests for DockerProvider._rename_container_interface."""

    @pytest.mark.asyncio
    async def test_normal_rename_success(self):
        """Successful rename: down, rename (rc=0), set MTU, up."""
        p = _make_provider()
        result = {"errors": [], "fixed": 0}

        procs = [
            _make_proc(0),  # ip link set down
            _make_proc(0),  # ip link set name (rename succeeds)
            _make_proc(0),  # ip link set mtu
            _make_proc(0),  # ip link set up
        ]
        call_count = 0

        async def _fake_subprocess(*args, **kwargs):
            nonlocal call_count
            proc = procs[call_count]
            call_count += 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await p._rename_container_interface(
                pid=1234,
                actual_name="eth99",
                intended_name="eth1",
                container_name="archetype-lab1-r1",
                result=result,
            )

        assert result["errors"] == []
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_rename_file_exists_conflict(self):
        """'File exists' error triggers conflict resolution: rename conflicting
        interface to temp name, then retry rename, then delete stale."""
        p = _make_provider()
        result = {"errors": [], "fixed": 0}

        proc_down = _make_proc(0)
        proc_rename_fail = _make_proc(1, stderr=b"RTNETLINK answers: File exists")
        # check temp name exists -> rc != 0 means temp name is available
        proc_check_temp = _make_proc(1)
        # down the conflicting interface
        proc_conflict_down = _make_proc(0)
        # rename conflicting to temp
        proc_conflict_rename = _make_proc(0)
        # retry rename -> success
        proc_retry_rename = _make_proc(0)
        # delete stale temp
        proc_delete_stale = _make_proc(0)
        # MTU set
        proc_mtu = _make_proc(0)
        # bring up
        proc_up = _make_proc(0)

        procs = [
            proc_down,
            proc_rename_fail,
            proc_check_temp,
            proc_conflict_down,
            proc_conflict_rename,
            proc_retry_rename,
            proc_delete_stale,
            proc_mtu,
            proc_up,
        ]
        call_idx = 0

        async def _fake_subprocess(*args, **kwargs):
            nonlocal call_idx
            proc = procs[call_idx]
            call_idx += 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await p._rename_container_interface(
                pid=5678,
                actual_name="eth55",
                intended_name="eth2",
                container_name="archetype-lab1-r2",
                result=result,
            )

        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_rename_generic_failure_adds_error(self):
        """Non-'File exists' rename failure adds error to result."""
        p = _make_provider()
        result = {"errors": [], "fixed": 0}

        proc_down = _make_proc(0)
        proc_rename_fail = _make_proc(1, stderr=b"Operation not permitted")

        procs = [proc_down, proc_rename_fail]
        call_idx = 0

        async def _fake_subprocess(*args, **kwargs):
            nonlocal call_idx
            proc = procs[call_idx]
            call_idx += 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await p._rename_container_interface(
                pid=9999,
                actual_name="eth77",
                intended_name="eth3",
                container_name="archetype-lab1-r3",
                result=result,
            )

        assert len(result["errors"]) == 1
        assert "Operation not permitted" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_file_exists_retry_also_fails(self):
        """If retry rename after conflict resolution also fails, error is recorded."""
        p = _make_provider()
        result = {"errors": [], "fixed": 0}

        proc_down = _make_proc(0)
        proc_rename_fail = _make_proc(1, stderr=b"RTNETLINK answers: File exists")
        proc_check_temp = _make_proc(1)  # temp name available
        proc_conflict_down = _make_proc(0)
        proc_conflict_rename = _make_proc(0)
        # retry rename fails again
        proc_retry_fail = _make_proc(1, stderr=b"Device or resource busy")

        procs = [
            proc_down,
            proc_rename_fail,
            proc_check_temp,
            proc_conflict_down,
            proc_conflict_rename,
            proc_retry_fail,
        ]
        call_idx = 0

        async def _fake_subprocess(*args, **kwargs):
            nonlocal call_idx
            proc = procs[call_idx]
            call_idx += 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await p._rename_container_interface(
                pid=1111,
                actual_name="eth88",
                intended_name="eth4",
                container_name="archetype-lab1-r4",
                result=result,
            )

        assert len(result["errors"]) == 1
        assert "Device or resource busy" in result["errors"][0]


# ---------------------------------------------------------------------------
# discover_labs
# ---------------------------------------------------------------------------


class TestDiscoverLabs:
    """Tests for DockerProvider.discover_labs."""

    @pytest.mark.asyncio
    async def test_discovers_labs_from_containers(self):
        p = _make_provider()

        c1 = MagicMock()
        c1.labels = {
            "archetype.provider": "docker",
            "archetype.lab_id": "lab-a",
            "archetype.node_name": "r1",
        }
        c1.status = "running"
        c1.short_id = "c1id"
        c1.image.tags = ["ceos:latest"]
        c1.attrs = {"NetworkSettings": {"Networks": {}}}

        c2 = MagicMock()
        c2.labels = {
            "archetype.provider": "docker",
            "archetype.lab_id": "lab-a",
            "archetype.node_name": "r2",
        }
        c2.status = "running"
        c2.short_id = "c2id"
        c2.image.tags = ["srlinux:latest"]
        c2.attrs = {"NetworkSettings": {"Networks": {}}}

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        p._docker.containers.list.return_value = [c1, c2]

        with patch("asyncio.to_thread", side_effect=fake_to_thread):
            result = await p.discover_labs()

        assert "lab-a" in result
        assert len(result["lab-a"]) == 2

    @pytest.mark.asyncio
    async def test_skips_containers_without_lab_id(self):
        p = _make_provider()

        c1 = MagicMock()
        c1.labels = {"archetype.provider": "docker"}
        # No lab_id label

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        p._docker.containers.list.return_value = [c1]

        with patch("asyncio.to_thread", side_effect=fake_to_thread):
            result = await p.discover_labs()

        assert result == {}

    @pytest.mark.asyncio
    async def test_handles_docker_error(self):
        """Returns empty dict on Docker API error."""
        p = _make_provider()

        async def raise_err(fn, *args, **kwargs):
            raise RuntimeError("Docker not available")

        with patch("asyncio.to_thread", side_effect=raise_err):
            result = await p.discover_labs()

        assert result == {}
