# Design Notes — colcon-systemd

## Architecture

colcon-systemd is a **colcon event-handler extension** that hooks into the
build lifecycle to generate systemd service unit files for opted-in packages.

```
colcon build
  ├── Package A builds successfully → JobEnded(rc=0)
  │   ├── Has colcon-systemd.yaml? → YES
  │   │   ├── Parse config
  │   │   ├── Render wrapper script (.sh)
  │   │   └── Render service unit (.service)
  │   └── Write files to install tree
  └── Package B builds successfully → JobEnded(rc=0)
      └── Has colcon-systemd.yaml? → NO → skip
```

### Extension Point

We use `colcon_core.event_handler.EventHandlerExtensionPoint` — the event
handler listens for `JobEnded` events with `rc == 0` (successful builds) and
checks each package for a `colcon-systemd.yaml` configuration file.

**Why event handler instead of a task extension?**

- An event handler is the least invasive integration point — it observes the
  build without modifying any build task.
- It works with *all* package types without needing per-type task overrides.
- It runs after the build is complete, so installed files are already in place.
- No risk of breaking the build if the extension has a bug (it only generates
  extra files).

**Caveat:** Event handlers run in the event reactor thread.  Heavy I/O should
be avoided; writing a few small text files is fine.

### Direct Execution vs `ros2 run`

We generate wrapper scripts that **directly execute the installed entry point**
rather than using `ros2 run`.

**Rationale:**

| Approach | Pros | Cons |
|----------|------|------|
| Direct execution | No dependency on `ros2` CLI at runtime; simpler signal handling; systemd can track the real PID | Requires a wrapper script to source setup.bash |
| `ros2 run` | Handles environment setup automatically | Extra process layer; depends on `ros2` CLI being installed; complicates PID tracking and signal delivery |

The wrapper script that sources `setup.bash` before exec-ing the entry point
gives us the best of both worlds: proper environment setup with direct process
management.

### Config Opt-in

Packages opt in by placing a `colcon-systemd.yaml` file in their source root:

```yaml
services:
  - name: my_node
    entry_point: my_node        # console_scripts entry point name
    description: "My ROS 2 node"
    environment:
      ROS_DOMAIN_ID: "42"
    restart: on-failure
```

Only packages with this file get service units generated.  All others are
silently skipped.

### Output Layout

Generated files are placed in the install tree under the package directory:

```
install/
└── my_pkg/
    └── share/
        └── colcon-systemd/
            ├── my_node.sh         # wrapper script
            └── my_node.service    # systemd unit file
```

This respects the constraint of not writing to `/etc/systemd/system`.
Users can symlink or copy units to their systemd user directory:

```bash
mkdir -p ~/.config/systemd/user
ln -s $(pwd)/install/my_pkg/share/colcon-systemd/my_node.service \
      ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start my_node
```

## Supported Package Types

| Type | Support Level | Notes |
|------|--------------|-------|
| `ros.ament_python` | Primary | Best tested; targets console_scripts entry points |
| `python` | Full | Same executable layout as ament_python |
| `ros.ament_cmake` | Full | Uses `executable` field for CMake-installed binaries |
| `cmake` | Full | Same as ament_cmake |
| `ros.catkin` | Full | Same as cmake |

Unsupported types produce a warning but still generate units (the executable
path convention may need manual adjustment).

## Tradeoffs

1. **Event handler vs. build task override:** We chose the non-invasive event
   handler approach.  The trade-off is that we cannot influence the build
   itself (e.g., we cannot add install rules).  This is acceptable because we
   only need to *read* the install tree, not modify the build.

2. **YAML config vs. package.xml export metadata:** We use a standalone YAML
   file for clarity and to avoid coupling with package.xml parsing.  Support
   for package.xml export metadata could be added in the future.

3. **Wrapper script:** The extra `.sh` file adds a layer of indirection.  The
   benefit is that it cleanly handles environment sourcing without modifying
   the original entry point.

## Limitations

- **Linux only:** systemd is Linux-specific.  The handler silently skips
  non-Linux platforms.
- **User-level units:** Generated units are for `systemctl --user`, not system
  services.  System-level deployment requires manual copying to
  `/etc/systemd/system` and adjusting `User=` / `Group=` fields.
- **No automatic installation:** The handler does not install units into
  systemd directories.  This is by design (unprivileged operation).
- **Entry point validation:** We do not verify that the entry point binary
  exists at generation time.  A future improvement could add a post-generation
  check.
- **Single workspace:** The wrapper script sources the workspace's `setup.bash`.
  Multi-workspace overlays require manual adjustment.
