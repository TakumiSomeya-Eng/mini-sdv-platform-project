# Mini SDV Platform — Architecture Review & Study Guide
## Milestone 3: ROS2 Integration (DDS + COVESA VSS Path Migration)

> **Date:** 2026-05-24

---

## Table of Contents

1. [What M3 Adds and Why](#1-what-m3-adds-and-why)
2. [The Three-Consumer Pattern — The Core M3 Lesson](#2-the-three-consumer-pattern--the-core-m3-lesson)
3. [COVESA VSS Path Migration](#3-covesa-vss-path-migration)
4. [ROS2 Fundamentals for SDV Engineers](#4-ros2-fundamentals-for-sdv-engineers)
5. [DDS (Data Distribution Service) — The Transport Layer](#5-dds-data-distribution-service--the-transport-layer)
6. [CycloneDDS and Docker — Solving the Multicast Problem](#6-cyclonedds-and-docker--solving-the-multicast-problem)
7. [ros2-bridge — Service Deep Dive](#7-ros2-bridge--service-deep-dive)
   - 7-1. Threading Model: Two Blocking Loops
   - 7-2. SIGNAL_MAP and Topic Naming Convention
   - 7-3. VehicleSignalBridgeNode
   - 7-4. kuksa_subscribe_loop (Background Thread)
   - 7-5. Exponential Back-off Reconnect
8. [ros2-subscriber — Verification Service Deep Dive](#8-ros2-subscriber--verification-service-deep-dive)
9. [Full M3 Architecture Walkthrough](#9-full-m3-architecture-walkthrough)
10. [Protocol Comparison: gRPC vs. MQTT vs. DDS](#10-protocol-comparison-grpc-vs-mqtt-vs-dds)
11. [Docker Compose Changes in M3](#11-docker-compose-changes-in-m3)
12. [Known Constraints and M4 Preview](#12-known-constraints-and-m4-preview)
13. [Review Quiz](#13-review-quiz)

---

## 1. What M3 Adds and Why

### The gap M3 closes

After M2, you can observe vehicle signals flow from ECUs → Kuksa Databroker → cloud (MQTT). But modern SDV platforms are not just connected to the cloud — they are the compute foundation for **autonomous driving software**.

A path planner, emergency braking node, or sensor fusion module all need vehicle speed and battery state in real time. These systems speak **ROS2**, not MQTT and not raw gRPC.

M3 bridges the gap:

```
Before M3:                          After M3:
──────────                          ─────────
Kuksa Databroker                    Kuksa Databroker
  │                                   │          │
  ├──▶ Dashboard (human UI)           ├──▶ Dashboard (human UI)      M1
  └──▶ MQTT Bridge → cloud           ├──▶ MQTT Bridge → cloud        M2
                                      └──▶ ROS2 Bridge → AD stack    M3 NEW
```

**One Databroker — three consumers — three protocol paradigms.** That is the complete M3 thesis.

### Why ROS2 specifically?

| Reason | Detail |
|---|---|
| Industry standard | Used by Autoware (leading open-source AD stack), Apollo (Baidu), TierIV |
| SDV ecosystem | Eclipse SDV maintains an official `kuksa-ros2-bridge`; this project reproduces that pattern |
| AUTOSAR Adaptive | DDS (ROS2's transport) is mandated in several AUTOSAR Adaptive service communication profiles |
| Timing | Autoware Universe completed Jazzy full support in April 2026; Jazzy is now the target distro for all new AD projects |

---

## 2. The Three-Consumer Pattern — The Core M3 Lesson

```
┌─────────────────────────────────────────────────────────┐
│                   Kuksa Databroker :55555                │
│         (Single Source of Truth for all signals)         │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────┼──────────────┐
          │             │              │
          ▼             ▼              ▼
    [dashboard]   [mqtt-bridge]  [ros2-bridge]
    gRPC poll     gRPC subscribe  gRPC subscribe
    (1 s timer)   → MQTT publish  → ROS2 publish
                  → Mosquitto     → DDS topics
                  → Cloud         → AD stack
```

Each consumer uses the same Databroker but with a different access pattern and output protocol:

| Consumer | Access Pattern | Output | Use Case |
|---|---|---|---|
| `dashboard` | `get_current_values()` — poll every 1 s | HTTP (Streamlit) | Human instrument cluster |
| `mqtt-bridge` | `subscribe_current_values()` — event-driven | MQTT JSON to Mosquitto | Cloud telemetry (AWS IoT, Azure IoT Hub) |
| `ros2-bridge` | `subscribe_current_values()` — event-driven | `std_msgs/Float32` ROS2 topics via DDS | Autonomous driving software (Autoware, path planner) |

**Why does `ros2-bridge` use subscribe (not poll)?**

Both `mqtt-bridge` and `ros2-bridge` are **reactive forwarders** — their job is to immediately propagate a signal change downstream, not to snapshot the state on a timer. Subscribing means they transmit data the moment it changes. Polling would introduce artificial latency and unnecessary traffic. See the M1/M2 study guide for a full compare of subscribe vs. poll.

---

## 3. COVESA VSS Path Migration

M3 also corrects a known limitation carried since M1: the non-standard VSS signal paths used for simplicity are replaced with their official COVESA VSS 4.x equivalents.

### Path mapping

| Signal | M1/M2 Custom Path | M3 COVESA Standard Path |
|---|---|---|
| Vehicle speed | `Vehicle.Speed` | `Vehicle.Speed` *(already correct)* |
| Battery state of charge | `Vehicle.Battery.SoC` | `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` |
| Cabin temperature | `Vehicle.Cabin.Temperature` | `Vehicle.Cabin.HVAC.AmbientAirTemperature` |

### Why COVESA paths matter

COVESA (Connected Vehicle Systems Alliance) maintains the VSS standard. By using standard paths:

- Any VSS-aware tool (a third-party dashboard, a cloud service, an OEM integration) can discover and consume signals without custom mapping
- The signal name encodes the hierarchy (`Powertrain` → `TractionBattery` → `StateOfCharge` → `Current`), making it self-describing
- It matches what a real production Databroker uses

### Files changed in M3

| File | Change |
|---|---|
| `config/vss/vss_mini_covesa.json` | Restructured to full COVESA hierarchy with proper parent/child branches |
| `config/vss/vss_mini.json` | Keys updated to standard paths, descriptions updated |
| `services/ecu-simulator/main.py` | `SIGNAL_SOC` and `SIGNAL_TEMP` updated |
| `services/dashboard/main.py` | `SIGNALS` dict keys updated |
| `services/mqtt-bridge/main.py` | `SIGNALS` dict keys and MQTT topic comments updated |
| `services/ros2-bridge/main.py` | `SIGNAL_MAP` keys use standard paths from day one |

**Migration impact is zero for the user.** Because all signal names live in one constant per service (`SIGNAL_MAP`, `SIGNALS`), the update touches only the signal name strings — no logic changes.

---

## 4. ROS2 Fundamentals for SDV Engineers

### What is ROS2?

**ROS2 (Robot Operating System 2)** is an open-source middleware framework for robotics and autonomous systems. Despite the name "operating system," it is a middleware layer that runs on top of Linux (or Windows/macOS). It provides:

- **Topic-based pub/sub communication** between nodes
- **Service (RPC)** calls between nodes
- **Parameter management**, **lifecycle nodes**, **bag recording**, and more

In an autonomous vehicle, ROS2 typically runs on a dedicated **AD compute platform** (NVIDIA Drive AGX, Intel Mobileye, etc.) and manages everything from LiDAR point cloud processing to emergency braking decisions.

### Key ROS2 concepts

| Concept | Description | Analogy |
|---|---|---|
| **Node** | An independent process with a single responsibility | A microservice |
| **Topic** | A named channel for a stream of typed messages | An MQTT topic, but typed |
| **Publisher** | A node that writes messages to a topic | MQTT publisher |
| **Subscriber** | A node that reads messages from a topic | MQTT subscriber |
| **Message type** | Strongly typed schema for topic data | Protobuf schema |
| **QoS** | Quality of Service — reliability, depth, durability | MQTT QoS levels |
| **ROS_DOMAIN_ID** | Integer that scopes DDS discovery; nodes with different IDs cannot see each other | MQTT broker isolation |

### Message type used in M3: `std_msgs/msg/Float32`

M3 uses the standard `Float32` message type from the `std_msgs` package — the simplest possible numeric type:

```
std_msgs/Float32:
  float32 data
```

This avoids the need to define and build custom message types (which requires a `colcon` workspace build). The trade-off: unit metadata (`km/h`, `percent`, `celsius`) is not carried in the message itself — it lives only in the service's `SIGNAL_MAP` dict and in log output.

Custom message types (carrying value + unit + VSS path + timestamp in one structured message) are planned for M4.

### ROS2 distro: Jazzy Jalisco

M3 targets **ROS2 Jazzy Jalisco (LTS, May 2024 – May 2029)**.

| Distro | LTS EOL | Status (May 2026) |
|---|---|---|
| Humble Hawksbill | May 2027 | Maintenance mode; Autoware soft-freeze Jan 2027 |
| **Jazzy Jalisco** | **May 2029** | **Active; Autoware full support April 2026** |
| Rolling | — | Unstable; not for production |

Jazzy is chosen because Autoware — the leading reference for how a real AD stack integrates with vehicle middleware — completed Jazzy migration in April 2026. New projects starting today target Jazzy.

---

## 5. DDS (Data Distribution Service) — The Transport Layer

### What is DDS?

**DDS** is an open middleware standard (OMG — Object Management Group) for real-time, data-centric publish/subscribe communication. ROS2 uses DDS as its underlying transport layer via an abstraction called **RMW (ROS2 Middleware interface)**.

### DDS vs. MQTT vs. gRPC

| Dimension | gRPC (M1/M2/M3) | MQTT (M2) | DDS / ROS2 (M3) |
|---|---|---|---|
| Architecture | Client–server (RPC) | Broker-based pub/sub | **Brokerless** peer-to-peer pub/sub |
| Discovery | Manual (host:port) | Manual (broker address) | **Automatic** (Simple Discovery Protocol) |
| Single point of failure | Server | Broker | **None** — no central broker |
| Latency | Low (HTTP/2) | Low–medium | **Very low** (designed for real-time) |
| Typing | Protobuf (strongly typed) | No typing (JSON string) | **Strongly typed** (IDL/OMG) |
| Primary use | Kuksa VAL API | IoT cloud telemetry | ADAS/AD real-time control |
| AUTOSAR Adaptive | Yes (vsomeip) | No | **Yes — mandated** |

**Brokerless is the key DDS insight.** MQTT requires a Mosquitto broker; if it goes down, telemetry stops. DDS nodes discover each other directly on the network — there is no broker to fail. This matters enormously in an autonomous vehicle where a crashed broker could stop the emergency braking system.

### CycloneDDS — the DDS implementation used in M3

The RMW (transport plugin) used in M3 is **Eclipse CycloneDDS**, which is the default RMW for ROS2 Jazzy and the preferred choice within the Eclipse SDV ecosystem. The alternative is eProsima FastDDS.

CycloneDDS was chosen for M3 over FastDDS for one practical reason: **CycloneDDS handles Docker bridge network environments more reliably**, particularly when multicast discovery is disabled in favor of unicast peer lists (see §6).

---

## 6. CycloneDDS and Docker — Solving the Multicast Problem

### The problem: Docker bridge networks block multicast

DDS Simple Discovery Protocol (SDP) uses **multicast UDP** to announce participants on the local network. In a physical LAN, this works automatically. In a Docker bridge network, however, multicast UDP packets are **not reliably forwarded between containers**.

This means that if left at default settings, `ros2-bridge` and `ros2-subscriber` would fail to discover each other even though they're on the same Docker network — and no ROS2 messages would be delivered.

### The solution: unicast peer discovery via `CYCLONEDDS_URI`

CycloneDDS can be configured to skip multicast entirely and use **direct unicast addresses** to contact known peers. This configuration is injected via the `CYCLONEDDS_URI` environment variable as an XML string:

```xml
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

`AllowMulticast: false` disables the UDP multicast announcements. The `<Peers>` list tells CycloneDDS exactly which hostnames to contact for DDS discovery. Docker's built-in DNS resolves `ros2-bridge` and `ros2-subscriber` to their container IPs automatically — no hardcoded IPs needed.

### YAML anchor in docker-compose.yml

Because both `ros2-bridge` and `ros2-subscriber` need identical `CYCLONEDDS_URI` values, the XML string is defined once using a **YAML anchor** and merged into both services:

```yaml
# Defined once at the top level:
x-cyclonedds-config: &cyclonedds-config
  CYCLONEDDS_URI: >-
    <CycloneDDS>...</CycloneDDS>

# Reused in each ROS2 service:
ros2-bridge:
  environment:
    <<: *cyclonedds-config   # ← merges all keys from the anchor
    DATABROKER_HOST: databroker
    ...

ros2-subscriber:
  environment:
    <<: *cyclonedds-config   # ← same config, zero duplication
    ...
```

`>-` is YAML's **block scalar folded strip** syntax — it folds newlines into spaces and strips the trailing newline, resulting in a single-line string. This is necessary because environment variable values cannot span multiple lines in Docker Compose.

**Real-world note:** In a physical vehicle, all ROS2 nodes run on the same Ethernet segment or CAN domain, so multicast discovery works without this workaround. The unicast peer list is a Docker-specific necessity, not a fundamental DDS pattern.

---

## 7. ros2-bridge — Service Deep Dive

### 7-1. Threading Model: Two Blocking Loops

`ros2-bridge` must run two operations that each block indefinitely:

| Operation | Blocks because… |
|---|---|
| `rclpy.spin(node)` | Keeps the ROS2 node alive; processes callbacks; handles SIGINT |
| `client.subscribe_current_values(paths)` | gRPC streaming iterator — blocks until the server sends an update |

You cannot run both in the same thread. The solution:

```
Main Thread                          Background Thread (daemon=True)
───────────                          ───────────────────────────────
rclpy.init()                         while rclpy.ok():
node = VehicleSignalBridgeNode()       with VSSClient(...) as client:
thread.start()  ──────────────────▶      for updates in client.subscribe...:
rclpy.spin(node)   [blocks here]           node.publish(path, value)
```

**Why `daemon=True`?** A daemon thread is automatically killed when the main thread exits. Without it, the container would hang after `rclpy.spin()` returns (on SIGINT/docker stop), waiting for the background thread to finish — which it never would.

**Thread safety:** `rclpy` publishers are documented as thread-safe for `publish()` calls. The background thread can call `node.publish()` directly without acquiring a lock.

### 7-2. SIGNAL_MAP and Topic Naming Convention

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

Topic naming follows the convention `/vehicle/{signal_label}` where `signal_label` is a short, slash-separated human label — not the full VSS path. The full COVESA path is too long (`/vehicle/powertrain/traction_battery/state_of_charge/current`) for a topic name used by an AD node daily.

The SIGNAL_MAP is the single source of truth: add a new signal here and the rest of the service adapts automatically (publisher creation, subscribe list, log output).

### 7-3. VehicleSignalBridgeNode

```python
class VehicleSignalBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("vehicle_signal_bridge")

        # One publisher per signal, created at init and reused.
        self._publishers = {
            path: self.create_publisher(Float32, meta["topic"], qos_profile=10)
            for path, meta in SIGNAL_MAP.items()
        }

    def publish(self, vss_path: str, value: float) -> None:
        msg = Float32()
        msg.data = value
        self._publishers[vss_path].publish(msg)
```

Publishers are created once in `__init__` and stored in a dict. Creating publishers is expensive; calling `publish()` is cheap and thread-safe. The `qos_profile=10` sets a history depth of 10 messages — enough to buffer signal updates during brief subscriber disconnects at a 1 Hz signal rate.

### 7-4. kuksa_subscribe_loop (Background Thread)

```python
def kuksa_subscribe_loop(node):
    retry_delay = 2.0
    while rclpy.ok():
        try:
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
                retry_delay = 2.0  # reset on successful connect
                for updates in client.subscribe_current_values(SIGNAL_PATHS):
                    if not rclpy.ok():
                        break
                    for path, datapoint in updates.items():
                        if datapoint and datapoint.value is not None:
                            node.publish(path, float(datapoint.value))
        except Exception as exc:
            log.warning(f"Connection error: {exc}")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)
```

Key patterns:

- `rclpy.ok()` is checked on every iteration — if ROS2 is shutting down, the loop exits cleanly
- `None` datapoint check: the Databroker may emit a signal entry before the ECU has published a value; skip it
- `float()` cast: Kuksa datapoints carry typed values; explicit cast ensures compatibility with `Float32.data`

### 7-5. Exponential Back-off Reconnect

Identical to `mqtt-bridge` from M2:

```
Attempt 1: wait 2 s
Attempt 2: wait 4 s
Attempt 3: wait 8 s
...
Attempt n: wait min(2^n × initial, 30) s   → capped at 30 s
```

Reset to 2 s on every successful connection. This is the same cloud-native reconnect pattern used throughout the project — and in production automotive software.

---

## 8. ros2-subscriber — Verification Service Deep Dive

`ros2-subscriber` is a **verification-only service** — it has no business logic. Its only purpose is to subscribe to the three `/vehicle/*` topics and log each received message, providing a visible end-to-end proof that DDS delivery between containers works.

```python
TOPICS = ["/vehicle/speed", "/vehicle/battery/soc", "/vehicle/cabin/temperature"]

class VehicleSignalSubscriberNode(Node):
    def __init__(self) -> None:
        super().__init__("vehicle_signal_subscriber")
        for topic in TOPICS:
            # lambda captures `topic` by value (t=topic) to avoid the
            # classic Python closure-in-loop bug.
            self.create_subscription(
                Float32, topic,
                lambda msg, t=topic: self.get_logger().info(
                    f"[{t}] value={msg.data}"
                ),
                qos_profile=10,
            )
```

**The closure-in-loop bug** is a subtle Python gotcha worth understanding:

```python
# WRONG — all lambdas capture the same `topic` variable by reference.
# When the loop ends, topic = last value for all three.
for topic in TOPICS:
    self.create_subscription(..., lambda msg: print(topic), ...)

# CORRECT — captures by value using a default argument.
for topic in TOPICS:
    self.create_subscription(..., lambda msg, t=topic: print(t), ...)
```

**Real-world equivalent:** In a production autonomous driving stack (Autoware), this role is played by a path-planner node, an emergency-braking node, or a sensor-fusion node — any ROS2 node that subscribes to `/vehicle/speed` to make driving decisions.

**Expected output when working correctly:**

```
[/vehicle/speed] value=87.3
[/vehicle/battery/soc] value=72.4
[/vehicle/cabin/temperature] value=22.1
```

---

## 9. Full M3 Architecture Walkthrough

Trace a single vehicle speed update from physics simulation to autonomous driving stack:

```
1. ECU Simulator (ecu-simulator/main.py)
   ├─ Physics: speed = 65 + 27.5 × sin(2π × t / 60) + Gaussian(0, 2)
   └─ gRPC SetCurrentValues("Vehicle.Speed", 87.3)
            │
            ▼
2. Kuksa Databroker (:55555)
   ├─ Stores: Vehicle.Speed = 87.3 km/h
   └─ Notifies all active subscribers immediately
            │
     ┌──────┼──────────────────────────┐
     ▼      ▼                          ▼
3a. dashboard          3b. mqtt-bridge         3c. ros2-bridge
    (poll, 1 s)             (subscribe)              (subscribe, background thread)
    gRPC Get                gRPC stream              gRPC stream
    → HTTP :8501            → MQTT publish           → node.publish("Vehicle.Speed", 87.3)
                            sdv/vehicle-001/             │
                            Vehicle/Speed                ▼
                            {"value": 87.3}    4. ROS2 topic: /vehicle/speed
                            → Mosquitto :1883      Float32.data = 87.3
                                                   DDS unicast → sdv-net
                                                       │
                                                       ▼
                                              5. ros2-subscriber
                                                 [/vehicle/speed] value=87.3
                                                 → docker compose logs
```

All of steps 3a, 3b, and 3c happen concurrently — the Databroker notifies all three subscribers within the same gRPC response cycle.

---

## 10. Protocol Comparison: gRPC vs. MQTT vs. DDS

| Feature | gRPC | MQTT | DDS (ROS2) |
|---|---|---|---|
| **Architecture** | Client–server | Broker hub | Brokerless peer-to-peer |
| **Transport** | HTTP/2 | TCP | UDP (+ TCP fallback) |
| **Discovery** | Manual (host:port) | Manual (broker address) | Automatic (SDP) |
| **Typing** | Protobuf | None (byte payload) | IDL / strongly typed |
| **Reliability** | Built-in retries | QoS 0/1/2 | QoS policies (DDS) |
| **Latency** | Low | Low–medium | Very low (sub-ms real-time) |
| **Scale** | Millions of calls | Millions of clients | Thousands of nodes (same domain) |
| **Broker required?** | No | **Yes** | **No** |
| **Primary role in SDV** | In-vehicle VAL API | V2C cloud telemetry | In-vehicle AD stack |
| **AUTOSAR Adaptive** | Yes (ara::com gRPC) | No | **Yes (DDS profile)** |

**When to use which:**

- **gRPC** — when you need typed, versioned, request/response or streaming RPCs with strong schema enforcement. Perfect for a Vehicle Abstraction Layer API.
- **MQTT** — when you need many cloud subscribers, fire-and-forget telemetry, and broker-managed QoS. Perfect for cloud IoT.
- **DDS/ROS2** — when you need real-time, no-single-point-of-failure pub/sub between software nodes on the same physical vehicle network. Required for ADAS/AD.

---

## 11. Docker Compose Changes in M3

### New services

| Service | Image | Role |
|---|---|---|
| `ros2-bridge` | `ros:jazzy-ros-base` (built) | Kuksa → ROS2 bridge (AD gateway) |
| `ros2-subscriber` | `ros:jazzy-ros-base` (built) | DDS subscriber (verification only) |

### `ros2-bridge` service definition (key parts)

```yaml
ros2-bridge:
  build:
    context: ./services/ros2-bridge
  environment:
    DATABROKER_HOST: databroker
    DATABROKER_PORT: "55555"
    ROS_DOMAIN_ID: "0"
    <<: *cyclonedds-config       # injects CYCLONEDDS_URI
  depends_on:
    databroker:
      condition: service_healthy # waits for Databroker gRPC to be ready
  networks:
    - sdv-net
  restart: on-failure
```

### `ros2-subscriber` service definition (key parts)

```yaml
ros2-subscriber:
  build:
    context: ./services/ros2-subscriber
  environment:
    ROS_DOMAIN_ID: "0"
    <<: *cyclonedds-config       # same peer list for DDS discovery
  depends_on:
    ros2-bridge:
      condition: service_started # only needs bridge to have started (not healthy)
  networks:
    - sdv-net
  restart: on-failure
```

Note `service_started` vs. `service_healthy`. The subscriber does not need the bridge to be producing messages — it just needs it to be in the Docker network so DDS discovery can find it. `service_healthy` would require a healthcheck on `ros2-bridge`, which is unnecessary complexity for a verification service.

### Dockerfile pattern for both ROS2 services

```dockerfile
FROM ros:jazzy-ros-base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROS_DOMAIN_ID=0

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .

CMD ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && python3 main.py"]
```

**`source /opt/ros/jazzy/setup.bash` in CMD** is mandatory. Without it, Python cannot import `rclpy` — the ROS2 packages are installed as shell-sourced overlays, not system Python packages. The `source` command sets up `PYTHONPATH`, `LD_LIBRARY_PATH`, and `AMENT_PREFIX_PATH` that rclpy requires.

This is why the CMD uses `/bin/bash -c "..."` rather than the exec form `["python3", "main.py"]` — the exec form does not invoke a shell, so `source` would not work.

---

## 12. Known Constraints and M4 Preview

### M3 Known Constraints

| Constraint | Description | Planned resolution |
|---|---|---|
| `std_msgs/Float32` | Unit metadata not in message payload | M4: custom `VehicleSignal.msg` with value + unit + VSS path |
| Docker-only DDS | No host `ros2 topic echo` without ROS2 installed | Host ROS2 installation (WSL2 or native) |
| Read-only bridge | ros2-bridge cannot actuate (write to Databroker) | M5: AI agent actuation |
| Simulated ECU | Speed/SoC/temp are sinusoidal — not real sensor data | M4: virtual CAN bus with SocketCAN |

### M4 Preview: Virtual CAN Bus

M4 will introduce **SocketCAN** — Linux's virtual CAN network interface — to simulate the physical CAN bus layer that sits below the Databroker in real vehicles:

```
M4 Architecture:
  candump (virtual CAN frames)
       │
  socketcan-ecu (python-can)
       │ ISO 11898 CAN frame
       ▼
  can-gateway  ── decodes frames → gRPC SetCurrentValues ──▶ Kuksa Databroker
                  (Gateway ECU pattern)
```

This completes the lower half of the SDV stack — the hardware→middleware boundary — and makes the ECU simulation physically accurate in addition to functionally accurate.

---

## 13. Review Quiz

Test your understanding with these questions. Answers follow.

**Q1.** What is the "three-consumer pattern" introduced in M3?

**Q2.** Why does `ros2-bridge` use `subscribe_current_values()` instead of `get_current_values()` (poll)?

**Q3.** What makes DDS fundamentally different from MQTT in terms of architecture?

**Q4.** Why does CycloneDDS fail to discover peers on a Docker bridge network by default, and how is it fixed?

**Q5.** What is the purpose of `CYCLONEDDS_URI`? How is it injected without duplicating the value across both ROS2 services?

**Q6.** Why does the `ros2-bridge` Dockerfile use `/bin/bash -c "source /opt/ros/jazzy/setup.bash && python3 main.py"` as its CMD instead of simply `["python3", "main.py"]`?

**Q7.** Explain the threading model in `ros2-bridge`. Why is the Kuksa subscribe loop run in a background thread, and why is it `daemon=True`?

**Q8.** What is the Python closure-in-loop bug, and how is it avoided in `ros2-subscriber`?

**Q9.** What were the two VSS paths migrated to COVESA standard in M3? What were the original M1/M2 names?

**Q10.** Why is `ros2-subscriber` described as a "verification-only service"? What would it be replaced by in a production AD stack?

---

### Answers

**A1.** One Databroker — three consumers — three protocols: `dashboard` (gRPC poll → human UI), `mqtt-bridge` (gRPC subscribe → MQTT → cloud), `ros2-bridge` (gRPC subscribe → DDS → autonomous driving stack). All three consume the same Databroker simultaneously.

**A2.** Because `ros2-bridge` is a reactive forwarder — its job is to propagate a signal change to the AD stack the moment it occurs, not on a fixed timer. Polling would introduce latency equal to the poll interval and generate unnecessary gRPC calls even when signals haven't changed.

**A3.** MQTT requires a broker (Mosquitto, AWS IoT Core) that every publisher and subscriber must connect to. If the broker fails, all communication stops. DDS is **brokerless** — nodes discover and communicate with each other directly via the Simple Discovery Protocol. There is no central point of failure.

**A4.** DDS Simple Discovery Protocol uses multicast UDP to announce participants. Docker bridge networks do not reliably forward multicast UDP between containers, so automatic discovery fails. The fix is to configure CycloneDDS with `AllowMulticast: false` and provide an explicit list of peer container hostnames via `CYCLONEDDS_URI`.

**A5.** `CYCLONEDDS_URI` is an environment variable that injects an XML configuration string into the CycloneDDS runtime, overriding its defaults. To avoid duplicating the long XML string in both `ros2-bridge` and `ros2-subscriber` service definitions, it is defined once as a **YAML anchor** (`x-cyclonedds-config: &cyclonedds-config`) and merged into each service with `<<: *cyclonedds-config`.

**A6.** `rclpy` and all ROS2 Python packages are installed as shell-overlay packages, not system Python packages. They require environment variables (`PYTHONPATH`, `LD_LIBRARY_PATH`, `AMENT_PREFIX_PATH`) that are only set by `source /opt/ros/jazzy/setup.bash`. The exec form `["python3", "main.py"]` does not invoke a shell, so `source` cannot run, and `import rclpy` would fail with `ModuleNotFoundError`.

**A7.** Both `rclpy.spin()` and `subscribe_current_values()` are blocking operations — they both run infinite loops. Only one can run on the main thread. The solution: run the Kuksa subscribe loop in a `threading.Thread(daemon=True)`. `daemon=True` ensures the thread is killed automatically when `rclpy.spin()` returns (on SIGINT/docker stop), preventing the container from hanging. `rclpy` publishers are thread-safe, so `node.publish()` can be called from the background thread without a lock.

**A8.** When creating multiple lambdas inside a `for` loop in Python, all lambdas share the same reference to the loop variable. After the loop ends, the variable holds only its final value — so all lambdas would log the same (last) topic name. The fix is to capture the value at lambda creation time using a default argument: `lambda msg, t=topic: print(t)`. The `t=topic` captures the current value of `topic` as a default argument for `t`.

**A9.** `Vehicle.Battery.SoC` → `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` and `Vehicle.Cabin.Temperature` → `Vehicle.Cabin.HVAC.AmbientAirTemperature`. `Vehicle.Speed` was already the standard COVESA path.

**A10.** `ros2-subscriber` only subscribes to topics and logs the values — it has no business logic and makes no decisions. It exists solely to prove that DDS delivery between containers works end-to-end. In a production AD stack, its role would be played by a real ROS2 node: a path planner that reads `/vehicle/speed` to compute safe trajectories, an emergency braking node that monitors `/vehicle/battery/soc`, or a sensor fusion node that combines vehicle speed with LiDAR point clouds.

---

*This document is part of the mini-sdv-platform living documentation. See also: `docs/learning/architecture_review_m1_m2.md` for M1 and M2 concepts.*
