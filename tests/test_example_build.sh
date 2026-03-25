#!/usr/bin/env bash
# Test that colcon-systemd builds the example my_node package and verifies that
# the generated wrapper starts a real ROS 2 publisher that publishes messages.
#
# Usage:  bash tests/test_example_build.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$(mktemp -d)"

# Track background publisher PID for cleanup
PUBLISHER_PID=""

cleanup() {
    if [[ -n "${PUBLISHER_PID:-}" ]] && kill -0 "$PUBLISHER_PID" 2>/dev/null; then
        kill "$PUBLISHER_PID" 2>/dev/null || true
        wait "$PUBLISHER_PID" 2>/dev/null || true
    fi
    rm -rf "$WORKSPACE"
}
trap cleanup EXIT

echo "=== colcon-systemd example build test ==="

# ---------------------------------------------------------------
# Step 1: Set up workspace
# ---------------------------------------------------------------
echo "[1/4] Setting up workspace..."
mkdir -p "$WORKSPACE/src"
cp -r "$REPO_ROOT/examples/my_node" "$WORKSPACE/src/my_node"

# Install colcon-systemd and the colcon extensions it needs.
# --break-system-packages is required when running inside the ros:jazzy
# Docker container (Ubuntu 24.04), which enforces PEP 668.  The container
# is a fully isolated test environment, so bypassing the guard is safe.
pip install -e "$REPO_ROOT" --quiet --break-system-packages 2>&1 | tail -1
pip install colcon-python-setup-py colcon-bash colcon-recursive-crawl \
    --quiet --break-system-packages 2>&1 | tail -1

# Detect ROS 2 installation.  The glob expands in alphabetical order so the
# first match is the lexicographically lowest distro name (e.g. "humble"
# before "jazzy").  Override by setting ROS_SETUP in the environment before
# calling this script.
if [[ -z "${ROS_SETUP:-}" ]]; then
    for distro_dir in /opt/ros/*/; do
        if [[ -f "${distro_dir}setup.bash" ]]; then
            ROS_SETUP="${distro_dir}setup.bash"
            break
        fi
    done
fi

if [[ -n "$ROS_SETUP" ]]; then
    echo "  Found ROS 2: $ROS_SETUP"
    # Source ROS so that colcon embeds the ROS underlay into setup.bash
    # shellcheck source=/dev/null
    source "$ROS_SETUP"
else
    echo "  ROS 2 not found — skipping publisher verification step"
fi

echo "  Workspace ready."

# ---------------------------------------------------------------
# Step 2: colcon build
# ---------------------------------------------------------------
echo "[2/4] Running colcon build..."
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
echo "[3/4] Validating .service file content..."

grep -q "^\[Unit\]"                      "$SERVICE" || { echo "FAIL: [Unit] missing";    exit 1; }
grep -q "^\[Service\]"                   "$SERVICE" || { echo "FAIL: [Service] missing"; exit 1; }
grep -q "^\[Install\]"                   "$SERVICE" || { echo "FAIL: [Install] missing"; exit 1; }
grep -q "ExecStart=$WRAPPER"             "$SERVICE" || { echo "FAIL: ExecStart missing"; exit 1; }
grep -q "Restart=on-failure"             "$SERVICE" || { echo "FAIL: Restart missing";   exit 1; }

echo "  PASS: .service file content is valid"

# ---------------------------------------------------------------
# Step 4: Start node as service and verify ROS 2 messages (ROS only)
# ---------------------------------------------------------------
if [[ -z "$ROS_SETUP" ]]; then
    echo "[4/4] Skipping publisher verification (ROS 2 not available)"
    echo ""
    echo "=== PASS: all checks passed ==="
    exit 0
fi

echo "[4/4] Starting node via wrapper and verifying ROS 2 publishing..."

TOPIC="/my_node/chatter"
PUB_OUT="$WORKSPACE/publisher.out"

# Start the publisher node via the generated wrapper
"$WRAPPER" > "$PUB_OUT" 2>&1 &
PUBLISHER_PID=$!

# Resolve the ros2 CLI binary: prefer the version already on PATH (set by
# sourcing ROS setup.bash above), falling back to the sibling bin/ directory.
if command -v ros2 &>/dev/null; then
    ROS2_BIN="ros2"
else
    ROS2_BIN="$(dirname "$ROS_SETUP")/bin/ros2"
fi

# Wait up to 15 s for the node to appear on the ROS graph
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
    echo "--- publisher output ---"
    cat "$PUB_OUT" || true
    exit 1
fi

# Give DDS discovery a moment to propagate
sleep 0.5

# Run ros2 topic echo --once to receive a single message
ECHO_OUT=$(
    "$ROS2_BIN" topic echo "$TOPIC" "std_msgs/msg/String" --once \
        2>/dev/null
)

if echo "$ECHO_OUT" | grep -q "^data:"; then
    echo "  PASS: received ROS 2 message from my_node: $(echo "$ECHO_OUT" | grep '^data:' | head -1)"
else
    echo "FAIL: ros2 topic echo received no message on $TOPIC"
    echo "--- echo output ---"
    echo "$ECHO_OUT"
    echo "--- publisher output ---"
    cat "$PUB_OUT" || true
    exit 1
fi

# Graceful shutdown: SIGTERM propagates through the wrapper to rclpy.spin()
kill "$PUBLISHER_PID" 2>/dev/null || true
wait "$PUBLISHER_PID" 2>/dev/null || true
PUBLISHER_PID=""

echo ""
echo "=== PASS: all checks passed ==="

