#!/bin/bash
set -e

# Archetype Infrastructure-as-Code Installer
#
# Usage:
#   ./install.sh                              # Install controller + local agent
#   ./install.sh --controller                 # Install controller only
#   ./install.sh --agent --controller-url URL # Install agent only (for remote hosts)
#
# Options:
#   --controller           Install controller (uses Docker Compose)
#   --agent                Install agent (uses systemd)
#   --name NAME            Agent name (default: hostname)
#   --controller-url URL   Controller URL for remote agents
#   --ip IP                Local IP for VXLAN networking (auto-detected)
#   --port PORT            Agent port (default: 8001)
#   --no-libvirt           Disable libvirt/KVM installation for standalone agents
#   --uninstall            Remove installation
#   --fresh                Clean reinstall (removes database/volumes)

INSTALL_DIR="/opt/archetype-controller"
AGENT_INSTALL_DIR="/opt/archetype-agent"
REPO_URL="https://github.com/riannom/archetype-iac.git"
BRANCH=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_section() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }

# Defaults
INSTALL_CONTROLLER=false
INSTALL_AGENT=false
AGENT_NAME=""
CONTROLLER_URL=""
LOCAL_IP=""
AGENT_PORT="8001"
INSTALL_LIBVIRT=true
INSTALL_LIBVIRT_PYTHON=false
UNINSTALL=false
FRESH_INSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --controller)
            INSTALL_CONTROLLER=true
            shift
            ;;
        --agent)
            INSTALL_AGENT=true
            shift
            ;;
        --name)
            AGENT_NAME="$2"
            shift 2
            ;;
        --controller-url)
            CONTROLLER_URL="$2"
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
        --libvirt)
            INSTALL_LIBVIRT=true
            shift
            ;;
        --no-libvirt)
            INSTALL_LIBVIRT=false
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --fresh)
            FRESH_INSTALL=true
            shift
            ;;
        --branch)
            BRANCH_OVERRIDE="$2"
            shift 2
            ;;
        --version)
            CURRENT="none"
            if [ -f "$INSTALL_DIR/VERSION" ]; then
                CURRENT=$(cat "$INSTALL_DIR/VERSION")
            fi
            LATEST=$(curl -sS --max-time 10 \
                https://api.github.com/repos/riannom/archetype-iac/releases/latest 2>/dev/null \
                | grep -oP '"tag_name":\s*"\K[^"]+' 2>/dev/null || echo "unknown")
            echo "Installed: $CURRENT | Latest: $LATEST"
            exit 0
            ;;
        --help|-h)
            echo "Archetype Infrastructure-as-Code Installer"
            echo ""
            echo "Usage:"
            echo "  $0                                    # Install controller + local agent"
            echo "  $0 --controller                       # Install controller only"
            echo "  $0 --agent --controller-url URL       # Install agent only"
            echo ""
            echo "Options:"
            echo "  --controller         Install controller (Docker Compose)"
            echo "  --agent              Install standalone agent (systemd)"
            echo "  --name NAME          Agent name (default: hostname)"
            echo "  --controller-url URL Controller URL for remote agents"
            echo "  --ip IP              Local IP for VXLAN (auto-detected)"
            echo "  --port PORT          Agent port (default: 8001)"
            echo "  --no-libvirt         Disable libvirt/KVM for standalone agents"
            echo "  --uninstall          Remove installation completely"
            echo "  --fresh              Clean reinstall (removes database/volumes)"
            echo "  --branch BRANCH      Install specific branch/tag (default: latest release)"
            echo "  --version            Show installed and latest version"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1 (use --help for usage)"
            exit 1
            ;;
    esac
done

# Resolve branch/tag to install
resolve_install_target() {
    if [ -n "$BRANCH_OVERRIDE" ]; then
        BRANCH="$BRANCH_OVERRIDE"
        log_info "Using specified branch/tag: $BRANCH"
        return
    fi
    LATEST_TAG=$(curl -sS --max-time 10 \
        https://api.github.com/repos/riannom/archetype-iac/releases/latest 2>/dev/null \
        | grep -oP '"tag_name":\s*"\K[^"]+' 2>/dev/null || echo "")
    if [ -n "$LATEST_TAG" ]; then
        BRANCH="$LATEST_TAG"
        log_info "Installing release: $LATEST_TAG"
    else
        BRANCH="main"
        log_warn "Could not fetch latest release, falling back to main branch"
    fi
}

resolve_install_target

# Default: install both if neither specified
if [ "$INSTALL_CONTROLLER" = false ] && [ "$INSTALL_AGENT" = false ]; then
    INSTALL_CONTROLLER=true
    INSTALL_AGENT=true
fi

# Clean up overlay network interfaces
cleanup_overlay() {
    log_info "Cleaning up overlay network interfaces..."
    # Remove any archetype bridges and vxlan interfaces
    for br in $(ip link show 2>/dev/null | grep -oP 'archetype-br-\d+' || true); do
        ip link set "$br" down 2>/dev/null || true
        ip link delete "$br" 2>/dev/null || true
    done
    for vx in $(ip link show 2>/dev/null | grep -oP 'vxlan\d+' || true); do
        ip link delete "$vx" 2>/dev/null || true
    done
    # Clean up any orphaned veth pairs from overlay
    for veth in $(ip link show 2>/dev/null | grep -oP 'v\d+[a-f0-9]+h' || true); do
        ip link delete "$veth" 2>/dev/null || true
    done
}

# Uninstall
if [ "$UNINSTALL" = true ]; then
    log_section "Uninstalling Archetype"

    if [ -d "$INSTALL_DIR" ]; then
        log_info "Stopping controller services..."
        (cd "$INSTALL_DIR" 2>/dev/null && docker compose -f docker-compose.gui.yml down -v 2>/dev/null) || true
        rm -rf $INSTALL_DIR
        log_info "Controller removed (including database volumes)"
    fi

    if systemctl is-active --quiet archetype-agent 2>/dev/null; then
        log_info "Stopping agent service..."
        systemctl stop archetype-agent
        systemctl disable archetype-agent
        rm -f /etc/systemd/system/archetype-agent.service
        systemctl daemon-reload
    fi

    if [ -d "$AGENT_INSTALL_DIR" ]; then
        rm -rf $AGENT_INSTALL_DIR
        log_info "Agent removed"
    fi

    # Clean up agent workspace
    if [ -d "/var/lib/archetype-agent" ]; then
        rm -rf /var/lib/archetype-agent
        log_info "Agent workspace removed"
    fi

    cleanup_overlay

    log_info "Uninstall complete"
    exit 0
fi

# Check root
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo)"
    exit 1
fi

# Fresh install - remove existing and start clean
if [ "$FRESH_INSTALL" = true ]; then
    log_section "Fresh Install Requested"

    if [ -d "$INSTALL_DIR" ]; then
        log_info "Removing existing controller (including database)..."
        (cd "$INSTALL_DIR" 2>/dev/null && docker compose -f docker-compose.gui.yml down -v 2>/dev/null) || true
        rm -rf $INSTALL_DIR
    fi

    if systemctl is-active --quiet archetype-agent 2>/dev/null; then
        log_info "Stopping existing agent..."
        systemctl stop archetype-agent
        systemctl disable archetype-agent 2>/dev/null || true
    fi

    if [ -d "$AGENT_INSTALL_DIR" ]; then
        rm -rf $AGENT_INSTALL_DIR
    fi

    cleanup_overlay
    log_info "Cleaned up previous installation"
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    log_error "Cannot detect OS"
    exit 1
fi

# Auto-detect local IP
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || hostname -I | awk '{print $1}')
fi

# Default agent name
if [ -z "$AGENT_NAME" ]; then
    AGENT_NAME=$(hostname -s)
fi

# Validate: agent-only needs controller URL
if [ "$INSTALL_AGENT" = true ] && [ "$INSTALL_CONTROLLER" = false ] && [ -z "$CONTROLLER_URL" ]; then
    # Check if we can read interactively
    if [ -t 0 ] || [ -e /dev/tty ]; then
        echo ""
        echo "Enter the controller URL (e.g., http://192.168.1.100:8000)"
        echo -n "Controller URL: "
        read CONTROLLER_URL < /dev/tty 2>/dev/null || true
    fi

    if [ -z "$CONTROLLER_URL" ]; then
        log_error "Controller URL is required for standalone agent"
        echo ""
        echo "When installing via curl pipe, use --controller-url:"
        echo ""
        echo "  curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | \\"
        echo "    sudo bash -s -- --agent --controller-url http://CONTROLLER_IP:8000 --name agent-name"
        echo ""
        echo "Or download first, then run interactively:"
        echo ""
        echo "  curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh -o install.sh"
        echo "  sudo bash install.sh --agent"
        echo ""
        exit 1
    fi
fi

# Show what we're installing
log_section "Installation Plan"
echo "OS:              $OS"
echo "Local IP:        $LOCAL_IP"
if [ "$INSTALL_CONTROLLER" = true ]; then
    echo "Controller:      YES (at $INSTALL_DIR)"
fi
if [ "$INSTALL_AGENT" = true ]; then
    echo "Agent:           YES (name: $AGENT_NAME)"
    if [ "$INSTALL_LIBVIRT" = true ]; then
        echo "VM support:      ENABLED (libvirt/KVM)"
    else
        echo "VM support:      DISABLED (--no-libvirt)"
    fi
    if [ "$INSTALL_CONTROLLER" = true ]; then
        echo "Agent connects:  http://localhost:8000 (local controller)"
    else
        echo "Agent connects:  $CONTROLLER_URL"
    fi
fi
echo ""

# Install system dependencies
log_section "Installing Dependencies"

install_docker() {
    if command -v docker &> /dev/null; then
        log_info "Docker already installed"
        return
    fi

    log_info "Installing Docker..."
    case $OS in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq ca-certificates curl gnupg
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            chmod a+r /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
            apt-get update -qq
            apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        centos|rhel|rocky|almalinux|fedora)
            dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null || true
            dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
    esac
    systemctl start docker
    systemctl enable docker
    log_info "Docker installed"
}

install_base_deps() {
    log_info "Installing base dependencies..."
    case $OS in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq git curl iproute2 jq bridge-utils openvswitch-switch openssl
            ;;
        centos|rhel|rocky|almalinux|fedora)
            dnf install -y git curl iproute jq bridge-utils openvswitch openssl
            ;;
    esac

    # Ensure OVS is running
    if systemctl list-unit-files | grep -q openvswitch-switch; then
        systemctl enable --now openvswitch-switch
    elif systemctl list-unit-files | grep -q openvswitch; then
        systemctl enable --now openvswitch
    fi

    # Create Docker plugin directories for OVS network plugin
    mkdir -p /run/docker/plugins /etc/docker/plugins
}

disable_auto_suspend() {
    log_info "Disabling auto-suspend (prevents GDM from suspending headless servers)..."

    # Mask systemd suspend/sleep targets
    systemctl mask suspend.target sleep.target 2>/dev/null || true

    # Configure GDM greeter to never auto-suspend on AC power
    if [ -f /etc/gdm3/greeter.dconf-defaults ]; then
        if grep -q "^sleep-inactive-ac-type=" /etc/gdm3/greeter.dconf-defaults; then
            sed -i "s/^sleep-inactive-ac-type=.*/sleep-inactive-ac-type='nothing'/" /etc/gdm3/greeter.dconf-defaults
        else
            sed -i "s/^# sleep-inactive-ac-type=.*/sleep-inactive-ac-type='nothing'/" /etc/gdm3/greeter.dconf-defaults
        fi
        if grep -q "^sleep-inactive-ac-timeout=" /etc/gdm3/greeter.dconf-defaults; then
            sed -i "s/^sleep-inactive-ac-timeout=.*/sleep-inactive-ac-timeout=0/" /etc/gdm3/greeter.dconf-defaults
        else
            sed -i "s/^# sleep-inactive-ac-timeout=.*/sleep-inactive-ac-timeout=0/" /etc/gdm3/greeter.dconf-defaults
        fi
        dpkg-reconfigure gdm3 2>/dev/null || true
    fi

    log_info "Auto-suspend disabled"
}

install_agent_deps() {
    log_info "Installing agent dependencies..."
    case $OS in
        ubuntu|debian)
            apt-get install -y -qq python3 python3-venv python3-pip
            ;;
        centos|rhel|rocky|almalinux|fedora)
            dnf install -y python3 python3-pip
            ;;
    esac

    if [ "$INSTALL_LIBVIRT" = true ]; then
        log_info "Installing libvirt/KVM dependencies..."
        case $OS in
            ubuntu|debian)
                apt-get install -y -qq qemu-kvm libvirt-daemon-system libvirt-clients \
                    libvirt-dev pkg-config gcc libc-dev qemu-utils
                ;;
            centos|rhel|rocky|almalinux|fedora)
                dnf install -y qemu-kvm libvirt libvirt-client libvirt-devel pkgconfig gcc qemu-img
                ;;
        esac
        systemctl enable --now libvirtd 2>/dev/null || true
        INSTALL_LIBVIRT_PYTHON=true
    fi

    # vrnetlab for building VM images from qcow2
    VRNETLAB_DIR="/opt/vrnetlab"
    if [ ! -d "$VRNETLAB_DIR" ]; then
        log_info "Cloning vrnetlab for VM image building..."
        git clone --depth 1 https://github.com/hellt/vrnetlab.git $VRNETLAB_DIR
    else
        log_info "Updating vrnetlab..."
        cd $VRNETLAB_DIR && git pull
    fi
}

prevent_nbd_lvm_crash() {
    # When qemu-nbd connects a VM disk image (e.g. N9Kv qcow2), udev triggers LVM to
    # auto-activate internal logical volumes. If NBD disconnects, dangling dm devices
    # cause cascading I/O errors that hang the system (D-state processes, SSH failure).
    log_info "Configuring NBD/LVM crash prevention..."

    # Layer 1: LVM global filter - prevent LVM from scanning NBD devices
    if [ -f /etc/lvm/lvm.conf ]; then
        # Clean up broken filter lines from previous installs (literal \t instead of tab)
        sed -i '/^\\t.*global_filter.*nbd/d' /etc/lvm/lvm.conf
        if ! grep -q '^\s*global_filter.*nbd' /etc/lvm/lvm.conf; then
            if grep -q '^\s*# global_filter = ' /etc/lvm/lvm.conf; then
                sed -i $'/^\\s*# global_filter = /a\\\\\tglobal_filter = [ "r|/dev/nbd.*|", "a|.*|" ]' /etc/lvm/lvm.conf
            else
                echo 'devices { global_filter = [ "r|/dev/nbd.*|", "a|.*|" ] }' >> /etc/lvm/lvm.conf
            fi
            # Verify LVM accepts the config
            if lvm dumpconfig devices/global_filter 2>/dev/null | grep -q 'nbd'; then
                log_info "LVM global_filter configured to exclude NBD devices"
            else
                log_warn "LVM global_filter may not be valid, check /etc/lvm/lvm.conf manually"
            fi
        else
            log_info "LVM global_filter already excludes NBD devices"
        fi
    fi

    # Layer 2: Blacklist NBD kernel module
    if [ ! -f /etc/modprobe.d/blacklist-nbd.conf ]; then
        echo "blacklist nbd" > /etc/modprobe.d/blacklist-nbd.conf
        log_info "NBD kernel module blacklisted"
    else
        log_info "NBD kernel module already blacklisted"
    fi

    # Unload NBD module if currently loaded
    if lsmod | grep -q '^nbd '; then
        rmmod nbd 2>/dev/null && log_info "Unloaded nbd kernel module" || log_warn "nbd module in use, will be blocked after reboot"
    fi
}

install_base_deps
install_docker
disable_auto_suspend
prevent_nbd_lvm_crash

# Clone vrnetlab for VM image building (needed by worker container)
VRNETLAB_DIR="/opt/vrnetlab"
if [ ! -d "$VRNETLAB_DIR" ]; then
    log_info "Cloning vrnetlab for VM image building..."
    git clone --depth 1 https://github.com/hellt/vrnetlab.git $VRNETLAB_DIR
else
    log_info "Updating vrnetlab..."
    cd $VRNETLAB_DIR && git pull
fi

# Install Controller
if [ "$INSTALL_CONTROLLER" = true ]; then
    log_section "Installing Controller"

    mkdir -p $INSTALL_DIR

    # Clone/update repo
    if [ -d "$INSTALL_DIR/.git" ]; then
        log_info "Updating repository..."
        cd $INSTALL_DIR
        git fetch origin
        git reset --hard origin/$BRANCH
    else
        log_info "Cloning repository..."
        git clone --branch $BRANCH $REPO_URL $INSTALL_DIR
    fi

    cd $INSTALL_DIR

    # Generate .env if not exists
    if [ ! -f .env ]; then
        log_info "Generating configuration..."
        ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-16)
        JWT_SECRET=$(openssl rand -hex 32)
        SESSION_SECRET=$(openssl rand -hex 32)

        cat > .env << EOF
# Archetype Controller Configuration
# Generated on $(date)

# Admin credentials
ADMIN_EMAIL=admin@localhost
ADMIN_PASSWORD=$ADMIN_PASS

# Security (change in production)
JWT_SECRET=$JWT_SECRET
SESSION_SECRET=$SESSION_SECRET

# Ports
API_PORT=8000
WEB_PORT=8080

# Local agent configuration
ARCHETYPE_AGENT_NAME=local-agent
ARCHETYPE_AGENT_LOCAL_IP=$LOCAL_IP
INTERNAL_URL=http://$LOCAL_IP:8000

# Logging
LOG_FORMAT=json
LOG_LEVEL=INFO
EOF
        chmod 644 .env
        log_info "Generated .env with admin password: $ADMIN_PASS"
    else
        log_info "Using existing .env configuration"
        ADMIN_PASS=$(grep ADMIN_PASSWORD .env | cut -d= -f2)
    fi

    # Start services
    export GIT_SHA=$(cd $INSTALL_DIR && git rev-parse HEAD 2>/dev/null || echo "unknown")
    log_info "Building and starting controller services..."
    log_info "(First run may take several minutes to build containers)"
    docker compose -f docker-compose.gui.yml up -d --build

    # Wait for API
    log_info "Waiting for API to be ready..."
    for i in {1..60}; do
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            break
        fi
        sleep 2
    done

    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        log_info "Controller is running!"

        # Wait for agent to register
        log_info "Waiting for local agent to register..."
        for i in {1..30}; do
            AGENTS=$(curl -s http://localhost:8000/agents 2>/dev/null | jq -r '.[].name' 2>/dev/null || echo "")
            if echo "$AGENTS" | grep -q "local-agent"; then
                log_info "Local agent registered successfully!"
                break
            fi
            sleep 2
        done
    else
        log_warn "Controller may still be starting. Check: docker compose -f docker-compose.gui.yml logs"
    fi
fi

# Install Standalone Agent (when not using embedded agent)
if [ "$INSTALL_AGENT" = true ] && [ "$INSTALL_CONTROLLER" = false ]; then
    log_section "Installing Standalone Agent"

    install_agent_deps

    mkdir -p $AGENT_INSTALL_DIR

    # Clone/update repo
    if [ -d "$AGENT_INSTALL_DIR/repo" ]; then
        log_info "Updating repository..."
        cd $AGENT_INSTALL_DIR/repo
        git fetch origin
        git reset --hard origin/$BRANCH
    else
        log_info "Cloning repository..."
        git clone --branch $BRANCH --depth 1 $REPO_URL $AGENT_INSTALL_DIR/repo
    fi

    # Python venv
    log_info "Setting up Python environment..."
    python3 -m venv $AGENT_INSTALL_DIR/venv
    source $AGENT_INSTALL_DIR/venv/bin/activate
    log_info "Upgrading pip..."
    pip install --upgrade pip 2>&1 | tail -1
    log_info "Installing Python dependencies..."
    pip install --progress-bar off -r $AGENT_INSTALL_DIR/repo/agent/requirements.txt 2>&1 | grep -E "^(Collecting|Installing|Successfully)" || true
    if [ "${INSTALL_LIBVIRT_PYTHON:-false}" = true ]; then
        log_info "Installing libvirt-python..."
        pip install --progress-bar off libvirt-python >/dev/null
    fi
    log_info "Python dependencies installed"

    # Config
    AGENT_COMMIT=$(cd $AGENT_INSTALL_DIR/repo && git rev-parse HEAD 2>/dev/null || echo "unknown")
    cat > $AGENT_INSTALL_DIR/agent.env << EOF
ARCHETYPE_AGENT_AGENT_NAME=$AGENT_NAME
ARCHETYPE_AGENT_CONTROLLER_URL=$CONTROLLER_URL
ARCHETYPE_AGENT_LOCAL_IP=$LOCAL_IP
ARCHETYPE_AGENT_AGENT_PORT=$AGENT_PORT
ARCHETYPE_AGENT_ENABLE_CONTAINERLAB=true
ARCHETYPE_AGENT_ENABLE_DOCKER=true
ARCHETYPE_AGENT_ENABLE_LIBVIRT=$INSTALL_LIBVIRT
ARCHETYPE_AGENT_ENABLE_VXLAN=true
ARCHETYPE_AGENT_WORKSPACE_PATH=/var/lib/archetype-agent
ARCHETYPE_GIT_SHA=$AGENT_COMMIT
EOF
    chmod 644 $AGENT_INSTALL_DIR/agent.env

    echo "$AGENT_COMMIT" > $AGENT_INSTALL_DIR/repo/agent/GIT_SHA

    mkdir -p /var/lib/archetype-agent

    # Systemd service
    cat > /etc/systemd/system/archetype-agent.service << EOF
[Unit]
Description=Archetype Network Lab Agent
After=network.target docker.service libvirtd.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$AGENT_INSTALL_DIR/repo
EnvironmentFile=$AGENT_INSTALL_DIR/agent.env
ExecStart=$AGENT_INSTALL_DIR/venv/bin/python -m agent.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable archetype-agent
    systemctl start archetype-agent

    # Wait and verify
    log_info "Waiting for agent to start..."
    sleep 3
    if systemctl is-active --quiet archetype-agent; then
        log_info "Agent is running!"

        # Verify registration with controller
        log_info "Verifying agent registration..."
        for i in {1..10}; do
            RESPONSE=$(curl -s "${CONTROLLER_URL}/agents" 2>/dev/null || echo "")
            if echo "$RESPONSE" | jq -e ".[] | select(.name == \"$AGENT_NAME\")" > /dev/null 2>&1; then
                log_info "Agent registered with controller successfully!"
                break
            fi
            sleep 2
        done
    else
        log_error "Agent failed to start. Check: journalctl -u archetype-agent -f"
    fi
fi

# Summary
log_section "Installation Complete"

if [ "$INSTALL_CONTROLLER" = true ]; then
    echo -e "${GREEN}Controller:${NC}"
    echo "  Web UI:     http://$LOCAL_IP:8080"
    echo "  API:        http://$LOCAL_IP:8000"
    echo "  Admin:      admin@localhost"
    echo "  Password:   $ADMIN_PASS"
    echo ""
    echo -e "${GREEN}Observability:${NC}"
    echo "  Grafana:    http://$LOCAL_IP:3000"
    echo "  Prometheus: http://$LOCAL_IP:9090"
    echo "  API metrics: http://$LOCAL_IP:8000/metrics"
    echo ""
    echo "  Logs:       cd $INSTALL_DIR && docker compose -f docker-compose.gui.yml logs -f"
    echo "  Restart:    cd $INSTALL_DIR && docker compose -f docker-compose.gui.yml restart"
    echo ""
fi

if [ "$INSTALL_AGENT" = true ] && [ "$INSTALL_CONTROLLER" = false ]; then
    echo -e "${GREEN}Agent:${NC}"
    echo "  Name:       $AGENT_NAME"
    echo "  Controller: $CONTROLLER_URL"
    echo "  Local IP:   $LOCAL_IP"
    if [ "$INSTALL_LIBVIRT" = true ]; then
        echo "  Libvirt:    enabled"
    else
        echo "  Libvirt:    disabled"
    fi
    echo ""
    echo "  Status:     systemctl status archetype-agent"
    echo "  Logs:       journalctl -u archetype-agent -f"
    echo ""
fi

if [ "$INSTALL_CONTROLLER" = true ]; then
    echo -e "${GREEN}Quick Start:${NC}"
    echo "  1. Open http://$LOCAL_IP:8080 in your browser"
    echo "  2. Login with admin@localhost / $ADMIN_PASS"
    echo "  3. Create a lab and import a topology"
    echo ""
    echo -e "${GREEN}Add Remote Agents:${NC}"
    echo "  curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | \\"
    echo "    sudo bash -s -- --agent --controller-url http://$LOCAL_IP:8000 --name agent-name"
    echo ""
    echo -e "${GREEN}Reinstall Fresh (if needed):${NC}"
    echo "  curl -fsSL https://raw.githubusercontent.com/riannom/archetype-iac/main/install.sh | \\"
    echo "    sudo bash -s -- --fresh"
    echo ""
fi
