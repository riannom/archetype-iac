#!/bin/bash
set -e

# Archetype Agent Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/agent/install.sh | sudo bash -s -- [OPTIONS]
#
# Options:
#   --name NAME           Agent name (required for install)
#   --controller URL      Controller URL (required for install)
#   --redis URL           Redis URL for distributed locks (required for multi-host)
#   --ip IP               Local IP for multi-host networking (auto-detected if not set)
#   --port PORT           Agent port (default: 8001)
#   --mtu MTU             Target MTU for jumbo frames (default: 9000, 0 to skip)
#   --no-docker           Skip Docker installation
#   --libvirt             Install libvirt/KVM for VM-based devices (IOSv, CSR1000v, etc.)
#   --update              Quick update: pull latest code and restart (no full reinstall)
#   --uninstall           Remove the agent
#
# Examples:
#   Install:  curl ... | sudo bash -s -- --name myagent --controller http://192.168.1.100:8000
#   Update:   curl ... | sudo bash -s -- --update
#   Remove:   curl ... | sudo bash -s -- --uninstall

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
INSTALL_LIBVIRT=false
INSTALL_LIBVIRT_PYTHON=false
UNINSTALL=false
UPDATE_ONLY=false
TARGET_MTU="9000"  # Desired MTU for jumbo frames (0 = skip MTU config)

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
        --mtu)
            TARGET_MTU="$2"
            shift 2
            ;;
        --no-docker)
            INSTALL_DOCKER=false
            shift
            ;;
        --libvirt)
            INSTALL_LIBVIRT=true
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --update)
            UPDATE_ONLY=true
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Update only mode - quick update without full reinstall
if [ "$UPDATE_ONLY" = true ]; then
    if [ ! -d "$INSTALL_DIR/repo" ]; then
        log_error "Agent not installed. Run without --update first."
        exit 1
    fi

    log_info "Updating Archetype Agent..."
    cd $INSTALL_DIR/repo

    # Fetch and reset to latest
    git fetch origin
    CURRENT=$(git rev-parse HEAD)
    git reset --hard origin/$BRANCH
    NEW=$(git rev-parse HEAD)

    if [ "$CURRENT" = "$NEW" ]; then
        log_info "Already up to date."
    else
        log_info "Updated: $CURRENT -> $NEW"

        # Update dependencies in case requirements changed
        log_info "Updating Python dependencies..."
        source $INSTALL_DIR/venv/bin/activate
        pip install --quiet -r $INSTALL_DIR/repo/agent/requirements.txt
    fi

    # Restart service
    log_info "Restarting agent..."
    systemctl restart $SERVICE_NAME
    sleep 2

    if systemctl is-active --quiet $SERVICE_NAME; then
        log_info "Agent updated and running!"
    else
        log_error "Agent failed to start. Check: journalctl -u $SERVICE_NAME -f"
        exit 1
    fi
    exit 0
fi

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
        apt-get install -y -qq python3 python3-venv python3-pip git curl iproute2 ca-certificates gnupg openvswitch-switch
        ;;
    centos|rhel|rocky|almalinux|fedora)
        dnf install -y python3 python3-pip git curl iproute ca-certificates openvswitch
        ;;
    *)
        log_warn "Unsupported OS: $OS. Attempting generic install..."
        ;;
esac

# Ensure OVS is running
if systemctl list-unit-files | grep -q openvswitch-switch; then
    systemctl enable --now openvswitch-switch 2>/dev/null || true
elif systemctl list-unit-files | grep -q openvswitch; then
    systemctl enable --now openvswitch 2>/dev/null || true
fi

# Create Docker plugin directories for OVS network plugin
mkdir -p /run/docker/plugins /etc/docker/plugins

# Configure MTU for jumbo frames (if requested)
OVERLAY_MTU=1450  # Default fallback
LOCAL_MTU=9000    # Local veth pairs (no physical limit)
CONFIGURED_IFACE=""  # Will be set if MTU is configured

if [ "$TARGET_MTU" != "0" ] && [ -n "$TARGET_MTU" ]; then
    log_info "Configuring network MTU..."

    # Detect primary network interface (the one with default route)
    PRIMARY_IFACE=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' | head -1)

    if [ -n "$PRIMARY_IFACE" ]; then
        CURRENT_MTU=$(ip link show "$PRIMARY_IFACE" 2>/dev/null | grep -oP 'mtu \K\d+')
        log_info "Primary interface: $PRIMARY_IFACE (current MTU: $CURRENT_MTU)"

        if [ "$CURRENT_MTU" -lt "$TARGET_MTU" ]; then
            log_info "Attempting to set $PRIMARY_IFACE MTU to $TARGET_MTU..."

            # Try to set the MTU
            if ip link set "$PRIMARY_IFACE" mtu "$TARGET_MTU" 2>/dev/null; then
                NEW_MTU=$(ip link show "$PRIMARY_IFACE" 2>/dev/null | grep -oP 'mtu \K\d+')

                if [ "$NEW_MTU" -eq "$TARGET_MTU" ]; then
                    log_info "Successfully set $PRIMARY_IFACE MTU to $TARGET_MTU"

                    # Calculate overlay MTU (path MTU - VXLAN overhead)
                    OVERLAY_MTU=$((TARGET_MTU - 50))
                    log_info "Overlay MTU set to $OVERLAY_MTU (jumbo frames enabled)"

                    # Make MTU persistent with a boot-time service
                    log_info "Creating MTU persistence service..."
                    cat > /etc/systemd/system/archetype-mtu.service << MTUSVC
[Unit]
Description=Set network MTU for Archetype Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/sbin/ip link set $PRIMARY_IFACE mtu $TARGET_MTU
ExecStart=/sbin/ip link set arch-ovs mtu $TARGET_MTU
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MTUSVC
                    systemctl daemon-reload
                    systemctl enable archetype-mtu.service 2>/dev/null || true
                    log_info "MTU will persist across reboots"
                    CONFIGURED_IFACE="$PRIMARY_IFACE"
                else
                    log_warn "MTU change didn't stick (got $NEW_MTU). Infrastructure may not support jumbo frames."
                fi
            else
                log_warn "Failed to set MTU to $TARGET_MTU. Infrastructure may not support jumbo frames."
            fi
        else
            log_info "$PRIMARY_IFACE already has MTU >= $TARGET_MTU"
            OVERLAY_MTU=$((TARGET_MTU - 50))
            CONFIGURED_IFACE="$PRIMARY_IFACE"
        fi
    else
        log_warn "Could not detect primary network interface. Skipping MTU configuration."
    fi

    # Create/configure OVS bridge with matching MTU
    if command -v ovs-vsctl &> /dev/null; then
        # Create bridge if it doesn't exist
        ovs-vsctl --may-exist add-br arch-ovs 2>/dev/null || true

        # Set bridge MTU (use current interface MTU, not target, in case it didn't work)
        ACTUAL_MTU=$(ip link show "$PRIMARY_IFACE" 2>/dev/null | grep -oP 'mtu \K\d+' || echo "1500")
        ip link set arch-ovs mtu "$ACTUAL_MTU" 2>/dev/null || true

        # Set bridge to standalone mode for L2 switching
        ovs-vsctl set-fail-mode arch-ovs standalone 2>/dev/null || true

        # Bring bridge up
        ip link set arch-ovs up 2>/dev/null || true

        log_info "OVS bridge arch-ovs configured (MTU: $ACTUAL_MTU)"
    fi
fi

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

# Install Libvirt/KVM for VM-based devices (IOSv, CSR1000v, NX-OSv, etc.)
if [ "$INSTALL_LIBVIRT" = true ]; then
    if command -v virsh &> /dev/null && systemctl is-active --quiet libvirtd; then
        log_info "Libvirt already installed and running: $(virsh --version)"
    else
        log_info "Installing Libvirt/KVM for VM support..."

        # Check KVM support
        if [ ! -e /dev/kvm ]; then
            log_warn "/dev/kvm not found. Hardware virtualization may not be available."
            log_warn "VM-based devices may run slowly without KVM acceleration."
        fi

        case $OS in
            ubuntu|debian)
                apt-get install -y -qq qemu-kvm libvirt-daemon-system libvirt-clients \
                    libvirt-dev pkg-config gcc libc-dev qemu-utils
                ;;
            centos|rhel|rocky|almalinux|fedora)
                dnf install -y qemu-kvm libvirt libvirt-client \
                    libvirt-devel pkgconfig gcc qemu-img
                ;;
            *)
                log_warn "Please install libvirt manually for VM support"
                ;;
        esac

        # Start and enable libvirtd
        systemctl enable --now libvirtd
        log_info "Libvirt installed successfully"
    fi

    # Install libvirt-python in venv (done later after venv is created)
    INSTALL_LIBVIRT_PYTHON=true
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

# Install libvirt-python if libvirt was installed
if [ "${INSTALL_LIBVIRT_PYTHON:-false}" = true ]; then
    log_info "Installing libvirt-python..."
    pip install --quiet libvirt-python
fi

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
ARCHETYPE_AGENT_ENABLE_LIBVIRT=$INSTALL_LIBVIRT
ARCHETYPE_AGENT_ENABLE_VXLAN=true
ARCHETYPE_AGENT_WORKSPACE_PATH=/var/lib/archetype-agent

# MTU settings
# overlay_mtu: Used for VXLAN tenant interfaces (path MTU - 50 byte overhead)
# local_mtu: Used for local veth pairs (no physical constraint)
ARCHETYPE_AGENT_OVERLAY_MTU=$OVERLAY_MTU
ARCHETYPE_AGENT_LOCAL_MTU=$LOCAL_MTU
EOF

# Create workspace directory
mkdir -p /var/lib/archetype-agent

# Create systemd service
log_info "Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Archetype Network Lab Agent
After=network.target docker.service libvirtd.service
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
echo "Overlay MTU:   $OVERLAY_MTU (VXLAN tenant interfaces)"
echo "Local MTU:     $LOCAL_MTU (same-host veth pairs)"
if [ -n "$CONFIGURED_IFACE" ]; then
    echo "Network:       $CONFIGURED_IFACE @ MTU $TARGET_MTU (jumbo frames)"
fi
if [ "$INSTALL_LIBVIRT" = true ]; then
    echo "Libvirt:       Enabled (VM support for IOSv, CSR1000v, etc.)"
fi
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
