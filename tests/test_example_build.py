# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""
Pytest wrapper for the example build bash test.

This calls tests/test_example_build.sh which:
  1. Creates a temp colcon workspace with examples/my_node
  2. Runs `colcon build`
  3. Verifies that .service and .sh files were generated
"""

import platform
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="colcon build e2e test requires Linux",
)
def test_example_build() -> None:
    """Run the example build bash test."""
    script = Path(__file__).parent / "test_example_build.sh"
    assert script.exists(), f"Test script not found: {script}"

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

    assert result.returncode == 0, (
        f"example build test failed (rc={result.returncode}):\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
