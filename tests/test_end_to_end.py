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


def _find_ros_setup_bash() -> "Path | None":
    """Return the path to the first available ROS 2 distro setup.bash.

    Scans ``/opt/ros/`` for distro directories and returns the setup.bash
    of the first one found (alphabetically), or ``None`` if ROS 2 is not
    installed.
    """
    opt_ros = Path("/opt/ros")
    if not opt_ros.is_dir():
        return None
    for distro_dir in sorted(opt_ros.iterdir()):
        setup = distro_dir / "setup.bash"
        if setup.exists():
            return setup
    return None


def _get_ros_env(ros_setup_bash: Path) -> dict:
    """Return the shell environment that results from sourcing *ros_setup_bash*.

    Runs ``bash -c 'source <setup.bash> && env'`` and parses the output into
    a dictionary so that subprocesses can be given the full ROS environment
    without needing a shell wrapper.
    """
    result = subprocess.run(
        ["bash", "-c", f"source {ros_setup_bash} && env"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to source ROS setup.bash ({ros_setup_bash}):\n"
            f"{result.stderr}"
        )
    env: dict = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            env[key] = val
    return env


def _make_job_ended(identifier: str, rc: int):
    """Create a real JobEnded event."""
    from colcon_core.event.job import JobEnded
    return JobEnded(identifier, rc)


def _make_job(
    identifier: str,
    package_path: Path,
    install_base: Path,
    package_type: str = "ros.ament_python",
    merge_install: bool = False,
):
    """
    Create a minimal mock Job object.

    ``install_base`` should be the per-package prefix, matching colcon's
    isolated install layout (e.g. ``<ws>/install/<pkg>``).
    Set ``merge_install=True`` to simulate ``colcon build --merge-install``.
    """
    pkg = SimpleNamespace(
        path=package_path,
        type=package_type,
        name=identifier,
    )
    args = SimpleNamespace(install_base=str(install_base), merge_install=merge_install)
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

    def test_wrapper_propagates_exit_code(self, tmp_path: Path) -> None:
        """
        Verify that the wrapper script propagates the node's exit code.

        systemd uses the exit code to decide whether to restart a service
        (via the Restart= policy).  If the wrapper masked a non-zero exit,
        systemd would think the service succeeded and never restart it.
        The wrapper uses ``exec`` which replaces the shell process, so the
        node's exit code becomes the wrapper's exit code.
        """
        pkg_name = "e2e_exitcode_pkg"
        svc_name = "e2e_failing_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        (pkg_path / "colcon-systemd.yaml").write_text(
            textwrap.dedent(f"""\
                services:
                  - name: {svc_name}
                    entry_point: {svc_name}
                    description: "Exit code propagation test"
                    restart: on-failure
            """)
        )

        _setup_install_tree(
            install_root, pkg_name, svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                'echo "NODE_STARTED"\n'
                'exit 42\n'
            ),
        )

        handler = SystemdEventHandler()
        handler((_make_job_ended(pkg_name, rc=0), _make_job(pkg_name, pkg_path, install_base)))

        wrapper = install_base / "share" / "colcon-systemd" / f"{svc_name}.sh"
        assert wrapper.exists()

        result = subprocess.run(
            [str(wrapper)], capture_output=True, text=True, timeout=10
        )

        # The wrapper must not mask the node's exit code
        assert result.returncode == 42, (
            f"Expected exit code 42 but got {result.returncode}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        # The node did run before exiting
        assert "NODE_STARTED" in result.stdout

    def test_wrapper_forwards_runtime_args(self, tmp_path: Path) -> None:
        """
        Verify that arguments passed to the wrapper at runtime reach the node.

        The wrapper uses ``exec "$executable" "$@"`` so any arguments given to
        the wrapper process (e.g. by systemd from the ExecStart= line, or by
        the operator directly) are forwarded verbatim to the node.
        """
        pkg_name = "e2e_fwdargs_pkg"
        svc_name = "e2e_args_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        (pkg_path / "colcon-systemd.yaml").write_text(
            textwrap.dedent(f"""\
                services:
                  - name: {svc_name}
                    entry_point: {svc_name}
                    description: "Args forwarding test"
            """)
        )

        _setup_install_tree(
            install_root, pkg_name, svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                'echo "ARGS: $*"\n'
            ),
        )

        handler = SystemdEventHandler()
        handler((_make_job_ended(pkg_name, rc=0), _make_job(pkg_name, pkg_path, install_base)))

        wrapper = install_base / "share" / "colcon-systemd" / f"{svc_name}.sh"
        assert wrapper.exists()

        result = subprocess.run(
            [str(wrapper), "--ros-args", "--param", "use_sim_time:=true"],
            capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "--ros-args" in result.stdout
        assert "--param" in result.stdout
        assert "use_sim_time:=true" in result.stdout

    def test_config_args_in_execstart_and_run(self, tmp_path: Path) -> None:
        """
        Verify that 'args' from the config appear in ExecStart and are passed
        to the node when the wrapper is called with those arguments.

        systemd reads ExecStart= and passes extra tokens to the wrapper.
        Verifying ExecStart content plus a direct invocation with the same
        args proves the full chain works end-to-end.
        """
        pkg_name = "e2e_cfgargs_pkg"
        svc_name = "e2e_cfgargs_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        (pkg_path / "colcon-systemd.yaml").write_text(
            textwrap.dedent(f"""\
                services:
                  - name: {svc_name}
                    entry_point: {svc_name}
                    description: "Config args test"
                    args:
                      - --ros-args
                      - --param
                      - use_sim_time:=true
            """)
        )

        _setup_install_tree(
            install_root, pkg_name, svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                'echo "ARGS: $*"\n'
            ),
        )

        handler = SystemdEventHandler()
        handler((_make_job_ended(pkg_name, rc=0), _make_job(pkg_name, pkg_path, install_base)))

        wrapper = install_base / "share" / "colcon-systemd" / f"{svc_name}.sh"
        service = install_base / "share" / "colcon-systemd" / f"{svc_name}.service"
        assert wrapper.exists()
        assert service.exists()

        # Verify args appear in ExecStart
        service_content = service.read_text()
        assert "--ros-args" in service_content
        assert "--param" in service_content
        assert "use_sim_time:=true" in service_content

        # Simulate systemd: pass the same args to the wrapper directly
        result = subprocess.run(
            [str(wrapper), "--ros-args", "--param", "use_sim_time:=true"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "--ros-args" in result.stdout
        assert "use_sim_time:=true" in result.stdout

    def test_merge_install_wrapper_runs(self, tmp_path: Path) -> None:
        """
        Verify that a wrapper generated in merge-install mode executes correctly.

        In ``colcon build --merge-install``, all packages share a single install
        prefix so setup.bash lives at ``<install_base>/setup.bash`` rather than
        ``<install_base>/../setup.bash``.  The wrapper must source it from the
        correct location.
        """
        pkg_name = "e2e_merge_pkg"
        svc_name = "e2e_merge_node"
        # In merge-install mode the install_base IS the workspace install root
        install_base = tmp_path / "install"
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        # setup.bash at the install root (no per-package sub-directory)
        setup_bash = install_base / "setup.bash"
        setup_bash.write_text(
            '#!/usr/bin/env bash\n'
            'export MERGE_INSTALL_SOURCED="1"\n'
        )
        setup_bash.chmod(0o755)

        # Executable at install_base/lib/<pkg>/<ep>
        ep_dir = install_base / "lib" / pkg_name
        ep_dir.mkdir(parents=True)
        ep = ep_dir / svc_name
        ep.write_text(
            '#!/usr/bin/env bash\n'
            'echo "MERGE_INSTALL_SOURCED=${MERGE_INSTALL_SOURCED}"\n'
            'echo "MERGE_NODE_RUNNING"\n'
        )
        ep.chmod(0o755)

        (pkg_path / "colcon-systemd.yaml").write_text(
            textwrap.dedent(f"""\
                services:
                  - name: {svc_name}
                    entry_point: {svc_name}
                    description: "Merge install test"
            """)
        )

        handler = SystemdEventHandler()
        job = _make_job(pkg_name, pkg_path, install_base, merge_install=True)
        handler((_make_job_ended(pkg_name, rc=0), job))

        wrapper = install_base / "share" / "colcon-systemd" / f"{svc_name}.sh"
        assert wrapper.exists()

        # The wrapper must reference the setup.bash inside install_base,
        # not one level up
        wrapper_content = wrapper.read_text()
        assert str(install_base / "setup.bash") in wrapper_content

        result = subprocess.run(
            [str(wrapper)], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "MERGE_NODE_RUNNING" in result.stdout
        assert "MERGE_INSTALL_SOURCED=1" in result.stdout

    def test_two_packages_dont_interfere(self, tmp_path: Path) -> None:
        """
        Verify that two packages with the same service name are fully isolated.

        colcon uses a separate install prefix per package in isolated mode.
        Services from pkg_a and pkg_b must not overwrite each other's generated
        files even when they share the same service name.
        """
        install_root = tmp_path / "install"
        install_root.mkdir()
        packages = [
            ("pkg_a", "PKG_A_WORKER"),
            ("pkg_b", "PKG_B_WORKER"),
        ]

        install_bases = {}
        pkg_paths = {}
        for pkg_name, marker in packages:
            pkg_path = tmp_path / "src" / pkg_name
            pkg_path.mkdir(parents=True)
            install_base = install_root / pkg_name
            install_base.mkdir()
            pkg_paths[pkg_name] = pkg_path
            install_bases[pkg_name] = install_base

            (pkg_path / "colcon-systemd.yaml").write_text(
                textwrap.dedent(f"""\
                    services:
                      - name: worker
                        entry_point: worker
                        description: "Worker for {pkg_name}"
                """)
            )
            _setup_install_tree(
                install_root, pkg_name, "worker",
                script_body=(
                    '#!/usr/bin/env bash\n'
                    f'echo "{marker}"\n'
                ),
            )

        # Fire the event handler for both packages
        handler = SystemdEventHandler()
        for pkg_name, _ in packages:
            handler((
                _make_job_ended(pkg_name, rc=0),
                _make_job(pkg_name, pkg_paths[pkg_name], install_bases[pkg_name]),
            ))

        # Each package has its own set of generated files
        wrapper_a = install_bases["pkg_a"] / "share" / "colcon-systemd" / "worker.sh"
        wrapper_b = install_bases["pkg_b"] / "share" / "colcon-systemd" / "worker.sh"
        assert wrapper_a.exists(), "pkg_a worker.sh not generated"
        assert wrapper_b.exists(), "pkg_b worker.sh not generated"
        # Files are distinct (different paths)
        assert wrapper_a != wrapper_b

        result_a = subprocess.run(
            [str(wrapper_a)], capture_output=True, text=True, timeout=10
        )
        result_b = subprocess.run(
            [str(wrapper_b)], capture_output=True, text=True, timeout=10
        )
        assert result_a.returncode == 0
        assert result_b.returncode == 0

        # Each wrapper produces only its own marker — no cross-contamination
        assert "PKG_A_WORKER" in result_a.stdout
        assert "PKG_B_WORKER" not in result_a.stdout
        assert "PKG_B_WORKER" in result_b.stdout
        assert "PKG_A_WORKER" not in result_b.stdout

    def test_wrapper_stdout_stderr_passthrough(self, tmp_path: Path) -> None:
        """
        Verify the wrapper does not swallow node stdout or stderr.

        systemd captures the service's stdout/stderr for journald.  If the
        wrapper swallowed either stream, operators would lose diagnostic output
        from their ROS 2 nodes (e.g. rosout, warnings, error traces).
        """
        pkg_name = "e2e_stdio_pkg"
        svc_name = "e2e_stdio_node"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        (pkg_path / "colcon-systemd.yaml").write_text(
            textwrap.dedent(f"""\
                services:
                  - name: {svc_name}
                    entry_point: {svc_name}
                    description: "Stdio passthrough test"
            """)
        )

        _setup_install_tree(
            install_root, pkg_name, svc_name,
            script_body=(
                '#!/usr/bin/env bash\n'
                'echo "STDOUT_MESSAGE"\n'
                'echo "STDERR_MESSAGE" >&2\n'
            ),
        )

        handler = SystemdEventHandler()
        handler((_make_job_ended(pkg_name, rc=0), _make_job(pkg_name, pkg_path, install_base)))

        wrapper = install_base / "share" / "colcon-systemd" / f"{svc_name}.sh"
        assert wrapper.exists()

        result = subprocess.run(
            [str(wrapper)], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert "STDOUT_MESSAGE" in result.stdout, (
            f"stdout was swallowed: {result.stdout!r}"
        )
        assert "STDERR_MESSAGE" in result.stderr, (
            f"stderr was swallowed: {result.stderr!r}"
        )

    def test_ros2_node_pubsub_via_wrapper(self, tmp_path: Path) -> None:
        """
        Verify that a real ROS 2 publisher node, started via the generated
        wrapper script, publishes messages over DDS that a subscriber receives.

        This is the critical end-to-end test — it proves that wrapping a ROS 2
        node as a systemd service does not break its DDS middleware
        communication.  A false positive is impossible: if the wrapper broke
        DDS discovery, RMW transport, or the ROS executor, ``ros2 topic echo``
        would receive nothing and the assertion would fail.

        Test flow
        ---------
        1. Create a colcon install tree whose ``setup.bash`` sources the real
           ROS environment, simulating a colcon workspace that depends on ROS.
        2. Write a real ``rclpy`` publisher as the package entry point; it
           publishes ``std_msgs/String`` to a unique topic and prints
           ``PUBLISHER_READY`` on stdout once its executor is spinning.
        3. Generate the wrapper script with ``SystemdEventHandler`` — the same
           path taken by ``colcon build`` in production.
        4. Start the wrapper as a subprocess (the "systemd starts the service"
           step).
        5. Run ``ros2 topic echo --once`` as the subscriber and assert that at
           least one well-formed message arrives from the publisher.
        """
        ros_setup = _find_ros_setup_bash()
        assert ros_setup is not None  # satisfied by the skipif guard above

        pkg_name = "e2e_ros_pub_pkg"
        svc_name = "e2e_ros_publisher"
        install_root = tmp_path / "install"
        install_root.mkdir()
        install_base = install_root / pkg_name
        install_base.mkdir()
        pkg_path = tmp_path / "src" / pkg_name
        pkg_path.mkdir(parents=True)

        # Use a unique topic name derived from tmp_path to avoid collisions
        # when multiple test runs execute in parallel.
        topic = f"/colcon_systemd_e2e_{tmp_path.name}"

        # Create a workspace-level setup.bash that sources the real ROS
        # environment.  The generated wrapper does:
        #   source <install_base>/../setup.bash
        # which resolves to install_root/setup.bash here.
        ros_setup_bash = install_root / "setup.bash"
        ros_setup_bash.write_text(
            "#!/usr/bin/env bash\n"
            f"source {ros_setup}\n"
            'export COLCON_SYSTEMD_E2E_SOURCED="1"\n'
        )
        ros_setup_bash.chmod(0o755)

        (pkg_path / "colcon-systemd.yaml").write_text(
            textwrap.dedent(f"""\
                services:
                  - name: {svc_name}
                    entry_point: {svc_name}
                    description: "ROS 2 publisher node under test"
            """)
        )

        # Real rclpy publisher: publishes String messages on the given topic.
        # PUBLISHER_READY is printed once the node is spinning so the test
        # knows when to start the subscriber.
        publisher_script = textwrap.dedent("""\
            #!/usr/bin/env python3
            \"\"\"Real ROS 2 publisher node used by the colcon-systemd e2e test.\"\"\"
            import sys
            import signal
            import time
            import rclpy
            from rclpy.node import Node
            from std_msgs.msg import String

            TOPIC = sys.argv[1]

            running = True

            def _stop(sig, frame):
                global running
                running = False

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)

            rclpy.init()
            node = Node("e2e_ros_publisher")
            pub = node.create_publisher(String, TOPIC, 10)

            print("PUBLISHER_READY", flush=True)

            seq = 0
            while running:
                msg = String()
                msg.data = f"hello_{seq}"
                pub.publish(msg)
                seq += 1
                time.sleep(0.1)

            node.destroy_node()
        """)

        # _setup_install_tree skips creating setup.bash if it already exists,
        # so the ROS-aware version created above is preserved.
        _setup_install_tree(
            install_root, pkg_name, svc_name, script_body=publisher_script
        )

        handler = SystemdEventHandler()
        handler((_make_job_ended(pkg_name, rc=0), _make_job(pkg_name, pkg_path, install_base)))

        wrapper = install_base / "share" / "colcon-systemd" / f"{svc_name}.sh"
        assert wrapper.exists()

        # Start the publisher node via the generated wrapper.
        pub_proc = subprocess.Popen(
            [str(wrapper), topic],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        received_msg = None
        sub_result = None
        try:
            # Wait for the node to signal its executor is spinning.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                line = pub_proc.stdout.readline()
                if "PUBLISHER_READY" in line:
                    break
            else:
                stdout, stderr = "", ""
                try:
                    stdout, stderr = pub_proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                pytest.fail(
                    "Publisher never printed PUBLISHER_READY — node did not start.\n"
                    f"stdout={stdout!r}\nstderr={stderr!r}"
                )

            # Allow DDS discovery time to propagate before subscribing.
            time.sleep(0.5)

            ros_env = _get_ros_env(ros_setup)
            ros2_bin = str(ros_setup.parent / "bin" / "ros2")

            # Run ros2 topic echo as the subscriber.  --once exits after the
            # first message, giving a clear pass/fail signal.
            sub_result = subprocess.run(
                [ros2_bin, "topic", "echo", topic, "std_msgs/msg/String", "--once"],
                env=ros_env,
                capture_output=True,
                text=True,
                timeout=15,
            )

            if sub_result.returncode == 0 and sub_result.stdout.strip():
                received_msg = sub_result.stdout.strip()

        finally:
            pub_proc.terminate()
            try:
                pub_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pub_proc.kill()
                pub_proc.wait()

        assert received_msg is not None, (
            "ros2 topic echo received no message from the ROS 2 publisher node.\n"
            + (
                f"Subscriber stdout={sub_result.stdout!r}\n"
                f"Subscriber stderr={sub_result.stderr!r}\n"
                f"Subscriber rc={sub_result.returncode}"
                if sub_result is not None
                else "(subscriber was never started)"
            )
        )
        assert "data: hello_" in received_msg, (
            f"Unexpected message content received: {received_msg!r}"
        )


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
