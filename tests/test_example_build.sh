#!/usr/bin/env bash
# Test that colcon-systemd generates .service and .sh files for the example package.
#
# Usage:  bash tests/test_example_build.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$(mktemp -d)"

cleanup() { rm -rf "$WORKSPACE"; }
trap cleanup EXIT

echo "=== colcon-systemd example build test ==="

# Set up a minimal colcon workspace with the example package
mkdir -p "$WORKSPACE/src"
cp -r "$REPO_ROOT/examples/my_node" "$WORKSPACE/src/my_node"

# Install colcon-systemd and the colcon extensions it needs.
# --break-system-packages is required when running inside the ros:jazzy
# Docker container (Ubuntu 24.04), which enforces PEP 668.  The container
# is a fully isolated test environment, so bypassing the guard is safe.
pip install -e "$REPO_ROOT" --quiet --break-system-packages 2>&1 | tail -1
pip install colcon-python-setup-py colcon-bash colcon-recursive-crawl \
    --quiet --break-system-packages 2>&1 | tail -1

# Build the workspace
cd "$WORKSPACE"
colcon build

# Validate that the expected artifacts were generated
SERVICE="$WORKSPACE/install/my_node/share/colcon-systemd/my_node.service"
WRAPPER="$WORKSPACE/install/my_node/share/colcon-systemd/my_node.sh"

test -f "$SERVICE" || { echo "FAIL: $SERVICE not found"; exit 1; }
test -f "$WRAPPER" || { echo "FAIL: $WRAPPER not found"; exit 1; }
test -x "$WRAPPER" || { echo "FAIL: $WRAPPER is not executable"; exit 1; }

echo "PASS: .service and .sh files generated successfully"
