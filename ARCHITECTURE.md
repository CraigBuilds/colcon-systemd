# Architecture — colcon-systemd

This document is a developer-oriented reference for the colcon-systemd codebase.
For design rationale and tradeoff analysis see [DESIGN.md](DESIGN.md).
For user-facing documentation see [README.md](README.md).

## Repository Layout

```
colcon-systemd/
├── colcon_systemd/              # Python package (the plugin)
│   ├── __init__.py              # License header
│   ├── event_handler.py         # Extension point — orchestrates generation
│   ├── config.py                # YAML parsing and validation
│   └── render.py                # Template rendering and file I/O
├── tests/                       # Test suite
│   ├── test_config.py           # Unit tests for config.py
│   ├── test_event_handler.py    # Unit tests for event_handler.py
│   ├── test_render.py           # Unit tests for render.py
│   ├── test_end_to_end.py       # Integration tests (full pipeline)
│   ├── test_colcon_build_e2e.py # Pytest entry point for the bash e2e test
│   └── test_colcon_build_e2e.sh # Bash e2e test against a real colcon build
├── test_packages/               # Example packages used in the bash e2e test
│   ├── simple_node/             # Python package with one service
│   └── my_rclpy_node/           # ROS 2 ament_python package
├── DESIGN.md                    # Design rationale and tradeoffs
├── README.md                    # User documentation
└── pyproject.toml               # Package metadata and entry points
```

## Modules

### `event_handler.py` — Extension Point

`SystemdEventHandler` is the entry point registered with colcon's plugin system.
It extends `colcon_core.event_handler.EventHandlerExtensionPoint` and is
discovered automatically by colcon via the `colcon_core.event_handler` entry
point group in `pyproject.toml`.

**Key attributes:**

| Attribute | Value | Notes |
|-----------|-------|-------|
| `PRIORITY` | `150` | Runs after built-in console handlers (priority 100) |
| `_generated` | `list[Path]` | Accumulates paths of generated `.service` files |

**Guard conditions** (each returns early silently):

1. `not isinstance(data, JobEnded)` — ignore non-build events
2. `data.rc != 0` — ignore failed builds
3. `platform.system() != "Linux"` — skip non-Linux platforms
4. `args.install_base is None` — skip non-build verbs
5. `find_config(package_path) is None` — skip packages without `colcon-systemd.yaml`

**Unsupported package type:** Logs a warning via `logger.warning` but continues
generating — the executable path convention may need manual adjustment.

**Error handling:**

- `ConfigError` → prints to `stderr`, returns without generating any services
- Any `Exception` during file generation → prints to `stderr` for that service,
  continues to the next service in the list

---

### `config.py` — YAML Parsing

Provides data classes and parsing functions for `colcon-systemd.yaml`.

**Classes:**

```
PackageSystemdConfig
  └── services: List[ServiceConfig]

ServiceConfig
  ├── name: str                    (required)
  ├── entry_point: Optional[str]   (mutually exclusive with executable)
  ├── executable: Optional[str]    (mutually exclusive with entry_point)
  ├── description: str             (default: "")
  ├── after: List[str]             (default: ["network.target"])
  ├── environment: Dict[str, str]  (default: {})
  ├── restart: str                 (default: "on-failure")
  └── working_directory: Optional[str]
```

**Public functions:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `find_config` | `(package_path: Path) -> Optional[Path]` | Return path to `colcon-systemd.yaml` or `None` |
| `parse_config` | `(config_path: Path) -> PackageSystemdConfig` | Parse and validate the YAML file |

`parse_config` raises `ConfigError` (a plain `Exception` subclass) for any
structural or semantic validation failure.  Numeric environment variable values
are coerced to strings at parse time.

---

### `render.py` — Template Rendering

Renders inline Python-string templates (no Jinja2) for the wrapper script and
the systemd unit file, and writes them to the install tree.

**Templates (inline string constants):**

| Constant | Output file | Key substitution fields |
|----------|-------------|------------------------|
| `_WRAPPER_TEMPLATE` | `<service>.sh` | `{setup_bash}`, `{executable}` |
| `_SERVICE_TEMPLATE` | `<service>.service` | `{description}`, `{after}`, `{exec_start}`, `{environment_lines}`, `{restart}`, `{working_directory_line}` |

**Public functions:**

| Function | Purpose |
|----------|---------|
| `render_wrapper_script(...)` | Return wrapper script content as `str` |
| `render_service_unit(...)` | Return unit file content as `str` |
| `write_service_files(...)` | Create output directory, write both files, set `chmod 0o755` on the wrapper, return `Path` to `.service` |

**Private helpers:**

| Function | Purpose |
|----------|---------|
| `_resolve_setup_bash(install_base, merge_install)` | Locate `setup.bash` in the install tree |
| `_resolve_executable_path(install_base, pkg, service, pkg_type)` | Build path `<install_base>/lib/<pkg>/<entry_point_or_executable>` |

## Data Flow

```
colcon build
│
├── Plugin discovery (import time)
│   └── importlib.metadata loads SystemdEventHandler via entry_points
│
└── For each package:
    [colcon runs the build task]
    │
    └── JobEnded(rc=0) event emitted
        │
        └── SystemdEventHandler.__call__(event)
            │
            ├── Guard checks (platform, rc, install_base, config file)
            │
            ├── config.find_config(package_path)
            │   └── returns Path to colcon-systemd.yaml (or None → return)
            │
            ├── config.parse_config(config_path)
            │   └── returns PackageSystemdConfig (or raises ConfigError → stderr)
            │
            └── For each ServiceConfig in config.services:
                │
                └── render.write_service_files(install_base, pkg_name, service, ...)
                    │
                    ├── render_wrapper_script()
                    │   ├── _resolve_setup_bash()    → path to setup.bash
                    │   ├── _resolve_executable_path() → path to binary
                    │   └── returns rendered .sh content
                    │
                    ├── Write <service>.sh, chmod 0o755
                    │
                    ├── render_service_unit()
                    │   └── returns rendered .service content
                    │
                    └── Write <service>.service → returns Path
```

## Extension Point Registration

colcon discovers plugins at runtime by calling
`importlib.metadata.entry_points(group=...)`.  The registration is in
`pyproject.toml`:

```toml
[project.entry-points.'colcon_core.event_handler']
systemd = "colcon_systemd.event_handler:SystemdEventHandler"
```

The extension can be controlled on the command line:

```bash
colcon build                              # enabled by default
colcon build --event-handlers systemd+   # explicitly enabled
colcon build --event-handlers systemd-   # disabled
```

## Generated Output

For a package named `my_pkg` with service name `my_node`, two files are created
under `<install_base>/share/colcon-systemd/`:

**`my_node.sh`** (mode `0755`):

```bash
#!/usr/bin/env bash
# Auto-generated by colcon-systemd — do not edit
# Note: -u (nounset) is intentionally omitted because colcon's setup.bash
# references variables like COLCON_TRACE that may be unset.
set -eo pipefail
source "<install_root>/setup.bash"
exec "<install_base>/lib/my_pkg/my_node" "$@"
```

**`my_node.service`**:

```ini
[Unit]
Description=my_pkg my_node
After=network.target

[Service]
Type=simple
ExecStart=<install_base>/share/colcon-systemd/my_node.sh
Environment="ROS_DOMAIN_ID=0"
Restart=on-failure

[Install]
WantedBy=default.target
```

`<install_root>` is:

- **Isolated mode** (default): `<install_base>/..` (one level above the
  per-package prefix)
- **`--merge-install` mode**: `<install_base>` (the prefix itself)

## Configuration Schema

`colcon-systemd.yaml` (placed in the package source root):

```yaml
services:
  - name: <str>                    # required; [a-zA-Z0-9_-]
    entry_point: <str>             # one of entry_point/executable required
    executable: <str>              #   (mutually exclusive with entry_point)
    description: <str>             # optional
    after:                         # optional; default: [network.target]
      - <str>
    environment:                   # optional
      KEY: value
    restart: <str>                 # optional; default: on-failure
    working_directory: <str>       # optional
```

Valid `restart` values: `no`, `on-success`, `on-failure`, `on-abnormal`,
`on-watchdog`, `on-abort`, `always`.

## Testing

```bash
pip install -e ".[test]"
pytest tests/ -v
```

| Test file | Scope |
|-----------|-------|
| `test_config.py` | Unit — YAML parsing, validation errors |
| `test_event_handler.py` | Unit — event filtering, guard conditions, error handling |
| `test_render.py` | Unit — template rendering, file writing, path resolution |
| `test_end_to_end.py` | Integration — full pipeline from event to generated files and executed wrappers |
| `test_colcon_build_e2e.py/.sh` | E2E — real `colcon build` against `test_packages/` |

Most tests that exercise generated files use `@pytest.mark.skipif(platform.system() != "Linux", ...)`.

The bash e2e test (`test_colcon_build_e2e.sh`) requires `colcon-bash`,
`colcon-python-setup-py`, and `colcon-recursive-crawl` to be installed in the
same environment.

## Dependencies

| Dependency | Used for |
|------------|----------|
| `colcon-core>=0.12.0` | `EventHandlerExtensionPoint`, `JobEnded`, plugin infrastructure |
| `PyYAML>=5.0` | `yaml.safe_load` in `config.py` |
