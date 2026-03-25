# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""
End-to-end tests for colcon-systemd.

These tests exercise the full pipeline: config → event handler → file
generation → wrapper script execution, proving that the generated service
units and wrapper scripts are valid and actually work at runtime.
"""

import os
import platform
import stat
import subprocess
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from colcon_systemd.event_handler import SystemdEventHandler


# All tests in this module require Linux.
pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="End-to-end tests require Linux",
)


def _make_job_ended(identifier: str, rc: int):
    """Create a real JobEnded event."""
    from colcon_core.event.job import JobEnded
    return JobEnded(identifier, rc)


def _make_job(
    identifier: str,
    package_path: Path,
    install_base: Path,
    package_type: str = "ros.ament_python",
):
    """
    Create a minimal mock Job object.

    ``install_base`` should be the per-package prefix, matching colcon's
    isolated install layout (e.g. ``<ws>/install/<pkg>``).
    """
    pkg = SimpleNamespace(
        path=package_path,
        type=package_type,
        name=identifier,
    )
    args = SimpleNamespace(install_base=str(install_base), merge_install=False)
    task_context = SimpleNamespace(pkg=pkg, args=args)
    return SimpleNamespace(task_context=task_context)


def _setup_install_tree(
    install_root: Path,
    package_name: str,
    entry_point_name: str,
    script_body: str,
) -> Path:
    """
    Create a simulated colcon install tree with a real executable.

    Sets up:
      <install_root>/setup.bash                     — workspace env script
      <install_root>/<pkg>/lib/<pkg>/<entry_point>   — executable

    Returns the path to the entry point executable.
    """
    # Create workspace-level setup.bash that exports a marker variable
    setup_bash = install_root / "setup.bash"
    if not setup_bash.exists():
        setup_bash.write_text(
            '#!/usr/bin/env bash\n'
            'export COLCON_SYSTEMD_E2E_SOURCED="1"\n'
        )
        setup_bash.chmod(0o755)

    # Create the per-package install prefix and entry point
    pkg_prefix = install_root / package_name
    pkg_prefix.mkdir(parents=True, exist_ok=True)
    ep_dir = pkg_prefix / "lib" / package_name
    ep_dir.mkdir(parents=True, exist_ok=True)
    ep_path = ep_dir / entry_point_name
    ep_path.write_text(script_body)
    ep_path.chmod(0o755)

    return ep_path


class TestEndToEnd:
    """
    Full pipeline tests: config → event handler → generated files → execution.
    """

    def test_generate_and_run_wrapper_script(self, tmp_path: Path) -> None:
        """
        End-to-end: generate service files via the event handler, then
        execute the wrapper script and verify the process actually runs.
        """
        pkg_name = "e2e_test_pkg"
        svc_name = "e2e_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        # 1. Write a colcon-systemd.yaml config
        config = pkg_path / "colcon-systemd.yaml"
        config.write_text(textwrap.dedent(f"""\
            services:
              - name: {svc_name}
                entry_point: {svc_name}
                description: "E2E test service"
                environment:
                  E2E_TEST_VAR: "hello_e2e"
                restart: on-failure
        """))

        # 2. Create a real executable that prints a marker and exits
        _setup_install_tree(
            install_root,
            pkg_name,
            svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                'echo "COLCON_SYSTEMD_E2E_SOURCED=${COLCON_SYSTEMD_E2E_SOURCED}"\n'
                'echo "E2E_TEST_VAR=${E2E_TEST_VAR}"\n'
                'echo "E2E_RUNNING"\n'
            ),
        )

        # 3. Fire the event handler (simulates colcon build completing)
        handler = SystemdEventHandler()
        event_data = _make_job_ended(pkg_name, rc=0)
        job = _make_job(pkg_name, pkg_path, install_base)
        handler((event_data, job))

        # 4. Verify files were generated
        output_dir = install_base / "share" / "colcon-systemd"
        service_file = output_dir / f"{svc_name}.service"
        wrapper_file = output_dir / f"{svc_name}.sh"

        assert output_dir.exists(), "Output directory was not created"
        assert service_file.exists(), ".service file was not generated"
        assert wrapper_file.exists(), "Wrapper .sh script was not generated"

        # 5. Verify service file has valid systemd unit structure
        service_content = service_file.read_text()
        assert "[Unit]" in service_content
        assert "[Service]" in service_content
        assert "[Install]" in service_content
        assert "Description=E2E test service" in service_content
        assert f"ExecStart={wrapper_file}" in service_content
        assert 'Environment="E2E_TEST_VAR=hello_e2e"' in service_content
        assert "Restart=on-failure" in service_content

        # 6. Verify wrapper script is executable and has correct content
        wrapper_content = wrapper_file.read_text()
        assert "#!/usr/bin/env bash" in wrapper_content
        assert "setup.bash" in wrapper_content
        assert "exec" in wrapper_content
        assert wrapper_file.stat().st_mode & stat.S_IXUSR

        # 7. Execute the wrapper script and verify it runs
        result = subprocess.run(
            [str(wrapper_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Wrapper script failed with rc={result.returncode}: "
            f"stderr={result.stderr}"
        )
        assert "E2E_RUNNING" in result.stdout, (
            f"Process did not produce expected output: {result.stdout}"
        )
        # Verify setup.bash was sourced (env var propagated)
        assert "COLCON_SYSTEMD_E2E_SOURCED=1" in result.stdout
        # Note: Environment variables from the config (E2E_TEST_VAR) are
        # set by systemd via Environment= directives in the .service file,
        # not by the wrapper script.  We verified those directives above
        # in the service file content check (step 5).

    def test_generate_and_run_long_running_service(
        self, tmp_path: Path
    ) -> None:
        """
        End-to-end: generate a long-running service, start it, verify it is
        running, then stop and clean up.  This simulates a real daemon node.
        """
        pkg_name = "e2e_daemon_pkg"
        svc_name = "e2e_daemon"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        pid_file = tmp_path / "daemon.pid"
        output_file = tmp_path / "daemon.out"

        # 1. Write config
        config = pkg_path / "colcon-systemd.yaml"
        config.write_text(textwrap.dedent(f"""\
            services:
              - name: {svc_name}
                entry_point: {svc_name}
                description: "E2E long-running daemon test"
                restart: on-failure
        """))

        # 2. Create a long-running executable that writes PID and loops
        _setup_install_tree(
            install_root,
            pkg_name,
            svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                f'echo $$ > "{pid_file}"\n'
                f'echo "DAEMON_STARTED" >> "{output_file}"\n'
                'trap \'echo "DAEMON_STOPPED" >> "' + str(output_file) + '"; '
                'exit 0\' SIGTERM SIGINT\n'
                'while true; do\n'
                f'  echo "DAEMON_HEARTBEAT" >> "{output_file}"\n'
                '  sleep 0.2\n'
                'done\n'
            ),
        )

        # 3. Fire the event handler
        handler = SystemdEventHandler()
        event_data = _make_job_ended(pkg_name, rc=0)
        job = _make_job(pkg_name, pkg_path, install_base)
        handler((event_data, job))

        # 4. Verify files were generated
        output_dir = install_base / "share" / "colcon-systemd"
        wrapper_file = output_dir / f"{svc_name}.sh"
        service_file = output_dir / f"{svc_name}.service"
        assert service_file.exists()
        assert wrapper_file.exists()

        # 5. Start the wrapper script as a background process
        proc = subprocess.Popen(
            [str(wrapper_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # 6. Wait for the daemon to start and produce output
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if pid_file.exists() and output_file.exists():
                    content = output_file.read_text()
                    if "DAEMON_STARTED" in content:
                        break
                time.sleep(0.1)
            else:
                pytest.fail("Daemon did not start within 5 seconds")

            # 7. Verify the process is actually running
            daemon_pid = int(pid_file.read_text().strip())
            try:
                os.kill(daemon_pid, 0)  # Check process exists
            except ProcessLookupError:
                pytest.fail(f"Daemon PID {daemon_pid} is not running")

            # 8. Wait for heartbeat output (proves it's looping)
            time.sleep(0.5)
            content = output_file.read_text()
            assert content.count("DAEMON_HEARTBEAT") >= 1, (
                "Daemon did not produce heartbeat output"
            )

        finally:
            # 9. Clean up: terminate the daemon
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        # 10. Verify graceful shutdown happened
        content = output_file.read_text()
        assert "DAEMON_STOPPED" in content, (
            "Daemon did not handle SIGTERM gracefully"
        )

    def test_systemd_analyze_verify(self, tmp_path: Path) -> None:
        """
        Validate the generated .service file with systemd-analyze verify.

        This proves the unit file is syntactically valid systemd.
        """
        # Skip if systemd-analyze is not available
        if not _systemd_analyze_available():
            pytest.skip("systemd-analyze not available")

        pkg_name = "e2e_verify_pkg"
        svc_name = "e2e_verify_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        config = pkg_path / "colcon-systemd.yaml"
        config.write_text(textwrap.dedent(f"""\
            services:
              - name: {svc_name}
                entry_point: {svc_name}
                description: "Verify test"
                environment:
                  MY_VAR: "test"
                restart: always
        """))

        _setup_install_tree(
            install_root, pkg_name, svc_name,
            script_body='#!/usr/bin/env bash\necho ok\n',
        )

        handler = SystemdEventHandler()
        event_data = _make_job_ended(pkg_name, rc=0)
        job = _make_job(pkg_name, pkg_path, install_base)
        handler((event_data, job))

        service_file = (
            install_base / "share" / "colcon-systemd"
            / f"{svc_name}.service"
        )
        assert service_file.exists()

        # systemd-analyze verify checks unit file syntax
        # It may warn about the ExecStart path not existing on the host,
        # so we only check for hard errors (exit code)
        result = subprocess.run(
            ["systemd-analyze", "verify", "--user", str(service_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # systemd-analyze verify returns 0 for valid units, even with
        # non-fatal warnings.  Non-zero means the unit has real errors.
        # Filter out expected warnings about missing executable paths.
        errors = [
            line for line in result.stderr.splitlines()
            if line.strip()
            and "not found" not in line.lower()
            and "does not exist" not in line.lower()
        ]
        assert result.returncode == 0 or not errors, (
            f"systemd-analyze verify failed:\n{result.stderr}"
        )

    def test_multiple_services_all_run(self, tmp_path: Path) -> None:
        """
        End-to-end: generate multiple services from one config, verify
        all wrapper scripts execute successfully.
        """
        pkg_name = "e2e_multi_pkg"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        config = pkg_path / "colcon-systemd.yaml"
        config.write_text(textwrap.dedent("""\
            services:
              - name: node_alpha
                entry_point: node_alpha
                description: "Alpha node"
              - name: node_beta
                entry_point: node_beta
                description: "Beta node"
        """))

        # Create both executables
        for name in ("node_alpha", "node_beta"):
            _setup_install_tree(
                install_root, pkg_name, name,
                script_body=(
                    '#!/usr/bin/env bash\n'
                    f'echo "{name.upper()}_RUNNING"\n'
                ),
            )

        # Fire event handler
        handler = SystemdEventHandler()
        event_data = _make_job_ended(pkg_name, rc=0)
        job = _make_job(pkg_name, pkg_path, install_base)
        handler((event_data, job))

        # Verify both services generated and runnable
        output_dir = install_base / "share" / "colcon-systemd"
        for name in ("node_alpha", "node_beta"):
            wrapper = output_dir / f"{name}.sh"
            service = output_dir / f"{name}.service"
            assert wrapper.exists(), f"{name}.sh not generated"
            assert service.exists(), f"{name}.service not generated"

            result = subprocess.run(
                [str(wrapper)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, (
                f"{name} wrapper failed: {result.stderr}"
            )
            assert f"{name.upper()}_RUNNING" in result.stdout

    def test_environment_propagation(self, tmp_path: Path) -> None:
        """
        Verify that environment variables from the config appear as
        Environment= directives in the generated .service file.
        """
        pkg_name = "e2e_env_pkg"
        svc_name = "e2e_env_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        config = pkg_path / "colcon-systemd.yaml"
        config.write_text(textwrap.dedent(f"""\
            services:
              - name: {svc_name}
                entry_point: {svc_name}
                environment:
                  ROS_DOMAIN_ID: "42"
                  CUSTOM_VAR: "test_value"
        """))

        # The executable prints environment variables it receives
        _setup_install_tree(
            install_root, pkg_name, svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                'echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"\n'
                'echo "CUSTOM_VAR=${CUSTOM_VAR}"\n'
            ),
        )

        handler = SystemdEventHandler()
        event_data = _make_job_ended(pkg_name, rc=0)
        job = _make_job(pkg_name, pkg_path, install_base)
        handler((event_data, job))

        wrapper = (
            install_base / "share" / "colcon-systemd"
            / f"{svc_name}.sh"
        )
        assert wrapper.exists()

        # The wrapper script itself does NOT set env vars (systemd does
        # that via Environment= directives).  But we can verify that
        # the .service file contains the right directives, and the
        # wrapper script runs the right executable.
        service_content = (
            install_base / "share" / "colcon-systemd"
            / f"{svc_name}.service"
        ).read_text()
        assert 'Environment="ROS_DOMAIN_ID=42"' in service_content
        assert 'Environment="CUSTOM_VAR=test_value"' in service_content

        # Run the wrapper directly — it should execute without error
        result = subprocess.run(
            [str(wrapper)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


def _systemd_analyze_available() -> bool:
    """Check if systemd-analyze is available on the system."""
    try:
        subprocess.run(
            ["systemd-analyze", "--version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
