from __future__ import annotations

import importlib
import json
import logging
import sys
import types
from types import SimpleNamespace

import pytest

import agent.events.base as events_base
import agent.logging_config as logging_config
import agent.version as version_module


class DockerException(Exception):
    pass


class APIError(Exception):
    pass


class NotFound(Exception):
    pass


def _install_docker_modules(monkeypatch) -> None:
    errors_mod = types.ModuleType("docker.errors")
    errors_mod.DockerException = DockerException
    errors_mod.APIError = APIError
    errors_mod.NotFound = NotFound

    docker_mod = types.ModuleType("docker")
    docker_mod.errors = errors_mod
    docker_mod.DockerClient = object

    def _from_env():
        return object()

    docker_mod.from_env = _from_env

    models_mod = types.ModuleType("docker.models")
    containers_mod = types.ModuleType("docker.models.containers")

    class Container:
        pass

    containers_mod.Container = Container
    models_mod.containers = containers_mod

    monkeypatch.setitem(sys.modules, "docker", docker_mod)
    monkeypatch.setitem(sys.modules, "docker.errors", errors_mod)
    monkeypatch.setitem(sys.modules, "docker.models", models_mod)
    monkeypatch.setitem(sys.modules, "docker.models.containers", containers_mod)


def _load_docker_events(monkeypatch):
    _install_docker_modules(monkeypatch)
    import agent.events.docker_events as docker_events
    importlib.reload(docker_events)
    return docker_events


def test_node_event_log_name() -> None:
    event = events_base.NodeEvent(
        lab_id="lab1",
        node_name="node1",
        display_name="Node One",
        container_id="cid",
        event_type=events_base.NodeEventType.STARTED,
        timestamp=events_base.datetime.now(),
        status="running",
    )
    assert event.log_name() == "Node One(node1)"


def test_events_package_exports(monkeypatch) -> None:
    _install_docker_modules(monkeypatch)
    import agent.events as events_pkg
    assert "DockerEventListener" in events_pkg.__all__


def test_docker_event_parse(monkeypatch) -> None:
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()

    assert listener._parse_event({"Type": "image"}) is None
    assert listener._parse_event({"Type": "container", "Action": "pause"}) is None

    event = {
        "Type": "container",
        "Action": "die",
        "timeNano": 1_700_000_000_000_000_000,
        "Actor": {
            "ID": "abcd",
            "Attributes": {
                "archetype.node_name": "node1",
                "archetype.lab_id": "lab1",
                "archetype.node_kind": "ceos",
                "archetype.node_display_name": "Node 1",
                "exitCode": "137",
                "image": "ceos:latest",
                "name": "container1",
            },
        },
    }

    parsed = listener._parse_event(event)
    assert parsed is not None
    assert parsed.event_type == docker_events.NodeEventType.DIED
    assert parsed.status == "exited (code 137)"
    assert parsed.display_name == "Node 1"


class FakeStdout:
    def __init__(self, data: str | bytes) -> None:
        self._data = data

    async def read(self, size: int):
        return self._data


class FakeStdin:
    def __init__(self) -> None:
        self.data: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.data.append(data)


class FakeProcess:
    def __init__(self, data: str | bytes = "output") -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(data)
        self.resize_calls: list[tuple[int, int]] = []
        self.closed = False

    def change_terminal_size(self, cols: int, rows: int) -> None:
        self.resize_calls.append((rows, cols))

    def close(self) -> None:
        self.closed = True


class FakeConn:
    def __init__(self, process: FakeProcess) -> None:
        self._process = process
        self.closed = False

    async def create_process(self, **kwargs):
        return self._process

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _load_ssh_console(monkeypatch, process: FakeProcess):
    asyncssh_mod = types.ModuleType("asyncssh")

    class Error(Exception):
        pass

    async def _connect(*args, **kwargs):
        return FakeConn(process)

    asyncssh_mod.connect = _connect
    asyncssh_mod.Error = Error
    asyncssh_mod.SSHClientConnection = FakeConn
    asyncssh_mod.SSHClientProcess = FakeProcess

    monkeypatch.setitem(sys.modules, "asyncssh", asyncssh_mod)

    import agent.console.ssh_console as ssh_console
    importlib.reload(ssh_console)
    return ssh_console


@pytest.mark.asyncio
async def test_ssh_console_session(monkeypatch) -> None:
    process = FakeProcess("hello")
    ssh_console = _load_ssh_console(monkeypatch, process)

    console = ssh_console.SSHConsole("127.0.0.1", "user", "pass")
    assert await console.start() is True
    assert await console.resize(24, 80) is True
    assert await console.write(b"show version\n") is True
    data = await console.read()
    assert data == b"hello"
    await console.close()


@pytest.mark.asyncio
async def test_ssh_console_session_helper(monkeypatch) -> None:
    process = FakeProcess(b"data")
    ssh_console = _load_ssh_console(monkeypatch, process)

    output: list[bytes] = []

    def on_output(chunk: bytes) -> None:
        output.append(chunk)

    def get_input() -> bytes | None:
        return None

    await ssh_console.ssh_console_session(
        host="127.0.0.1",
        username="user",
        password="pass",
        on_output=on_output,
        get_input=get_input,
    )

    assert output == [b"data"]


def test_console_package_import(monkeypatch) -> None:
    _install_docker_modules(monkeypatch)
    import agent.console as console_pkg
    assert "DockerConsole" in console_pkg.__all__


def test_logging_formatters(monkeypatch) -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.custom = {"a": 1}

    formatter = logging_config.AgentJSONFormatter(agent_id="agent-123")
    payload = json.loads(formatter.format(record))
    assert payload["service"] == "agent"
    assert payload["agent_id"] == "agent-123"
    assert payload["extra"]["custom"] == {"a": 1}

    text_formatter = logging_config.AgentTextFormatter(agent_id="agent-123")
    message = text_formatter.format(record)
    # AgentTextFormatter truncates agent_id to 8 characters
    assert "agent-12" in message
    assert "hello world" in message


def test_setup_agent_logging(monkeypatch) -> None:
    monkeypatch.setattr(logging_config.settings, "log_level", "INFO", raising=False)
    monkeypatch.setattr(logging_config.settings, "log_format", "json", raising=False)
    logging_config.setup_agent_logging(agent_id="agent-xyz")

    root = logging.getLogger()
    assert root.handlers
    assert isinstance(root.handlers[-1].formatter, logging_config.AgentJSONFormatter)


def test_version_git_tag_fallback(monkeypatch) -> None:
    monkeypatch.setattr(version_module.Path, "exists", lambda self: False)

    def _run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="v1.2.3\n")

    monkeypatch.setattr(version_module.subprocess, "run", _run)
    assert version_module.get_version() == "1.2.3"


def test_version_commit_env(monkeypatch) -> None:
    monkeypatch.setenv("ARCHETYPE_GIT_SHA", "deadbeef")
    assert version_module.get_commit() == "deadbeef"


def test_console_extractor_clean_config(monkeypatch) -> None:
    import agent.console_extractor as console_extractor

    extractor = console_extractor.SerialConsoleExtractor.__new__(
        console_extractor.SerialConsoleExtractor
    )
    raw = "\x1b[0mshow run\r\nBuilding configuration...\r\n\r\ninterface eth0\r\n"
    cleaned = extractor._clean_config(raw, "show run")
    assert "Building configuration" not in cleaned
    assert "interface eth0" in cleaned


def test_extract_vm_config_no_pexpect(monkeypatch) -> None:
    import agent.console_extractor as console_extractor

    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", False)
    result = console_extractor.extract_vm_config("domain1", "kind1")
    assert result.success is False
    assert "pexpect package is not installed" in result.error


def test_run_vm_post_boot_commands_no_commands(monkeypatch) -> None:
    import agent.console_extractor as console_extractor

    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    console_extractor.clear_vm_post_boot_cache()

    fake_vendors = types.ModuleType("agent.vendors")
    fake_vendors.get_vendor_config = lambda kind: SimpleNamespace(post_boot_commands=[])
    fake_vendors.get_config_extraction_settings = lambda kind: SimpleNamespace(
        user="",
        password="",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
    )

    monkeypatch.setitem(sys.modules, "agent.vendors", fake_vendors)

    result = console_extractor.run_vm_post_boot_commands("vm1", "kind1")
    assert result.success is True
    assert result.commands_run == 0

    # Idempotent on second call
    second = console_extractor.run_vm_post_boot_commands("vm1", "kind1")
    assert second.success is True
    assert second.commands_run == 0
