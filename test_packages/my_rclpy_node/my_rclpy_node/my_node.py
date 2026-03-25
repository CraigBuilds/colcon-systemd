# Copyright 2024 CraigBuilds
# Licensed under the Apache License, Version 2.0
"""Minimal rclpy node for colcon-systemd integration testing."""

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class MinimalPublisher(Node):
    """A minimal ROS 2 publisher node."""

    def __init__(self) -> None:
        super().__init__("minimal_publisher")
        self.publisher_ = self.create_publisher(String, "colcon_systemd_test", 10)
        self.timer = self.create_timer(1.0, self.timer_callback)
        self.get_logger().info("MinimalPublisher started")

    def timer_callback(self) -> None:
        msg = String()
        msg.data = "colcon-systemd-test"
        self.publisher_.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MinimalPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
