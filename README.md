# colcon-systemd

Colcon plugin that generates systemd service units for opted-in packages during
`colcon build`, installable with `pip install`.

## Installation

### From PyPI (once published)

Install into your current Python environment (the same one where `colcon` is
installed):

```bash
pip install colcon-systemd
```

This installs the plugin into your user or virtualenv site-packages — it does
**not** install anything system-wide or require root.  colcon discovers the
plugin automatically via entry points.

### From Source (development)

```bash
git clone https://github.com/CraigBuilds/colcon-systemd.git
cd colcon-systemd
pip install -e ".[test]"
```

The `-e` (editable) flag means changes you make to the source take effect
immediately without reinstalling.  Run this from the cloned `colcon-systemd/`
directory.

### Verifying the Installation

After installing, confirm colcon can see the plugin:

```bash
colcon build --event-handlers systemd+
# You should not get "unknown event handler" errors
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

## License

Apache-2.0
