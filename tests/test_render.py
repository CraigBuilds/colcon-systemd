# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""Tests for colcon_systemd.render module."""

from pathlib import Path

import pytest

from colcon_systemd.config import ServiceConfig
from colcon_systemd.render import (
    render_service_unit,
    render_wrapper_script,
    write_service_files,
)


@pytest.fixture()
def install_base(tmp_path: Path) -> Path:
    """Create a mock install base directory."""
    base = tmp_path / "install"
    base.mkdir()
    return base


def _make_service(**overrides) -> ServiceConfig:
    defaults = dict(name="my_node", entry_point="my_node")
    defaults.update(overrides)
    return ServiceConfig(**defaults)


# ---------------------------------------------------------------------------
# render_wrapper_script
# ---------------------------------------------------------------------------

class TestRenderWrapperScript:
    """Tests for wrapper script rendering."""

    def test_basic_wrapper(self, install_base: Path) -> None:
        svc = _make_service()
        script = render_wrapper_script(
            install_base, "my_pkg", svc, "ros.ament_python"
        )
        assert "#!/usr/bin/env bash" in script
        assert f'source "{install_base}/setup.bash"' in script
        assert f'exec "{install_base}/my_pkg/lib/my_pkg/my_node"' in script
        assert "set -euo pipefail" in script

    def test_executable_variant(self, install_base: Path) -> None:
        svc = _make_service(entry_point=None, executable="talker_node")
        script = render_wrapper_script(
            install_base, "my_pkg", svc, "cmake"
        )
        assert "talker_node" in script


# ---------------------------------------------------------------------------
# render_service_unit
# ---------------------------------------------------------------------------

class TestRenderServiceUnit:
    """Tests for service unit rendering."""

    def test_basic_unit(self, install_base: Path) -> None:
        svc = _make_service(description="Test node")
        wrapper_path = install_base / "my_pkg" / "share" / "colcon-systemd" / "my_node.sh"
        unit = render_service_unit(
            install_base, "my_pkg", svc, "ros.ament_python", wrapper_path
        )
        assert "[Unit]" in unit
        assert "Description=Test node" in unit
        assert "[Service]" in unit
        assert f"ExecStart={wrapper_path}" in unit
        assert "Restart=on-failure" in unit
        assert "[Install]" in unit
        assert "WantedBy=default.target" in unit

    def test_unit_with_environment(self, install_base: Path) -> None:
        svc = _make_service(
            environment={"ROS_DOMAIN_ID": "42", "RMW": "cyclone"}
        )
        wrapper_path = Path("/tmp/wrapper.sh")
        unit = render_service_unit(
            install_base, "my_pkg", svc, "ros.ament_python", wrapper_path
        )
        assert 'Environment="ROS_DOMAIN_ID=42"' in unit
        assert 'Environment="RMW=cyclone"' in unit

    def test_unit_with_custom_after(self, install_base: Path) -> None:
        svc = _make_service(after=["network.target", "ros2.service"])
        wrapper_path = Path("/tmp/wrapper.sh")
        unit = render_service_unit(
            install_base, "my_pkg", svc, "ros.ament_python", wrapper_path
        )
        assert "After=network.target ros2.service" in unit

    def test_unit_with_working_directory(self, install_base: Path) -> None:
        svc = _make_service(working_directory="/home/ros")
        wrapper_path = Path("/tmp/wrapper.sh")
        unit = render_service_unit(
            install_base, "my_pkg", svc, "ros.ament_python", wrapper_path
        )
        assert "WorkingDirectory=/home/ros" in unit

    def test_unit_default_description(self, install_base: Path) -> None:
        svc = _make_service(description="")
        wrapper_path = Path("/tmp/wrapper.sh")
        unit = render_service_unit(
            install_base, "my_pkg", svc, "ros.ament_python", wrapper_path
        )
        assert "Description=my_pkg my_node" in unit

    def test_restart_always(self, install_base: Path) -> None:
        svc = _make_service(restart="always")
        wrapper_path = Path("/tmp/wrapper.sh")
        unit = render_service_unit(
            install_base, "my_pkg", svc, "ros.ament_python", wrapper_path
        )
        assert "Restart=always" in unit


# ---------------------------------------------------------------------------
# write_service_files
# ---------------------------------------------------------------------------

class TestWriteServiceFiles:
    """Tests for the full write_service_files workflow."""

    def test_creates_files(self, install_base: Path) -> None:
        svc = _make_service(description="Test node")
        service_path = write_service_files(
            install_base, "my_pkg", svc, "ros.ament_python"
        )
        assert service_path is not None
        assert service_path.exists()

        wrapper_path = (
            install_base / "my_pkg" / "share" / "colcon-systemd" / "my_node.sh"
        )
        assert wrapper_path.exists()

        # Wrapper should be executable
        import stat
        mode = wrapper_path.stat().st_mode
        assert mode & stat.S_IXUSR

    def test_service_file_content(self, install_base: Path) -> None:
        svc = _make_service(description="Test node")
        service_path = write_service_files(
            install_base, "my_pkg", svc, "ros.ament_python"
        )
        content = service_path.read_text()
        assert "Description=Test node" in content
        assert "ExecStart=" in content

    def test_multiple_services_in_same_package(
        self, install_base: Path
    ) -> None:
        svc_a = _make_service(name="node_a", entry_point="node_a")
        svc_b = _make_service(name="node_b", entry_point="node_b")

        path_a = write_service_files(
            install_base, "my_pkg", svc_a, "ros.ament_python"
        )
        path_b = write_service_files(
            install_base, "my_pkg", svc_b, "ros.ament_python"
        )

        assert path_a.name == "node_a.service"
        assert path_b.name == "node_b.service"
        assert path_a != path_b

    def test_idempotent_overwrite(self, install_base: Path) -> None:
        svc = _make_service()
        path1 = write_service_files(
            install_base, "my_pkg", svc, "ros.ament_python"
        )
        content1 = path1.read_text()

        path2 = write_service_files(
            install_base, "my_pkg", svc, "ros.ament_python"
        )
        content2 = path2.read_text()

        assert content1 == content2
