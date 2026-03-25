"""Minimal node for colcon-systemd integration testing."""

import signal
import sys
import time


def main() -> None:
    """Entry point that runs as a long-lived daemon process.

    Prints SIMPLE_NODE_RUNNING on startup, then emits SIMPLE_NODE_HEARTBEAT
    every 0.5 s until SIGTERM/SIGINT is received, at which point it prints
    SIMPLE_NODE_STOPPED and exits 0.

    Pass --once to exit immediately after the startup message (useful for
    quick wrapper-script validation without starting a long-running process).
    """
    print("SIMPLE_NODE_RUNNING", flush=True)

    if "--once" in sys.argv:
        sys.exit(0)

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
