"""Round 12 tests for CarrierMonitor: event-driven carrier changes, callback
dispatch, and error recovery.

Covers gaps not addressed by test_carrier_monitor.py:
- Notifier callback receives correct arguments (lab_id, node, iface, on/off)
- Notifier exception does not crash the poll loop
- OVS query failure returns empty and poll loop continues
- Newly managed port appearing mid-lifecycle is recorded without notification
- Stale ports pruned when managed set shrinks between polls
- Rapid up->down->up transitions in consecutive polls
- start() is idempotent (second call is a no-op)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.network.carrier_monitor import CarrierMonitor, MonitoredPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ports(*tuples):
    """Build ``{port_name: MonitoredPort}`` from (port, container, iface, lab) tuples."""
    return {
        t[0]: MonitoredPort(port_name=t[0], container_name=t[1],
                            interface_name=t[2], lab_id=t[3])
        for t in tuples
    }


def _ovs_json(*rows):
    """Encode OVS ``list Interface`` JSON with (name, link_state) rows."""
    return json.dumps({"data": list(rows)}).encode()


def _mock_proc(stdout: bytes, rc: int = 0):
    """Return an AsyncMock mimicking ``asyncio.create_subprocess_exec`` result."""
    proc = AsyncMock()
    proc.returncode = rc
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCallbackDispatchArguments:
    """Verify the notifier callback receives the correct carrier event args."""

    @pytest.mark.asyncio
    async def test_notifier_receives_correct_args_on_carrier_off(self):
        """When a port goes down the notifier is called with carrier='off'
        and the correct lab_id / node / interface."""
        ports = _make_ports(
            ("vhAA11e1ab", "archetype-lab42-router1", "eth1", "lab42"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed: port up
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhAA11e1ab", "up"]))):
            await mon._seed_initial_state()

        # Poll: port goes down -- capture the coroutine passed to create_task
        captured_coro = None

        def capture_task(coro):
            nonlocal captured_coro
            captured_coro = coro
            return AsyncMock()  # fake Task

        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhAA11e1ab", "down"]))):
            with patch("asyncio.create_task", side_effect=capture_task):
                await mon._poll_once()

        # Await the captured coroutine so the notifier is actually invoked
        assert captured_coro is not None
        await captured_coro

        notifier.assert_awaited_once_with("lab42", "router1", "eth1", "off")

    @pytest.mark.asyncio
    async def test_notifier_receives_correct_args_on_carrier_on(self):
        """When a port comes back up the notifier is called with carrier='on'."""
        ports = _make_ports(
            ("vhBB22e2cd", "archetype-labXY-sw1", "eth2", "labXY"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed: port down
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhBB22e2cd", "down"]))):
            await mon._seed_initial_state()

        # Poll: port comes up
        captured_coro = None

        def capture_task(coro):
            nonlocal captured_coro
            captured_coro = coro
            return AsyncMock()

        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhBB22e2cd", "up"]))):
            with patch("asyncio.create_task", side_effect=capture_task):
                await mon._poll_once()

        assert captured_coro is not None
        await captured_coro

        notifier.assert_awaited_once_with("labXY", "sw1", "eth2", "on")


class TestCallbackErrorRecovery:
    """Notifier exceptions must not crash the poll loop."""

    @pytest.mark.asyncio
    async def test_create_task_exception_is_swallowed(self):
        """If asyncio.create_task itself raises, _poll_once still completes
        and state tracking is updated (the transition is recorded)."""
        ports = _make_ports(
            ("vhERR1e1ab", "archetype-lab1-err1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed: up
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhERR1e1ab", "up"]))):
            await mon._seed_initial_state()

        # Poll: down, but create_task blows up
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhERR1e1ab", "down"]))):
            with patch("asyncio.create_task", side_effect=RuntimeError("boom")):
                # Should NOT raise -- the exception is caught internally
                await mon._poll_once()

        # State still updated even though notification failed
        assert mon._last_link_states["vhERR1e1ab"] == "down"

    @pytest.mark.asyncio
    async def test_poll_loop_survives_ovs_exception(self):
        """If _query_ovs_link_states raises, _poll_loop logs and continues."""
        ports = _make_ports(
            ("vhOK01e1ab", "archetype-lab1-ok1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        call_count = 0

        async def flaky_poll():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("OVS socket gone")
            # Second call: cancel to exit the loop
            raise asyncio.CancelledError()

        mon._poll_once = flaky_poll  # type: ignore[assignment]

        # _poll_loop should survive the first OSError and exit on CancelledError
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await mon._poll_loop(interval=0.01)

        assert call_count == 2  # survived first error, hit second


class TestOVSQueryFailure:
    """OVS subprocess failures are handled gracefully."""

    @pytest.mark.asyncio
    async def test_ovs_nonzero_returncode_returns_empty(self):
        """When ovs-vsctl exits non-zero, _query_ovs_link_states returns {}."""
        mon = CarrierMonitor("arch-ovs", lambda: {}, AsyncMock())

        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(b"", rc=1)):
            result = await mon._query_ovs_link_states()

        assert result == {}

    @pytest.mark.asyncio
    async def test_ovs_empty_stdout_returns_empty(self):
        """When ovs-vsctl produces no output, returns {}."""
        mon = CarrierMonitor("arch-ovs", lambda: {}, AsyncMock())

        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(b"", rc=0)):
            result = await mon._query_ovs_link_states()

        assert result == {}

    @pytest.mark.asyncio
    async def test_ovs_invalid_json_returns_empty(self):
        """Malformed JSON from OVS does not raise -- returns {}."""
        mon = CarrierMonitor("arch-ovs", lambda: {}, AsyncMock())

        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(b"not json at all", rc=0)):
            result = await mon._query_ovs_link_states()

        assert result == {}


class TestNewPortAppearsAtRuntime:
    """A port added to managed set between polls is picked up without
    firing a spurious transition notification."""

    @pytest.mark.asyncio
    async def test_new_port_recorded_without_notification(self):
        """First time seeing a managed port records state, does not notify."""
        # Start with one port
        ports = _make_ports(
            ("vhOLD1e1ab", "archetype-lab1-old1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhOLD1e1ab", "up"]))):
            await mon._seed_initial_state()

        # Add a new port to the managed set
        ports["vhNEW1e1ab"] = MonitoredPort(
            port_name="vhNEW1e1ab",
            container_name="archetype-lab1-new1",
            interface_name="eth1",
            lab_id="lab1",
        )

        # Poll: OVS now shows both ports
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(
                        ["vhOLD1e1ab", "up"],
                        ["vhNEW1e1ab", "down"],
                    ))):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        # New port is recorded...
        assert mon._last_link_states["vhNEW1e1ab"] == "down"
        # ...but no notification (first observation, not a transition)
        mock_task.assert_not_called()


class TestStalePorts:
    """Ports removed from managed set are pruned from tracking."""

    @pytest.mark.asyncio
    async def test_stale_port_pruned_on_poll(self):
        """When a port leaves the managed set, its tracking entry is removed."""
        mutable_ports = _make_ports(
            ("vhGONEe1ab", "archetype-lab1-gone", "eth1", "lab1"),
            ("vhSTAYe1ab", "archetype-lab1-stay", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: mutable_ports, notifier)

        # Seed both
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(
                        ["vhGONEe1ab", "up"],
                        ["vhSTAYe1ab", "up"],
                    ))):
            await mon._seed_initial_state()

        assert "vhGONEe1ab" in mon._last_link_states

        # Remove one port from managed set (simulating container removal)
        del mutable_ports["vhGONEe1ab"]

        # Poll: OVS still knows about both, but managed set only has one
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(
                        ["vhGONEe1ab", "up"],
                        ["vhSTAYe1ab", "up"],
                    ))):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        # Stale port pruned
        assert "vhGONEe1ab" not in mon._last_link_states
        assert "vhSTAYe1ab" in mon._last_link_states
        mock_task.assert_not_called()


class TestRapidTransitions:
    """Consecutive up->down->up transitions each fire a notification."""

    @pytest.mark.asyncio
    async def test_up_down_up_fires_two_notifications(self):
        """Two consecutive state changes produce two separate notifications."""
        ports = _make_ports(
            ("vhFLAPe1ab", "archetype-lab1-flap", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed: up
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhFLAPe1ab", "up"]))):
            await mon._seed_initial_state()

        task_calls = []

        def track_task(coro):
            task_calls.append(coro)
            return AsyncMock()

        # Poll 1: goes down
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhFLAPe1ab", "down"]))):
            with patch("asyncio.create_task", side_effect=track_task):
                await mon._poll_once()

        assert len(task_calls) == 1
        assert mon._last_link_states["vhFLAPe1ab"] == "down"

        # Poll 2: comes back up
        with patch("asyncio.create_subprocess_exec",
                    return_value=_mock_proc(_ovs_json(["vhFLAPe1ab", "up"]))):
            with patch("asyncio.create_task", side_effect=track_task):
                await mon._poll_once()

        assert len(task_calls) == 2
        assert mon._last_link_states["vhFLAPe1ab"] == "up"

        # Verify the actual carrier values passed
        # First transition: up -> down = "off"
        await task_calls[0]
        # Second transition: down -> up = "on"
        await task_calls[1]

        assert notifier.await_args_list[0].args == ("lab1", "flap", "eth1", "off")
        assert notifier.await_args_list[1].args == ("lab1", "flap", "eth1", "on")


class TestStartIdempotency:
    """Calling start() twice should not create a second background task."""

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """Second start() call is a no-op when already running."""
        ports = _make_ports()
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Patch _seed_initial_state and create_task to avoid real async work
        mon._seed_initial_state = AsyncMock()  # type: ignore[method-assign]

        fake_task = AsyncMock(spec=asyncio.Task)
        with patch("asyncio.create_task", return_value=fake_task) as ct:
            await mon.start(interval=5.0)
            first_task = mon._task
            assert first_task is not None

            await mon.start(interval=5.0)
            second_task = mon._task

        # Same task, create_task called only once
        assert first_task is second_task
        assert ct.call_count == 1
