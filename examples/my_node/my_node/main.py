# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""Minimal ROS 2 publisher node managed by colcon-systemd."""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String


class MinimalPublisher(Node):
    """Publishes a counter message on ``/my_node/chatter`` every second."""

    def __init__(self) -> None:
        """Create publisher and 1 Hz timer."""
        super().__init__('my_node')
        self.publisher_ = self.create_publisher(String, 'my_node/chatter', 10)
        self.timer = self.create_timer(1.0, self.timer_callback)
        self._count = 0
        self.get_logger().info('my_node started — publishing on /my_node/chatter')

    def timer_callback(self) -> None:
        """Publish a String message with an incrementing counter."""
        msg = String()
        msg.data = f'hello from my_node: count={self._count}'
        self.publisher_.publish(msg)
        self._count += 1


def main(args=None) -> None:
    """Start the MinimalPublisher node and spin until shutdown."""
    rclpy.init(args=args)
    node = MinimalPublisher()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # KeyboardInterrupt: Ctrl-C from the terminal.
        # ExternalShutdownException: rclpy SIGTERM handler called try_shutdown()
        # before spin() returned; the context is already being torn down.
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
