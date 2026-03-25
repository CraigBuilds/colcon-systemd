# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""Tests for colcon_systemd.event_handler module."""

import platform
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from colcon_systemd.event_handler import SystemdEventHandler


def _make_job_ended(identifier: str, rc: int):
    """Create a mock JobEnded event data."""
    from colcon_core.event.job import JobEnded
    return JobEnded(identifier, rc)


def _make_job(
    identifier: str,
    package_path: Path,
    install_base: Path,
    package_type: str = "ros.ament_python",
):
    """Create a minimal mock Job object."""
    pkg = SimpleNamespace(
        path=package_path,
        type=package_type,
        name=identifier,
    )
    args = SimpleNamespace(install_base=str(install_base))
    task_context = SimpleNamespace(pkg=pkg, args=args)
    job = SimpleNamespace(task_context=task_context)
    return job


def _write_config(package_path: Path, content: str) -> None:
    cfg = package_path / "colcon-systemd.yaml"
    cfg.write_text(textwrap.dedent(content))


class TestSystemdEventHandler:
    """Tests for the SystemdEventHandler event handler."""

    def test_ignores_non_job_ended_events(self) -> None:
        handler = SystemdEventHandler()
        # Simulate a non-JobEnded event
        event = (SimpleNamespace(), SimpleNamespace())
        handler(event)  # Should not raise

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_ignores_failed_builds(self, tmp_path: Path) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        _write_config(pkg_path, """\
            services:
              - name: my_node
                entry_point: my_node
        """)

        event_data = _make_job_ended("my_pkg", rc=1)
        job = _make_job("my_pkg", pkg_path, install_base)
        handler((event_data, job))

        # No files should be generated for failed builds
        output_dir = install_base / "my_pkg" / "share" / "colcon-systemd"
        assert not output_dir.exists()

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_ignores_packages_without_config(self, tmp_path: Path) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        event_data = _make_job_ended("my_pkg", rc=0)
        job = _make_job("my_pkg", pkg_path, install_base)
        handler((event_data, job))

        output_dir = install_base / "my_pkg" / "share" / "colcon-systemd"
        assert not output_dir.exists()

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_generates_files_for_configured_package(
        self, tmp_path: Path
    ) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        _write_config(pkg_path, """\
            services:
              - name: my_node
                entry_point: my_node
                description: "Test node"
        """)

        event_data = _make_job_ended("my_pkg", rc=0)
        job = _make_job("my_pkg", pkg_path, install_base)
        handler((event_data, job))

        output_dir = install_base / "my_pkg" / "share" / "colcon-systemd"
        assert output_dir.exists()
        assert (output_dir / "my_node.service").exists()
        assert (output_dir / "my_node.sh").exists()

        # Verify service file content
        content = (output_dir / "my_node.service").read_text()
        assert "Description=Test node" in content
        assert "ExecStart=" in content

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_generates_multiple_services(self, tmp_path: Path) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        _write_config(pkg_path, """\
            services:
              - name: node_a
                entry_point: node_a
              - name: node_b
                executable: node_b
        """)

        event_data = _make_job_ended("my_pkg", rc=0)
        job = _make_job("my_pkg", pkg_path, install_base)
        handler((event_data, job))

        output_dir = install_base / "my_pkg" / "share" / "colcon-systemd"
        assert (output_dir / "node_a.service").exists()
        assert (output_dir / "node_b.service").exists()
        assert (output_dir / "node_a.sh").exists()
        assert (output_dir / "node_b.sh").exists()

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_handles_invalid_config_gracefully(
        self, tmp_path: Path, capsys
    ) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        _write_config(pkg_path, """\
            services:
              - invalid_entry
        """)

        event_data = _make_job_ended("my_pkg", rc=0)
        job = _make_job("my_pkg", pkg_path, install_base)
        handler((event_data, job))

        # Should print an error message but not crash
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_skips_on_non_linux(self, tmp_path: Path) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        _write_config(pkg_path, """\
            services:
              - name: my_node
                entry_point: my_node
        """)

        event_data = _make_job_ended("my_pkg", rc=0)
        job = _make_job("my_pkg", pkg_path, install_base)

        with patch("colcon_systemd.event_handler.platform") as mock_platform:
            mock_platform.system.return_value = "Darwin"
            handler((event_data, job))

        output_dir = install_base / "my_pkg" / "share" / "colcon-systemd"
        assert not output_dir.exists()

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_skips_non_build_verb(self, tmp_path: Path) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()

        _write_config(pkg_path, """\
            services:
              - name: my_node
                entry_point: my_node
        """)

        # No install_base in args (e.g., test verb)
        pkg = SimpleNamespace(
            path=pkg_path, type="ros.ament_python", name="my_pkg"
        )
        args = SimpleNamespace()  # No install_base
        task_context = SimpleNamespace(pkg=pkg, args=args)
        job = SimpleNamespace(task_context=task_context)

        event_data = _make_job_ended("my_pkg", rc=0)
        handler((event_data, job))
        # Should not crash or generate anything

    @pytest.mark.skipif(
        platform.system() != "Linux",
        reason="systemd handler only runs on Linux",
    )
    def test_warns_on_unsupported_package_type(
        self, tmp_path: Path, caplog
    ) -> None:
        handler = SystemdEventHandler()
        pkg_path = tmp_path / "my_pkg"
        pkg_path.mkdir()
        install_base = tmp_path / "install"
        install_base.mkdir()

        _write_config(pkg_path, """\
            services:
              - name: my_node
                entry_point: my_node
        """)

        event_data = _make_job_ended("my_pkg", rc=0)
        job = _make_job(
            "my_pkg", pkg_path, install_base, package_type="custom_type"
        )

        import logging
        with caplog.at_level(logging.WARNING):
            handler((event_data, job))

        # Files should still be generated
        output_dir = install_base / "my_pkg" / "share" / "colcon-systemd"
        assert (output_dir / "my_node.service").exists()
        # Warning should be logged
        assert any("custom_type" in r.message for r in caplog.records)
