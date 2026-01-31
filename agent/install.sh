#!/bin/bash
set -e

# Archetype Agent Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/agent/install.sh | sudo bash -s -- [OPTIONS]
#
# Options:
#   --name NAME           Agent name (required)
#   --controller URL      Controller URL (required)
#   --redis URL           Redis URL for distributed locks (required for multi-host)
#   --ip IP               Local IP for multi-host networking (auto-detected if not set)
#   --port PORT           Agent port (default: 8001)
#   --no-docker           Skip Docker installation
#   --uninstall           Remove the agent

INSTALL_DIR="/opt/archetype-agent"
SERVICE_NAME="archetype-agent"
REPO_URL="https://github.com/riannom/archetype-iac.git"
BRANCH="main"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Default values
AGENT_NAME=""
CONTROLLER_URL=""
REDIS_URL=""
LOCAL_IP=""
AGENT_PORT="8001"
INSTALL_DOCKER=true
UNINSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            AGENT_NAME="$2"
            shift 2
            ;;
        --controller)
            CONTROLLER_URL="$2"
            shift 2
            ;;
        --redis)
            REDIS_URL="$2"
            shift 2
            ;;
        --ip)
            LOCAL_IP="$2"
            shift 2
            ;;
        --port)
            AGENT_PORT="$2"
            shift 2
            ;;
        --no-docker)
            INSTALL_DOCKER=false
            shift
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
    log_info "Uninstalling Archetype Agent..."
    systemctl stop $SERVICE_NAME 2>/dev/null || true
    systemctl disable $SERVICE_NAME 2>/dev/null || true
    rm -f /etc/systemd/system/$SERVICE_NAME.service
    systemctl daemon-reload
    rm -rf $INSTALL_DIR
    log_info "Archetype Agent uninstalled successfully"
    exit 0
fi

# Auto-detect local IP if not provided
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || hostname -I | awk '{print $1}')
    echo -e "${GREEN}[INFO]${NC} Auto-detected local IP: $LOCAL_IP"
fi

# Interactive prompts if required values not provided
# Use /dev/tty to read from terminal even when script is piped
if [ -z "$AGENT_NAME" ]; then
    # Generate default name from hostname
    DEFAULT_NAME=$(hostname -s)
    echo ""
    echo -n "Enter agent name [$DEFAULT_NAME]: "
    read AGENT_NAME < /dev/tty || AGENT_NAME=""
    AGENT_NAME=${AGENT_NAME:-$DEFAULT_NAME}
fi

if [ -z "$CONTROLLER_URL" ]; then
    echo ""
    echo "Enter the controller URL (e.g., http://192.168.1.100:8000)"
    echo -n "Controller URL: "
    read CONTROLLER_URL < /dev/tty || true
    if [ -z "$CONTROLLER_URL" ]; then
        log_error "Controller URL is required"
        echo ""
        echo "Run with arguments instead:"
        echo "  curl ... | sudo bash -s -- --controller http://192.168.1.100:8000"
        exit 1
    fi
fi

if [ -z "$REDIS_URL" ]; then
    # Extract host from controller URL for default Redis URL
    CONTROLLER_HOST=$(echo "$CONTROLLER_URL" | sed -E 's|https?://([^:/]+).*|\1|')
    DEFAULT_REDIS="redis://${CONTROLLER_HOST}:16379/0"
    echo ""
    echo "Enter Redis URL for distributed locks (required for multi-host deployments)"
    echo -n "Redis URL [$DEFAULT_REDIS]: "
    read REDIS_URL < /dev/tty || REDIS_URL=""
    REDIS_URL=${REDIS_URL:-$DEFAULT_REDIS}
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
    VERSION=$VERSION_ID
else
    log_error "Cannot detect OS"
    exit 1
fi

log_info "Detected OS: $OS $VERSION"
log_info "Installing Archetype Agent: $AGENT_NAME"
log_info "Controller: $CONTROLLER_URL"
log_info "Redis: $REDIS_URL"
log_info "Local IP: $LOCAL_IP"
log_info "Port: $AGENT_PORT"

# Install system dependencies
log_info "Installing system dependencies..."

case $OS in
    ubuntu|debian)
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip git curl iproute2 ca-certificates gnupg
        ;;
    centos|rhel|rocky|almalinux)
        dnf install -y python3 python3-pip git curl iproute ca-certificates
        ;;
    fedora)
        dnf install -y python3 python3-pip git curl iproute ca-certificates
        ;;
    *)
        log_warn "Unsupported OS: $OS. Attempting generic install..."
        ;;
esac

# Install Docker
if [ "$INSTALL_DOCKER" = true ]; then
    if command -v docker &> /dev/null; then
        log_info "Docker already installed: $(docker --version)"
    else
        log_info "Installing Docker..."
        case $OS in
            ubuntu|debian)
                # Add Docker GPG key
                install -m 0755 -d /etc/apt/keyrings
                curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
                chmod a+r /etc/apt/keyrings/docker.gpg

                # Add repository
                echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

                # Install
                apt-get update -qq
                apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
                ;;
            centos|rhel|rocky|almalinux|fedora)
                dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null || \
                    dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
                dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
                ;;
            *)
                log_warn "Please install Docker manually"
                ;;
        esac

        # Start and enable Docker
        systemctl start docker
        systemctl enable docker
        log_info "Docker installed successfully"
    fi
fi

# Create install directory
log_info "Setting up Archetype Agent in $INSTALL_DIR..."
mkdir -p $INSTALL_DIR

# Clone or update repository
if [ -d "$INSTALL_DIR/repo" ]; then
    log_info "Updating repository..."
    cd $INSTALL_DIR/repo
    git fetch origin
    git reset --hard origin/$BRANCH
else
    log_info "Cloning repository..."
    git clone --branch $BRANCH --depth 1 $REPO_URL $INSTALL_DIR/repo
fi

# Create virtual environment
log_info "Setting up Python virtual environment..."
python3 -m venv $INSTALL_DIR/venv
source $INSTALL_DIR/venv/bin/activate

# Install Python dependencies
log_info "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r $INSTALL_DIR/repo/agent/requirements.txt

# Create environment file
log_info "Creating configuration..."
cat > $INSTALL_DIR/agent.env << EOF
# Archetype Agent Configuration
ARCHETYPE_AGENT_AGENT_NAME=$AGENT_NAME
ARCHETYPE_AGENT_CONTROLLER_URL=$CONTROLLER_URL
ARCHETYPE_AGENT_REDIS_URL=$REDIS_URL
ARCHETYPE_AGENT_LOCAL_IP=$LOCAL_IP
ARCHETYPE_AGENT_AGENT_PORT=$AGENT_PORT
ARCHETYPE_AGENT_ENABLE_DOCKER=true
ARCHETYPE_AGENT_ENABLE_VXLAN=true
ARCHETYPE_AGENT_WORKSPACE_PATH=/var/lib/archetype-agent
EOF

# Create workspace directory
mkdir -p /var/lib/archetype-agent

# Create systemd service
log_info "Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Archetype Network Lab Agent
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/repo
EnvironmentFile=$INSTALL_DIR/agent.env
ExecStart=$INSTALL_DIR/venv/bin/python -m agent.main
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable service
systemctl daemon-reload
systemctl enable $SERVICE_NAME

# Start the service
log_info "Starting Archetype Agent..."
systemctl start $SERVICE_NAME

# Wait for startup
sleep 3

# Check status
if systemctl is-active --quiet $SERVICE_NAME; then
    log_info "Archetype Agent started successfully!"
else
    log_error "Archetype Agent failed to start. Check logs with: journalctl -u $SERVICE_NAME -f"
    exit 1
fi

echo ""
echo "=============================================="
echo -e "${GREEN}Archetype Agent Installation Complete!${NC}"
echo "=============================================="
echo ""
echo "Agent Name:    $AGENT_NAME"
echo "Controller:    $CONTROLLER_URL"
echo "Redis:         $REDIS_URL"
echo "Local IP:      $LOCAL_IP"
echo "Port:          $AGENT_PORT"
echo ""
echo "Useful commands:"
echo "  Check status:    systemctl status $SERVICE_NAME"
echo "  View logs:       journalctl -u $SERVICE_NAME -f"
echo "  Restart:         systemctl restart $SERVICE_NAME"
echo "  Stop:            systemctl stop $SERVICE_NAME"
echo "  Uninstall:       $0 --uninstall"
echo ""
echo "Configuration:     $INSTALL_DIR/agent.env"
echo ""
