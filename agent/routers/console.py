"""Console access endpoints for Docker, SSH, and libvirt nodes."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.config import settings
from agent.console.docker_exec import DockerConsole
from agent.console.ssh_console import SSHConsole
from agent.docker_client import get_docker_client
from agent.providers import get_provider
from agent.providers.naming import libvirt_domain_name
from agent.vendors import get_console_credentials, get_console_method, get_console_shell

logger = logging.getLogger(__name__)
router = APIRouter(tags=["console"])

# Per-port asyncio locks for TCP serial console sessions.
# Prevents concurrent sessions from racing on chardev reset.
_tcp_console_locks: dict[int, asyncio.Lock] = {}


# --- Helper functions ---


async def _get_console_config(container_name: str) -> tuple[str, str, str, str]:
    """Get console configuration based on container's node kind.

    Returns:
        Tuple of (method, shell, username, password)
        method: "docker_exec" or "ssh"
        shell: Shell command for docker_exec
        username/password: Credentials for SSH
    """
    def _sync_get_config() -> tuple[str, str, str, str]:
        try:
            client = get_docker_client()
            container = client.containers.get(container_name)
            kind = container.labels.get("archetype.node_kind", "")
            method = get_console_method(kind)
            shell = get_console_shell(kind)
            username, password = get_console_credentials(kind)
            return (method, shell, username, password)
        except Exception:
            return ("docker_exec", "/bin/sh", "admin", "admin")

    return await asyncio.to_thread(_sync_get_config)


async def _get_container_ip(container_name: str) -> str | None:
    """Get the container's IP address for SSH access."""
    def _sync_get_ip() -> str | None:
        try:
            client = get_docker_client()
            container = client.containers.get(container_name)
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_name, net_config in networks.items():
                ip = net_config.get("IPAddress")
                if ip:
                    return ip
            return None
        except Exception:
            return None

    return await asyncio.to_thread(_sync_get_ip)


async def _get_container_boot_logs(container_name: str, tail_lines: int = 50) -> str | None:
    """Get recent boot logs from a container.

    Args:
        container_name: Name of the container
        tail_lines: Number of log lines to retrieve (default 50)

    Returns:
        Log output as string, or None if unavailable
    """
    def _sync_get_logs() -> str | None:
        try:
            client = get_docker_client()
            container = client.containers.get(container_name)
            logs = container.logs(tail=tail_lines, timestamps=False).decode("utf-8", errors="replace")
            return logs if logs.strip() else None
        except Exception:
            return None

    return await asyncio.to_thread(_sync_get_logs)


async def _check_container_exists(container_name: str) -> bool:
    """Check if a Docker container exists."""
    def _sync_check() -> bool:
        try:
            client = get_docker_client()
            client.containers.get(container_name)
            return True
        except Exception:
            return False

    return await asyncio.to_thread(_sync_check)


# --- WebSocket endpoint ---


@router.websocket("/console/{lab_id}/{node_name}")
async def console_websocket(
    websocket: WebSocket,
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
):
    """WebSocket endpoint for console access to a node.

    Args:
        lab_id: Lab identifier
        node_name: Node name within the lab
        provider_type: Optional provider type ("docker" or "libvirt").
                       If not specified, tries Docker first, then libvirt.
    """
    await websocket.accept()

    # If libvirt explicitly requested, use virsh console
    if provider_type == "libvirt":
        await _console_websocket_libvirt(websocket, lab_id, node_name)
        return

    # Try Docker first
    docker_provider = get_provider("docker")
    if docker_provider is not None:
        container_name = docker_provider.get_container_name(lab_id, node_name)

        # Check if Docker container exists
        container_exists = await _check_container_exists(container_name)
        if container_exists:
            try:
                # Get console configuration based on node kind
                method, shell_cmd, username, password = await _get_console_config(container_name)

                if method == "ssh":
                    # SSH-based console for vrnetlab/VM containers
                    # Falls back to docker exec if SSH fails (e.g. device still booting)
                    ssh_ok = await _console_websocket_ssh(
                        websocket, container_name, node_name, username, password
                    )
                    if not ssh_ok:
                        await _console_websocket_docker(
                            websocket, container_name, node_name, shell_cmd
                        )
                else:
                    # Docker exec-based console for native containers
                    await _console_websocket_docker(websocket, container_name, node_name, shell_cmd)
            except Exception as e:
                logger.error(f"Console error for {node_name}: {e}")
                await websocket.send_text("\r\nError: Console connection failed\r\n")
                await websocket.close(code=1011)
            return

    # Docker container not found, try libvirt if no specific provider requested
    if provider_type is None:
        libvirt_provider = get_provider("libvirt")
        if libvirt_provider is not None:
            await _console_websocket_libvirt(websocket, lab_id, node_name)
            return

    # No console available
    await websocket.send_text("\r\nError: Node not found (neither Docker nor libvirt)\r\n")
    await websocket.close(code=1011)


# --- Console handlers ---


async def _console_websocket_ssh(
    websocket: WebSocket,
    container_name: str,
    node_name: str,
    username: str,
    password: str,
) -> bool:
    """Handle console via SSH to container IP (for vrnetlab containers).

    Returns True if SSH session was established (even if it later ended),
    False if SSH connection failed (caller should fall back to docker exec).
    """

    # Send boot logs before connecting to CLI
    boot_logs = await _get_container_boot_logs(container_name)
    if boot_logs:
        await websocket.send_text("\r\n\x1b[90m--- Boot Log ---\x1b[0m\r\n")
        for line in boot_logs.splitlines():
            await websocket.send_text(f"\x1b[90m{line}\x1b[0m\r\n")
        await websocket.send_text("\x1b[90m--- Connecting to CLI ---\x1b[0m\r\n\r\n")

    # Get container IP
    container_ip = await _get_container_ip(container_name)
    if not container_ip:
        await websocket.send_text(f"\r\n\x1b[33mSSH unavailable for {node_name}, falling back to shell...\x1b[0m\r\n")
        return False

    console = SSHConsole(container_ip, username, password)

    # Try to start SSH console session
    if not await console.start():
        await websocket.send_text(f"\r\n\x1b[33mSSH unavailable for {node_name}, falling back to shell...\x1b[0m\r\n")
        return False

    # Set initial terminal size
    await console.resize(rows=24, cols=80)

    # Input buffer for data from WebSocket
    input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def read_websocket():
        """Read from WebSocket and queue input."""
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    await input_queue.put(None)
                    break
                elif message["type"] == "websocket.receive":
                    if "text" in message:
                        text = message["text"]
                        # Check for control messages (JSON)
                        if text.startswith("{"):
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "resize":
                                    rows = ctrl.get("rows", 24)
                                    cols = ctrl.get("cols", 80)
                                    await console.resize(rows=rows, cols=cols)
                                    continue  # Don't queue resize messages
                            except json.JSONDecodeError:
                                pass  # Not JSON, treat as terminal input
                        await input_queue.put(text.encode())
                    elif "bytes" in message:
                        await input_queue.put(message["bytes"])
        except WebSocketDisconnect:
            await input_queue.put(None)
        except Exception:
            await input_queue.put(None)

    async def read_ssh():
        """Read from SSH and send to WebSocket."""
        try:
            while console.is_running:
                data = await console.read()
                if data is None:
                    break
                if data:
                    await websocket.send_bytes(data)
        except Exception:
            pass

    async def write_ssh():
        """Read from input queue and write to SSH."""
        try:
            while console.is_running:
                try:
                    data = await asyncio.wait_for(
                        input_queue.get(), timeout=settings.console_input_timeout
                    )
                    if data is None:
                        break
                    if data:
                        await console.write(data)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

    # Run all tasks concurrently
    ws_task = asyncio.create_task(read_websocket())
    read_task = asyncio.create_task(read_ssh())
    write_task = asyncio.create_task(write_ssh())

    try:
        done, pending = await asyncio.wait(
            [ws_task, read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        await console.close()
        try:
            await websocket.close()
        except Exception:
            pass

    return True


async def _console_websocket_docker(
    websocket: WebSocket, container_name: str, node_name: str, shell_cmd: str
):
    """Handle console via docker exec (for native containers)."""

    # Send boot logs before connecting to CLI
    boot_logs = await _get_container_boot_logs(container_name)
    if boot_logs:
        await websocket.send_text("\r\n\x1b[90m--- Boot Log ---\x1b[0m\r\n")
        for line in boot_logs.splitlines():
            await websocket.send_text(f"\x1b[90m{line}\x1b[0m\r\n")
        await websocket.send_text("\x1b[90m--- Connecting to CLI ---\x1b[0m\r\n\r\n")

    console = DockerConsole(container_name)

    # Try to start console session with appropriate shell (using async version)
    if not await console.start_async(shell=shell_cmd):
        logger.error(f"Console connect failed: container '{container_name}' may not be running")
        await websocket.send_text(f"\r\nError: Could not connect to {node_name}. Node may not be running.\r\n")
        await websocket.close(code=1011)
        return

    # Set initial terminal size (resize is fast, no need to wrap)
    console.resize(rows=24, cols=80)

    # Input buffer for data from WebSocket
    input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def read_websocket():
        """Read from WebSocket and queue input."""
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    await input_queue.put(None)
                    break
                elif message["type"] == "websocket.receive":
                    if "text" in message:
                        text = message["text"]
                        # Check for control messages (JSON)
                        if text.startswith("{"):
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "resize":
                                    rows = ctrl.get("rows", 24)
                                    cols = ctrl.get("cols", 80)
                                    console.resize(rows=rows, cols=cols)
                                    continue  # Don't queue resize messages
                            except json.JSONDecodeError:
                                pass  # Not JSON, treat as terminal input
                        await input_queue.put(text.encode())
                    elif "bytes" in message:
                        await input_queue.put(message["bytes"])
        except WebSocketDisconnect:
            await input_queue.put(None)
        except Exception:
            await input_queue.put(None)

    async def read_container():
        """Read from container and send to WebSocket using event-driven I/O."""
        loop = asyncio.get_event_loop()
        data_available = asyncio.Event()

        def on_readable():
            data_available.set()

        fd = console.get_socket_fileno()
        if fd is None:
            return

        try:
            loop.add_reader(fd, on_readable)

            while console.is_running:
                try:
                    await asyncio.wait_for(
                        data_available.wait(), timeout=settings.console_read_timeout
                    )
                except asyncio.TimeoutError:
                    continue

                data_available.clear()

                data = console.read_nonblocking()
                if data is None:
                    break
                if data:
                    await websocket.send_bytes(data)

        except Exception:
            pass
        finally:
            try:
                loop.remove_reader(fd)
            except Exception:
                pass

    async def write_container():
        """Read from input queue and write to container."""
        try:
            while console.is_running:
                try:
                    data = await asyncio.wait_for(
                        input_queue.get(), timeout=settings.console_input_timeout
                    )
                    if data is None:
                        break
                    if data:
                        console.write(data)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

    # Run all tasks concurrently
    ws_task = asyncio.create_task(read_websocket())
    read_task = asyncio.create_task(read_container())
    write_task = asyncio.create_task(write_container())

    try:
        done, pending = await asyncio.wait(
            [ws_task, read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        console.close()
        try:
            await websocket.close()
        except Exception:
            pass


async def _reset_tcp_chardev(domain_name: str, tcp_port: int) -> None:
    """Reset QEMU TCP serial chardev to clear stale connections.

    QEMU's TCP telnet chardev in server mode only accepts one client at a
    time.  When a previous console session disconnects uncleanly, the chardev
    stays bound to the dead socket (CLOSE-WAIT) and refuses new connections.

    Fix: cycle the chardev through null backend and back to TCP, which forces
    QEMU to drop the stale socket and listen for new connections.
    """

    def _do_reset() -> None:
        import re
        import time

        uri = "qemu:///system"
        # Kill any lingering TCP telnet console processes on this port
        try:
            result = subprocess.run(
                ["ss", "-tnp", f"dst 127.0.0.1:{tcp_port}"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "python3" in line:
                    m = re.search(r"pid=(\d+)", line)
                    if m:
                        pid = int(m.group(1))
                        try:
                            subprocess.run(["kill", str(pid)], timeout=2)
                        except Exception:
                            pass
        except Exception:
            pass

        # Check if chardev has a stale connection via QEMU monitor
        try:
            result = subprocess.run(
                ["virsh", "-c", uri, "qemu-monitor-command", domain_name,
                 "--hmp", "info chardev"],
                capture_output=True, text=True, timeout=5,
            )
            # Look for charserial0 with a client connection (indicates <->)
            for line in result.stdout.splitlines():
                if "charserial0" in line and "<->" in line:
                    logger.info(
                        "Resetting stale TCP chardev for %s (port %d): %s",
                        domain_name, tcp_port, line.strip(),
                    )
                    # Cycle through null to release the port
                    subprocess.run(
                        ["virsh", "-c", uri, "qemu-monitor-command", domain_name,
                         "--hmp", "chardev-change charserial0 null"],
                        capture_output=True, text=True, timeout=5,
                    )
                    time.sleep(0.5)
                    # Restore TCP telnet server
                    subprocess.run(
                        ["virsh", "-c", uri, "qemu-monitor-command", domain_name,
                         "--hmp",
                         f"chardev-change charserial0 socket,host=127.0.0.1,"
                         f"port={tcp_port},server=on,telnet=on,wait=off"],
                        capture_output=True, text=True, timeout=5,
                    )
                    logger.info(
                        "TCP chardev reset complete for %s (port %d)",
                        domain_name, tcp_port,
                    )
                    break
        except Exception as e:
            logger.warning("Failed to reset TCP chardev for %s: %s", domain_name, e)

    await asyncio.to_thread(_do_reset)


async def _console_websocket_libvirt(
    websocket: WebSocket,
    lab_id: str,
    node_name: str,
):
    """Handle console via virsh console (for libvirt VMs)."""
    import pty
    import os
    import termios
    import struct
    import fcntl

    libvirt_provider = get_provider("libvirt")
    if libvirt_provider is None:
        await websocket.send_text("\r\nError: Libvirt provider not available\r\n")
        await websocket.close(code=1011)
        return

    # Get the virsh console command
    console_cmd = await libvirt_provider.get_console_command(
        lab_id, node_name, Path(settings.workspace_path) / lab_id
    )

    if not console_cmd:
        await websocket.send_text(f"\r\nError: VM {node_name} not found or not running\r\n")
        await websocket.close(code=1011)
        return

    # Determine if this is a virsh console (vs SSH) and get domain name for locking
    _is_virsh = "virsh" in console_cmd and "console" in console_cmd
    _virsh_domain = console_cmd[-1] if _is_virsh else None
    _lock_ctx = None

    # Detect TCP telnet console and extract port
    _is_tcp_telnet = (
        len(console_cmd) >= 4
        and console_cmd[0] == "python3"
        and console_cmd[1] == "-c"
    )
    _tcp_port = int(console_cmd[-1]) if _is_tcp_telnet else None

    if _virsh_domain:
        from agent.virsh_console_lock import console_lock
        try:
            _lock_ctx = console_lock(_virsh_domain, timeout=10, kill_orphans=True)
            await asyncio.to_thread(_lock_ctx.__enter__)
        except TimeoutError:
            await websocket.send_text(
                "\r\nError: Another session is using this console. "
                "Please try again shortly.\r\n"
            )
            await websocket.close(code=1011)
            return

    # For TCP telnet consoles, acquire per-port lock and reset chardev
    _tcp_lock = None
    if _is_tcp_telnet and _tcp_port:
        if _tcp_port not in _tcp_console_locks:
            _tcp_console_locks[_tcp_port] = asyncio.Lock()
        _tcp_lock = _tcp_console_locks[_tcp_port]
        try:
            await asyncio.wait_for(_tcp_lock.acquire(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.send_text(
                "\r\nError: Another session is using this console. "
                "Please try again shortly.\r\n"
            )
            await websocket.close(code=1011)
            return
        # Reset stale chardev while holding lock
        domain_name = libvirt_domain_name(lab_id, node_name)
        await _reset_tcp_chardev(domain_name, _tcp_port)

    await websocket.send_text("\r\n\x1b[90m--- Connecting to VM console ---\x1b[0m\r\n")
    await websocket.send_text("\x1b[90mPress Ctrl+] to disconnect\x1b[0m\r\n\r\n")

    # Create pseudo-terminal for virsh console
    master_fd, slave_fd = pty.openpty()

    # Set non-blocking on master
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    process = None
    _active_session = None
    try:
        # Start virsh console process with a controlling TTY
        process = await asyncio.create_subprocess_exec(
            *console_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,  # Create new session with PTY as controlling terminal
        )

        # Close slave_fd in parent process
        os.close(slave_fd)
        slave_fd = None

        # Brief delay to let virsh connect
        await asyncio.sleep(0.5)

        # Register session for piggyback config extraction
        if _virsh_domain:
            from agent.console_session_registry import (
                ActiveConsoleSession, register_session, unregister_session,
            )
            _active_session = ActiveConsoleSession(
                domain_name=_virsh_domain,
                master_fd=master_fd,
                loop=asyncio.get_event_loop(),
                websocket=websocket,
            )
            register_session(_virsh_domain, _active_session)

        # Check if process exited immediately (indicates error)
        if process.returncode is not None:
            # Try to read any error output
            try:
                error_data = os.read(master_fd, 4096)
                if error_data:
                    logger.error(f"Console process stderr: {error_data.decode('utf-8', errors='replace')}")
            except Exception:
                pass
            _label = "virsh console" if _is_virsh else "console"
            logger.error(f"Console process exited: {_label} code={process.returncode}, cmd={' '.join(console_cmd)}")
            await websocket.send_text("\r\nError: Console process exited unexpectedly\r\n")
            await websocket.close(code=1011)
            return

        input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def read_websocket():
            """Read from WebSocket and queue input."""
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        await input_queue.put(None)
                        break
                    elif message["type"] == "websocket.receive":
                        if "text" in message:
                            text = message["text"]
                            # Check for control messages (JSON)
                            if text.startswith("{"):
                                try:
                                    ctrl = json.loads(text)
                                    if ctrl.get("type") == "resize":
                                        rows = ctrl.get("rows", 24)
                                        cols = ctrl.get("cols", 80)
                                        # Resize PTY
                                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                                        continue
                                except json.JSONDecodeError:
                                    pass
                            await input_queue.put(text.encode())
                        elif "bytes" in message:
                            await input_queue.put(message["bytes"])
            except WebSocketDisconnect:
                await input_queue.put(None)
            except Exception:
                await input_queue.put(None)

        async def read_pty():
            """Read from PTY and send to WebSocket."""
            loop = asyncio.get_event_loop()
            data_available = asyncio.Event()

            def on_readable():
                data_available.set()

            try:
                loop.add_reader(master_fd, on_readable)

                while process.returncode is None:
                    # Pause gate: injector owns reads during extraction
                    if _active_session and not _active_session.pty_read_paused.is_set():
                        await asyncio.sleep(0.1)
                        data_available.clear()
                        continue

                    try:
                        await asyncio.wait_for(data_available.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    data_available.clear()

                    # Re-check pause after waking (extraction may have started)
                    if _active_session and not _active_session.pty_read_paused.is_set():
                        continue

                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        await websocket.send_bytes(data)
                    except (BlockingIOError, OSError):
                        continue

            except Exception:
                pass
            finally:
                try:
                    loop.remove_reader(master_fd)
                except Exception:
                    pass

        async def write_pty():
            """Read from input queue and write to PTY."""
            try:
                while process.returncode is None:
                    try:
                        data = await asyncio.wait_for(input_queue.get(), timeout=1.0)
                        if data is None:
                            break
                        # Pause gate: drop keystrokes during extraction
                        if _active_session and not _active_session.input_paused.is_set():
                            continue
                        if data:
                            os.write(master_fd, data)
                    except asyncio.TimeoutError:
                        continue
            except Exception:
                pass

        # Run all tasks concurrently
        ws_task = asyncio.create_task(read_websocket())
        read_task = asyncio.create_task(read_pty())
        write_task = asyncio.create_task(write_pty())

        try:
            done, pending = await asyncio.wait(
                [ws_task, read_task, write_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Send disconnect message with reason
            if process.returncode is not None:
                _label = "virsh console" if _is_virsh else "console"
                await websocket.send_text(
                    f"\r\n\x1b[90m[{_label} exited with code {process.returncode}]\x1b[0m\r\n"
                )
            else:
                await websocket.send_text("\r\n\x1b[90m[console disconnected]\x1b[0m\r\n")
        except Exception:
            pass

    finally:
        # Wait for any in-progress piggyback extraction to finish,
        # then unregister the session before closing the fd
        if _active_session is not None:
            if _active_session._lock.acquire(timeout=5):
                try:
                    from agent.console_session_registry import unregister_session
                    unregister_session(_virsh_domain)
                finally:
                    _active_session._lock.release()
            else:
                # Timed out waiting -- unregister anyway
                from agent.console_session_registry import unregister_session
                unregister_session(_virsh_domain)

        # Cleanup
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

        try:
            os.close(master_fd)
        except Exception:
            pass

        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except Exception:
                pass

        # Release console lock if held
        if _lock_ctx is not None:
            try:
                _lock_ctx.__exit__(None, None, None)
            except Exception:
                pass

        # Release TCP serial port lock if held
        if _tcp_lock is not None and _tcp_lock.locked():
            _tcp_lock.release()

        try:
            await websocket.close()
        except Exception:
            pass
