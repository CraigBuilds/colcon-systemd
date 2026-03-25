#!/usr/bin/env bash
# End-to-end test for colcon-systemd.
#
# This script:
#   1. Creates a temporary colcon workspace
#   2. Builds the simple_node test package with colcon build
#   3. Verifies that .service and .sh files were generated at the correct paths
#   4. Validates the .service file content
#   5. Runs the wrapper script (one-shot) to confirm it sources the environment
#   6. Installs the .service file and starts/stops it via systemctl --user
#
# Exit codes: 0 = all checks passed, 1 = a check failed.
# Usage:  bash tests/test_colcon_build_e2e.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$(mktemp -d)"
PKG_NAME="simple_node"
SVC_NAME="simple_node"
# Use a unique unit name to avoid collisions with any real deployment.
SERVICE_UNIT="colcon_systemd_simple_node_test"
SYSTEMD_USER_PID=""

cleanup() {
    systemctl --user stop "$SERVICE_UNIT" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/${SERVICE_UNIT}.service"
    systemctl --user daemon-reload 2>/dev/null || true
    rm -rf "$WORKSPACE"
    if [[ -n "${SYSTEMD_USER_PID:-}" ]]; then
        kill "$SYSTEMD_USER_PID" 2>/dev/null || true
    fi
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

# Ensure colcon-systemd is installed.
# --break-system-packages is required when running inside the ros:jazzy
# Docker container (Ubuntu 24.04), which enforces PEP 668.  The container
# is a fully isolated test environment, so bypassing the guard is safe.
pip install -e "$REPO_ROOT" --quiet --break-system-packages 2>&1 | tail -1
# Ensure colcon can build Python packages, discover them recursively,
# and generate setup.bash
pip install colcon-python-setup-py colcon-bash colcon-recursive-crawl \
    --quiet --break-system-packages 2>&1 | tail -1

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
    if ANALYZE_OUTPUT=$(systemd-analyze verify --user "$SERVICE_FILE" 2>&1); then
        ANALYZE_RC=0
    else
        ANALYZE_RC=$?
    fi
    check "systemd-analyze verify passes" test "$ANALYZE_RC" -eq 0
fi

echo ""

# ---------------------------------------------------------------
# Step 5: Run the wrapper script (one-shot) to validate environment
# ---------------------------------------------------------------
echo "[5/6] Running wrapper script (one-shot)..."

RUN_OUTPUT=$("$WRAPPER_FILE" --once 2>&1)
RUN_RC=$?

check "wrapper script exits with rc=0" test "$RUN_RC" -eq 0
if echo "$RUN_OUTPUT" | grep -q "SIMPLE_NODE_RUNNING"; then
    check "node produces expected output" true
else
    check "node produces expected output" false
fi

echo ""

# ---------------------------------------------------------------
# Step 6: Start via systemctl --user, verify running, then stop
# ---------------------------------------------------------------
echo "[6/6] Starting .service via systemctl --user..."

# Ensure a user systemd session is available.
# In container/CI environments the session may not be started automatically;
# in that case we start D-Bus and systemd --user ourselves.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR"

SYSTEMD_STATE=$(systemctl --user is-system-running 2>/dev/null || true)
if [[ ! "$SYSTEMD_STATE" =~ ^(running|degraded)$ ]]; then
    echo "  User systemd not running — starting session..."
    if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
        /usr/bin/dbus-daemon --session \
            --address="unix:path=$XDG_RUNTIME_DIR/bus" \
            --fork --nopidfile 2>/dev/null || true
        sleep 0.3
    fi
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
    /usr/lib/systemd/systemd --user 2>/dev/null &
    SYSTEMD_USER_PID=$!
    DEADLINE=$((SECONDS + 10))
    until [[ $(systemctl --user is-system-running 2>/dev/null || true) =~ ^(running|degraded)$ ]]; do
        if [[ $SECONDS -ge $DEADLINE ]]; then
            echo "ABORT: could not start user systemd session within 10 s"
            exit 1
        fi
        sleep 0.5
    done
    echo "  User systemd session started."
fi

# Install the generated .service file under a test-specific unit name and start it.
USER_UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_UNIT_DIR"
cp "$SERVICE_FILE" "$USER_UNIT_DIR/${SERVICE_UNIT}.service"
systemctl --user daemon-reload
systemctl --user start "${SERVICE_UNIT}.service"

check "service started (systemctl --user start)" true

# Wait up to 5 s for the service to become active
DEADLINE=$((SECONDS + 5))
ACTIVE=false
while [[ $SECONDS -lt $DEADLINE ]]; do
    if [[ "$(systemctl --user is-active "${SERVICE_UNIT}" 2>/dev/null || true)" == "active" ]]; then
        ACTIVE=true
        break
    fi
    sleep 0.2
done

check "service is active after start" $ACTIVE

if $ACTIVE; then
    # Confirm the underlying process is actually running by checking its MainPID
    MAIN_PID=$(systemctl --user show -p MainPID --value "${SERVICE_UNIT}" 2>/dev/null || echo 0)
    check "service MainPID is non-zero" test "${MAIN_PID:-0}" -gt 0
    if [[ "${MAIN_PID:-0}" -gt 0 ]]; then
        check "service process is alive" kill -0 "$MAIN_PID" 2>/dev/null
    fi

    # Wait a moment for heartbeat output, then stop the service
    sleep 1

    systemctl --user stop "${SERVICE_UNIT}.service"

    # Wait up to 5 s for the service to become inactive
    DEADLINE=$((SECONDS + 5))
    STOPPED=false
    while [[ $SECONDS -lt $DEADLINE ]]; do
        STATE=$(systemctl --user is-active "${SERVICE_UNIT}" 2>/dev/null || true)
        if [[ "$STATE" != "active" ]]; then
            STOPPED=true
            break
        fi
        sleep 0.2
    done

    check "service stopped cleanly (systemctl --user stop)" $STOPPED
fi

echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "=== Results: $passed passed, $failed failed ==="

if [ "$failed" -gt 0 ]; then
    exit 1
fi
exit 0
