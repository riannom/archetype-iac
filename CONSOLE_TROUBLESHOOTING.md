# Console Troubleshooting Notes

## Summary
- Console WebSocket backend works; the CLI test receives binary data.
- UI console still disconnects; likely due to frontend handling of binary frames or stale frontend bundle/cache.
- Multi-host routing now works (r1 to host-b, r2 to local-agent), but UI still fails.

## Environment
- Controller host: `10.14.23.36`
- Remote agent host (host-b): `10.14.23.11`
- Lab id: `a2fe3d2a-ebd0-4a06-b53c-baaec34fbb64`
- UI: `http://10.14.23.36:8080`

## Key Findings
- Agent connectivity is healthy and reachable from controller:
  - `curl http://10.14.23.11:8001/health` returns OK.
- Lab topology on controller has correct host placement:
  ```yaml
  nodes:
    r1:
      kind: linux
      image: alpine:latest
      host: host-b
    r2:
      kind: linux
      image: alpine:latest
      host: local-agent
  links:
    - r1:
        ifname: eth1
      r2:
        ifname: eth1
  ```
- Containers exist and `docker exec` works on both hosts:
  - `clab-a2fe3d2a-ebd0-4a06-b-r1` on host-b
  - `clab-a2fe3d2a-ebd0-4a06-b-r2` on controller host
- Routing confirmation:
  - `sudo journalctl -u archetype-agent --since "1 min ago" | grep -i console` shows r1 requests hitting host-b.
  - `docker logs --since 1m archetype-controller-agent-1 | grep -i console` shows r2 requests hitting local agent.
- Backend console WebSocket works and returns binary data:
  ```bash
  docker exec -it archetype-controller-api-1 sh -c 'printf "%s\n" \
  "import asyncio, websockets" \
  "async def main():" \
  "    uri = \"ws://localhost:8000/labs/a2fe3d2a-ebd0-4a06-b53c-baaec34fbb64/nodes/r1/console\"" \
  "    async with websockets.connect(uri) as ws:" \
  "        print(\"connected\")" \
  "        await ws.send(\"echo hi\\n\")" \
  "        msg = await ws.recv()" \
  "        print(\"recv:\", msg)" \
  "" \
  "asyncio.run(main())" \
  > /tmp/ws_test.py && python /tmp/ws_test.py'
  ```
  Output example:
  - `connected`
  - `recv: b'/ # \x1b[6n\r/ # \x1b[J'`

## Likely Root Cause
- UI console handler was treating WebSocket messages as text only.
- WebSocket delivers binary frames (Blob/ArrayBuffer). If not handled properly, xterm errors or console disconnects.
- Even after changes, browser may still be using cached bundle; verify with a fresh build and cache bypass.

## Frontend Fix (Needed in /opt/archetype-controller build)
Target file:
- `web/src/pages/LabDetailPage.tsx`

Desired handler:
```ts
  function handleConsoleMessage(data: unknown) {
    if (typeof data === "string") {
      terminalRef.current?.write(data);
      setConsoleOutput((prev) => prev + data);
      return;
    }

    if (data instanceof ArrayBuffer) {
      const bytes = new Uint8Array(data);
      terminalRef.current?.write(bytes);
      const text = new TextDecoder().decode(bytes);
      setConsoleOutput((prev) => prev + text);
      return;
    }

    if (data instanceof Blob) {
      data.arrayBuffer().then((buffer) => {
        const bytes = new Uint8Array(buffer);
        terminalRef.current?.write(bytes);
        const text = new TextDecoder().decode(bytes);
        setConsoleOutput((prev) => prev + text);
      });
    }
  }
```
And set on sockets:
```ts
const socket = new WebSocket(wsUrl);
socket.binaryType = "arraybuffer";
socket.onmessage = (event) => {
  handleConsoleMessage(event.data);
};
```

## Build / Deploy
- Rebuild web:
  ```bash
  cd /opt/archetype-controller
  docker compose -f docker-compose.gui.yml up -d --build web
  ```
- Verify bundle includes `binaryType` and `Uint8Array`:
  ```bash
  docker exec -it archetype-controller-web-1 /bin/sh -c 'grep -n "binaryType" /usr/share/nginx/html/assets/*.js | head -n 5'
  docker exec -it archetype-controller-web-1 /bin/sh -c 'grep -n "Uint8Array" /usr/share/nginx/html/assets/*.js | head -n 5'
  ```
- Use hard refresh or private window to avoid cached JS.

## Misc Noise (Not Root Cause)
- UI spams `/jobs/<id>/log?tail=200` with 404s; unrelated to console.

## Agents
- `GET /agents` shows:
  - `host-b` -> `10.14.23.11:8001` (online)
  - `local-agent` -> `10.14.23.36:8001` (online)

## Notes
- Multi-host placement must match agent names (`host-b`, `local-agent`).
- Console routing uses topology parsing; falls back to lab agent if not found.

## Controller Checks (2026-01-20)
- UI served bundle includes `binaryType="arraybuffer"` and `TextDecoder` handling.
- Nginx `/api/` proxy works: `curl http://127.0.0.1:8080/api/health` returns OK.
- Local WebSocket tests show binary frames delivered through both API and web proxy:
  - `ws://127.0.0.1:8000/labs/<lab>/nodes/r1/console`
  - `ws://127.0.0.1:8080/api/labs/<lab>/nodes/r1/console`
- WebSocket stays open when idle; backend does not auto-disconnect during a 5s wait.

## Controller Fix Applied (2026-01-20)
- Updated `/opt/archetype-controller/web/src/pages/LabDetailPage.tsx` to write `Uint8Array` directly to xterm for ArrayBuffer/Blob frames while still capturing decoded text for `consoleOutput`.
- Rebuilt and restarted web container: `docker compose -f /opt/archetype-controller/docker-compose.gui.yml up -d --build web`.
- New bundle is `http://127.0.0.1:8080/assets/index-u7EUbNhD.js` and includes `Uint8Array` handling in console message path.
