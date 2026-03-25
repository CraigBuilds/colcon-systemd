# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""
Pytest wrapper for the colcon build end-to-end bash test.

This calls tests/test_colcon_build_e2e.sh which:
  1. Creates a temp colcon workspace with test_packages/simple_node (ROS 2 node)
  2. Runs `colcon build` with ROS 2 sourced
  3. Verifies generated .service and .sh files
  4. Validates .service file content
  5. Installs the .service, starts it via systemctl --user, verifies the ROS 2
     node is on the graph and publishing, then stops it via systemctl --user
"""

import platform
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="colcon build e2e test requires Linux",
)
def test_colcon_build_end_to_end() -> None:
    """Run the bash-based colcon build integration test."""
    script = Path(__file__).parent / "test_colcon_build_e2e.sh"
    assert script.exists(), f"Test script not found: {script}"

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    # Print output for visibility in CI logs
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

    assert result.returncode == 0, (
        f"colcon build e2e test failed (rc={result.returncode}):\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
