# Archetype

A web-based network lab management platform for [containerlab](https://containerlab.dev/). Design network topologies with a drag-and-drop canvas, deploy them with one click, and access device consoles directly from your browser.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Features

- **Visual Topology Designer** - Drag-and-drop canvas for building network topologies
- **One-Click Deployment** - Deploy labs to containerlab with a single click
- **Web Console Access** - SSH/console access to devices directly in the browser via WebSocket
- **Multi-Host Support** - Distributed agents for running labs across multiple hosts with VXLAN overlay
- **Image Library** - Upload and manage container images (cEOS, etc.) and QCOW2 disk images
- **YAML Import/Export** - Import existing containerlab topologies or export for use elsewhere
- **User Management** - Local authentication and OIDC/SSO support
- **Lab Lifecycle** - Start, stop, restart, and destroy labs with job queuing

## Supported Devices

| Vendor | Devices |
|--------|---------|
| Arista | cEOS, CVX |
| Cisco | IOSv, IOS-XR, XRd, CSR1000v, Nexus 9000v, ASAv |
| Juniper | cRPD, vSRX3, vJunos Switch, vQFX |
| Nokia | SR Linux |
| Fortinet | FortiGate |
| Palo Alto | VM-Series |
| F5 | BIG-IP |
| Open Source | FRR, VyOS, SONiC, HAProxy, Linux |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Browser                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐  │
│  │ Topology Canvas │  │  Lab Controls   │  │   Console   │  │
│  │  (React Flow)   │  │                 │  │  (xterm.js) │  │
│  └────────┬────────┘  └────────┬────────┘  └──────┬──────┘  │
└───────────┼─────────────────────┼─────────────────┼─────────┘
            │ REST API            │ REST API        │ WebSocket
            ▼                     ▼                 ▼
┌─────────────────────────────────────────────────────────────┐
│                      API Server (FastAPI)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │  Auth    │  │  Labs    │  │  Images  │  │  WebSocket  │  │
│  │  (JWT)   │  │  CRUD    │  │  Store   │  │   Console   │  │
│  └──────────┘  └──────────┘  └──────────┘  └─────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ Job Queue (Redis + RQ)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                         Worker                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  containerlab deploy/destroy  │  netlab up/down      │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Container Runtime                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  cEOS   │  │  SRLinux│  │   FRR   │  │  Linux  │  ...   │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### One-Line Install (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install-controller.sh | sudo bash
```

This installs everything you need: Docker, the web UI, API, database, and a local agent.

After installation, you'll see:
- **Web UI**: `http://<your-ip>:8080`
- **Admin credentials**: Displayed at the end of installation (save these!)

### Manual Installation

#### Prerequisites

- Docker Engine 24.0+ with Docker Compose
- Linux host (Ubuntu 22.04+, Debian 12+, RHEL 9+, or similar)
- 4GB RAM minimum (8GB+ recommended for multiple labs)
- Root/sudo access (required for containerlab)

#### Step 1: Clone the Repository

```bash
git clone https://github.com/riannom/archetype-iac.git
cd archetype-iac
```

#### Step 2: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set secure secrets:

```bash
# Generate secure secrets
JWT_SECRET=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)

# Update .env with generated secrets
sed -i "s/CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32/$JWT_SECRET/" .env
sed -i "s/CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32/$SESSION_SECRET/" .env
```

Key configuration options:

| Variable | Description | Default |
|----------|-------------|---------|
| `WEB_PORT` | Web UI port | `8080` |
| `API_PORT` | API server port | `8000` |
| `JWT_SECRET` | JWT signing key (required) | - |
| `SESSION_SECRET` | Session encryption key (required) | - |
| `ADMIN_EMAIL` | Initial admin email | `admin@example.com` |
| `ADMIN_PASSWORD` | Initial admin password | `changeme123` |
| `LOCAL_AUTH_ENABLED` | Enable local authentication | `true` |

#### Step 3: Start Services

```bash
docker compose -f docker-compose.gui.yml up -d --build
```

This starts:
- **web** - Nginx serving the React frontend
- **api** - FastAPI backend
- **worker** - RQ worker for async jobs
- **postgres** - PostgreSQL database
- **redis** - Redis for job queue
- **agent** - Local containerlab agent

#### Step 4: Access the UI

Open `http://localhost:8080` in your browser and log in with your admin credentials.

## Multi-Host Setup

For larger deployments, you can run agents on multiple hosts to distribute lab workloads.

### Controller Host

Run the controller with the agent disabled:

```bash
docker compose -f docker-compose.gui.yml up -d --scale agent=0
```

### Agent Hosts

On each agent host, install the standalone agent:

```bash
curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/agent/install.sh | \
  sudo bash -s -- --name agent-01 --controller http://<controller-ip>:8000
```

Options:
- `--name <name>` - Unique agent name
- `--controller <url>` - Controller API URL
- `--port <port>` - Agent listen port (default: 8001)

Agents automatically register with the controller and appear in the UI.

## Usage

### Creating a Lab

1. Click **New Lab** from the dashboard
2. Drag devices from the catalog onto the canvas
3. Connect interfaces by dragging between ports
4. Click **Deploy** to start the lab

### Accessing Consoles

1. Click on a running device in the topology
2. Select **Console** from the context menu
3. A terminal opens in your browser with SSH/console access

### Importing Topologies

You can import existing containerlab YAML files:

1. Open a lab
2. Click **Import** → **From YAML**
3. Paste or upload your topology file

### Managing Images

Before deploying certain devices (like Arista cEOS), you need to upload their images:

1. Go to **Catalog** → **Images**
2. Click **Upload Image**
3. Select your image file (`.tar`, `.tar.xz`, or `.qcow2`)
4. The image is automatically detected and added to the library

## Development

### Local Development Setup

**Backend:**

```bash
cd api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**

```bash
cd web
npm install
npm run dev
```

**Worker:**

```bash
cd api
source venv/bin/activate
rq worker netlab
```

### Running Tests

```bash
# Backend tests
cd api
pytest

# Frontend tests
cd web
npm test
```

### Database Migrations

```bash
cd api
alembic upgrade head                     # Apply migrations
alembic revision --autogenerate -m "description"  # Create migration
```

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WEB_PORT` | Web UI port | `8080` |
| `API_PORT` | API server port | `8000` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg://netlab:netlab@postgres:5432/netlab_gui` |
| `REDIS_URL` | Redis connection string | `redis://redis:6379/0` |
| `NETLAB_WORKSPACE` | Lab workspace directory | `/var/lib/netlab-gui` |
| `NETLAB_PROVIDER` | Lab provider (`clab`) | `clab` |
| `JWT_SECRET` | JWT signing secret | (required) |
| `SESSION_SECRET` | Session encryption secret | (required) |
| `ADMIN_EMAIL` | Initial admin email | - |
| `ADMIN_PASSWORD` | Initial admin password | - |
| `LOCAL_AUTH_ENABLED` | Enable local auth | `true` |
| `MAX_CONCURRENT_JOBS_PER_USER` | Job limit per user | `2` |

### OIDC/SSO Configuration

```bash
OIDC_ISSUER_URL=https://accounts.google.com
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
OIDC_REDIRECT_URI=http://localhost:8000/auth/oidc/callback
OIDC_SCOPES=openid profile email
OIDC_APP_REDIRECT_URL=http://localhost:8080/auth/callback
```

## Operations

### Backup

```bash
./scripts/backup.sh
```

Creates timestamped backups in `./backups/`:
- `db-<timestamp>.sql` - Database dump
- `workspaces-<timestamp>.tar.gz` - Lab workspace files

### Restore

```bash
./scripts/restore.sh
```

### View Logs

```bash
# All services
docker compose -f docker-compose.gui.yml logs -f

# Specific service
docker compose -f docker-compose.gui.yml logs -f api
docker compose -f docker-compose.gui.yml logs -f worker
docker compose -f docker-compose.gui.yml logs -f agent
```

### Restart Services

```bash
docker compose -f docker-compose.gui.yml restart
```

### Rebuild After Code Changes

```bash
docker compose -f docker-compose.gui.yml up -d --build
```

### Uninstall

If installed via the install script:

```bash
sudo /opt/archetype-controller/install-controller.sh --uninstall
```

Manual cleanup:

```bash
docker compose -f docker-compose.gui.yml down -v
```

## Troubleshooting

### Lab Deployment Fails

1. Check worker logs: `docker compose -f docker-compose.gui.yml logs worker`
2. Verify required images are uploaded in **Catalog** → **Images**
3. Ensure Docker socket is mounted and accessible

### Console Connection Fails

1. Check that the device is running: `docker ps`
2. Verify WebSocket connectivity to the API
3. Check API logs for connection errors

### Agent Not Registering

1. Verify network connectivity between agent and controller
2. Check agent logs: `journalctl -u archetype-agent -f`
3. Ensure firewall allows traffic on agent port (default 8001)

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Commit changes: `git commit -m "feat: add my feature"`
4. Push to the branch: `git push origin feat/my-feature`
5. Open a Pull Request

Please use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [containerlab](https://containerlab.dev/) - The underlying lab orchestration engine
- [netlab](https://netlab.tools/) - Network lab automation framework
- [React Flow](https://reactflow.dev/) - Topology canvas library
- [xterm.js](https://xtermjs.org/) - Terminal emulator for web consoles
