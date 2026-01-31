# Archetype-IAC API Reference

## Base URL

```
http://<controller-ip>:8000
```

## Authentication

### Login

```bash
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=admin@localhost&password=YOUR_PASSWORD
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

Use the token in subsequent requests:
```
Authorization: Bearer <access_token>
```

---

## Agents

### List Agents

```bash
GET /agents
```

**Response:**
```json
[
  {
    "id": "abc123",
    "name": "local-agent",
    "address": "10.14.23.36:8001",
    "status": "online",
    "capabilities": {
      "providers": ["docker"],
      "features": ["console", "status", "vxlan"]
    }
  }
]
```

---

## Labs

### Create Lab

```bash
POST /labs
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "my-lab"
}
```

### Import Topology (YAML)

```bash
POST /labs/{lab_id}/import-yaml
Authorization: Bearer <token>
Content-Type: application/json

{
  "content": "<yaml-string>"
}
```

**Note:** The YAML is passed as a string in the `content` field, not as raw YAML.

### Deploy Lab

```bash
POST /labs/{lab_id}/up
Authorization: Bearer <token>
```

### Destroy Lab

```bash
POST /labs/{lab_id}/down
Authorization: Bearer <token>
```

### Get Lab Status

```bash
GET /labs/{lab_id}
Authorization: Bearer <token>
```

### List Jobs

```bash
GET /labs/{lab_id}/jobs
Authorization: Bearer <token>
```

---

## Topology Format

Archetype uses a YAML format compatible with containerlab topologies, with an additional `host` field for multi-host deployment.

### Single-Host Topology

```yaml
nodes:
  r1:
    kind: linux
    image: alpine:latest
  r2:
    kind: linux
    image: alpine:latest
links:
  - r1:
      ifname: eth1
    r2:
      ifname: eth1
```

### Multi-Host Topology

Add `host: <agent-name>` to specify which agent deploys each node.
Add `ipv4:` to link endpoints for automatic IP configuration:

```yaml
nodes:
  r1:
    kind: linux
    image: alpine:latest
    host: local-agent
  r2:
    kind: linux
    image: alpine:latest
    host: host-b
links:
  - r1:
      ifname: eth1
      ipv4: 10.0.0.1/24
    r2:
      ifname: eth1
      ipv4: 10.0.0.2/24
```

When deployed, the system will:
1. Deploy `r1` on `local-agent`, `r2` on `host-b`
2. Create a VXLAN tunnel between the agents
3. Create veth pairs and attach containers to the overlay bridge
4. Configure the IP addresses automatically on `eth1`

### Important Fields

| Field | Required | Description |
|-------|----------|-------------|
| `kind` | Yes | Node type. Use `linux` for generic containers |
| `image` | Yes | Docker image to use |
| `host` | No | Agent name for multi-host deployment |

### Link Format

Links use the following format with optional IP configuration:

```yaml
links:
  - <node1>:
      ifname: <interface>
      ipv4: <ip/prefix>   # Optional: auto-configured on cross-host links
    <node2>:
      ifname: <interface>
      ipv4: <ip/prefix>   # Optional: auto-configured on cross-host links
```

**NOT** the `endpoints` format:
```yaml
# WRONG - not supported
links:
  - endpoints: ["r1:eth1", "r2:eth1"]
```

---

## Complete Example

### 1. Login and get token

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@localhost&password=YOUR_PASSWORD" | jq -r '.access_token')
```

### 2. Create lab

```bash
LAB_ID=$(curl -s -X POST http://localhost:8000/labs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "test-lab"}' | jq -r '.id')
```

### 3. Import multi-host topology with IPs

```bash
curl -s -X POST "http://localhost:8000/labs/${LAB_ID}/import-yaml" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "nodes:\n  r1:\n    kind: linux\n    image: alpine:latest\n    host: local-agent\n  r2:\n    kind: linux\n    image: alpine:latest\n    host: host-b\nlinks:\n  - r1:\n      ifname: eth1\n      ipv4: 10.0.0.1/24\n    r2:\n      ifname: eth1\n      ipv4: 10.0.0.2/24"
  }'
```

### 4. Deploy

```bash
curl -s -X POST "http://localhost:8000/labs/${LAB_ID}/up" \
  -H "Authorization: Bearer $TOKEN"
```

The deployment will:
- Deploy nodes to their assigned agents
- Set up VXLAN overlay between hosts
- Configure IP addresses automatically from the topology

### 5. Check status

```bash
curl -s "http://localhost:8000/labs/${LAB_ID}/jobs" \
  -H "Authorization: Bearer $TOKEN" | jq '.jobs[0]'
```

### 6. Test connectivity

```bash
# Ping from r1 to r2 across hosts
docker exec archetype-<lab-id>-r1 ping -c 3 10.0.0.2
```

### 7. Destroy when done

```bash
curl -s -X POST "http://localhost:8000/labs/${LAB_ID}/down" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Health Check

```bash
GET /health
```

No authentication required. Returns:
```json
{
  "status": "ok",
  "timestamp": "2026-01-20T02:00:00.000000"
}
```
