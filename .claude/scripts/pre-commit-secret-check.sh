#!/usr/bin/env bash
# Claude Code pre-commit hook: scan staged changes for secrets with gitleaks.
# Runs BEFORE the test coverage check for fast feedback.
#
# Exit codes:
#   0 = pass (allow commit)
#   2 = block (secrets found)

set -euo pipefail

# Only trigger on git commit commands
COMMAND="${TOOL_INPUT_command:-}"
if ! echo "$COMMAND" | grep -qE '^\s*git\s+commit'; then
    exit 0
fi

# Require gitleaks
if ! command -v gitleaks &>/dev/null; then
    echo "Warning: gitleaks not installed. Run 'make install-gitleaks' for local secret scanning."
    echo "CI will enforce secret scanning on push."
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '.')"

# Check staged changes for secrets
if ! gitleaks protect --staged --config "${REPO_ROOT}/.gitleaks.toml" 2>&1; then
    echo ""
    echo "BLOCK: Secrets detected in staged changes."
    echo "Remove the secret, then stage and commit again."
    exit 2
fi

exit 0
