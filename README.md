# colcon-systemd

Colcon plugin that generates systemd service units for opted-in packages during
`colcon build`, installable with `pip install`.

## Installation

```bash
pip install colcon-systemd
```

Or for development:

```bash
git clone https://github.com/CraigBuilds/colcon-systemd.git
cd colcon-systemd
pip install -e ".[test]"
```

## Quick Start

1. Add a `colcon-systemd.yaml` to your package source root:

```yaml
services:
  - name: my_node
    entry_point: my_node          # console_scripts entry point name
    description: "My ROS 2 node"
    environment:
      ROS_DOMAIN_ID: "0"
    restart: on-failure
```

2. Build with colcon:

```bash
colcon build
```

3. Find the generated files in the install tree:

```
install/<pkg>/share/colcon-systemd/my_node.service
install/<pkg>/share/colcon-systemd/my_node.sh
```

4. Install the user service:

```bash
mkdir -p ~/.config/systemd/user
ln -s "$(pwd)/install/<pkg>/share/colcon-systemd/my_node.service" \
      ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start my_node
systemctl --user status my_node
```

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
