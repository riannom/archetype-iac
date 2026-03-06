"""Round 12 coverage tests for deep paths in DockerProvider (docker.py).

Covers:
- _create_containers error paths (cleanup on failure, existing container handling)
- _capture_container_vlans (VLAN capture, persistence, error handling)
- _recover_stale_network (allocation recovery, container validation)
- _validate_images delegation
- _is_transient_docker_error classification
- _retry_docker_call retry/exhaust paths
- _get_interface_vlan multi-step resolution
- deploy() error branches (no topology, no nodes, missing images)
- destroy() volume cleanup and error branches
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import NotFound, APIError

# Ensure agent root is importable
_AGENT_ROOT = str(Path(__file__).resolve().parents[1])
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

from agent.providers.docker import (  # noqa: E402
    DockerProvider,
    TopologyNode,
    TopologyLink,
    ParsedTopology,
    LABEL_LAB_ID,
    LABEL_NODE_NAME,
    LABEL_NODE_KIND,
)
from agent.providers.base import NodeStatus  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider() -> DockerProvider:
    """Create DockerProvider via __new__() to skip __init__ side-effects."""
    p = DockerProvider.__new__(DockerProvider)
    p._docker = MagicMock()
    p._local_network = MagicMock()
    p._ovs_manager = MagicMock()
    p._lab_network_locks = {}
    p._vlan_allocations = {}
    p._next_vlan = {}
    return p


def _make_topology(nodes: dict[str, TopologyNode] | None = None,
                   links: list[TopologyLink] | None = None) -> ParsedTopology:
    """Build a minimal ParsedTopology."""
    if nodes is None:
        nodes = {
            "r1": TopologyNode(name="r1", kind="linux", image="alpine:latest"),
        }
    return ParsedTopology(name="test-lab", nodes=nodes, links=links or [])


def _make_container(name: str = "archetype-lab1-r1",
                    status: str = "running",
                    labels: dict | None = None,
                    pid: int = 12345) -> MagicMock:
    """Create a mock Docker container object."""
    c = MagicMock()
    c.name = name
    c.status = status
    c.id = "abc123def456"
    c.short_id = "abc123d"
    c.labels = labels or {
        LABEL_LAB_ID: "lab1",
        LABEL_NODE_NAME: "r1",
        LABEL_NODE_KIND: "linux",
    }
    c.attrs = {
        "State": {"Pid": pid},
        "NetworkSettings": {"Networks": {}},
    }
    c.image = MagicMock()
    c.image.tags = ["alpine:latest"]
    c.remove = MagicMock()
    c.start = MagicMock()
    c.reload = MagicMock()
    c.logs = MagicMock(return_value=b"some logs")
    return c


def _make_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    """Create a mock async subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _api_error(status_code: int, message: str = "api error") -> APIError:
    response = MagicMock()
    response.status_code = status_code
    return APIError(message, response=response)


# ---------------------------------------------------------------------------
# _create_containers error paths
# ---------------------------------------------------------------------------


class TestCreateContainersErrorPaths:
    """Tests for _create_containers failure and cleanup logic."""

    @pytest.mark.asyncio
    async def test_cleanup_on_creation_failure_removes_created_containers(self, monkeypatch):
        """When container creation fails partway, already-created containers are cleaned up."""
        p = _make_provider()
        monkeypatch.setattr(type(p), "use_ovs_plugin", property(lambda self: False))

        node_ok = TopologyNode(name="r1", kind="linux", image="alpine:latest")
        node_fail = TopologyNode(name="r2", kind="linux", image="alpine:latest")
        topo = _make_topology(
            nodes={"r1": node_ok, "r2": node_fail},
            links=[],
        )

        container_r1 = _make_container(name="archetype-lab1-r1", status="created")

        call_count = {"n": 0}

        def create_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return container_r1
            raise RuntimeError("image pull failed")

        # First call: containers.get raises NotFound (no existing container)
        p._docker.containers.get.side_effect = NotFound("no such container")

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        # Patch the lambda-based create call
        p._docker.containers.create = MagicMock(side_effect=create_side_effect)

        monkeypatch.setattr(p, "_calculate_required_interfaces", lambda topo: 2)
        monkeypatch.setattr(p, "_count_node_interfaces", lambda n, t: 1)
        monkeypatch.setattr(p, "_create_container_config", lambda node, lid, ws, interface_count=0: {
            "name": f"archetype-lab1-{node.name}",
            "image": node.image,
            "network_mode": "none",
        })

        with pytest.raises(RuntimeError, match="image pull failed"):
            await p._create_containers(topo, "lab1", Path("/tmp/ws"))

        # r1's container should have been cleaned up
        container_r1.remove.assert_called_once_with(force=True, v=True)

    @pytest.mark.asyncio
    async def test_cleanup_networks_on_failure_with_ovs_plugin(self, monkeypatch):
        """When creation fails and OVS plugin is enabled, networks are also cleaned up."""
        p = _make_provider()
        monkeypatch.setattr(type(p), "use_ovs_plugin", property(lambda self: True))

        topo = _make_topology(
            nodes={"r1": TopologyNode(name="r1", kind="linux", image="alpine:latest")},
        )

        p._docker.containers.get.side_effect = NotFound("no such container")

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        monkeypatch.setattr(p, "_calculate_required_interfaces", lambda topo: 1)
        monkeypatch.setattr(p, "_create_lab_networks", AsyncMock())
        monkeypatch.setattr(p, "_delete_lab_networks", AsyncMock(return_value=2))
        monkeypatch.setattr(p, "_count_node_interfaces", lambda n, t: 1)

        # Vendor config for network setup
        mock_vendor = MagicMock()
        mock_vendor.management_interface = "eth0"
        mock_vendor.reserved_nics = 0

        with patch("agent.providers.docker.get_config_by_device", return_value=mock_vendor):
            p._docker.containers.create = MagicMock(side_effect=RuntimeError("docker daemon error"))
            monkeypatch.setattr(p, "_create_container_config", lambda node, lid, ws, interface_count=0: {
                "name": "archetype-lab1-r1",
                "image": "alpine:latest",
            })

            with pytest.raises(RuntimeError):
                await p._create_containers(topo, "lab1", Path("/tmp/ws"))

        p._delete_lab_networks.assert_called_once_with("lab1")

    @pytest.mark.asyncio
    async def test_existing_running_container_is_reused(self, monkeypatch):
        """An already-running container is reused, not recreated."""
        p = _make_provider()
        monkeypatch.setattr(type(p), "use_ovs_plugin", property(lambda self: False))

        topo = _make_topology()
        container_running = _make_container(status="running")

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        p._docker.containers.get.return_value = container_running
        monkeypatch.setattr(p, "_calculate_required_interfaces", lambda topo: 1)

        result = await p._create_containers(topo, "lab1", Path("/tmp/ws"))
        assert "r1" in result
        assert result["r1"] is container_running
        # create should NOT have been called
        p._docker.containers.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_stopped_container_is_removed_then_recreated(self, monkeypatch):
        """A stopped existing container is removed before creating a new one."""
        p = _make_provider()
        monkeypatch.setattr(type(p), "use_ovs_plugin", property(lambda self: False))

        topo = _make_topology()
        stopped = _make_container(status="exited")
        new_container = _make_container(status="created")

        get_calls = {"n": 0}

        def get_side_effect(name):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                return stopped  # First call: existing stopped container
            raise NotFound("gone")  # Should not be called again in this flow

        p._docker.containers.get.side_effect = get_side_effect
        p._docker.containers.create.return_value = new_container

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        monkeypatch.setattr(p, "_calculate_required_interfaces", lambda topo: 1)
        monkeypatch.setattr(p, "_count_node_interfaces", lambda n, t: 1)
        monkeypatch.setattr(p, "_create_container_config", lambda node, lid, ws, interface_count=0: {
            "name": "archetype-lab1-r1",
            "image": "alpine:latest",
            "network_mode": "none",
        })

        result = await p._create_containers(topo, "lab1", Path("/tmp/ws"))
        stopped.remove.assert_called_once_with(force=True)
        assert "r1" in result


# ---------------------------------------------------------------------------
# _capture_container_vlans
# ---------------------------------------------------------------------------


class TestCaptureContainerVlans:
    """Tests for VLAN capture from running containers."""

    @pytest.mark.asyncio
    async def test_captures_vlans_for_running_container(self, monkeypatch):
        """Captures VLAN tags from OVS for each container interface."""
        p = _make_provider()
        topo = _make_topology()
        container = _make_container(pid=9999)

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        p._docker.containers.get.return_value = container

        # nsenter ls /sys/class/net/ returns eth0 and eth1
        ls_proc = _make_proc(returncode=0, stdout=b"lo eth0 eth1\n")

        async def fake_subprocess(*args, **kwargs):
            return ls_proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        # _get_interface_vlan returns different VLANs for each
        vlan_calls = []

        async def fake_get_vlan(pid, iface):
            vlan_calls.append(iface)
            if iface == "eth0":
                return 100
            elif iface == "eth1":
                return 200
            return None

        monkeypatch.setattr(p, "_get_interface_vlan", fake_get_vlan)
        monkeypatch.setattr(p, "_save_vlan_allocations", MagicMock())

        await p._capture_container_vlans("lab1", topo, Path("/tmp/ws"))

        assert "r1" in p._vlan_allocations.get("lab1", {})
        assert p._vlan_allocations["lab1"]["r1"] == [100, 200]
        assert p._next_vlan["lab1"] == 201  # max(100, 200) + 1
        p._save_vlan_allocations.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_container_not_found(self, monkeypatch):
        """Gracefully skips containers that don't exist anymore."""
        p = _make_provider()
        topo = _make_topology()

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        p._docker.containers.get.side_effect = NotFound("gone")

        await p._capture_container_vlans("lab1", topo, Path("/tmp/ws"))

        # No allocations should be stored
        assert p._vlan_allocations.get("lab1", {}) == {}

    @pytest.mark.asyncio
    async def test_handles_nsenter_failure(self, monkeypatch):
        """Handles nsenter failure when listing interfaces."""
        p = _make_provider()
        topo = _make_topology()
        container = _make_container(pid=9999)

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        p._docker.containers.get.return_value = container

        # nsenter fails
        fail_proc = _make_proc(returncode=1, stderr=b"permission denied")
        async def fake_subprocess(*args, **kwargs):
            return fail_proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        await p._capture_container_vlans("lab1", topo, Path("/tmp/ws"))

        # No allocations
        assert p._vlan_allocations.get("lab1", {}) == {}

    @pytest.mark.asyncio
    async def test_no_save_when_no_vlans_captured(self, monkeypatch):
        """Does not persist allocations if no VLANs were captured."""
        p = _make_provider()
        topo = _make_topology()
        container = _make_container(pid=9999)

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        p._docker.containers.get.return_value = container

        # nsenter returns only lo
        ls_proc = _make_proc(returncode=0, stdout=b"lo\n")
        async def fake_subprocess(*args, **kwargs):
            return ls_proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
        monkeypatch.setattr(p, "_save_vlan_allocations", MagicMock())

        await p._capture_container_vlans("lab1", topo, Path("/tmp/ws"))

        p._save_vlan_allocations.assert_not_called()


# ---------------------------------------------------------------------------
# _recover_stale_network
# ---------------------------------------------------------------------------


class TestRecoverStaleNetwork:
    """Tests for stale network recovery logic."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_vlan_file(self, monkeypatch):
        """Returns empty dict when no persisted VLAN allocations exist."""
        p = _make_provider()
        monkeypatch.setattr(p, "_load_vlan_allocations", lambda lab_id, ws: False)

        result = await p._recover_stale_network("lab1", Path("/tmp/ws"))
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_allocations_empty(self, monkeypatch):
        """Returns empty dict when loaded allocations have no entries."""
        p = _make_provider()

        def fake_load(lab_id, ws):
            p._vlan_allocations["lab1"] = {}
            return True
        monkeypatch.setattr(p, "_load_vlan_allocations", fake_load)

        result = await p._recover_stale_network("lab1", Path("/tmp/ws"))
        assert result == {}

    @pytest.mark.asyncio
    async def test_recovers_nodes_with_existing_containers(self, monkeypatch):
        """Keeps allocations for nodes whose containers still exist."""
        p = _make_provider()

        def fake_load(lab_id, ws):
            p._vlan_allocations["lab1"] = {
                "r1": [100, 101],
                "r2": [200, 201],
            }
            return True
        monkeypatch.setattr(p, "_load_vlan_allocations", fake_load)
        monkeypatch.setattr(p, "_save_vlan_allocations", MagicMock())

        # Only r1 container exists
        c1 = _make_container(name="archetype-lab1-r1", labels={
            LABEL_LAB_ID: "lab1",
            LABEL_NODE_NAME: "r1",
        })

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        p._docker.containers.list.return_value = [c1]

        result = await p._recover_stale_network("lab1", Path("/tmp/ws"))
        assert "r1" in result
        assert result["r1"] == [100, 101]
        assert "r2" not in result
        p._save_vlan_allocations.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_on_docker_exception(self, monkeypatch):
        """Returns empty dict when Docker API call fails during recovery."""
        p = _make_provider()

        def fake_load(lab_id, ws):
            p._vlan_allocations["lab1"] = {"r1": [100]}
            return True
        monkeypatch.setattr(p, "_load_vlan_allocations", fake_load)

        async def fake_to_thread(func, *args, **kwargs):
            raise APIError("daemon unreachable")
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        result = await p._recover_stale_network("lab1", Path("/tmp/ws"))
        assert result == {}


# ---------------------------------------------------------------------------
# _is_transient_docker_error
# ---------------------------------------------------------------------------


class TestIsTransientDockerError:
    """Tests for transient error classification."""

    def test_500_api_error_is_transient(self):
        p = _make_provider()
        err = _api_error(500, "Internal Server Error")
        assert p._is_transient_docker_error(err) is True

    def test_502_api_error_is_transient(self):
        p = _make_provider()
        err = _api_error(502, "Bad Gateway")
        assert p._is_transient_docker_error(err) is True

    def test_404_api_error_is_not_transient(self):
        p = _make_provider()
        err = _api_error(404, "Not Found")
        assert p._is_transient_docker_error(err) is False

    def test_timeout_message_is_transient(self):
        p = _make_provider()
        assert p._is_transient_docker_error(Exception("connection timed out")) is True

    def test_connection_reset_is_transient(self):
        p = _make_provider()
        assert p._is_transient_docker_error(Exception("connection reset by peer")) is True

    def test_docker_daemon_not_running_is_transient(self):
        p = _make_provider()
        assert p._is_transient_docker_error(Exception("docker daemon is not running")) is True

    def test_permission_denied_is_not_transient(self):
        p = _make_provider()
        assert p._is_transient_docker_error(Exception("permission denied")) is False

    def test_broken_pipe_is_transient(self):
        p = _make_provider()
        assert p._is_transient_docker_error(Exception("broken pipe")) is True


# ---------------------------------------------------------------------------
# _retry_docker_call
# ---------------------------------------------------------------------------


class TestRetryDockerCall:
    """Tests for Docker operation retry logic."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        p = _make_provider()
        result = await p._retry_docker_call("test_op", lambda: 42)
        assert result == 42

    @pytest.mark.asyncio
    async def test_retries_on_transient_error_then_succeeds(self, monkeypatch):
        p = _make_provider()
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        call_count = {"n": 0}

        def flaky_func():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise _api_error(500, "Internal Server Error")
            return "ok"

        result = await p._retry_docker_call("test_op", flaky_func)
        assert result == "ok"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self, monkeypatch):
        p = _make_provider()
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        def always_fail():
            raise _api_error(500, "Internal Server Error")

        with pytest.raises(APIError):
            await p._retry_docker_call("test_op", always_fail)

    @pytest.mark.asyncio
    async def test_non_transient_error_not_retried(self):
        p = _make_provider()

        def perm_fail():
            raise _api_error(404, "Not Found")

        with pytest.raises(APIError):
            await p._retry_docker_call("test_op", perm_fail)


# ---------------------------------------------------------------------------
# _get_interface_vlan
# ---------------------------------------------------------------------------


class TestGetInterfaceVlan:
    """Tests for OVS VLAN tag lookup from container interface."""

    @pytest.mark.asyncio
    async def test_returns_vlan_on_success(self, monkeypatch):
        """Successfully resolves VLAN tag through iflink -> ip link -> ovs-vsctl."""
        p = _make_provider()

        procs = [
            # nsenter cat iflink -> peer index 42
            _make_proc(returncode=0, stdout=b"42\n"),
            # ip -o link show -> find host veth
            _make_proc(returncode=0, stdout=b"42: vhABC12@if43: <BROADCAST>\n"),
            # ovs-vsctl get port tag
            _make_proc(returncode=0, stdout=b"150\n"),
        ]
        proc_idx = {"i": 0}

        async def fake_subprocess(*args, **kwargs):
            idx = proc_idx["i"]
            proc_idx["i"] += 1
            return procs[idx]
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        result = await p._get_interface_vlan(1234, "eth1")
        assert result == 150

    @pytest.mark.asyncio
    async def test_returns_none_when_iflink_fails(self, monkeypatch):
        p = _make_provider()

        fail_proc = _make_proc(returncode=1)
        async def fake_subprocess(*args, **kwargs):
            return fail_proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        result = await p._get_interface_vlan(1234, "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_host_veth_not_found(self, monkeypatch):
        p = _make_provider()

        procs = [
            _make_proc(returncode=0, stdout=b"42\n"),
            # ip link output doesn't contain matching index
            _make_proc(returncode=0, stdout=b"99: vethXYZ@if100: <BROADCAST>\n"),
        ]
        proc_idx = {"i": 0}
        async def fake_subprocess(*args, **kwargs):
            idx = proc_idx["i"]
            proc_idx["i"] += 1
            return procs[idx]
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        result = await p._get_interface_vlan(1234, "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_ovs_query_fails(self, monkeypatch):
        p = _make_provider()

        procs = [
            _make_proc(returncode=0, stdout=b"42\n"),
            _make_proc(returncode=0, stdout=b"42: vhABC@if43: <BROADCAST>\n"),
            _make_proc(returncode=1, stderr=b"no such port"),  # ovs-vsctl fails
        ]
        proc_idx = {"i": 0}
        async def fake_subprocess(*args, **kwargs):
            idx = proc_idx["i"]
            proc_idx["i"] += 1
            return procs[idx]
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

        result = await p._get_interface_vlan(1234, "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, monkeypatch):
        p = _make_provider()

        async def boom(*args, **kwargs):
            raise OSError("subprocess failed")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

        result = await p._get_interface_vlan(1234, "eth1")
        assert result is None


# ---------------------------------------------------------------------------
# deploy() error branches
# ---------------------------------------------------------------------------


class TestDeployErrorBranches:
    """Tests for deploy() returning failure results."""

    @pytest.mark.asyncio
    async def test_deploy_no_topology_returns_failure(self):
        """deploy() with no topology returns error result."""
        p = _make_provider()
        result = await p.deploy("lab1", None, Path("/tmp/ws"))
        assert result.success is False
        assert "No topology" in result.error

    @pytest.mark.asyncio
    async def test_deploy_empty_nodes_returns_failure(self, monkeypatch):
        """deploy() with empty node list returns error."""
        p = _make_provider()
        empty_topo = MagicMock()
        empty_topo.nodes = {}

        with patch.object(p, "_topology_from_json", return_value=ParsedTopology(name="lab", nodes={}, links=[])):
            result = await p.deploy("lab1", MagicMock(), Path("/tmp/ws"))
        assert result.success is False
        assert "No nodes" in result.error

    @pytest.mark.asyncio
    async def test_deploy_missing_images_returns_failure(self, monkeypatch):
        """deploy() with missing images returns descriptive error."""
        p = _make_provider()
        topo = _make_topology()

        monkeypatch.setattr(p, "_topology_from_json", lambda dt: topo)
        monkeypatch.setattr(p, "_recover_stale_network", AsyncMock(return_value={}))

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        monkeypatch.setattr(p, "_validate_images", lambda t: [("r1", "missing:image")])

        result = await p.deploy("lab1", MagicMock(), Path("/tmp/ws"))
        assert result.success is False
        assert "Missing" in result.error
        assert "missing:image" in result.stderr


# ---------------------------------------------------------------------------
# _get_container_status mapping
# ---------------------------------------------------------------------------


class TestGetContainerStatus:
    """Tests for Docker status -> NodeStatus mapping."""

    def test_running(self):
        p = _make_provider()
        c = MagicMock()
        c.status = "running"
        assert p._get_container_status(c) == NodeStatus.RUNNING

    def test_created(self):
        p = _make_provider()
        c = MagicMock()
        c.status = "created"
        assert p._get_container_status(c) == NodeStatus.PENDING

    def test_exited(self):
        p = _make_provider()
        c = MagicMock()
        c.status = "exited"
        assert p._get_container_status(c) == NodeStatus.STOPPED

    def test_paused(self):
        p = _make_provider()
        c = MagicMock()
        c.status = "paused"
        assert p._get_container_status(c) == NodeStatus.STOPPED

    def test_restarting(self):
        p = _make_provider()
        c = MagicMock()
        c.status = "restarting"
        assert p._get_container_status(c) == NodeStatus.STARTING

    def test_unknown_status(self):
        p = _make_provider()
        c = MagicMock()
        c.status = "removing"
        assert p._get_container_status(c) == NodeStatus.UNKNOWN


# ---------------------------------------------------------------------------
# _node_from_container
# ---------------------------------------------------------------------------


class TestNodeFromContainer:
    """Tests for container -> NodeInfo conversion."""

    def test_returns_none_without_node_name_label(self):
        p = _make_provider()
        c = _make_container()
        c.labels = {}  # No LABEL_NODE_NAME
        assert p._node_from_container(c) is None

    def test_returns_node_info_with_valid_labels(self):
        p = _make_provider()
        c = _make_container()
        node = p._node_from_container(c)
        assert node is not None
        assert node.name == "r1"
        assert node.status == NodeStatus.RUNNING
        assert node.container_id == "abc123d"

    def test_handles_image_without_tags(self):
        p = _make_provider()
        c = _make_container()
        c.image.tags = []
        c.image.id = "sha256:abcdef123456789012"
        node = p._node_from_container(c)
        assert node is not None
        assert node.image == "sha256:abcde"  # [:12]


# ---------------------------------------------------------------------------
# _cleanup_lab_volumes
# ---------------------------------------------------------------------------


class TestCleanupLabVolumes:
    """Tests for lab volume cleanup."""

    @pytest.mark.asyncio
    async def test_removes_matching_volumes(self, monkeypatch):
        p = _make_provider()
        vol1 = MagicMock()
        vol1.name = "vol1"
        vol1.remove = MagicMock()
        vol2 = MagicMock()
        vol2.name = "vol2"
        vol2.remove = MagicMock()

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        p._docker.volumes.list.return_value = [vol1, vol2]

        count = await p._cleanup_lab_volumes("lab1")
        assert count == 2
        vol1.remove.assert_called_once_with(force=True)
        vol2.remove.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    async def test_handles_volume_in_use(self, monkeypatch):
        p = _make_provider()
        vol1 = MagicMock()
        vol1.name = "vol1"
        vol1.remove = MagicMock(side_effect=APIError("volume is in use"))

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        p._docker.volumes.list.return_value = [vol1]

        count = await p._cleanup_lab_volumes("lab1")
        assert count == 0  # Failed to remove

    @pytest.mark.asyncio
    async def test_handles_list_failure(self, monkeypatch):
        p = _make_provider()

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        p._docker.volumes.list.side_effect = APIError("daemon error")

        count = await p._cleanup_lab_volumes("lab1")
        assert count == 0
