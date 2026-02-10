#!/usr/bin/env bash
set -euo pipefail

# Bump version across all locations in the repository.
# Usage: ./scripts/bump-version.sh <version>
# Example: ./scripts/bump-version.sh 0.4.0

VERSION="$1"
if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.4.0"
    exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# 1. VERSION file (single source of truth)
echo "$VERSION" > "$ROOT_DIR/VERSION"

# 2. web/package.json
if command -v jq &> /dev/null; then
    jq --arg v "$VERSION" '.version = $v' "$ROOT_DIR/web/package.json" > "$ROOT_DIR/web/package.json.tmp" \
        && mv "$ROOT_DIR/web/package.json.tmp" "$ROOT_DIR/web/package.json"
else
    # Fallback: sed-based replacement
    sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION\"/" "$ROOT_DIR/web/package.json"
fi

# 3. api/app/main.py FastAPI version parameter
sed -i "s/version=\"[^\"]*\"/version=\"$VERSION\"/" "$ROOT_DIR/api/app/main.py"

echo "Version bumped to $VERSION"
echo "  VERSION:          $(cat "$ROOT_DIR/VERSION")"
echo "  web/package.json: $(grep '"version"' "$ROOT_DIR/web/package.json" | head -1 | tr -d ' ,')"
echo "  api/app/main.py:  $(grep 'version=' "$ROOT_DIR/api/app/main.py" | head -1 | tr -d ' ')"
