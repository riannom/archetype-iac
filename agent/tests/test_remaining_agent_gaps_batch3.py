from __future__ import annotations

import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

import pytest


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "OK") -> None:
        self.status_code = status_code
        self.text = text


class FakeAsyncClient:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self._response = response or FakeResponse()
        self.posts: list[tuple[str, dict | None, float | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None, timeout: float | None = None):
        self.posts.append((url, json, timeout))
        return self._response


@pytest.mark.asyncio
async def test_callbacks_deliver_and_dead_letter(monkeypatch) -> None:
    import agent.callbacks as callbacks

    callbacks._dead_letters.clear()
    client = FakeAsyncClient(response=FakeResponse(status_code=500, text="fail"))
    monkeypatch.setattr(callbacks.httpx, "AsyncClient", lambda **_: client)
    monkeypatch.setattr(callbacks, "DEFAULT_RETRY_DELAYS", [0])
    monkeypatch.setattr(callbacks, "MAX_RETRY_ATTEMPTS", 1)

    payload = callbacks.CallbackPayload(job_id="job1", agent_id="agent1", status="failed")
    delivered = await callbacks.deliver_callback("http://controller/callbacks/job/job1", payload)
    assert delivered is False
    assert callbacks._dead_letters


@pytest.mark.asyncio
async def test_callbacks_heartbeat(monkeypatch) -> None:
    import agent.callbacks as callbacks

    client = FakeAsyncClient(response=FakeResponse(status_code=204, text=""))
    monkeypatch.setattr(callbacks.httpx, "AsyncClient", lambda **_: client)
    result = await callbacks.send_heartbeat("http://controller/callbacks/job/job1", "job1")
    assert result is True
    assert client.posts[0][0].endswith("/heartbeat")


@pytest.mark.asyncio
async def test_execute_with_callback_success(monkeypatch) -> None:
    import agent.callbacks as callbacks

    monkeypatch.setattr(callbacks, "deliver_callback", lambda *_: asyncio.sleep(0))

    async def operation():
        return SimpleNamespace(success=True, stdout="ok", stderr="", error=None)

    await callbacks.execute_with_callback("job1", "agent1", "http://cb", operation)


def test_agent_init_import() -> None:
    import agent
    import agent.__init__ as agent_init
    assert hasattr(agent, "__file__")
    assert agent.__file__ == agent_init.__file__


def test_agent_init_includes_empty() -> None:
    import agent
    with open(agent.__file__, "r") as handle:
        assert handle.read().strip() == ""


def test_agent_tests_init_import() -> None:
    import agent.tests
    assert hasattr(agent.tests, "__file__")


def test_network_init_imports() -> None:
    import agent.network as network

    assert "OverlayManager" in network.__all__
    assert "DockerOVSPlugin" in network.__all__


def _install_ovs_backend_dependencies(monkeypatch) -> None:
    overlay_mod = types.ModuleType("agent.network.overlay")

    class OverlayManager:
        async def recover_allocations(self):
            return 1

        async def recover_link_tunnels(self):
            return 0

        async def create_tunnel(self, **_):
            return "tunnel"

        async def create_bridge(self, *_):
            return None

        async def get_bridges_for_lab(self, *_):
            return []

        async def attach_container(self, **_):
            return True

        async def cleanup_lab(self, *_):
            return {"tunnels_deleted": 0, "bridges_deleted": 0, "errors": []}

        def get_tunnel_status(self):
            return {"tunnels": []}

        def get_vtep(self, *_):
            return None

        async def ensure_vtep(self, **_):
            return "vtep"

        async def attach_overlay_interface(self, **_):
            return True

        async def detach_overlay_interface(self, **_):
            return {"success": True}

    overlay_mod.OverlayManager = OverlayManager

    ovs_mod = types.ModuleType("agent.network.ovs")

    class OVSNetworkManager:
        def __init__(self):
            self._initialized = False

        async def initialize(self):
            self._initialized = True

        async def recover_allocations(self):
            return 2

        def get_status(self):
            return {"bridge": "arch-ovs"}

        def get_links_for_lab(self, *_):
            return []

        async def handle_container_restart(self, *_):
            return {"reprovisioned_ports": 0, "reconnected_links": 0, "errors": []}

        async def connect_to_external(self, **_):
            return 100

        async def create_patch_to_bridge(self, **_):
            return "patch0"

        async def delete_patch_to_bridge(self, *_):
            return True

        async def detach_external_interface(self, *_):
            return True

        async def list_external_connections(self):
            return []

    ovs_mod.OVSNetworkManager = OVSNetworkManager

    plugin_mod = types.ModuleType("agent.network.docker_plugin")

    class PluginRunner:
        async def cleanup(self):
            return None

    class DockerOVSPlugin:
        async def start(self):
            return PluginRunner()

        async def shutdown(self):
            return None

    plugin_mod.get_docker_ovs_plugin = lambda: DockerOVSPlugin()

    monkeypatch.setitem(sys.modules, "agent.network.overlay", overlay_mod)
    monkeypatch.setitem(sys.modules, "agent.network.ovs", ovs_mod)
    monkeypatch.setitem(sys.modules, "agent.network.docker_plugin", plugin_mod)


@pytest.mark.asyncio
async def test_ovs_backend_initialize_and_shutdown(monkeypatch) -> None:
    _install_ovs_backend_dependencies(monkeypatch)
    import agent.network.backends.ovs_backend as ovs_backend
    importlib.reload(ovs_backend)

    monkeypatch.setattr(ovs_backend.settings, "enable_vxlan", True, raising=False)
    monkeypatch.setattr(ovs_backend.settings, "enable_ovs", True, raising=False)
    monkeypatch.setattr(ovs_backend.settings, "enable_docker", True, raising=False)
    monkeypatch.setattr(ovs_backend.settings, "enable_ovs_plugin", True, raising=False)

    backend = ovs_backend.OVSBackend()
    info = await backend.initialize()
    assert info["vnis_recovered"] == 1
    assert info["vlans_recovered"] == 2
    assert info["ovs_plugin_started"] is True

    await backend.shutdown()


def test_ovs_backend_check_port_exists(monkeypatch) -> None:
    _install_ovs_backend_dependencies(monkeypatch)
    import agent.network.backends.ovs_backend as ovs_backend
    importlib.reload(ovs_backend)

    class Result:
        def __init__(self, returncode: int):
            self.returncode = returncode

    monkeypatch.setattr(ovs_backend.subprocess, "run", lambda *_args, **_kwargs: Result(0))
    backend = ovs_backend.OVSBackend()
    assert backend.check_port_exists("vnet0") is True


@pytest.mark.asyncio
async def test_updater_detect_modes(monkeypatch) -> None:
    import agent.updater as updater

    monkeypatch.setattr(updater.Path, "exists", lambda self: True)
    assert updater.detect_deployment_mode() == updater.DeploymentMode.DOCKER

    monkeypatch.setattr(updater.Path, "exists", lambda self: False)
    monkeypatch.setattr(updater.os, "environ", {})

    class Result:
        def __init__(self, returncode: int):
            self.returncode = returncode

    monkeypatch.setattr(updater.subprocess, "run", lambda *_args, **_kwargs: Result(3))
    assert updater.detect_deployment_mode() == updater.DeploymentMode.SYSTEMD


@pytest.mark.asyncio
async def test_updater_report_progress(monkeypatch) -> None:
    import agent.updater as updater

    client = FakeAsyncClient()
    await updater.report_progress(
        client=client,
        callback_url="http://controller/update",
        job_id="job1",
        agent_id="agent1",
        status="downloading",
        progress_percent=10,
    )
    assert client.posts
