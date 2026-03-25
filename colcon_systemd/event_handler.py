# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""
Event handler that generates systemd service units after successful builds.

This is the main extension point for colcon-systemd.  It registers as a
colcon_core.event_handler and listens for ``JobEnded`` events.  For each
successful build of a package that contains a ``colcon-systemd.yaml`` file,
it generates systemd service unit files and wrapper scripts in the install
tree.

The handler is **enabled by default** so that a plain ``colcon build``
generates the units for configured packages without extra flags.
"""

import logging
import platform
import sys
from pathlib import Path

from colcon_core.event.job import JobEnded
from colcon_core.event_handler import EventHandlerExtensionPoint
from colcon_core.plugin_system import satisfies_version

from colcon_systemd.config import ConfigError, find_config, parse_config
from colcon_systemd.render import write_service_files

logger = logging.getLogger(__name__)

# Supported package types.  ament_python is the primary path.
_SUPPORTED_TYPES = frozenset({
    "ros.ament_python",
    "ros.ament_cmake",
    "ros.catkin",
    "cmake",
    "python",
})


class SystemdEventHandler(EventHandlerExtensionPoint):
    """
    Generate systemd service units for opted-in packages.

    A package opts in by placing a ``colcon-systemd.yaml`` file in its source
    root.  After a successful build of that package, this handler reads the
    config, renders systemd ``.service`` files and wrapper scripts, and writes
    them into the install tree.

    The handler is enabled by default (``--event-handlers systemd+``).
    Disable with ``--event-handlers systemd-``.
    """

    # Lower priority number = higher priority.  100 is the default.
    # We use 150 so that we run *after* the built-in console handlers.
    PRIORITY = 150

    def __init__(self) -> None:  # noqa: D107
        super().__init__()
        satisfies_version(
            EventHandlerExtensionPoint.EXTENSION_POINT_VERSION, "^1.0"
        )
        self._generated: list[Path] = []

    def __call__(self, event) -> None:  # noqa: D102
        data = event[0]

        if not isinstance(data, JobEnded):
            return

        # Only act on successful builds
        if data.rc:
            return

        # Only run on Linux
        if platform.system() != "Linux":
            return

        job = event[1]
        task_context = job.task_context
        pkg = task_context.pkg
        args = task_context.args

        # Check if this is a build verb (args should have install_base)
        install_base_str = getattr(args, "install_base", None)
        if install_base_str is None:
            return

        install_base = Path(install_base_str).resolve()
        merge_install = getattr(args, "merge_install", False)
        package_path = Path(str(pkg.path)).resolve()

        # Look for colcon-systemd.yaml
        config_path = find_config(package_path)
        if config_path is None:
            return

        package_name = pkg.name
        package_type = pkg.type or "unknown"

        if package_type not in _SUPPORTED_TYPES:
            logger.warning(
                "[colcon-systemd] Package '%s' has type '%s' which is not in "
                "the supported set %s.  Generating units anyway, but the "
                "executable path may need adjustment.",
                package_name,
                package_type,
                sorted(_SUPPORTED_TYPES),
            )

        # Parse configuration
        try:
            config = parse_config(config_path)
        except ConfigError as exc:
            print(
                f"[colcon-systemd] ERROR: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return

        # Generate service files for each configured service
        for service in config.services:
            try:
                service_path = write_service_files(
                    install_base=install_base,
                    package_name=package_name,
                    service=service,
                    package_type=package_type,
                    merge_install=merge_install,
                )
                if service_path:
                    self._generated.append(service_path)
                    print(
                        f"[colcon-systemd] Generated {service_path}",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"[colcon-systemd] ERROR generating service "
                    f"'{service.name}' for package '{package_name}': {exc}",
                    file=sys.stderr,
                    flush=True,
                )
