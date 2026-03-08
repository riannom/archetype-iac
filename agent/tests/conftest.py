from __future__ import annotations

import os
import sys
import types
import inspect
import asyncio
import threading

import pytest
import httpx

from agent.config import settings

# Test modules import ``agent.main.app`` at collection time, before fixtures run.
# Set the flag here so the app is built in test mode and avoids lifespan/TestClient deadlocks.
os.environ.setdefault("ARCHETYPE_AGENT_TESTING", "1")


@pytest.fixture(autouse=True)
def _set_testing_env(monkeypatch, tmp_path):
    """Ensure agent startup tasks are disabled during unit tests.

    Also redirect workspace_path to a temp directory so tests don't
    try to create /var/lib/archetype-agent (which fails in CI).
    """
    monkeypatch.setenv("ARCHETYPE_AGENT_TESTING", "1")
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
    yield


@pytest.fixture(autouse=True)
def _clear_docker_client_cache():
    """Clear the cached Docker client between tests.

    This ensures test-level patches of ``docker.from_env`` take effect
    and prevents a cached real/stub client from leaking across tests.
    """
    from agent.docker_client import get_docker_client
    get_docker_client.cache_clear()
    yield
    get_docker_client.cache_clear()


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


def _item_source(item: pytest.Item) -> str:
    try:
        obj = getattr(item, "obj", None)
        if obj is None:
            return ""
        return inspect.getsource(obj)
    except Exception:
        return ""


_FILE_TEXT_CACHE: dict[str, str] = {}


def _file_text(item: pytest.Item) -> str:
    path = str(getattr(item, "fspath", ""))
    if path not in _FILE_TEXT_CACHE:
        try:
            _FILE_TEXT_CACHE[path] = open(path, encoding="utf-8").read()
        except Exception:
            _FILE_TEXT_CACHE[path] = ""
    return _FILE_TEXT_CACHE[path]


def _file_uses_test_client(item: pytest.Item) -> bool:
    text = _file_text(item)
    return "TestClient" in text or "websocket_connect(" in text


def _file_uses_asyncio_run(item: pytest.Item) -> bool:
    return "asyncio.run(" in _file_text(item)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Order tests to avoid TestClient deadlocks and skip integration by default.

    In this environment, Starlette TestClient can deadlock after earlier tests
    call ``asyncio.run(...)`` in the same process. Prefer HTTP/websocket client
    tests first, then ordinary tests, then direct ``asyncio.run`` callers.
    """

    def _priority(item: pytest.Item) -> tuple[int, str]:
        if _file_uses_test_client(item):
            return (0, item.nodeid)
        if _file_uses_asyncio_run(item):
            return (2, item.nodeid)
        return (1, item.nodeid)

    items.sort(key=_priority)
    if os.getenv("ARCHETYPE_RUN_INTEGRATION") in {"1", "true", "TRUE", "yes", "YES"}:
        return

    skip_integration = pytest.mark.skip(
        reason="Integration tests require Docker. Set ARCHETYPE_RUN_INTEGRATION=1 to run."
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


class _AsyncRequestRunner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="agent-test-http-loop",
            daemon=True,
        )
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


@pytest.fixture(autouse=True, scope="session")
def _patch_testclient_http_requests():
    """Route TestClient HTTP requests through ASGITransport in tests.

    In this environment, Starlette's blocking portal can deadlock once enough
    agent tests have been collected. Plain HTTP requests do not need the portal,
    so use httpx.ASGITransport directly and keep websocket paths on the native
    TestClient implementation.
    """

    from starlette.testclient import TestClient as StarletteTestClient

    original_request = StarletteTestClient.request
    runner = _AsyncRequestRunner()

    def _request(self, method: str, url: str, *args, **kwargs):
        if url.startswith("ws://") or url.startswith("wss://"):
            return original_request(self, method, url, *args, **kwargs)

        async def _send():
            transport_config = getattr(self, "_transport", None)
            transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=getattr(transport_config, "raise_server_exceptions", True),
                root_path=getattr(transport_config, "root_path", ""),
                client=getattr(transport_config, "client", ("testclient", 50000)),
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url=str(getattr(self, "_base_url", "http://testserver")),
                headers=getattr(self, "_headers", None),
                cookies=getattr(self, "_cookies", None),
                follow_redirects=self.follow_redirects,
            ) as client:
                return await client.request(method, url, *args, **kwargs)

        response = runner.run(_send())
        getattr(self, "_cookies").update(response.cookies)
        return response

    StarletteTestClient.request = _request
    try:
        yield
    finally:
        StarletteTestClient.request = original_request
        runner.close()


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
