#!/usr/bin/env bash
# End-to-end test for colcon-systemd.
#
# This script:
#   1. Creates a temporary colcon workspace
#   2. Builds the simple_node test package with colcon build
#   3. Verifies that .service and .sh files were generated at the correct paths
#   4. Validates the .service file content
#   5. Runs the wrapper script and checks the node actually executes
#   6. Starts the node as a daemon, verifies it is running, then stops it
#
# Exit codes: 0 = all checks passed, 1 = a check failed.
# Usage:  bash tests/test_colcon_build_e2e.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$(mktemp -d)"
PKG_NAME="simple_node"
SVC_NAME="simple_node"

cleanup() {
    if [[ -n "${DAEMON_PID:-}" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
    rm -rf "$WORKSPACE"
}
trap cleanup EXIT

passed=0
failed=0

check() {
    local description="$1"
    shift
    if "$@"; then
        echo "  PASS: $description"
        passed=$((passed + 1))
    else
        echo "  FAIL: $description"
        failed=$((failed + 1))
    fi
}

echo "=== colcon-systemd end-to-end build test ==="
echo "Workspace: $WORKSPACE"
echo "Repo:      $REPO_ROOT"
echo ""

# ---------------------------------------------------------------
# Step 1: Set up workspace
# ---------------------------------------------------------------
echo "[1/6] Setting up workspace..."
mkdir -p "$WORKSPACE/src"
cp -r "$REPO_ROOT/test_packages/$PKG_NAME" "$WORKSPACE/src/$PKG_NAME"

# Ensure colcon-systemd is installed
pip install -e "$REPO_ROOT" --quiet 2>&1 | tail -1
# Ensure colcon can build Python packages, discover them recursively,
# and generate setup.bash
pip install colcon-python-setup-py colcon-bash colcon-recursive-crawl --quiet 2>&1 | tail -1

echo "  Workspace ready."

# ---------------------------------------------------------------
# Step 2: colcon build
# ---------------------------------------------------------------
echo "[2/6] Running colcon build..."
cd "$WORKSPACE"
set +e
BUILD_OUTPUT=$(colcon build 2>&1)
BUILD_RC=$?
set -e

check "colcon build exits with rc=0" test "$BUILD_RC" -eq 0

if [ "$BUILD_RC" -ne 0 ]; then
    echo "  Build output:"
    echo "$BUILD_OUTPUT"
    echo ""
    echo "ABORT: colcon build failed, cannot continue."
    exit 1
fi

if echo "$BUILD_OUTPUT" | grep -q "\[colcon-systemd\] Generated"; then
    check "colcon-systemd generated message in build output" true
else
    check "colcon-systemd generated message in build output" false
fi

echo ""

# ---------------------------------------------------------------
# Step 3: Verify generated artifacts exist
# ---------------------------------------------------------------
echo "[3/6] Checking generated artifacts..."

SERVICE_FILE="$WORKSPACE/install/$PKG_NAME/share/colcon-systemd/$SVC_NAME.service"
WRAPPER_FILE="$WORKSPACE/install/$PKG_NAME/share/colcon-systemd/$SVC_NAME.sh"
EXECUTABLE="$WORKSPACE/install/$PKG_NAME/lib/$PKG_NAME/$SVC_NAME"
SETUP_BASH="$WORKSPACE/install/setup.bash"

check ".service file exists"    test -f "$SERVICE_FILE"
check "wrapper .sh file exists" test -f "$WRAPPER_FILE"
check "wrapper .sh is executable" test -x "$WRAPPER_FILE"
check "entry point executable exists" test -f "$EXECUTABLE"
check "setup.bash exists" test -f "$SETUP_BASH"

echo ""

# ---------------------------------------------------------------
# Step 4: Validate .service file content
# ---------------------------------------------------------------
echo "[4/6] Validating .service file content..."

check "[Unit] section present"       grep -q "^\[Unit\]"    "$SERVICE_FILE"
check "[Service] section present"    grep -q "^\[Service\]" "$SERVICE_FILE"
check "[Install] section present"    grep -q "^\[Install\]" "$SERVICE_FILE"
check "Description matches config"   grep -q "Description=Simple test node for colcon build integration testing" "$SERVICE_FILE"
check "ExecStart points to wrapper"  grep -q "ExecStart=$WRAPPER_FILE" "$SERVICE_FILE"
check "Restart=on-failure"           grep -q "Restart=on-failure" "$SERVICE_FILE"
check "Environment directive present" grep -q 'Environment="TEST_ENV_VAR=colcon_systemd_works"' "$SERVICE_FILE"
check "WantedBy=default.target"      grep -q "WantedBy=default.target" "$SERVICE_FILE"

# Validate with systemd-analyze if available
if command -v systemd-analyze &>/dev/null; then
    # systemd-analyze may warn about missing paths; we only care about hard errors
    ANALYZE_OUTPUT=$(systemd-analyze verify --user "$SERVICE_FILE" 2>&1 || true)
    ANALYZE_RC=$?
    check "systemd-analyze verify passes" test "$ANALYZE_RC" -eq 0
fi

echo ""

# ---------------------------------------------------------------
# Step 5: Run the wrapper script (one-shot)
# ---------------------------------------------------------------
echo "[5/6] Running wrapper script (one-shot)..."

RUN_OUTPUT=$("$WRAPPER_FILE" 2>&1)
RUN_RC=$?

check "wrapper script exits with rc=0" test "$RUN_RC" -eq 0
if echo "$RUN_OUTPUT" | grep -q "SIMPLE_NODE_RUNNING"; then
    check "node produces expected output" true
else
    check "node produces expected output" false
fi

echo ""

# ---------------------------------------------------------------
# Step 6: Run as daemon, verify it is running, then stop it
# ---------------------------------------------------------------
echo "[6/6] Running as daemon service..."

DAEMON_OUT="$WORKSPACE/daemon.out"
"$WRAPPER_FILE" --daemon > "$DAEMON_OUT" 2>&1 &
DAEMON_PID=$!

# Wait up to 5 seconds for the daemon to start
DEADLINE=$((SECONDS + 5))
STARTED=false
while [ $SECONDS -lt $DEADLINE ]; do
    if grep -q "SIMPLE_NODE_RUNNING" "$DAEMON_OUT" 2>/dev/null; then
        STARTED=true
        break
    fi
    sleep 0.2
done

check "daemon started within 5 seconds" $STARTED

if $STARTED; then
    # Verify the process is actually running
    check "daemon PID is alive" kill -0 "$DAEMON_PID" 2>/dev/null

    # Wait for heartbeat
    sleep 1
    HEARTBEAT_COUNT=$(grep -c "SIMPLE_NODE_HEARTBEAT" "$DAEMON_OUT" 2>/dev/null || echo 0)
    check "daemon produced heartbeat output (count=$HEARTBEAT_COUNT)" \
        test "$HEARTBEAT_COUNT" -ge 1

    # Send SIGTERM and verify graceful shutdown
    kill "$DAEMON_PID" 2>/dev/null
    wait "$DAEMON_PID" 2>/dev/null || true
    sleep 0.5

    check "daemon handled SIGTERM gracefully" \
        grep -q "SIMPLE_NODE_STOPPED" "$DAEMON_OUT"
fi

# Clear DAEMON_PID so cleanup doesn't try to kill again
unset DAEMON_PID

echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "=== Results: $passed passed, $failed failed ==="

if [ "$failed" -gt 0 ]; then
    exit 1
fi
exit 0
