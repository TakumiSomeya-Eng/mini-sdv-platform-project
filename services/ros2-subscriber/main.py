#!/usr/bin/env python3
"""
ROS2 Subscriber — mini-sdv-platform  Milestone 3
=================================================
A minimal ROS2 node that subscribes to all three vehicle signal topics
published by ros2-bridge and logs each received message to stdout.

Purpose:
  This service is a VERIFICATION TOOL only — it has no business logic.
  It exists to prove that ros2-bridge is publishing correctly and that
  DDS discovery between the two containers works as expected.

  Verification:
    docker compose logs -f ros2-subscriber

  Expected output (≥ 1 Hz per topic):
    [/vehicle/speed] value=87.3
    [/vehicle/battery/soc] value=72.4
    [/vehicle/cabin/temperature] value=22.1

DDS discovery:
  ros2-bridge and ros2-subscriber discover each other automatically via
  CycloneDDS Simple Discovery within the sdv-net Docker bridge network.
  No explicit connection address is needed — only matching ROS_DOMAIN_ID
  and the CYCLONEDDS_URI peer list (set via docker-compose.yml env vars).

Real-world equivalent:
  In a production autonomous driving stack (e.g. Autoware), this role
  would be played by a path-planner node, an emergency-braking node,
  or a sensor-fusion node — any ROS2 node that needs vehicle speed,
  battery state, or cabin temperature to make decisions.
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

TOPICS = [
    "/vehicle/speed",
    "/vehicle/battery/soc",
    "/vehicle/cabin/temperature",
]


class VehicleSignalSubscriberNode(Node):
    """
    ROS2 node that subscribes to all vehicle signal topics and logs them.

    One subscription is created per topic in __init__. The lambda callback
    logs the topic name and float value on every message received.
    """

    def __init__(self) -> None:
        super().__init__("vehicle_signal_subscriber")

        for topic in TOPICS:
            # QoS depth = 10 matches the publisher side in ros2-bridge.
            # The lambda captures `topic` by value (t=topic) to avoid the
            # classic Python closure-in-loop bug.
            self.create_subscription(
                Float32,
                topic,
                lambda msg, t=topic: self.get_logger().info(
                    f"[{t}] value={msg.data}"
                ),
                qos_profile=10,
            )

        self.get_logger().info(f"Subscribed to {len(TOPICS)} topics:")
        for topic in TOPICS:
            self.get_logger().info(f"  {topic}")


def main() -> None:
    rclpy.init()
    node = VehicleSignalSubscriberNode()
    try:
        # spin() blocks until the node is shut down (SIGINT / docker stop).
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
