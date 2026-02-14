#!/usr/bin/env bash
# Install gitleaks binary from GitHub releases.
# Override version: GITLEAKS_VERSION=8.22.1 ./scripts/install-gitleaks.sh
set -euo pipefail

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.22.1}"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

if command -v gitleaks &>/dev/null; then
    installed=$(gitleaks version 2>/dev/null || echo "unknown")
    echo "gitleaks already installed: ${installed}"
    exit 0
fi

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *)
        echo "Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

case "$OS" in
    linux)  EXT="tar.gz" ;;
    darwin) EXT="tar.gz" ;;
    *)
        echo "Unsupported OS: $OS"
        exit 1
        ;;
esac

URL="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${OS}_${ARCH}.tar.gz"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "Downloading gitleaks v${GITLEAKS_VERSION} for ${OS}/${ARCH}..."
curl -fsSL "$URL" -o "${TMP}/gitleaks.tar.gz"
tar -xzf "${TMP}/gitleaks.tar.gz" -C "$TMP"

if [ -w "$INSTALL_DIR" ]; then
    mv "${TMP}/gitleaks" "${INSTALL_DIR}/gitleaks"
else
    sudo mv "${TMP}/gitleaks" "${INSTALL_DIR}/gitleaks"
fi
chmod +x "${INSTALL_DIR}/gitleaks"

echo "gitleaks v${GITLEAKS_VERSION} installed to ${INSTALL_DIR}/gitleaks"
