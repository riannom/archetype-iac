#!/usr/bin/env bats
# Tests for the --version flag output shared by all install/upgrade scripts.
#
# The scripts share a common pattern:
#   CURRENT="none"
#   if [ -f "$PATH/VERSION" ]; then CURRENT=$(cat ...) fi
#   LATEST=$(curl ... | grep ... || echo "unknown")
#   echo "Installed: $CURRENT | Latest: $LATEST"
#
# We replicate that exact pattern here with mocked curl and temp VERSION files.

load helpers

setup() {
    setup_test_env
}

teardown() {
    teardown_test_env
}

# Helper that replicates the version-check pattern from all four scripts.
run_version_check() {
    local version_file="$1"
    CURRENT="none"
    if [ -f "$version_file" ]; then
        CURRENT=$(cat "$version_file")
    fi
    LATEST=$(curl -sS --max-time 10 \
        https://api.github.com/repos/riannom/archetype-iac/releases/latest 2>/dev/null \
        | grep -oP '"tag_name":\s*"\K[^"]+' 2>/dev/null || echo "unknown")
    echo "Installed: $CURRENT | Latest: $LATEST"
}

@test "version: shows installed version when VERSION file is present" {
    echo "0.4.0" > "$TEST_TMPDIR/VERSION"
    mock_curl_response '{"tag_name":"v0.5.0"}'

    run run_version_check "$TEST_TMPDIR/VERSION"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Installed: 0.4.0"* ]]
}

@test "version: shows 'none' when no VERSION file exists" {
    mock_curl_response '{"tag_name":"v0.5.0"}'

    run run_version_check "$TEST_TMPDIR/nonexistent"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Installed: none"* ]]
}

@test "version: shows latest release from GitHub API" {
    mock_curl_response '{"tag_name":"v0.5.0"}'

    run run_version_check "$TEST_TMPDIR/nonexistent"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Latest: v0.5.0"* ]]
}

@test "version: shows 'unknown' when API call fails" {
    mock_curl_exit 28

    run run_version_check "$TEST_TMPDIR/nonexistent"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Latest: unknown"* ]]
}

@test "version: output matches expected format pattern" {
    echo "1.2.3" > "$TEST_TMPDIR/VERSION"
    mock_curl_response '{"tag_name":"v2.0.0"}'

    run run_version_check "$TEST_TMPDIR/VERSION"
    [ "$status" -eq 0 ]
    [[ "$output" =~ Installed:\ .+\ \|\ Latest:\ .+ ]]
}

@test "version: exits with code 0" {
    mock_curl_response '{"tag_name":"v0.4.0"}'

    run run_version_check "$TEST_TMPDIR/nonexistent"
    [ "$status" -eq 0 ]
}
