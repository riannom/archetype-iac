from __future__ import annotations

import pytest

from agent.network import cmd as network_cmd


@pytest.mark.asyncio
async def test_run_cmd_executes_and_decodes(monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeProcess:
        returncode = 7

        async def communicate(self):
            return (b"stdout-text", b"stderr-text")

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        network_cmd.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    rc, stdout, stderr = await network_cmd.run_cmd(["ip", "link", "show"])

    assert rc == 7
    assert stdout == "stdout-text"
    assert stderr == "stderr-text"
    assert calls[0][0] == ("ip", "link", "show")
    assert calls[0][1]["stdout"] == network_cmd.asyncio.subprocess.PIPE
    assert calls[0][1]["stderr"] == network_cmd.asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_run_cmd_defaults_none_returncode_to_zero(monkeypatch) -> None:
    class FakeProcess:
        returncode = None

        async def communicate(self):
            return (b"", b"")

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(
        network_cmd.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    rc, stdout, stderr = await network_cmd.run_cmd(["true"])

    assert rc == 0
    assert stdout == ""
    assert stderr == ""


@pytest.mark.asyncio
async def test_ovs_vsctl_delegates_to_run_cmd(monkeypatch) -> None:
    called = {}

    async def fake_run_cmd(cmd):
        called["cmd"] = cmd
        return (0, "ok", "")

    monkeypatch.setattr(network_cmd, "run_cmd", fake_run_cmd)

    rc, stdout, stderr = await network_cmd.ovs_vsctl("show")

    assert called["cmd"] == ["ovs-vsctl", "show"]
    assert (rc, stdout, stderr) == (0, "ok", "")


@pytest.mark.asyncio
async def test_ip_link_exists_maps_return_code(monkeypatch) -> None:
    async def fake_run_cmd_ok(_cmd):
        return (0, "", "")

    async def fake_run_cmd_bad(_cmd):
        return (1, "", "")

    monkeypatch.setattr(network_cmd, "run_cmd", fake_run_cmd_ok)
    assert await network_cmd.ip_link_exists("eth0") is True

    monkeypatch.setattr(network_cmd, "run_cmd", fake_run_cmd_bad)
    assert await network_cmd.ip_link_exists("eth0") is False
