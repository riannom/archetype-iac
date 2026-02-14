#!/usr/bin/env bash
# Install git pre-commit hook for secret scanning.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
HOOK_FILE="${REPO_ROOT}/.git/hooks/pre-commit"

if [ -f "$HOOK_FILE" ]; then
    if grep -q 'gitleaks' "$HOOK_FILE"; then
        echo "pre-commit hook already contains gitleaks check."
        exit 0
    fi
    echo "Existing pre-commit hook found. Appending gitleaks check..."
    cat >> "$HOOK_FILE" << 'HOOK'

# --- gitleaks secret scan ---
if command -v gitleaks &>/dev/null; then
    gitleaks protect --staged --config "${0%/*}/../../.gitleaks.toml" --verbose
    if [ $? -ne 0 ]; then
        echo "gitleaks: secrets detected in staged changes. Commit blocked."
        echo "Fix the issue or use --no-verify to bypass (CI will still catch it)."
        exit 1
    fi
else
    echo "Warning: gitleaks not installed. Run 'make install-gitleaks' for local secret scanning."
    echo "CI will enforce secret scanning on push."
fi
HOOK
else
    cat > "$HOOK_FILE" << 'HOOK'
#!/usr/bin/env bash
# Pre-commit hook: secret scanning with gitleaks
set -euo pipefail

if command -v gitleaks &>/dev/null; then
    gitleaks protect --staged --config "${0%/*}/../../.gitleaks.toml" --verbose
    if [ $? -ne 0 ]; then
        echo "gitleaks: secrets detected in staged changes. Commit blocked."
        echo "Fix the issue or use --no-verify to bypass (CI will still catch it)."
        exit 1
    fi
else
    echo "Warning: gitleaks not installed. Run 'make install-gitleaks' for local secret scanning."
    echo "CI will enforce secret scanning on push."
fi
HOOK
fi

chmod +x "$HOOK_FILE"
echo "Pre-commit hook installed at ${HOOK_FILE}"
