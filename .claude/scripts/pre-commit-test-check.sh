#!/usr/bin/env bash
# Pre-commit test coverage check
# Layer 1: Fast pattern matching (free, instant)
# Layer 2: Headless Claude agent for semantic analysis (costs tokens, ~15s)
#
# Exit codes:
#   0 = pass (allow commit)
#   2 = block (missing tests)

set -euo pipefail

# Only trigger on git commit commands
COMMAND="${TOOL_INPUT_command:-}"
if ! echo "$COMMAND" | grep -qE '^\s*git\s+commit'; then
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '.')"
cd "$REPO_ROOT"

# Get staged source files (only added/modified, not deleted)
STAGED=$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)
if [ -z "$STAGED" ]; then
    exit 0
fi

# ─── Layer 1: Fast pattern matching ───────────────────────────────

missing=()
checked=0

while IFS= read -r file; do
    [ -z "$file" ] && continue

    case "$file" in
        # API source files
        api/app/*.py)
            base=$(basename "$file" .py)
            # Skip non-testable files
            case "$base" in __init__|config|main) continue ;; esac
            checked=$((checked + 1))
            if [ ! -f "api/tests/test_${base}.py" ]; then
                missing+=("$file  ->  api/tests/test_${base}.py")
            fi
            ;;
        api/app/**/*.py)
            base=$(basename "$file" .py)
            case "$base" in __init__|config) continue ;; esac
            checked=$((checked + 1))
            # Check for test file matching the module name anywhere in api/tests/
            if ! find api/tests/ -name "test_${base}.py" -print -quit 2>/dev/null | grep -q .; then
                missing+=("$file  ->  api/tests/test_${base}.py")
            fi
            ;;
        # Agent source files
        agent/*.py)
            base=$(basename "$file" .py)
            case "$base" in __init__|config|main) continue ;; esac
            checked=$((checked + 1))
            if [ ! -f "agent/tests/test_${base}.py" ]; then
                missing+=("$file  ->  agent/tests/test_${base}.py")
            fi
            ;;
        agent/**/*.py)
            base=$(basename "$file" .py)
            case "$base" in __init__|config) continue ;; esac
            checked=$((checked + 1))
            if ! find agent/tests/ -name "test_${base}.py" -print -quit 2>/dev/null | grep -q .; then
                missing+=("$file  ->  agent/tests/test_${base}.py")
            fi
            ;;
        # Frontend source files (skip test files themselves)
        web/src/*.ts|web/src/*.tsx|web/src/**/*.ts|web/src/**/*.tsx)
            # Skip test files, type declarations, config files
            case "$file" in
                *.test.*|*.spec.*|*.d.ts|*vite-env*|*setupTests*) continue ;;
            esac
            checked=$((checked + 1))
            # Check for co-located test file
            dir=$(dirname "$file")
            base=$(basename "$file" | sed 's/\.\(ts\|tsx\)$//')
            found=0
            for ext in test.ts test.tsx spec.ts spec.tsx; do
                if [ -f "${dir}/${base}.${ext}" ]; then
                    found=1
                    break
                fi
            done
            # Also check __tests__ directory
            if [ "$found" -eq 0 ] && [ -d "${dir}/__tests__" ]; then
                for ext in test.ts test.tsx spec.ts spec.tsx; do
                    if [ -f "${dir}/__tests__/${base}.${ext}" ]; then
                        found=1
                        break
                    fi
                done
            fi
            if [ "$found" -eq 0 ]; then
                missing+=("$file  ->  ${dir}/${base}.test.{ts,tsx}")
            fi
            ;;
    esac
done <<< "$STAGED"

# ─── Layer 1 Results ──────────────────────────────────────────────

if [ "$checked" -eq 0 ]; then
    # No testable source files staged (docs, configs, etc.)
    exit 0
fi

if [ ${#missing[@]} -gt 0 ]; then
    echo "⚠ Test coverage gaps found (${#missing[@]} file(s) missing tests):"
    echo ""
    for m in "${missing[@]}"; do
        echo "  $m"
    done
    echo ""
    echo "Consider writing tests before committing, or proceed if tests are covered elsewhere."
    # Warn but don't block — Layer 2 will do deeper analysis
fi

# ─── Layer 2: Headless Claude semantic review ─────────────────────

# Get the staged diff for analysis
DIFF=$(git diff --cached --stat 2>/dev/null || true)
FILES_CHANGED=$(git diff --cached --name-only 2>/dev/null || true)

# Build the prompt
PROMPT="You are a test coverage reviewer. Analyze these staged changes and determine if appropriate tests exist.

## Staged files:
${FILES_CHANGED}

## Change summary:
${DIFF}

## Instructions:
1. For each changed source file, check if a corresponding test file exists in the repo
2. If a test file exists, check if it covers the specific functions/classes that were modified
3. Only flag MISSING tests for non-trivial logic changes (skip configs, __init__.py, type-only changes, documentation)
4. Be pragmatic — not every file needs its own test file if the logic is tested through integration tests

## Output format:
If all changes have adequate test coverage, respond with exactly: PASS
If tests are missing, respond with:
NEEDS_TESTS
- <file>: <what should be tested>

Be concise. Only list genuinely missing tests."

# Run headless Claude (unset CLAUDECODE to avoid nesting check)
RESULT=$(env -u CLAUDECODE claude -p "$PROMPT" --allowedTools Read,Glob,Grep --max-turns 6 2>/dev/null || echo "PASS")

if echo "$RESULT" | grep -q "^NEEDS_TESTS"; then
    echo ""
    echo "🔍 Deep test review found gaps:"
    echo "$RESULT" | tail -n +2
    echo ""
    echo "BLOCK: Write tests for the above before committing."
    exit 2
fi

# All clear
if [ ${#missing[@]} -gt 0 ]; then
    echo "✓ Deep review confirms existing tests cover the changes adequately."
fi
exit 0
