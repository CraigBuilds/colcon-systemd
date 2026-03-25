# colcon-systemd

Colcon plugin that generates systemd service units for opted-in packages during
`colcon build`, installable with `pip install`.

## Installation

### Prerequisites

colcon-systemd requires a few companion colcon plugins to be installed in the
same Python environment:

| Package | Purpose |
|---------|---------|
| `colcon-bash` | Generates `setup.bash` in the install tree (sourced by the wrapper scripts) |
| `colcon-python-setup-py` | Builds Python packages (`setup.py`/`setup.cfg`) |
| `colcon-recursive-crawl` | Discovers packages in subdirectories (e.g. `src/<pkg>/`) |

Install them all at once:

```bash
pip install colcon-bash colcon-python-setup-py colcon-recursive-crawl
```

> **Note:** Without `colcon-bash`, the `install/setup.bash` file is never
> created and every generated wrapper script will fail at runtime.  Without
> `colcon-recursive-crawl`, colcon will not find any packages inside your
> `src/` directory.

### From PyPI (once published)

Install into your current Python environment (the same one where `colcon` is
installed):

```bash
pip install colcon-systemd colcon-bash colcon-python-setup-py colcon-recursive-crawl
```

This installs the plugin into your user or virtualenv site-packages — it does
**not** install anything system-wide or require root.  colcon discovers the
plugin automatically via entry points.

### From Source (development)

```bash
git clone https://github.com/CraigBuilds/colcon-systemd.git
cd colcon-systemd
pip install -e ".[test]"
pip install colcon-bash colcon-python-setup-py colcon-recursive-crawl
```

The `-e` (editable) flag means changes you make to the source take effect
immediately without reinstalling.  Run this from the cloned `colcon-systemd/`
directory.

### Verifying the Installation

After installing, confirm colcon can see the plugin:

```bash
# Check that the entry point is registered:
python -c "
import importlib.metadata, sys
eps = list(importlib.metadata.entry_points(group='colcon_core.event_handler'))
names = [ep.name for ep in eps]
print('Registered event handlers:', names)
if 'systemd' not in names:
    print('ERROR: colcon-systemd is NOT registered!', file=sys.stderr)
    sys.exit(1)
print('colcon-systemd is registered correctly.')
"
```

You can also do a quick sanity-check build in an empty directory:

```bash
mkdir /tmp/check_ws && cd /tmp/check_ws
colcon build --event-handlers systemd+
# No "unknown event handler" error → plugin is registered
```

## Quick Start

### 1. Add a Config File to Your Package

In the source root of a ROS 2 / colcon package (next to `package.xml`), create
a `colcon-systemd.yaml`:

```yaml
services:
  - name: my_node
    entry_point: my_node          # console_scripts entry point name
    description: "My ROS 2 node"
    environment:
      ROS_DOMAIN_ID: "0"
    restart: on-failure
```

### 2. Build from Your Workspace Root

Run `colcon build` from the **workspace root** (the directory that contains
`src/`, or wherever your packages live — the same place you'd normally run
`colcon build`):

```bash
cd ~/my_ros2_ws    # your colcon workspace root
colcon build
```

The plugin runs automatically for any package that contains a
`colcon-systemd.yaml` file.  Packages without the file are unaffected.

`--symlink-install` is fully supported and does not affect how colcon-systemd
locates entry points or generates service files:

```bash
colcon build --symlink-install
```

### 3. Find the Generated Files

The generated service files are written into the colcon **install tree** — they
are never written to `/etc/systemd/system` or any system directory:

```
install/<pkg>/share/colcon-systemd/my_node.service   # systemd unit
install/<pkg>/share/colcon-systemd/my_node.sh         # wrapper script
```

### 4. Activate the Service (Optional)

To actually run the service with systemd, symlink the unit into your user
systemd directory:

```bash
mkdir -p ~/.config/systemd/user
ln -s "$(pwd)/install/<pkg>/share/colcon-systemd/my_node.service" \
      ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start my_node
systemctl --user status my_node
```

This uses `systemctl --user`, which does not require root.

## Configuration Reference

Each service in `colcon-systemd.yaml` accepts:

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | — | Service name (alphanumeric, hyphens, underscores) |
| `entry_point` | One of entry_point/executable | — | console_scripts entry point name |
| `executable` | One of entry_point/executable | — | Executable name |
| `description` | No | `<pkg> <name>` | systemd unit description |
| `after` | No | `[network.target]` | systemd `After=` dependencies |
| `environment` | No | `{}` | Environment variables |
| `restart` | No | `on-failure` | systemd restart policy |
| `working_directory` | No | — | Working directory for the service |
| `args` | No | `[]` | Command-line arguments passed to the executable |

## Node Execution

### How Nodes Are Started

colcon-systemd does **not** use `ros2 run`.  Instead, it generates a small
bash **wrapper script** that sources the workspace `setup.bash` and then
`exec`s the installed entry point directly:

```bash
# Generated wrapper script (my_node.sh)
#!/usr/bin/env bash
set -eo pipefail
source "/path/to/install/setup.bash"
exec "/path/to/install/my_pkg/lib/my_pkg/my_node" "$@"
```

The systemd unit's `ExecStart` calls this wrapper, so systemd tracks the real
process PID and can send signals directly.

### Python and C++ Nodes

Both are fully supported:

- **Python (ament_python / python):** `colcon` installs a launcher script into
  `install/<pkg>/lib/<pkg>/<entry_point>` that already contains the correct
  `#!/usr/bin/env python3` shebang — no extra setup is needed.
- **C++ (ament_cmake / cmake):** the compiled binary is installed in the same
  path.  Use the `executable` field in the config instead of `entry_point`.

### Passing Arguments to Your Node

Use the `args` field in `colcon-systemd.yaml` to pass command-line arguments
(equivalent to the extra flags you'd give `ros2 run`):

```yaml
services:
  - name: my_node
    entry_point: my_node
    args:
      - "--arg1"
      - "val1"
      - "--arg2"
      - "val2"
```

A plain string is also accepted and split on whitespace:

```yaml
args: "--arg1 val1 --arg2 val2"
```

These arguments are appended to `ExecStart` in the generated service unit and
forwarded to the executable via the wrapper script's `"$@"`.

### Workspace Sourcing and ROS 2 Underlays

The wrapper script sources `install/setup.bash` from the colcon workspace root.
This file is generated by `colcon-bash` and **chains your ROS 2 underlay
automatically** (e.g., `/opt/ros/humble/setup.bash`) when the workspace was
built with the underlay already sourced.

> **Do not rely on `~/.bashrc` for services.**  systemd user services do not
> read `~/.bashrc`, so any workspace sourcing done there will be absent when
> the service runs.  The wrapper script handles all sourcing correctly without
> `~/.bashrc`.

If you use multiple overlapping workspaces, the generated `setup.bash` from
each workspace already chains into the next.  The wrapper sources only the
top-level workspace's `setup.bash`, which is sufficient in the common case.

## How It Works

colcon-systemd registers as a colcon **event handler** extension.  During
`colcon build`, it listens for successful build completions (`JobEnded` events
with `rc == 0`).  For each package that has a `colcon-systemd.yaml` file, it:

1. Parses and validates the configuration
2. Generates a bash wrapper script that sources `setup.bash` and `exec`s the
   entry point
3. Generates a systemd `.service` unit file that runs the wrapper script

This approach uses **direct execution** of the installed entry point rather
than `ros2 run`, for simpler signal handling and no runtime dependency on the
`ros2` CLI.  See [DESIGN.md](DESIGN.md) for detailed rationale.

## Supported Package Types

| Type | Support |
|------|---------|
| `ros.ament_python` | Primary (best tested) |
| `python` | Full |
| `ros.ament_cmake` | Full |
| `cmake` | Full |
| `ros.catkin` | Full |

Other types generate a warning but still produce units.

## Disabling the Extension

```bash
colcon build --event-handlers systemd-
```

## Testing

```bash
pip install -e ".[test]"
pytest tests/ -v
```

## Recommended Next Steps

The following improvements would take this plugin from beta to production-ready:

### Robustness
- **Post-generation validation**: after writing the wrapper script, verify that
  the target executable actually exists in the install tree and emit a clear
  warning (not a silent failure) when it doesn't.
- **Atomic writes**: write files to a `.tmp` path and rename atomically so that
  a partial failure never leaves a half-written service unit on disk.
- **`colcon-bash` guard**: at handler start-up, check whether `setup.bash` will
  be generated (i.e. `colcon-bash` is available) and warn the user if not,
  rather than letting the wrapper fail at runtime.

### Features
- **`install` sub-command / helper**: add a `colcon systemd install` convenience
  command that symlinks the generated `.service` files into
  `~/.config/systemd/user/` and runs `systemctl --user daemon-reload`.
- **System-level deployment**: add an `install_mode` option (`user` / `system`)
  to the YAML config; for `system` mode, generate units with `User=` and
  `Group=` directives and document how to copy them to `/etc/systemd/system`.
- **`wants` / `requires` relationships**: support `Wants=` and `Requires=`
  between services in the same workspace (e.g. a DDS discovery node that other
  nodes depend on).
- **`EnvironmentFile=` support**: allow loading env vars from a file path in
  addition to inline `Environment=` directives — useful for secrets or
  machine-specific configuration.
- **Multi-workspace overlays**: document and/or support sourcing a chain of
  `setup.bash` files when a ROS 2 underlay is involved.

### Developer Experience
- **Publish to PyPI**: make the package available via `pip install colcon-systemd`.
- **`colcon-argcomplete` integration**: register tab-completion hints for the
  `--event-handlers systemd+/systemd-` flags.
- **Pre-built example workspace**: add a `examples/` directory with a
  ready-to-clone multi-package ROS 2 workspace that demonstrates the full
  workflow end-to-end.
- **VS Code / devcontainer integration**: provide a `.devcontainer/` that
  installs all prerequisites so contributors can get started with one click.

## License

Apache-2.0
