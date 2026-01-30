#!/bin/bash
set -e

# Archetype Controller Installer
# Installs the full Archetype controller stack (API, Web UI, Database, Redis)
#
# Usage: curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install-controller.sh | sudo bash

INSTALL_DIR="/opt/archetype-controller"
REPO_URL="https://github.com/riannom/archetype-iac.git"
BRANCH="main"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Defaults
WEB_PORT="8080"
API_PORT="8000"
UNINSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --web-port)
            WEB_PORT="$2"
            shift 2
            ;;
        --api-port)
            API_PORT="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Uninstall
if [ "$UNINSTALL" = true ]; then
    log_info "Uninstalling Archetype Controller..."
    cd $INSTALL_DIR 2>/dev/null && docker compose -f docker-compose.gui.yml down -v 2>/dev/null || true
    rm -rf $INSTALL_DIR
    log_info "Archetype Controller uninstalled successfully"
    exit 0
fi

# Check root
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo)"
    exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    log_error "Cannot detect OS"
    exit 1
fi

log_info "Detected OS: $OS"
log_info "Installing Archetype Controller..."

# Install system dependencies
log_info "Installing system dependencies..."

case $OS in
    ubuntu|debian)
        apt-get update -qq
        apt-get install -y -qq git curl ca-certificates gnupg openssl
        ;;
    centos|rhel|rocky|almalinux|fedora)
        dnf install -y git curl ca-certificates openssl
        ;;
    *)
        log_warn "Unsupported OS. Attempting to continue..."
        ;;
esac

# Install Docker if not present
if command -v docker &> /dev/null; then
    log_info "Docker already installed: $(docker --version)"
else
    log_info "Installing Docker..."
    case $OS in
        ubuntu|debian)
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            chmod a+r /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
            apt-get update -qq
            apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        centos|rhel|rocky|almalinux|fedora)
            dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null || \
                dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        *)
            log_error "Please install Docker manually and re-run"
            exit 1
            ;;
    esac
    systemctl start docker
    systemctl enable docker
fi

# Create install directory
log_info "Setting up Archetype Controller in $INSTALL_DIR..."
mkdir -p $INSTALL_DIR
cd $INSTALL_DIR

# Clone or update repository
if [ -d "$INSTALL_DIR/.git" ]; then
    log_info "Updating repository..."
    git fetch origin
    git reset --hard origin/$BRANCH
else
    log_info "Cloning repository..."
    cd /opt
    rm -rf archetype-controller
    git clone --branch $BRANCH $REPO_URL archetype-controller
    cd $INSTALL_DIR
fi

# Generate secrets
JWT_SECRET=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)
ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)

# Get local IP for display
LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || hostname -I | awk '{print $1}')

# Create .env file
log_info "Creating configuration..."
cat > $INSTALL_DIR/.env << EOF
# Archetype Controller Configuration
# Generated on $(date)

# Web UI
WEB_PORT=$WEB_PORT
API_BASE_URL=http://localhost:$API_PORT

# API
API_PORT=$API_PORT
DATABASE_URL=postgresql+psycopg://archetype:archetype@postgres:5432/archetype
REDIS_URL=redis://redis:6379/0
WORKSPACE=/var/lib/archetype
PROVIDER=clab
LOCAL_AUTH_ENABLED=true
MAX_CONCURRENT_JOBS_PER_USER=2

# Security - Auto-generated secrets (keep these safe!)
JWT_SECRET=$JWT_SECRET
SESSION_SECRET=$SESSION_SECRET

# Admin account
ADMIN_EMAIL=admin@localhost
ADMIN_PASSWORD=$ADMIN_PASSWORD

# OIDC (optional - configure if using SSO)
OIDC_ISSUER_URL=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
OIDC_REDIRECT_URI=http://localhost:$API_PORT/auth/oidc/callback
OIDC_SCOPES=openid profile email
OIDC_APP_REDIRECT_URL=http://localhost:$WEB_PORT/auth/callback

# Local agent (built into docker-compose)
ARCHETYPE_AGENT_NAME=local-agent
ARCHETYPE_AGENT_LOCAL_IP=$LOCAL_IP
EOF

# Make readable by docker group (or use 600 and run with sudo for tighter security)
chmod 644 $INSTALL_DIR/.env

# Build and start containers
log_info "Building and starting containers (this may take a few minutes)..."
docker compose -f docker-compose.gui.yml build --quiet
docker compose -f docker-compose.gui.yml up -d

# Wait for services to be ready
log_info "Waiting for services to start..."
for i in {1..30}; do
    if curl -s http://localhost:$API_PORT/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

# Check if API is responding
if curl -s http://localhost:$API_PORT/health > /dev/null 2>&1; then
    log_info "API is ready!"
else
    log_warn "API may still be starting. Check logs with: docker compose -f $INSTALL_DIR/docker-compose.gui.yml logs -f api"
fi

echo ""
echo "=============================================="
echo -e "${GREEN}Archetype Controller Installation Complete!${NC}"
echo "=============================================="
echo ""
echo -e "${CYAN}Access URLs:${NC}"
echo "  Web UI:      http://$LOCAL_IP:$WEB_PORT"
echo "  API:         http://$LOCAL_IP:$API_PORT"
echo "  API Health:  http://$LOCAL_IP:$API_PORT/health"
echo ""
echo -e "${CYAN}Admin Credentials:${NC}"
echo "  Email:       admin@localhost"
echo "  Password:    $ADMIN_PASSWORD"
echo ""
echo -e "${CYAN}Agent Installation:${NC}"
echo "  On each agent host, run:"
echo ""
echo "  curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/agent/install.sh | \\"
echo "    sudo bash -s -- --name <agent-name> --controller http://$LOCAL_IP:$API_PORT"
echo ""
echo -e "${CYAN}Useful Commands:${NC}"
echo "  View logs:       cd $INSTALL_DIR && docker compose -f docker-compose.gui.yml logs -f"
echo "  Restart:         cd $INSTALL_DIR && docker compose -f docker-compose.gui.yml restart"
echo "  Stop:            cd $INSTALL_DIR && docker compose -f docker-compose.gui.yml down"
echo "  Uninstall:       $0 --uninstall"
echo ""
echo "Configuration:     $INSTALL_DIR/.env"
echo ""
echo -e "${YELLOW}IMPORTANT: Save your admin password! It won't be shown again.${NC}"
echo ""
