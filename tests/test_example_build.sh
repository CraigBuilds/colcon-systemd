#!/usr/bin/env bash
# Test that colcon-systemd builds the example my_node package and verifies that
# the generated .service file can be installed as a real user systemd service
# that publishes ROS 2 messages.
#
# Usage:  bash tests/test_example_build.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$(mktemp -d)"

# Unit name used when installing to ~/.config/systemd/user/
# Using a test-specific name avoids collisions with a real my_node deployment.
SERVICE_UNIT="colcon_systemd_my_node_test"
SYSTEMD_USER_PID=""

cleanup() {
    systemctl --user stop "$SERVICE_UNIT" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/${SERVICE_UNIT}.service"
    systemctl --user daemon-reload 2>/dev/null || true
    rm -rf "$WORKSPACE"
    # Stop the user systemd session we started (container environments)
    if [[ -n "${SYSTEMD_USER_PID:-}" ]]; then
        kill "$SYSTEMD_USER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "=== colcon-systemd example build test ==="

# ---------------------------------------------------------------
# Step 1: Set up workspace
# ---------------------------------------------------------------
echo "[1/5] Setting up workspace..."
mkdir -p "$WORKSPACE/src"
cp -r "$REPO_ROOT/examples/my_node" "$WORKSPACE/src/my_node"

# Install colcon-systemd and the colcon extensions it needs.
# --break-system-packages is required when running inside the ros:jazzy
# Docker container (Ubuntu 24.04), which enforces PEP 668.  The container
# is a fully isolated test environment, so bypassing the guard is safe.
pip install -e "$REPO_ROOT" --quiet --break-system-packages 2>&1 | tail -1
pip install colcon-python-setup-py colcon-bash colcon-recursive-crawl colcon-ros \
    --quiet --break-system-packages 2>&1 | tail -1

# ROS 2 is required for this test.  Detect the installation directory.
# The glob expands in alphabetical order; override by setting $ROS_SETUP.
if [[ -z "${ROS_SETUP:-}" ]]; then
    for distro_dir in /opt/ros/*/; do
        if [[ -f "${distro_dir}setup.bash" ]]; then
            ROS_SETUP="${distro_dir}setup.bash"
            break
        fi
    done
fi

if [[ -z "${ROS_SETUP:-}" ]]; then
    echo "FAIL: ROS 2 not found in /opt/ros/ — ROS 2 is required for this test"
    exit 1
fi

echo "  Found ROS 2: $ROS_SETUP"
# Source ROS so that colcon embeds the ROS underlay into setup.bash
# shellcheck source=/dev/null
source "$ROS_SETUP"

# Ensure a user systemd session is available (needed for systemctl --user).
# In Docker/CI container environments the session is not started automatically;
# in that case we start D-Bus and systemd --user ourselves.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR"

# systemctl is-system-running exits non-zero even for "degraded" (units failed
# but the manager itself is running), so we capture the text instead of relying
# on the exit code to avoid pipefail false-positives.
SYSTEMD_STATE=$(systemctl --user is-system-running 2>/dev/null || true)
if [[ ! "$SYSTEMD_STATE" =~ ^(running|degraded)$ ]]; then
    echo "  User systemd not running — starting session..."
    # Start a D-Bus session bus if the socket doesn't exist yet.
    if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
        /usr/bin/dbus-daemon --session \
            --address="unix:path=$XDG_RUNTIME_DIR/bus" \
            --fork --nopidfile 2>/dev/null || true
        # Wait for the socket file to appear before continuing.
        sleep 0.3
    fi
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
    /usr/lib/systemd/systemd --user 2>/dev/null &
    SYSTEMD_USER_PID=$!
    DEADLINE=$((SECONDS + 10))
    until [[ $(systemctl --user is-system-running 2>/dev/null || true) =~ ^(running|degraded)$ ]]; do
        if [[ $SECONDS -ge $DEADLINE ]]; then
            echo "FAIL: could not start user systemd session within 10 s"
            exit 1
        fi
        # Poll at 500 ms intervals — fast enough to detect startup quickly
        # without hammering systemctl.
        sleep 0.5
    done
    echo "  User systemd session started."
fi

echo "  Workspace ready."

# ---------------------------------------------------------------
# Step 2: colcon build
# ---------------------------------------------------------------
echo "[2/5] Running colcon build..."
cd "$WORKSPACE"
colcon build

SERVICE="$WORKSPACE/install/my_node/share/colcon-systemd/my_node.service"
WRAPPER="$WORKSPACE/install/my_node/share/colcon-systemd/my_node.sh"

test -f "$SERVICE" || { echo "FAIL: $SERVICE not found"; exit 1; }
test -f "$WRAPPER" || { echo "FAIL: $WRAPPER not found"; exit 1; }
test -x "$WRAPPER" || { echo "FAIL: $WRAPPER is not executable"; exit 1; }

echo "  PASS: .service and .sh files generated"

# ---------------------------------------------------------------
# Step 3: Validate .service file content
# ---------------------------------------------------------------
echo "[3/5] Validating .service file content..."

grep -q "^\[Unit\]"          "$SERVICE" || { echo "FAIL: [Unit] missing";    exit 1; }
grep -q "^\[Service\]"       "$SERVICE" || { echo "FAIL: [Service] missing"; exit 1; }
grep -q "^\[Install\]"       "$SERVICE" || { echo "FAIL: [Install] missing"; exit 1; }
grep -q "ExecStart=$WRAPPER" "$SERVICE" || { echo "FAIL: ExecStart missing"; exit 1; }
grep -q "Restart=on-failure" "$SERVICE" || { echo "FAIL: Restart missing";   exit 1; }

echo "  PASS: .service file content is valid"

# ---------------------------------------------------------------
# Step 4: Install .service and start via systemctl --user
# ---------------------------------------------------------------
echo "[4/5] Installing and starting service via systemd..."

USER_UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_UNIT_DIR"

# Install the generated .service file to the user unit directory and reload.
# This is the same flow a user would follow: copy unit → daemon-reload → start.
cp "$SERVICE" "$USER_UNIT_DIR/${SERVICE_UNIT}.service"
systemctl --user daemon-reload
systemctl --user start "${SERVICE_UNIT}.service"

echo "  Service started via systemctl --user."

# Resolve the ros2 CLI binary.
if command -v ros2 &>/dev/null; then
    ROS2_BIN="ros2"
else
    ROS2_BIN="$(dirname "$ROS_SETUP")/bin/ros2"
fi

TOPIC="/my_node/chatter"

# Wait up to 15 s for the node to appear on the ROS graph.
NODE_READY=false
DEADLINE=$((SECONDS + 15))
while [[ $SECONDS -lt $DEADLINE ]]; do
    if "$ROS2_BIN" node list 2>/dev/null | grep -q "my_node"; then
        NODE_READY=true
        break
    fi
    sleep 0.5
done

if ! $NODE_READY; then
    echo "FAIL: my_node did not appear on the ROS graph within 15 seconds"
    echo "--- systemctl status ---"
    systemctl --user status "${SERVICE_UNIT}.service" 2>&1 || true
    exit 1
fi

echo "  Node is visible on the ROS graph."

# ---------------------------------------------------------------
# Step 5: Verify the node is publishing messages
# ---------------------------------------------------------------
echo "[5/5] Verifying ROS 2 message publishing..."

# Allow DDS discovery to propagate: the publisher started < 1 s ago and the
# subscriber (ros2 topic echo) needs to locate it via the RMW discovery layer.
sleep 0.5

ECHO_OUT=$(
    "$ROS2_BIN" topic echo "$TOPIC" "std_msgs/msg/String" --once \
        2>/dev/null
)

if echo "$ECHO_OUT" | grep -q "^data:"; then
    echo "  PASS: received ROS 2 message: $(echo "$ECHO_OUT" | grep '^data:' | head -1)"
else
    echo "FAIL: ros2 topic echo received no message on $TOPIC"
    echo "--- echo output ---"
    echo "$ECHO_OUT"
    echo "--- systemctl status ---"
    systemctl --user status "${SERVICE_UNIT}.service" 2>&1 || true
    exit 1
fi

echo ""
echo "=== PASS: all checks passed ==="

