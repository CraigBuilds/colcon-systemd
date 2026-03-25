"""Minimal node for colcon-systemd integration testing."""

import signal
import sys
import time


def main() -> None:
    """Entry point that prints a marker and optionally runs as a daemon."""
    print("SIMPLE_NODE_RUNNING", flush=True)

    # If --daemon flag is passed, run as a long-lived process
    if "--daemon" in sys.argv:
        running = True

        def handle_signal(signum, frame):
            nonlocal running
            print("SIMPLE_NODE_STOPPED", flush=True)
            running = False

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        while running:
            print("SIMPLE_NODE_HEARTBEAT", flush=True)
            time.sleep(0.5)

        sys.exit(0)


if __name__ == "__main__":
    main()
