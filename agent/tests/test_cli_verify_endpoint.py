"""Tests for the libvirt CLI verification endpoint."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.main import verify_node_cli
from agent.schemas import CliVerifyRequest


def _run(coro):
    return asyncio.run(coro)


def _make_libvirt_provider(kind: str = "cisco_n9kv"):
    return SimpleNamespace(
        _uri="qemu:///system",
        get_runtime_profile=lambda _lab, _node: {
            "domain_name": "arch-lab1-node1",
            "runtime": {"kind": kind},
        },
        get_node_kind=lambda _lab, _node: kind,
    )


def test_cli_verify_uses_default_n9kv_commands() -> None:
    provider = _make_libvirt_provider("cisco_n9kv")
    captured: dict[str, object] = {}

    def _fake_run(*, domain_name, kind, commands, libvirt_uri, timeout, retries):
        captured["domain_name"] = domain_name
        captured["kind"] = kind
        captured["commands"] = list(commands)
        captured["libvirt_uri"] = libvirt_uri
        captured["timeout"] = timeout
        captured["retries"] = retries
        outputs = [
            SimpleNamespace(command=cmd, success=True, output=f"ok:{cmd}", error="")
            for cmd in commands
        ]
        return SimpleNamespace(success=True, commands_run=len(outputs), outputs=outputs, error="")

    async def _fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.console_extractor.run_vm_cli_commands", side_effect=_fake_run):
            with patch("agent.main.asyncio.to_thread", new=AsyncMock(side_effect=_fake_to_thread)):
                res = _run(
                    verify_node_cli("lab1", "node1", CliVerifyRequest(), provider="libvirt")
                )

    assert res.success is True
    assert res.domain_name == "arch-lab1-node1"
    assert res.commands_run == 4
    assert captured["kind"] == "cisco_n9kv"
    assert captured["libvirt_uri"] == "qemu:///system"
    assert captured["timeout"] is None
    assert captured["retries"] == 2
    assert captured["commands"] == [
        "show running-config | include system no poap",
        "show startup-config | include system no poap",
        "show startup-config | include hostname",
        "show boot | include POAP",
    ]


def test_cli_verify_rejects_non_libvirt_provider() -> None:
    with pytest.raises(HTTPException) as exc:
        _run(verify_node_cli("lab1", "node1", CliVerifyRequest(commands=["show clock"]), provider="docker"))
    assert exc.value.status_code == 400


def test_cli_verify_requires_kind_when_runtime_missing() -> None:
    provider = SimpleNamespace(
        _uri="qemu:///system",
        get_runtime_profile=lambda _lab, _node: {"domain_name": "arch-lab1-node1", "runtime": {}},
        get_node_kind=lambda _lab, _node: None,
    )

    with patch("agent.main.get_provider", return_value=provider):
        with pytest.raises(HTTPException) as exc:
            _run(verify_node_cli("lab1", "node1", CliVerifyRequest(commands=["show clock"]), provider="libvirt"))

    assert exc.value.status_code == 400

