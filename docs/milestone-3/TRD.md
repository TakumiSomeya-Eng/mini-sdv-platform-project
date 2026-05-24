# Technical Requirements Document (TRD)
## Milestone 3: ROS2 Integration
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | TRD |
| Milestone | 3 — ROS2 Integration |
| Status | Draft |
| Hypothesis Layer | Implementation (L5) |
| Created | 2026-05-24 |
| Version | 1.0 |
| Depends On | [FRD.md](FRD.md) · Milestone 2 TRD |

---

## 1. Architecture Overview

### 1.1 Updated Service Map

| Service | Type | Image / Build | Internal Port | Host Port | Role |
|---|---|---|---|---|---|
| `databroker` | Upstream | `ghcr.io/eclipse/kuksa.val/databroker:0.4.4` | 55555 (gRPC) | — | Vehicle signal middleware (M1) |
| `ecu-simulator` | Custom | `./services/ecu-simulator` | — | — | Signal producer — **updated VSS paths** (M1+M3) |
| `dashboard` | Custom | `./services/dashboard` | 8501 (HTTP) | 8501 | Signal visualizer — **updated VSS paths** (M1+M3) |
| `mosquitto` | Upstream | `eclipse-mosquitto:2.0` | 1883 (MQTT) | 1883 | MQTT broker (M2) |
| `mqtt-bridge` | Custom | `./services/mqtt-bridge` | — | — | Kuksa → MQTT forwarder — **updated VSS paths** (M2+M3) |
| `ros2-bridge` | Custom | `./services/ros2-bridge` | — | — | Kuksa → ROS2 forwarder **(M3 NEW)** |
| `ros2-subscriber` | Custom | `./services/ros2-subscriber` | — | — | ROS2 test subscriber **(M3 NEW)** |

### 1.2 Network Topology

All services remain on `sdv-net` (bridge network). No new host-exposed ports in M3.

```
Host Machine
  └── :8501 ──▶ dashboard    (Streamlit UI)     [M1]
  └── :1883 ──▶ mosquitto    (MQTT CLI access)  [M2]

Docker network: sdv-net (bridge)
  ├── databroker      :55555  (gRPC, internal)
  ├── ecu-simulator           (→ gRPC databroker)
  ├── dashboard       :8501   (→ gRPC databroker)
  ├── mosquitto       :1883   (← MQTT from mqtt-bridge)
  ├── mqtt-bridge             (→ gRPC subscribe databroker, → MQTT mosquitto)
  ├── ros2-bridge             (→ gRPC subscribe databroker, → DDS ros2-subscriber)
  └── ros2-subscriber         (← DDS from ros2-bridge)

DDS domain: ROS_DOMAIN_ID=0 (shared by ros2-bridge and ros2-subscriber)
  DDS discovery is brokerless — ros2-bridge and ros2-subscriber find
  each other automatically within sdv-net via DDS Simple Discovery.
```

### 1.3 Startup Order

```
Phase 1 ── databroker      health: TCP :55555
           mosquitto        health: TCP :1883

Phase 2 ── ecu-simulator   depends_on: databroker healthy  [M1]
           dashboard        depends_on: databroker healthy  [M1]
           mqtt-bridge      depends_on: databroker + mosquitto healthy  [M2]
           ros2-bridge      depends_on: databroker healthy  [M3]

Phase 3 ── ros2-subscriber  depends_on: ros2-bridge started  [M3]
           (service_started, not service_healthy — no health check on ros2-bridge)
```

---

## 2. The Core M3 Technical Challenge: ROS2 in Docker

Running ROS2 inside Docker containers on a bridge network requires careful DDS configuration. This section explains the key decisions.

### 2.1 DDS Middleware Selection — CycloneDDS over FastDDS

ROS2 Jazzy ships with **Eclipse CycloneDDS** as the default RMW (ROS Middleware). FastDDS (eProsima) is the alternative.

| | CycloneDDS (default Jazzy) | FastDDS |
|---|---|---|
| Docker bridge network | Works reliably with unicast discovery | Requires extra multicast config on bridge networks |
| Discovery | Peer list config (`CYCLONEDDS_URI`) | XML config file |
| In-container usage | Simpler — one env var | More complex XML setup |
| Eclipse SDV alignment | ✅ Same vendor (Eclipse Foundation) | ✗ |

**Decision: CycloneDDS (Jazzy default) with peer list discovery.**

On Docker bridge networks, multicast UDP may not be forwarded between containers. CycloneDDS supports explicit peer configuration via `CYCLONEDDS_URI` which bypasses multicast and uses direct unicast discovery — essential for reliable inter-container ROS2 communication.

### 2.2 CycloneDDS Unicast Peer Configuration

Both `ros2-bridge` and `ros2-subscriber` must know each other's address for DDS discovery. Since Docker's internal DNS resolves service names to IP addresses, we configure CycloneDDS to use Docker service names as peers:

```xml
<!-- Inline XML passed via CYCLONEDDS_URI env var -->
<CycloneDDS>
  <Domain>
    <General>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <Peers>
        <Peer Address="ros2-bridge"/>
        <Peer Address="ros2-subscriber"/>
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
```

This is set identically in both services via the `CYCLONEDDS_URI` environment variable.

### 2.3 Threading Model in ros2-bridge

The `ros2-bridge` must run two blocking operations simultaneously:
1. The Kuksa gRPC `subscribe_current_values()` iterator (blocks waiting for updates)
2. The rclpy executor spin loop (blocks to process ROS2 callbacks and keep the node alive)

**Solution: run the Kuksa subscribe loop in a background thread; publish to ROS2 from that thread.**

```
Main thread:          rclpy.spin(node)          ← keeps ROS2 node alive
Background thread:    kuksa subscribe loop       ← receives Datapoints
                      → node.publisher.publish() ← thread-safe in rclpy
```

`rclpy` publishers are thread-safe for `publish()` calls. The background thread can call `publisher.publish()` without needing to go through the main rclpy executor.

---

## 3. Service Specifications

### 3.1 ROS2 Bridge (`ros2-bridge`)

| Property | Value |
|---|---|
| Base Image | `ros:jazzy-ros-base` |
| Working Directory | `/app` |
| Key Dependencies | `kuksa-client==0.4.3` · rclpy (bundled in base image) · `std_msgs` (bundled) |
| Entry Point | `python3 main.py` (sourcing ROS2 setup first) |
| Depends On | `databroker` (condition: `service_healthy`) |
| Restart Policy | `on-failure` |
| Environment Variables | `DATABROKER_HOST` · `DATABROKER_PORT` · `ROS_DOMAIN_ID=0` · `CYCLONEDDS_URI` |

**CMD wrapper pattern** (required to source ROS2 environment):
```dockerfile
CMD ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && python3 main.py"]
```

The ROS2 environment (`/opt/ros/jazzy/setup.bash`) must be sourced before any rclpy import. This is a ROS2-in-Docker standard pattern.

**pip installation inside a ROS2 image:**
```dockerfile
RUN pip install kuksa-client==0.4.3 --break-system-packages
```

The `ros:jazzy-ros-base` image uses system Python managed by apt. The `--break-system-packages` flag is required (same as all pip installs in this project's Python services).

### 3.2 ROS2 Subscriber (`ros2-subscriber`)

| Property | Value |
|---|---|
| Base Image | `ros:jazzy-ros-base` |
| Working Directory | `/app` |
| Key Dependencies | rclpy (bundled) · `std_msgs` (bundled) |
| Entry Point | `/bin/bash -c "source /opt/ros/jazzy/setup.bash && python3 main.py"` |
| Depends On | `ros2-bridge` (condition: `service_started`) |
| Restart Policy | `on-failure` |
| Environment Variables | `ROS_DOMAIN_ID=0` · `CYCLONEDDS_URI` |

---

## 4. VSS Path Migration (All Existing Services)

### 4.1 Signal Constants Update

All four services that reference VSS paths must update their signal constant definitions:

```python
# BEFORE (M1/M2)
SIGNAL_SOC  = "Vehicle.Battery.SoC"
SIGNAL_TEMP = "Vehicle.Cabin.Temperature"

# AFTER (M3 — COVESA VSS 4.x)
SIGNAL_SOC  = "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current"
SIGNAL_TEMP = "Vehicle.Cabin.HVAC.AmbientAirTemperature"
```

`Vehicle.Speed` is already a standard COVESA path — unchanged.

### 4.2 VSS Catalog Files Update

**`config/vss/vss_mini_covesa.json`** — COVESA hierarchical format, loaded by Databroker:

```
Vehicle (branch)
├── Speed (sensor, float, km/h, 0–250)                          ← unchanged
├── Powertrain (branch)                                          ← NEW
│   └── TractionBattery (branch)
│       └── StateOfCharge (branch)
│           └── Current (sensor, float, percent, 0–100)
└── Cabin (branch)
    └── HVAC (branch)                                           ← NEW
        └── AmbientAirTemperature (sensor, float, celsius, -40–100)
```

**`config/vss/vss_mini.json`** — flat human-readable reference, updated to match.

### 4.3 MQTT Topic Changes (mqtt-bridge)

The VSS path migration automatically changes the MQTT topics for two signals (dot-to-slash conversion):

| Signal | M2 MQTT Topic | M3 MQTT Topic |
|---|---|---|
| Battery SoC | `sdv/vehicle-001/Vehicle/Battery/SoC` | `sdv/vehicle-001/Vehicle/Powertrain/TractionBattery/StateOfCharge/Current` |
| Cabin Temp | `sdv/vehicle-001/Vehicle/Cabin/Temperature` | `sdv/vehicle-001/Vehicle/Cabin/HVAC/AmbientAirTemperature` |
| Speed | `sdv/vehicle-001/Vehicle/Speed` | `sdv/vehicle-001/Vehicle/Speed` *(unchanged)* |

The wildcard subscription `sdv/vehicle-001/#` continues to capture all signals.

### 4.4 Dashboard `SIGNALS` Dict Update

```python
SIGNALS = {
    "Vehicle.Speed": {
        "label": "Vehicle Speed", "unit": "km/h", ...
    },
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": {
        "label": "Battery State of Charge", "unit": "%", ...
    },
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": {
        "label": "Cabin Temperature", "unit": "°C", ...
    },
}
```

---

## 5. Implementation: ros2-bridge (`services/ros2-bridge/main.py`)

### 5.1 Module Structure

```
main.py
├── SIGNAL_MAP dict       (COVESA VSS path → {topic, unit})
├── vss_to_topic()        (pass-through — topic stored in SIGNAL_MAP)
├── VehicleSignalBridgeNode (rclpy.node.Node subclass)
│   ├── __init__()        → creates one Publisher per signal
│   └── publish()         → thread-safe publish of Float32 message
├── kuksa_subscribe_loop()  → background thread: gRPC subscribe → node.publish()
└── main()                → init rclpy, create node, start thread, spin
```

### 5.2 Signal Map

```python
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
```

### 5.3 Node and Publisher Setup

```python
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

class VehicleSignalBridgeNode(Node):
    def __init__(self):
        super().__init__("vehicle_signal_bridge")
        # One publisher per signal, keyed by VSS path for fast lookup
        self._publishers = {
            path: self.create_publisher(Float32, meta["topic"], qos_profile=10)
            for path, meta in SIGNAL_MAP.items()
        }

    def publish(self, vss_path: str, value: float) -> None:
        msg = Float32()
        msg.data = value
        self._publishers[vss_path].publish(msg)
        self.get_logger().info(
            f"Published {SIGNAL_MAP[vss_path]['topic']} = {value}"
        )
```

**QoS depth = 10:** The publisher queue depth of 10 ensures that a brief subscriber disconnect does not cause message loss. ROS2 QoS is separate from MQTT QoS — it controls the in-process message buffer, not delivery guarantees over the network.

### 5.4 Threading Model

```python
import threading

def kuksa_subscribe_loop(node: VehicleSignalBridgeNode) -> None:
    """
    Background thread: subscribes to Kuksa and forwards to ROS2.
    Runs the same exponential back-off reconnect pattern as mqtt-bridge.
    """
    retry_delay = 2.0
    while rclpy.ok():
        try:
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
                retry_delay = 2.0
                for updates in client.subscribe_current_values(SIGNAL_PATHS):
                    for path, datapoint in updates.items():
                        if datapoint and datapoint.value is not None:
                            node.publish(path, float(datapoint.value))
        except Exception as exc:
            node.get_logger().warning(f"Kuksa error: {exc}. Retry in {retry_delay}s")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)

def main() -> None:
    rclpy.init()
    node = VehicleSignalBridgeNode()

    # Start Kuksa subscribe loop in a background thread
    thread = threading.Thread(target=kuksa_subscribe_loop, args=(node,), daemon=True)
    thread.start()

    # Block main thread on rclpy spin — keeps the node alive and
    # processes any ROS2 callbacks (parameter changes, shutdown signals, etc.)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
```

The background thread is `daemon=True`: it is automatically killed when the main thread exits, preventing zombie processes.

---

## 6. Implementation: ros2-subscriber (`services/ros2-subscriber/main.py`)

```python
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

TOPICS = ["/vehicle/speed", "/vehicle/battery/soc", "/vehicle/cabin/temperature"]

class VehicleSignalSubscriberNode(Node):
    def __init__(self):
        super().__init__("vehicle_signal_subscriber")
        for topic in TOPICS:
            self.create_subscription(
                Float32,
                topic,
                lambda msg, t=topic: self.get_logger().info(
                    f"[{t}] value={msg.data}"
                ),
                qos_profile=10,
            )
        self.get_logger().info(f"Subscribed to: {TOPICS}")

def main() -> None:
    rclpy.init()
    node = VehicleSignalSubscriberNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
```

This service has no business logic — it is purely a verification tool. The lambda callback logs each received message directly.

---

## 7. Dockerfile Design

### 7.1 ROS2 Services Dockerfile (shared pattern)

```dockerfile
FROM ros:jazzy-ros-base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROS_DOMAIN_ID=0

# Install Python deps (only ros2-bridge needs kuksa-client)
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .

# Must source ROS2 setup before running any rclpy code.
# /opt/ros/jazzy/setup.bash sets AMENT_PREFIX_PATH, PYTHONPATH,
# and other variables that rclpy depends on at import time.
CMD ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && python3 main.py"]
```

**Why not `USER 1000`?**
The `ros:jazzy-ros-base` image requires root or the `ros` group for some DDS operations during discovery. Non-root execution is a known limitation in M3 (see §10, C20).

### 7.2 ros2-bridge `requirements.txt`

```
kuksa-client==0.4.3
```

`rclpy` and `std_msgs` are pre-installed in `ros:jazzy-ros-base` and do not need to be in `requirements.txt`.

### 7.3 ros2-subscriber `requirements.txt`

```
# No additional dependencies — rclpy and std_msgs are in the base image
```

An empty (or comment-only) `requirements.txt` is kept for structural consistency with other services.

---

## 8. docker-compose.yml Changes

### 8.1 CycloneDDS URI (shared between both ROS2 services)

```yaml
x-cyclonedds-config: &cyclonedds-config
  CYCLONEDDS_URI: >-
    <CycloneDDS><Domain><General><AllowMulticast>false</AllowMulticast></General>
    <Discovery><Peers>
    <Peer Address="ros2-bridge"/>
    <Peer Address="ros2-subscriber"/>
    </Peers></Discovery></Domain></CycloneDDS>
```

Using a YAML anchor (`&cyclonedds-config`) avoids duplicating the XML string in both service definitions.

### 8.2 New: `ros2-bridge`

```yaml
ros2-bridge:
  build:
    context: ./services/ros2-bridge
    dockerfile: Dockerfile
  environment:
    DATABROKER_HOST: databroker
    DATABROKER_PORT: "55555"
    ROS_DOMAIN_ID: "0"
    <<: *cyclonedds-config
  depends_on:
    databroker:
      condition: service_healthy
  networks:
    - sdv-net
  restart: on-failure
```

### 8.3 New: `ros2-subscriber`

```yaml
ros2-subscriber:
  build:
    context: ./services/ros2-subscriber
    dockerfile: Dockerfile
  environment:
    ROS_DOMAIN_ID: "0"
    <<: *cyclonedds-config
  depends_on:
    ros2-bridge:
      condition: service_started
  networks:
    - sdv-net
  restart: on-failure
```

---

## 9. Technology Decision Records (TDRs)

### TDR-20: CycloneDDS over FastDDS in Docker

| | |
|---|---|
| **Decision** | Use CycloneDDS (Jazzy default) with explicit unicast peer configuration |
| **Alternatives** | FastDDS with multicast; FastDDS with XML unicast config |
| **Rationale** | Docker bridge networks do not reliably forward multicast UDP between containers. CycloneDDS supports direct peer-to-peer unicast discovery via `CYCLONEDDS_URI` with a compact inline XML string — no separate config file needed. CycloneDDS is also developed by the Eclipse Foundation, aligning with the rest of the Eclipse SDV toolchain used in this project. |
| **Tradeoff** | Peer list must be maintained manually if new ROS2 services are added in M4/M5. Acceptable for the current 2-node ROS2 graph. |

### TDR-21: Threading (Kuksa loop in background thread) over Async/Await

| | |
|---|---|
| **Decision** | Run the Kuksa subscribe loop in a `threading.Thread`; block main thread on `rclpy.spin()` |
| **Alternatives** | `asyncio` event loop integrating both rclpy and kuksa-client; executor-based approach |
| **Rationale** | `rclpy.spin()` blocks the main thread and processes ROS2 callbacks. `subscribe_current_values()` also blocks. A simple background thread is the most readable solution — consistent with how the M2 `mqtt-bridge` uses `paho-mqtt`'s `loop_start()` background thread. An asyncio approach requires an async-compatible ROS2 executor and an async-compatible Kuksa client, adding significant complexity for no educational benefit in M3. |
| **Tradeoff** | Threading is less efficient than async for very high message rates. At 1–3 Hz signal updates, this is not a concern. |

### TDR-22: `std_msgs/msg/Float32` over Custom Message Type

| | |
|---|---|
| **Decision** | Use `std_msgs/msg/Float32` for all vehicle signal topics |
| **Alternatives** | Custom `VehicleSignal.msg` with fields: `string path`, `float32 value`, `string unit`, `string timestamp` |
| **Rationale** | Custom message types require a ROS2 package (`ament_cmake` or `ament_python`), a `.msg` IDL file, and compile-time code generation. This adds substantial Dockerfile complexity (colcon build, workspace overlay) that distracts from the M3 architectural lesson. `std_msgs/msg/Float32` carries a single `float32 data` field — sufficient for demonstrating the Kuksa→ROS2 bridge pattern. |
| **Tradeoff** | The topic carries only the value; metadata (unit, VSS path, timestamp) is lost in transit. This is a known limitation documented for M4. |
| **Future Path** | M4 introduces a custom `VehicleSignal.msg` type when CAN frame decoding requires richer metadata. |

### TDR-23: `ros:jazzy-ros-base` over Building rclpy from Source

| | |
|---|---|
| **Decision** | Use the official `ros:jazzy-ros-base` Docker image as the base |
| **Alternatives** | `python:3.11-slim` + install rclpy via pip or from source |
| **Rationale** | `rclpy` is deeply coupled to the ROS2 build system (ament, colcon) and the DDS RMW implementation. Installing it correctly on a plain Python image is non-trivial. The official ROS image provides a pre-configured, tested environment. The tradeoff is a larger image (~600 MB vs ~150 MB for python:3.11-slim), which is acceptable for a development/educational project. |
| **Tradeoff** | Larger image size. Cannot run as `USER 1000` without additional group configuration. |

### TDR-24: VSS Path Migration in M3

| | |
|---|---|
| **Decision** | Migrate all VSS paths to COVESA VSS 4.x standard in M3 |
| **Alternatives** | Migrate in M4; keep custom paths permanently |
| **Rationale** | M3 introduces ROS2 topic naming that mirrors the VSS hierarchy. If VSS paths remain non-standard, the ROS2 topics would also be non-standard, compounding the divergence. M3 is the right inflection point: adding new services is less risky than modifying existing ones in later milestones. This also closes a documented known limitation from M1. |
| **Impact** | MQTT topic names for SoC and Cabin Temp change. The wildcard `sdv/vehicle-001/#` subscription continues to work. Any consumer that hard-coded the specific old topic strings must update. |

---

## 10. Known Constraints and Limitations

| ID | Constraint | Impact | Resolution Path |
|---|---|---|---|
| C20 | `ros:jazzy-ros-base` requires root for DDS discovery | Container runs as root — deviation from M1/M2 non-root pattern | Resolve in M4 by adding `ros` group and `USER` directive |
| C21 | `std_msgs/msg/Float32` carries only value — no unit, path, or timestamp | ROS2 topic is not self-describing | Custom `VehicleSignal.msg` in M4 |
| C22 | CycloneDDS peer list is hardcoded to 2 services | Adding a new ROS2 node in M4/M5 requires updating the peer list | Move `CYCLONEDDS_URI` to a mounted config file in M4 |
| C23 | `ros:jazzy-ros-base` image is ~600 MB | Larger than other service images | Acceptable for educational use; production would use a slim ROS2 image |
| C24 | MQTT topics for SoC and Cabin Temp change in M3 | Breaking change for consumers that subscribed to M2 topic names | Document migration; wildcard `#` subscription not affected |
| C25 | No ROS2 QoS policy tuning (deadline, liveliness, lifespan) | Default QoS may drop messages under high load | ROS2 QoS deep-dive deferred to M4 |

---

## 11. Non-Functional Requirements (Technical)

| ID | Category | Requirement | Implementation |
|---|---|---|---|
| TNF-20 | Latency | ROS2 message MUST arrive at `ros2-subscriber` within 500 ms of ECU publish | gRPC subscribe eliminates polling delay; DDS publish is synchronous |
| TNF-21 | Image Size | `ros2-bridge` and `ros2-subscriber` images MUST be ≤ 800 MB | `ros:jazzy-ros-base` ~600 MB + kuksa-client |
| TNF-22 | Startup Time | All 6 services healthy/running within 120 seconds of `docker compose up` | `ros:jazzy-ros-base` pull is the bottleneck on first run; cached on subsequent runs |
| TNF-23 | Backward Compatibility | All M1 and M2 acceptance criteria MUST pass without modification to M1/M2 service logic | Only signal constant strings are changed; logic is identical |
| TNF-24 | Portability | ROS2 services MUST run on Linux, macOS (Docker Desktop), and Windows (Docker Desktop) | Docker handles OS abstraction; no host ROS2 required |

---

## 12. File Structure (Implementation Target)

```
02_mini-sdv-platform project/
├── docker-compose.yml                        ← UPDATED: add ros2-bridge + ros2-subscriber
├── README.md                                 ← UPDATED: M3 architecture + verification commands
├── config/
│   └── vss/
│       ├── vss_mini_covesa.json              ← UPDATED: COVESA standard paths
│       └── vss_mini.json                    ← UPDATED: flat reference, standard paths
├── services/
│   ├── ros2-bridge/                          ← NEW
│   │   ├── Dockerfile                        ← NEW [1]: ros:jazzy-ros-base
│   │   ├── requirements.txt                  ← NEW [1]: kuksa-client==0.4.3
│   │   └── main.py                           ← NEW [2]: VehicleSignalBridgeNode + threading
│   ├── ros2-subscriber/                      ← NEW
│   │   ├── Dockerfile                        ← NEW [3]: ros:jazzy-ros-base
│   │   ├── requirements.txt                  ← NEW [3]: (empty)
│   │   └── main.py                           ← NEW [3]: VehicleSignalSubscriberNode
│   ├── ecu-simulator/
│   │   └── main.py                           ← UPDATED [4]: new VSS path constants
│   ├── dashboard/
│   │   └── main.py                           ← UPDATED [4]: new VSS path constants
│   └── mqtt-bridge/
│       └── main.py                           ← UPDATED [4]: new VSS path constants
└── docs/
    ├── milestone-1/                          ← unchanged
    ├── milestone-2/                          ← unchanged
    └── milestone-3/
        ├── PRD.md
        ├── FRD.md
        └── TRD.md                            ← this document
```

### 12.1 Implementation Priority Order

| Order | File(s) | Validates |
|---|---|---|
| 1 | `config/vss/vss_mini_covesa.json` + `vss_mini.json` | Databroker loads COVESA-standard VSS catalog |
| 2 | `services/ecu-simulator/main.py` | ECU sim publishes to new paths; all M1 tests pass |
| 3 | `services/dashboard/main.py` | Dashboard reads from new paths; M1 dashboard still works |
| 4 | `services/mqtt-bridge/main.py` | MQTT bridge forwards new paths; M2 MQTT tests pass |
| 5 | `services/ros2-bridge/Dockerfile` + `requirements.txt` + `main.py` | Bridge container builds; ROS2 topics publish |
| 6 | `services/ros2-subscriber/Dockerfile` + `requirements.txt` + `main.py` | Subscriber receives topics; end-to-end M3 verified |
| 7 | `docker-compose.yml` | All 6 services start; full stack smoke test passes |
| 8 | `README.md` | Learner can verify M3 from documentation alone |
