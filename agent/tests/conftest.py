from __future__ import annotations

import os
import sys
import types

import pytest

from agent.config import settings


@pytest.fixture(autouse=True)
def _set_testing_env(monkeypatch, tmp_path):
    """Ensure agent startup tasks are disabled during unit tests.

    Also redirect workspace_path to a temp directory so tests don't
    try to create /var/lib/archetype-agent (which fails in CI).
    """
    monkeypatch.setenv("ARCHETYPE_AGENT_TESTING", "1")
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
    yield


def _install_docker_stub() -> None:
    """Provide a minimal docker module stub when docker isn't installed.

    This avoids import-time failures in unit tests that mock Docker behavior.
    """
    try:
        import docker  # noqa: F401
        return
    except Exception:
        pass

    docker_mod = types.ModuleType("docker")

    class DockerException(Exception):
        pass

    class NotFound(DockerException):
        pass

    class APIError(DockerException):
        pass

    class ImageNotFound(DockerException):
        pass

    errors_mod = types.ModuleType("docker.errors")
    errors_mod.DockerException = DockerException
    errors_mod.NotFound = NotFound
    errors_mod.APIError = APIError
    errors_mod.ImageNotFound = ImageNotFound

    class DockerClient:
        def __init__(self, *args, **kwargs):
            pass

    def from_env(*args, **kwargs):
        return DockerClient()

    types_mod = types.ModuleType("docker.types")

    class IPAMPool:
        def __init__(self, *args, **kwargs):
            pass

    class IPAMConfig:
        def __init__(self, *args, **kwargs):
            pass

    types_mod.IPAMPool = IPAMPool
    types_mod.IPAMConfig = IPAMConfig

    docker_mod.errors = errors_mod
    docker_mod.types = types_mod
    docker_mod.DockerClient = DockerClient
    docker_mod.from_env = from_env

    sys.modules["docker"] = docker_mod
    sys.modules["docker.errors"] = errors_mod
    sys.modules["docker.types"] = types_mod


def _install_aiohttp_stub() -> None:
    """Provide a minimal aiohttp.web stub when aiohttp isn't installed."""
    try:
        import aiohttp  # noqa: F401
        return
    except Exception:
        pass

    aiohttp_mod = types.ModuleType("aiohttp")
    web_mod = types.ModuleType("aiohttp.web")

    class _Router:
        def add_post(self, *_args, **_kwargs):
            return None

    class Application(dict):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.router = _Router()

    class Response:
        def __init__(self, *args, **kwargs):
            self.body = kwargs.get("text") or kwargs.get("body")

    def json_response(data=None, *args, **kwargs):
        return Response(body=data)

    class HTTPException(Exception):
        pass

    class HTTPBadRequest(HTTPException):
        pass

    class HTTPInternalServerError(HTTPException):
        pass

    class HTTPNotFound(HTTPException):
        pass

    web_mod.Application = Application
    web_mod.Response = Response
    web_mod.json_response = json_response
    web_mod.HTTPException = HTTPException
    web_mod.HTTPBadRequest = HTTPBadRequest
    web_mod.HTTPInternalServerError = HTTPInternalServerError
    web_mod.HTTPNotFound = HTTPNotFound

    aiohttp_mod.web = web_mod

    sys.modules["aiohttp"] = aiohttp_mod
    sys.modules["aiohttp.web"] = web_mod


_install_docker_stub()
_install_aiohttp_stub()


def _install_asyncssh_stub() -> None:
    """Provide a minimal asyncssh stub when asyncssh isn't installed."""
    try:
        import asyncssh  # noqa: F401
        return
    except Exception:
        pass

    asyncssh_mod = types.ModuleType("asyncssh")

    class Error(Exception):
        pass

    class SSHClientProcess:
        def __init__(self):
            self.stdin = types.SimpleNamespace(write=lambda _data: None)
            self.stdout = types.SimpleNamespace(read=lambda _size: b"")

        def change_terminal_size(self, *_args, **_kwargs):
            return None

        def close(self):
            return None

    class SSHClientConnection:
        async def create_process(self, *args, **kwargs):
            return SSHClientProcess()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def connect(*args, **kwargs):
        return SSHClientConnection()

    asyncssh_mod.Error = Error
    asyncssh_mod.SSHClientConnection = SSHClientConnection
    asyncssh_mod.SSHClientProcess = SSHClientProcess
    asyncssh_mod.connect = connect

    sys.modules["asyncssh"] = asyncssh_mod


_install_asyncssh_stub()


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless explicitly enabled."""
    if os.getenv("ARCHETYPE_RUN_INTEGRATION") in {"1", "true", "TRUE", "yes", "YES"}:
        return

    skip_integration = pytest.mark.skip(reason="Integration tests require Docker. Set ARCHETYPE_RUN_INTEGRATION=1 to run.")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
