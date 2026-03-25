# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""Configuration discovery and parsing for colcon-systemd."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class ConfigError(Exception):
    """Raised when a colcon-systemd.yaml configuration is invalid."""


class ServiceConfig:
    """Parsed configuration for a single systemd service."""

    def __init__(
        self,
        *,
        name: str,
        entry_point: Optional[str] = None,
        executable: Optional[str] = None,
        description: str = "",
        after: Optional[List[str]] = None,
        environment: Optional[Dict[str, str]] = None,
        restart: str = "on-failure",
        working_directory: Optional[str] = None,
    ) -> None:
        self.name = name
        self.entry_point = entry_point
        self.executable = executable
        self.description = description
        self.after = after or ["network.target"]
        self.environment = environment or {}
        self.restart = restart
        self.working_directory = working_directory

    def __repr__(self) -> str:
        return f"ServiceConfig(name={self.name!r})"


class PackageSystemdConfig:
    """Parsed colcon-systemd.yaml for a single package."""

    def __init__(self, *, services: List[ServiceConfig]) -> None:
        self.services = services

    def __repr__(self) -> str:
        return f"PackageSystemdConfig(services={self.services!r})"


def find_config(package_path: Path) -> Optional[Path]:
    """
    Find a colcon-systemd.yaml config file in a package directory.

    :param package_path: Path to the package source directory.
    :returns: Path to the config file, or None if not found.
    """
    config_path = package_path / "colcon-systemd.yaml"
    if config_path.is_file():
        return config_path
    return None


def _validate_service(service_dict: Any, index: int) -> ServiceConfig:
    """Validate and parse a single service definition from the config."""
    if not isinstance(service_dict, dict):
        raise ConfigError(
            f"Service at index {index} must be a mapping, "
            f"got {type(service_dict).__name__}"
        )

    name = service_dict.get("name")
    if not name or not isinstance(name, str):
        raise ConfigError(
            f"Service at index {index} must have a non-empty string 'name'"
        )

    # Validate name contains only safe characters
    if not all(c.isalnum() or c in "-_" for c in name):
        raise ConfigError(
            f"Service name '{name}' contains invalid characters. "
            "Only alphanumeric, hyphens, and underscores are allowed."
        )

    entry_point = service_dict.get("entry_point")
    executable = service_dict.get("executable")

    if not entry_point and not executable:
        raise ConfigError(
            f"Service '{name}' must specify either 'entry_point' or 'executable'"
        )
    if entry_point and executable:
        raise ConfigError(
            f"Service '{name}' must specify only one of 'entry_point' or 'executable', "
            "not both"
        )

    if entry_point and not isinstance(entry_point, str):
        raise ConfigError(
            f"Service '{name}': 'entry_point' must be a string"
        )
    if executable and not isinstance(executable, str):
        raise ConfigError(
            f"Service '{name}': 'executable' must be a string"
        )

    description = service_dict.get("description", "")
    if not isinstance(description, str):
        raise ConfigError(
            f"Service '{name}': 'description' must be a string"
        )

    after = service_dict.get("after")
    if after is not None:
        if not isinstance(after, list) or not all(
            isinstance(a, str) for a in after
        ):
            raise ConfigError(
                f"Service '{name}': 'after' must be a list of strings"
            )

    environment = service_dict.get("environment")
    if environment is not None:
        if not isinstance(environment, dict):
            raise ConfigError(
                f"Service '{name}': 'environment' must be a mapping"
            )
        for k, v in environment.items():
            if not isinstance(k, str) or not isinstance(v, (str, int, float)):
                raise ConfigError(
                    f"Service '{name}': 'environment' keys must be strings "
                    "and values must be strings or numbers"
                )

    valid_restart = {"no", "on-success", "on-failure", "on-abnormal",
                     "on-watchdog", "on-abort", "always"}
    restart = service_dict.get("restart", "on-failure")
    if not isinstance(restart, str) or restart not in valid_restart:
        raise ConfigError(
            f"Service '{name}': 'restart' must be one of {sorted(valid_restart)}"
        )

    working_directory = service_dict.get("working_directory")
    if working_directory is not None and not isinstance(working_directory, str):
        raise ConfigError(
            f"Service '{name}': 'working_directory' must be a string"
        )

    return ServiceConfig(
        name=name,
        entry_point=entry_point,
        executable=executable,
        description=description,
        after=after,
        environment={k: str(v) for k, v in (environment or {}).items()},
        restart=restart,
        working_directory=working_directory,
    )


def parse_config(config_path: Path) -> PackageSystemdConfig:
    """
    Parse and validate a colcon-systemd.yaml file.

    :param config_path: Path to the YAML config file.
    :returns: Parsed configuration.
    :raises ConfigError: If the config is invalid.
    """
    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(
            f"{config_path}: expected a YAML mapping at the top level, "
            f"got {type(raw).__name__}"
        )

    services_raw = raw.get("services")
    if not services_raw:
        raise ConfigError(
            f"{config_path}: 'services' key is required and must be non-empty"
        )

    if not isinstance(services_raw, list):
        raise ConfigError(
            f"{config_path}: 'services' must be a list"
        )

    services = [
        _validate_service(svc, i)
        for i, svc in enumerate(services_raw)
    ]

    return PackageSystemdConfig(services=services)
