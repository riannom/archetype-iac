#!/usr/bin/env bash
#
# Archetype Upgrade Script
#
# This script automates the upgrade process:
# 1. Creates a backup of the database and workspaces
# 2. Pulls the latest changes from git
# 3. Runs database migrations
# 4. Rebuilds and restarts containers
# 5. Verifies health
#
# Usage:
#   ./scripts/upgrade.sh [options]
#
# Options:
#   --skip-backup    Skip the backup step
#   --no-pull        Don't pull from git (use local changes)
#   --branch NAME    Pull from specific branch (default: main)
#   --help           Show this help message
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_FILE="$ROOT_DIR/docker-compose.gui.yml"
BACKUP_SCRIPT="$ROOT_DIR/scripts/backup.sh"
BRANCH=""
SKIP_BACKUP=false
NO_PULL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-backup)
            SKIP_BACKUP=true
            shift
            ;;
        --no-pull)
            NO_PULL=true
            shift
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --tag)
            BRANCH="$2"
            shift 2
            ;;
        --version)
            CURRENT="unknown"
            if [[ -f "$ROOT_DIR/VERSION" ]]; then
                CURRENT=$(cat "$ROOT_DIR/VERSION")
            fi
            LATEST=$(curl -sS --max-time 10 \
                https://api.github.com/repos/riannom/archetype-iac/releases/latest 2>/dev/null \
                | grep -oP '"tag_name":\s*"\K[^"]+' 2>/dev/null || echo "unknown")
            echo "Installed: $CURRENT | Latest: $LATEST"
            exit 0
            ;;
        --help|-h)
            echo "Archetype Upgrade Script"
            echo ""
            echo "Usage: ./scripts/upgrade.sh [options]"
            echo ""
            echo "Options:"
            echo "  --skip-backup    Skip the backup step"
            echo "  --no-pull        Don't pull from git (use local changes)"
            echo "  --branch NAME    Pull from specific branch (default: main)"
            echo "  --tag TAG            Upgrade to specific release tag (e.g., v0.4.0)"
            echo "  --version            Show installed and latest version"
            echo "  --help           Show this help message"
            echo ""
            echo "This script will:"
            echo "  1. Create a backup of the database and workspaces"
            echo "  2. Pull the latest release (or specified branch/tag)"
            echo "  3. Run database migrations"
            echo "  4. Rebuild and restart containers"
            echo "  5. Verify health"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Resolve upgrade target (latest release by default)
resolve_upgrade_target() {
    if [[ -n "$BRANCH" ]]; then
        # Already set via --branch or --tag
        return
    fi
    LATEST_TAG=$(curl -sS --max-time 10 \
        https://api.github.com/repos/riannom/archetype-iac/releases/latest 2>/dev/null \
        | grep -oP '"tag_name":\s*"\K[^"]+' 2>/dev/null || echo "")
    if [[ -n "$LATEST_TAG" ]]; then
        BRANCH="$LATEST_TAG"
        echo -e "${GREEN}Upgrading to release: $LATEST_TAG${NC}"
    else
        BRANCH="main"
        echo -e "${YELLOW}Could not fetch latest release, falling back to main branch${NC}"
    fi
}

resolve_upgrade_target

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Archetype Upgrade Script                         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if we're in the right directory
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo -e "${RED}Error: docker-compose.gui.yml not found.${NC}"
    echo "Please run this script from the archetype-iac repository root."
    exit 1
fi

# Get current version
CURRENT_VERSION="unknown"
if [[ -f "$ROOT_DIR/VERSION" ]]; then
    CURRENT_VERSION=$(cat "$ROOT_DIR/VERSION")
fi
echo -e "${BLUE}Current version:${NC} $CURRENT_VERSION"
echo ""

# Step 1: Backup
if [[ "$SKIP_BACKUP" == "false" ]]; then
    echo -e "${YELLOW}Step 1/5: Creating backup...${NC}"
    if [[ -x "$BACKUP_SCRIPT" ]]; then
        "$BACKUP_SCRIPT"
        echo -e "${GREEN}✓ Backup completed${NC}"
    else
        echo -e "${YELLOW}⚠ Backup script not found or not executable, skipping...${NC}"
    fi
else
    echo -e "${YELLOW}Step 1/5: Skipping backup (--skip-backup)${NC}"
fi
echo ""

# Step 2: Pull latest changes
if [[ "$NO_PULL" == "false" ]]; then
    echo -e "${YELLOW}Step 2/5: Pulling latest changes from $BRANCH...${NC}"

    # Check for uncommitted changes
    if ! git -C "$ROOT_DIR" diff --quiet 2>/dev/null; then
        echo -e "${YELLOW}⚠ You have uncommitted changes. Stashing them...${NC}"
        git -C "$ROOT_DIR" stash push -m "archetype-upgrade-$(date +%Y%m%d%H%M%S)"
    fi

    # Fetch and pull
    git -C "$ROOT_DIR" fetch origin
    git -C "$ROOT_DIR" checkout "$BRANCH"
    git -C "$ROOT_DIR" pull origin "$BRANCH"

    echo -e "${GREEN}✓ Git pull completed${NC}"
else
    echo -e "${YELLOW}Step 2/5: Skipping git pull (--no-pull)${NC}"
fi
echo ""

# Get new version
NEW_VERSION="unknown"
if [[ -f "$ROOT_DIR/VERSION" ]]; then
    NEW_VERSION=$(cat "$ROOT_DIR/VERSION")
fi
echo -e "${BLUE}New version:${NC} $NEW_VERSION"
echo ""

# Step 3: Run database migrations
echo -e "${YELLOW}Step 3/5: Running database migrations...${NC}"

# Check if the API container is running
if docker compose -f "$COMPOSE_FILE" ps api --status running -q 2>/dev/null | grep -q .; then
    docker compose -f "$COMPOSE_FILE" exec -T api alembic upgrade head
    echo -e "${GREEN}✓ Migrations completed${NC}"
else
    echo -e "${YELLOW}⚠ API container not running, migrations will run on startup${NC}"
fi
echo ""

# Step 4: Rebuild and restart containers
echo -e "${YELLOW}Step 4/5: Rebuilding and restarting containers...${NC}"
docker compose -f "$COMPOSE_FILE" up -d --build
echo -e "${GREEN}✓ Containers rebuilt and restarted${NC}"
echo ""

# Step 5: Health check
echo -e "${YELLOW}Step 5/5: Verifying health...${NC}"

# Wait for API to be ready
MAX_RETRIES=30
RETRY_COUNT=0
API_READY=false

echo -n "Waiting for API to be ready"
while [[ $RETRY_COUNT -lt $MAX_RETRIES ]]; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        API_READY=true
        break
    fi
    echo -n "."
    sleep 2
    ((RETRY_COUNT++))
done
echo ""

if [[ "$API_READY" == "true" ]]; then
    echo -e "${GREEN}✓ API is healthy${NC}"
else
    echo -e "${RED}✗ API health check failed after ${MAX_RETRIES} retries${NC}"
    echo "Check logs with: docker compose -f docker-compose.gui.yml logs api"
    exit 1
fi

# Check web frontend
if curl -sf http://localhost:8090 > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Web frontend is accessible${NC}"
else
    echo -e "${YELLOW}⚠ Web frontend not responding on port 8090${NC}"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Upgrade completed successfully!                  ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Upgraded from ${BLUE}$CURRENT_VERSION${NC} to ${GREEN}$NEW_VERSION${NC}"
echo ""
echo "If you encounter issues, restore from backup with:"
echo "  ./scripts/restore.sh"
