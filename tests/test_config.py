# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""Tests for colcon_systemd.config module."""

import textwrap
from pathlib import Path

import pytest

from colcon_systemd.config import (
    ConfigError,
    PackageSystemdConfig,
    find_config,
    parse_config,
)


# ---------------------------------------------------------------------------
# find_config
# ---------------------------------------------------------------------------

class TestFindConfig:
    """Tests for the find_config function."""

    def test_returns_path_when_config_exists(self, tmp_path: Path) -> None:
        config_file = tmp_path / "colcon-systemd.yaml"
        config_file.write_text("services: []")
        assert find_config(tmp_path) == config_file

    def test_returns_none_when_no_config(self, tmp_path: Path) -> None:
        assert find_config(tmp_path) is None

    def test_returns_none_when_config_is_directory(self, tmp_path: Path) -> None:
        (tmp_path / "colcon-systemd.yaml").mkdir()
        assert find_config(tmp_path) is None


# ---------------------------------------------------------------------------
# parse_config — valid cases
# ---------------------------------------------------------------------------

class TestParseConfigValid:
    """Tests for valid colcon-systemd.yaml configurations."""

    def _write_config(self, tmp_path: Path, content: str) -> Path:
        cfg = tmp_path / "colcon-systemd.yaml"
        cfg.write_text(textwrap.dedent(content))
        return cfg

    def test_minimal_entry_point(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: my_node
                entry_point: my_node
        """)
        result = parse_config(cfg)
        assert isinstance(result, PackageSystemdConfig)
        assert len(result.services) == 1
        svc = result.services[0]
        assert svc.name == "my_node"
        assert svc.entry_point == "my_node"
        assert svc.executable is None
        assert svc.restart == "on-failure"
        assert svc.after == ["network.target"]
        assert svc.environment == {}

    def test_minimal_executable(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: talker
                executable: talker_node
        """)
        result = parse_config(cfg)
        svc = result.services[0]
        assert svc.executable == "talker_node"
        assert svc.entry_point is None

    def test_full_config(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: my_node
                entry_point: my_node
                description: "Test node"
                after:
                  - network.target
                  - ros2.service
                environment:
                  ROS_DOMAIN_ID: "42"
                  RMW_IMPLEMENTATION: rmw_cyclonedds_cpp
                restart: always
                working_directory: /home/ros
        """)
        result = parse_config(cfg)
        svc = result.services[0]
        assert svc.description == "Test node"
        assert svc.after == ["network.target", "ros2.service"]
        assert svc.environment == {
            "ROS_DOMAIN_ID": "42",
            "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp",
        }
        assert svc.restart == "always"
        assert svc.working_directory == "/home/ros"

    def test_multiple_services(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: node_a
                entry_point: node_a
              - name: node_b
                executable: node_b
        """)
        result = parse_config(cfg)
        assert len(result.services) == 2
        assert result.services[0].name == "node_a"
        assert result.services[1].name == "node_b"

    def test_environment_numeric_values_converted_to_string(
        self, tmp_path: Path
    ) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: test
                entry_point: test
                environment:
                  ROS_DOMAIN_ID: 42
        """)
        result = parse_config(cfg)
        assert result.services[0].environment == {"ROS_DOMAIN_ID": "42"}


# ---------------------------------------------------------------------------
# parse_config — error cases
# ---------------------------------------------------------------------------

class TestParseConfigErrors:
    """Tests for invalid colcon-systemd.yaml configurations."""

    def _write_config(self, tmp_path: Path, content: str) -> Path:
        cfg = tmp_path / "colcon-systemd.yaml"
        cfg.write_text(textwrap.dedent(content))
        return cfg

    def test_not_a_mapping(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, "- item1\n- item2\n")
        with pytest.raises(ConfigError, match="expected a YAML mapping"):
            parse_config(cfg)

    def test_no_services_key(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, "other_key: value\n")
        with pytest.raises(ConfigError, match="'services' key is required"):
            parse_config(cfg)

    def test_services_not_a_list(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, "services: not_a_list\n")
        with pytest.raises(ConfigError, match="'services' must be a list"):
            parse_config(cfg)

    def test_service_not_a_mapping(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - just_a_string
        """)
        with pytest.raises(ConfigError, match="must be a mapping"):
            parse_config(cfg)

    def test_service_missing_name(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - entry_point: foo
        """)
        with pytest.raises(ConfigError, match="non-empty string 'name'"):
            parse_config(cfg)

    def test_service_no_entry_point_or_executable(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: test
        """)
        with pytest.raises(ConfigError, match="'entry_point' or 'executable'"):
            parse_config(cfg)

    def test_service_both_entry_point_and_executable(
        self, tmp_path: Path
    ) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: test
                entry_point: foo
                executable: bar
        """)
        with pytest.raises(ConfigError, match="only one of"):
            parse_config(cfg)

    def test_invalid_name_characters(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: "my node!"
                entry_point: test
        """)
        with pytest.raises(ConfigError, match="invalid characters"):
            parse_config(cfg)

    def test_invalid_restart_value(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: test
                entry_point: test
                restart: maybe
        """)
        with pytest.raises(ConfigError, match="'restart' must be one of"):
            parse_config(cfg)

    def test_after_not_a_list(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: test
                entry_point: test
                after: just_a_string
        """)
        with pytest.raises(ConfigError, match="'after' must be a list"):
            parse_config(cfg)

    def test_environment_not_a_mapping(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, """\
            services:
              - name: test
                entry_point: test
                environment: not_a_dict
        """)
        with pytest.raises(ConfigError, match="'environment' must be a mapping"):
            parse_config(cfg)

    def test_invalid_yaml_syntax(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, "{{invalid yaml")
        with pytest.raises(ConfigError, match="Failed to parse"):
            parse_config(cfg)

    def test_empty_services_list(self, tmp_path: Path) -> None:
        cfg = self._write_config(tmp_path, "services: []\n")
        with pytest.raises(ConfigError, match="'services' key is required"):
            parse_config(cfg)
