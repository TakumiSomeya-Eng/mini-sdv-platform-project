#!/usr/bin/env python3
"""
ROS2 Bridge — mini-sdv-platform  Milestone 3
=============================================
Subscribes to vehicle signals from the Kuksa Databroker using gRPC streaming
and publishes each update as a ROS2 topic message (std_msgs/msg/Float32).

SDV Concept — the three middleware layers:
  This bridge adds the third consumer of the Kuksa Databroker:

    Layer 1: Dashboard (M1)     — gRPC poll  → human UI
    Layer 2: MQTT Bridge (M2)   — gRPC subscribe → MQTT → cloud
    Layer 3: ROS2 Bridge (M3)   — gRPC subscribe → DDS  → AD software stack

  In a production SDV platform (e.g., Autoware-based autonomous vehicle):
    - Kuksa Databroker runs on the Central Vehicle Computer
    - ROS2 nodes run on the autonomous driving compute platform
    - This bridge is the interface between the two middleware worlds

DDS vs. MQTT vs. gRPC:
  gRPC  (M1/M2/M3): Request/Response + Streaming — used for the Kuksa VAL API
  MQTT  (M2):       Pub/Sub via broker — IoT-scale, cloud-native
  DDS   (M3):       Pub/Sub brokerless — peer-to-peer discovery, real-time,
                    no single point of failure, mandatory in AUTOSAR Adaptive

Threading model:
  rclpy.spin() blocks the main thread — it must not be interrupted.
  subscribe_current_values() also blocks (gRPC streaming iterator).
  Solution: Kuksa subscribe loop runs in a daemon background thread;
  main thread is dedicated to rclpy.spin(). rclpy publishers are
  thread-safe, so background thread can call publish() directly.

ROS2 Topics:
  /vehicle/speed              ← Vehicle.Speed
  /vehicle/battery/soc        ← Vehicle.Powertrain.TractionBattery.StateOfCharge.Current
  /vehicle/cabin/temperature  ← Vehicle.Cabin.HVAC.AmbientAirTemperature

Message type: std_msgs/msg/Float32
  A single float32 data field.  Unit and VSS path metadata are not
  carried in the message in M3 (known limitation — custom msg in M4).
"""

import logging
import os
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

from kuksa_client.grpc import VSSClient

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ros2-bridge")

# ── Configuration ─────────────────────────────────────────────────────────────
DATABROKER_HOST = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT = int(os.environ.get("DATABROKER_PORT", "55555"))

# ── Signal Map (COVESA VSS 4.x paths → ROS2 topic names) ─────────────────────
# Each entry maps a VSS dot-notation path to its corresponding ROS2 topic.
# Unit metadata is stored here so it can be logged; it is NOT included in
# the std_msgs/Float32 payload (known limitation documented in TRD C21).
SIGNAL_MAP: dict[str, dict] = {
    "Vehicle.Speed": {
        "topic": "/vehicle/speed",
        "unit":  "km/h",
    },
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": {
        "topic": "/vehicle/battery/soc",
        "unit":  "percent",
    },
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": {
        "topic": "/vehicle/cabin/temperature",
        "unit":  "celsius",
    },
}

SIGNAL_PATHS = list(SIGNAL_MAP.keys())


# ── ROS2 Node ─────────────────────────────────────────────────────────────────

class VehicleSignalBridgeNode(Node):
    """
    ROS2 node that holds one publisher per vehicle signal.

    Publishers are created at init time and reused for the lifetime of the node.
    rclpy publishers are thread-safe for publish() calls, so the Kuksa
    background thread can call self.publish() without acquiring a lock.
    """

    def __init__(self) -> None:
        # Node name follows ROS2 snake_case convention.
        super().__init__("vehicle_signal_bridge")

        # QoS depth = 10: buffer up to 10 undelivered messages per topic.
        # Prevents loss during brief subscriber disconnects at 1 Hz signal rate.
        self._publishers: dict[str, object] = {
            path: self.create_publisher(Float32, meta["topic"], qos_profile=10)
            for path, meta in SIGNAL_MAP.items()
        }

        self.get_logger().info("VehicleSignalBridgeNode initialised.")
        for path, meta in SIGNAL_MAP.items():
            self.get_logger().info(f"  {path}  →  {meta['topic']}")

    def publish(self, vss_path: str, value: float) -> None:
        """Publish a single signal value to its ROS2 topic (thread-safe)."""
        msg = Float32()
        msg.data = value
        self._publishers[vss_path].publish(msg)
        self.get_logger().info(
            f"Published {SIGNAL_MAP[vss_path]['topic']} = {value} "
            f"{SIGNAL_MAP[vss_path]['unit']}"
        )


# ── Kuksa Subscribe Loop (background thread) ──────────────────────────────────

def kuksa_subscribe_loop(node: VehicleSignalBridgeNode) -> None:
    """
    Runs in a daemon background thread.

    Connects to the Kuksa Databroker, subscribes to all vehicle signals,
    and forwards each Datapoint update to the ROS2 node for publishing.

    Uses the same exponential back-off reconnect pattern as mqtt-bridge:
    retries every 2 → 4 → 8 → … → 30 seconds on connection failure.

    rclpy.ok() is checked on every iteration so the thread exits cleanly
    when the ROS2 context shuts down.
    """
    retry_delay = 2.0

    while rclpy.ok():
        log.info(
            f"Connecting to Kuksa Databroker at "
            f"{DATABROKER_HOST}:{DATABROKER_PORT} ..."
        )
        try:
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
                log.info(
                    "Connected to Kuksa Databroker. "
                    "Starting gRPC subscribe loop."
                )
                retry_delay = 2.0  # reset back-off on successful connect

                # subscribe_current_values() returns a blocking iterator.
                # It yields a dict[str, Datapoint] whenever one or more
                # signals change — immediately, without a poll timer.
                # This is the same pattern as the M2 mqtt-bridge (DR-23).
                for updates in client.subscribe_current_values(SIGNAL_PATHS):
                    if not rclpy.ok():
                        break
                    for path, datapoint in updates.items():
                        if datapoint is None or datapoint.value is None:
                            # Signal registered but not yet published by ECU.
                            continue
                        node.publish(path, float(datapoint.value))

        except Exception as exc:
            log.warning(f"Kuksa connection error: {exc}")
            log.info(f"Retrying in {retry_delay:.0f} s ...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)


# ── Entry Point ───────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  mini-SDV Platform — ROS2 Bridge  (Milestone 3)")
    log.info(f"  Databroker : {DATABROKER_HOST}:{DATABROKER_PORT}")
    log.info(f"  ROS2 node  : vehicle_signal_bridge")
    for path, meta in SIGNAL_MAP.items():
        log.info(f"  {path}")
        log.info(f"    → ROS2 topic: {meta['topic']}")
    log.info("=" * 60)

    rclpy.init()
    node = VehicleSignalBridgeNode()

    # Start the Kuksa subscribe loop in a daemon background thread.
    # daemon=True: thread is killed automatically when the main thread exits,
    # preventing zombie processes on container shutdown.
    thread = threading.Thread(
        target=kuksa_subscribe_loop,
        args=(node,),
        daemon=True,
        name="kuksa-subscribe",
    )
    thread.start()

    # Block main thread on rclpy.spin().
    # This keeps the ROS2 node alive, processes parameter/lifecycle callbacks,
    # and handles SIGINT (Ctrl-C) gracefully.
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        log.info("Shutdown requested.")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
