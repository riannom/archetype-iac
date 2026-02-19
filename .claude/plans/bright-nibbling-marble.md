# Agent Health Check Failure Analysis

## Context

The local agent (FastAPI at `agent/main.py`, port 8001) continually transitions to `unhealthy` status per Docker's health check. Commit `1c65deb` already improved things by adding a lightweight `/healthz` endpoint, moving Docker prune to `asyncio.to_thread`, optimizing OVS startup discovery from O(ports x containers) to O(ports + containers), and adding a 120s `start_period`. But the agent still goes unhealthy.

**Docker health check config** (`docker-compose.gui.yml:237-242`):
- Endpoint: `curl -fsS http://localhost:8001/healthz`
- Interval: 30s, Timeout: 5s, Retries: 3, Start period: 120s
- 3 consecutive failures (5s timeout each) = unhealthy

The `/healthz` endpoint (line 1158) is `async def` returning `{"status": "ok"}` - trivially fast. For it to fail, either the **event loop is blocked** or the **process can't accept connections**.

---

## Root Causes Identified

### 1. CRITICAL: Bare Docker API Calls Blocking the Event Loop

Two async endpoints call `docker.containers.get()` **directly on the event loop** without `asyncio.to_thread()`:

| Location | Endpoint | Call |
|----------|----------|------|
| `main.py:2767` | `POST /overlay/attach` | `provider.docker.containers.get(full_container_name)` |
| `main.py:4181` | `GET /labs/{lab_id}/nodes/{node_name}/linux-interfaces` | `provider.docker.containers.get(container_name)` |

Docker SDK calls are synchronous HTTP calls to the Docker daemon socket. Each one blocks the event loop for **50-500ms** (longer if the daemon is busy). While blocked, **no other async handler can run**, including `/healthz`.

If the controller polls `lab_status` or overlay operations trigger `attach_container` concurrently, the event loop stalls.

### 2. CRITICAL: `_bootstrap_transport_config()` Blocks Event Loop at Startup

`main.py:716-818` - Called during lifespan startup (line 963), this function runs **up to 9 synchronous `subprocess.run()` calls** directly in the async context:

- Line 762: `subprocess.run(["ip", "link", "show", iface_name])`
- Lines 765-784: VLAN creation, MTU setting, IP flush/add, link up (6 calls)
- Lines 804-808: Dedicated interface MTU/IP/up (4 more calls)

Each `subprocess.run()` blocks the event loop for 10-100ms. Combined: **500ms-2s of solid event loop blocking** during startup. If this overlaps with the first health check after `start_period`, it fails.

### 3. HIGH: Heavyweight Heartbeat Resource Gathering

`_sync_get_resource_usage()` (main.py:464-605) runs every heartbeat interval via `asyncio.to_thread()`:

- `psutil.cpu_percent(interval=0.1)` - **blocks thread for 100ms** (intentional sleep for sampling)
- `docker.from_env()` + `client.api.containers(all=True)` - enumerates ALL containers, then individually `client.containers.get()` for each
- `libvirt_provider.conn.listAllDomains(0)` - queries libvirt for all VMs

This doesn't block the event loop directly (it's in a thread), but it **occupies a thread pool thread** for potentially seconds if there are many containers. Under load, this contributes to thread pool pressure.

### 4. HIGH: Docker Event Listener Constant Thread Pool Drain

`docker_events.py:153-155`:
```python
event = await asyncio.to_thread(self._event_queue.get, timeout=1.0)
```

This occupies a thread pool thread **every 1 second** perpetually, even with zero events. The `queue.get(timeout=1.0)` call blocks the thread for up to 1 second, then releases, then immediately reacquires. This means **one thread pool thread is permanently consumed** by the event listener.

### 5. MEDIUM: Periodic Cleanup Subprocess Storm (Every 5 Min)

`cleanup.py:166-186` - `_get_container_ifindexes()` spawns one `nsenter` subprocess per running container PID:

```python
for pid in pids:
    code, stdout, _ = await self._run_cmd(["nsenter", "-t", str(pid), "-n", "ip", "-o", "link", "show"])
```

Uses `asyncio.create_subprocess_exec` (non-blocking for event loop), but with 20+ containers, this creates a burst of 20+ concurrent subprocesses. On a loaded system, subprocess creation can become slow and back-pressure the event loop.

Additionally, `_is_veth_orphaned()` (cleanup.py:291) runs another subprocess per veth interface. Combined: **O(veths + containers) subprocesses** every 5 minutes.

### 6. MEDIUM: `_detect_local_ip()` Sync subprocess at Startup

`main.py:618`:
```python
result = subprocess.run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=5)
```

Synchronous subprocess with **5-second timeout** during startup. If network is slow to respond, blocks the event loop for up to 5s.

### 7. LOW: Sync `def` Endpoints Consume Thread Pool

Several endpoints use sync `def` (FastAPI runs these in the thread pool):
- `list_images()` (6633) - calls `_get_docker_images()` which lists ALL Docker images
- `check_image()` (6644) - file I/O
- `metrics()` (1189), `info()` (1198) - lightweight

`list_images()` is the most concerning - `client.images.list()` can take 1-5s with many images.

---

## Thread Pool Saturation Analysis

Python's default thread pool: `min(32, os.cpu_count() + 4)` threads.

Permanent thread consumers:
- Docker event listener: **1 thread** (permanently occupied)

Periodic thread consumers:
- Heartbeat resource gathering: **1 thread** every 30-60s (held for 0.5-5s)
- Cleanup container PID listing: **1 thread** every 5 min (via `asyncio.to_thread`)

Burst thread consumers (during API requests):
- Each `asyncio.to_thread()` call from any endpoint
- Each sync `def` endpoint handler

On a busy agent, 5-10 concurrent API requests + heartbeat + event listener can consume 10+ threads simultaneously. This doesn't directly block `/healthz` (it's `async def`), but if any of the above issues (items 1-2) are also present, the combination creates cascading stalls.

---

## Cascade Scenario (Most Likely Failure Mode)

1. Controller polls `/labs/{lab_id}/status` for multiple labs
2. Each `lab_status()` call triggers `docker_provider.status()` which does `asyncio.to_thread(docker.containers.list)` - consuming threads
3. Simultaneously, overlay operations trigger `attach_container()` which calls `docker.containers.get()` **directly on the event loop** (line 2767)
4. Docker daemon is slow to respond (busy with container operations)
5. Event loop blocks for 2-5s on the bare Docker API call
6. Health check arrives during this window - can't be served
7. After 3 consecutive failures (over 90s window), Docker marks container unhealthy

---

## Severity Summary

| # | Issue | Impact | Location |
|---|-------|--------|----------|
| 1 | Bare Docker API on event loop | **CRITICAL** - blocks all async handlers | main.py:2767, 4181 |
| 2 | Sync subprocess in bootstrap | **CRITICAL** - blocks startup | main.py:762-808 |
| 3 | Heavy heartbeat gathering | HIGH - thread pool pressure | main.py:464-605 |
| 4 | Event listener thread drain | HIGH - permanent thread consumption | docker_events.py:153 |
| 5 | Cleanup subprocess storm | MEDIUM - burst pressure every 5m | cleanup.py:166-186 |
| 6 | Sync subprocess at startup | MEDIUM - one-time startup block | main.py:618 |
| 7 | Sync endpoint thread usage | LOW - occasional thread pressure | main.py:6633 |

---

## Files Involved

- `agent/main.py` - Primary file with blocking calls (8000 lines)
- `agent/network/cleanup.py` - Periodic cleanup task
- `agent/network/cmd.py` - Shared async subprocess runner (properly async)
- `agent/network/carrier_monitor.py` - OVS polling (properly async)
- `agent/events/docker_events.py` - Docker event listener
- `agent/providers/docker.py` - Docker provider (mostly properly wrapped)
- `docker-compose.gui.yml` - Health check configuration
