from __future__ import annotations

import types

import pytest

from agent import console_session_registry as session_registry


def test_injector_send_and_sendline(monkeypatch):
    writes = []

    monkeypatch.setattr(session_registry.os, "write", lambda fd, data: writes.append((fd, data)))

    injector = session_registry.PtyInjector(fd=1)
    injector.send("abc")
    injector.sendline("xyz")

    assert writes == [(1, b"abc"), (1, b"xyz\r")]


def test_injector_expect_from_buffer():
    injector = session_registry.PtyInjector(fd=1)
    injector._buffer = b"hello>rest"

    before = injector.expect(r">", timeout=1)
    assert before == "hello"
    assert injector._buffer == b"rest"


def test_injector_expect_reads_and_forwards(monkeypatch):
    forwarded = []

    def ws_forward(data: bytes):
        forwarded.append(data)

    monkeypatch.setattr(session_registry.select, "select", lambda *_args, **_kwargs: ([1], [], []))
    monkeypatch.setattr(session_registry.os, "read", lambda *_args, **_kwargs: b"abc>def")
    monkeypatch.setattr(session_registry, "_monotonic", lambda: 0.0)

    injector = session_registry.PtyInjector(fd=1, ws_forward=ws_forward)
    before = injector.expect(r">", timeout=1)

    assert before == "abc"
    assert injector._buffer == b"def"
    assert forwarded == [b"abc>def"]


def test_injector_expect_timeout(monkeypatch):
    times = iter([0.0, 2.0])
    monkeypatch.setattr(session_registry, "_monotonic", lambda: next(times))
    monkeypatch.setattr(session_registry.select, "select", lambda *_args, **_kwargs: ([], [], []))

    injector = session_registry.PtyInjector(fd=1)
    with pytest.raises(TimeoutError):
        injector.expect(r">", timeout=1)


def test_injector_expect_closed_fd(monkeypatch):
    monkeypatch.setattr(session_registry.select, "select", lambda *_args, **_kwargs: ([1], [], []))
    monkeypatch.setattr(session_registry.os, "read", lambda *_args, **_kwargs: b"")
    monkeypatch.setattr(session_registry, "_monotonic", lambda: 0.0)

    injector = session_registry.PtyInjector(fd=1)
    with pytest.raises(OSError):
        injector.expect(r">", timeout=1)


def test_injector_drain_collects(monkeypatch):
    forwarded = []

    def ws_forward(data: bytes):
        forwarded.append(data)

    times = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(session_registry, "_monotonic", lambda: next(times, 0.2))
    monkeypatch.setattr(session_registry.select, "select", lambda *_args, **_kwargs: ([1], [], []))
    monkeypatch.setattr(session_registry.os, "read", lambda *_args, **_kwargs: b"chunk")

    injector = session_registry.PtyInjector(fd=1, ws_forward=ws_forward)
    data = injector.drain(duration=0.1)

    assert data == b"chunk"
    assert forwarded == [b"chunk"]


