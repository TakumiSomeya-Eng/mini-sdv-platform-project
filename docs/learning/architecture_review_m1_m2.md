# Mini SDV Platform — Architecture Review & Study Guide
## Milestone 1 (In-Vehicle Signal Pipeline) & Milestone 2 (MQTT Cloud Bridge)

> **Date:** 2026-05-23

---

## Table of Contents

1. [What This Project Teaches](#1-what-this-project-teaches)
2. [Project Methodology: Hypothesis Hierarchy Model](#2-project-methodology-hypothesis-hierarchy-model)
3. [What Is a Software Defined Vehicle?](#3-what-is-a-software-defined-vehicle)
4. [Overall Architecture Overview](#4-overall-architecture-overview)
5. [Milestone 1: In-Vehicle Signal Pipeline](#5-milestone-1-in-vehicle-signal-pipeline)
   - 5-1. VSS (Vehicle Signal Specification)
   - 5-2. Kuksa Databroker
   - 5-3. ECU Simulator
   - 5-4. Streamlit Dashboard
   - 5-5. gRPC and the Kuksa VAL API
6. [Milestone 2: Vehicle-to-Cloud (V2C) Gateway](#6-milestone-2-vehicle-to-cloud-v2c-gateway)
   - 6-1. Eclipse Mosquitto
   - 6-2. MQTT Bridge
   - 6-3. MQTT Topic Design
   - 6-4. Telemetry Payload Specification
7. [The Most Important Design Decision: Subscribe vs. Poll](#7-the-most-important-design-decision-subscribe-vs-poll)
8. [Docker Compose Orchestration](#8-docker-compose-orchestration)
9. [Deep Dive: Service Implementation Walkthroughs](#9-deep-dive-service-implementation-walkthroughs)
   - 9-1. ECU Simulator (`ecu-simulator/main.py`)
   - 9-2. MQTT Bridge (`mqtt-bridge/main.py`)
   - 9-3. Dashboard (`dashboard/main.py`)
10. [Container Design Principles](#10-container-design-principles)
11. [Known Constraints and the Road Ahead](#11-known-constraints-and-the-road-ahead)
12. [Review Quiz](#12-review-quiz)

---

## 1. What This Project Teaches

This project reproduces the **signal flow of a modern SDV (Software Defined Vehicle) platform** as a miniature system runnable with Docker alone.

Rather than diagrams and descriptions, it lets you observe — live — which component owns which responsibility and how data flows between them.

Every service maps to a pattern or technology used in real automotive software organizations:

| This Project | Production SDV Equivalent |
|---|---|
| ECU Simulator (Python) | Physical ECU (NXP S32, Renesas R-Car, etc.) |
| Sinusoidal + noise model | Real sensor data (wheel speed sensors, NTC thermistors) |
| Direct gRPC publish | CAN frame → Automotive Gateway ECU → gRPC |
| Kuksa Databroker | Central Vehicle Computer running AUTOSAR Adaptive / Android Automotive |
| Docker bridge network `sdv-net` | In-vehicle Ethernet (SOME/IP, DDS) |
| Streamlit dashboard | HMI app / Cloud telematics backend / AI monitoring agent |
| Mosquitto MQTT broker | AWS IoT Core / Azure IoT Hub / HiveMQ Cloud |

---

## 2. Project Methodology: Hypothesis Hierarchy Model

Before writing any code, this project defines and validates five hypothesis layers in strict order:

```
L1: Value Hypothesis        → Does this feature provide real value?
        ↓ validate before proceeding
L2: Behavior Hypothesis     → How will users interact with it?
        ↓ validate before proceeding
L3: Domain Hypothesis       → What are the business rules and domain logic?
        ↓ validate before proceeding
L4: Interaction Hypothesis  → What is the optimal UI/UX interaction?
        ↓ validate before proceeding
L5: Implementation Hypothesis → What is the optimal technical implementation?
```

**Why this order matters:** Starting at L5 (implementation) risks building the wrong thing perfectly. Each phase document maps directly to these layers:

| Document | Hypothesis Layers |
|---|---|
| PRD (Product Requirements) | L1 Value + L2 Behavior |
| FRD (Functional Requirements) | L3 Domain + L4 Interaction |
| TRD (Technical Requirements) | L5 Implementation |

---

## 3. What Is a Software Defined Vehicle?

### Traditional Vehicle Architecture (The Problem)

```
ECU-A ←──CAN──→ ECU-B ←──CAN──→ ECU-C
  (tightly coupled — hard to update, hard to extend)
```

Each ECU **owns** its own data. Adding a new feature (e.g., cloud telemetry) requires wiring into every relevant ECU individually. This does not scale.

### SDV Architecture (The Solution)

```
ECU-A ─┐
ECU-B ─┼──▶  Central Vehicle Middleware  ──▶  Any Application
ECU-C ─┘        (Databroker / VAL)
```

All vehicle data flows through a central **Vehicle Abstraction Layer (VAL)**. Applications — the instrument cluster, a cloud backend, an AI safety agent — subscribe to named signals without knowing which ECU produces them. ECUs can be replaced or updated without changing any application code.

> **This is the core concept this project demonstrates.** The ECU Simulator writes to the Databroker; the Dashboard and MQTT Bridge read from it. Nothing talks to an ECU directly.

---

## 4. Overall Architecture Overview

### Full system after Milestone 2 (5 services)

```
┌─────────────────────────────────────────────────────────────────┐
│                  Docker Compose: sdv-net                         │
│                                                                  │
│  ┌──────────────────┐   gRPC SetCurrentValues                   │
│  │  ecu-simulator   │─────────────────────────┐                │
│  │  (3 ECUs modeled)│                          ↓                │
│  └──────────────────┘            ┌─────────────────────────┐   │
│   ↑ 1 s physics tick             │   kuksa-databroker      │   │
│                                  │   :55555 (gRPC)         │   │
│                                  │                         │   │
│                                  │  • Vehicle.Speed        │   │
│                                  │  • Vehicle.Battery.SoC  │   │
│                                  │  • Vehicle.Cabin.Temp   │   │
│                                  └─────────────────────────┘   │
│                                     │              │            │
│                            gRPC Get │  gRPC        │ Subscribe  │
│                            (poll)   │  (stream)    │            │
│                                     ↓              ↓            │
│                        ┌──────────────┐  ┌──────────────────┐  │
│                        │  dashboard   │  │   mqtt-bridge    │  │
│                        │  :8501       │  │  (V2C gateway)   │  │
│                        └──────────────┘  └──────────────────┘  │
│                                                  │              │
│                                     MQTT publish :1883          │
│                                                  ↓              │
│                                       ┌──────────────────────┐  │
│                                       │   mosquitto          │  │
│                                       │   :1883              │  │
│                                       └──────────────────────┘  │
└──────────────────────────────────────────┬───────────────────────┘
         http://localhost:8501 (dashboard)  │ :1883 (MQTT)
                                            ↓
                               mosquitto_sub (host CLI)
```

---

## 5. Milestone 1: In-Vehicle Signal Pipeline

### 5-1. VSS (Vehicle Signal Specification)

VSS is a hierarchical naming standard for vehicle data, maintained by COVESA (Connected Vehicle Systems Alliance). Using a shared catalog means any tool or service that understands VSS can consume your signals without custom integration.

```json
// config/vss/vss_mini.json  (flat, human-readable format)
{
  "Vehicle.Speed": {
    "datatype": "float",
    "unit": "km/h",
    "min": 0,
    "max": 250
  }
}
```

```json
// config/vss/vss_mini_covesa.json  (COVESA hierarchical format — loaded by Databroker)
{
  "Vehicle": {
    "type": "branch",
    "children": {
      "Speed": { "datatype": "float", "type": "sensor", "unit": "km/h" }
    }
  }
}
```

**Why two files?** Kuksa Databroker only accepts the COVESA hierarchical (nested JSON) format, which is hard for humans to read. The flat `vss_mini.json` is kept as a human-readable companion reference; the hierarchical `vss_mini_covesa.json` is what the Databroker actually loads.

**The three VSS signals defined in this project:**

| VSS Path | Unit | Simulated Range | Simulated ECU |
|---|---|---|---|
| `Vehicle.Speed` | km/h | 10–120 | Powertrain ECU |
| `Vehicle.Battery.SoC` | percent | 55–85 | Battery Management System |
| `Vehicle.Cabin.Temperature` | celsius | 19.5–24.5 | HVAC Controller |

> **Note:** `Vehicle.Battery.SoC` is a shortened custom path chosen for M1 readability. The standard COVESA VSS 4.x equivalent is `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current`. Migration to standard paths is planned for M3.

---

### 5-2. Kuksa Databroker

**Role:** The **Single Source of Truth** for all vehicle signal state.

```yaml
# from docker-compose.yml
databroker:
  image: ghcr.io/eclipse/kuksa.val/databroker:0.4.4
  command: ["--vss", "/vss.json", "--insecure"]
  volumes:
    - ./config/vss/vss_mini_covesa.json:/vss.json:ro
```

**Core domain rule (DR-00):**
> The Databroker is the **single source of truth** for all vehicle signal state. No service communicates vehicle data directly with another service — all reads and writes flow through the Databroker.

**Health check design:**

```yaml
healthcheck:
  test: ["CMD-SHELL", "timeout 1 bash -c 'cat < /dev/null > /dev/tcp/localhost/55555'"]
  interval: 5s
  timeout: 3s
  retries: 10
  start_period: 10s
```

A TCP port probe is used instead of `grpc-health-probe` to avoid modifying the upstream Databroker image. Known constraint: confirms network availability only, not that the VSS catalog has loaded correctly. The `start_period: 10s` provides a buffer.

---

### 5-3. ECU Simulator

**Role:** Simulates three ECUs and writes vehicle signals to the Databroker via gRPC every second.

#### Physics Simulation (the `VehicleState` class)

```python
class VehicleState:
    def speed(self) -> float:
        # Sinusoid (65 + 50*sin(t*0.04)) + Gaussian noise
        # → smooth 10–120 km/h variation
        base  = 65.0 + 50.0 * math.sin(self._t * 0.04)
        noise = random.gauss(0.0, 1.5)
        return round(max(0.0, min(250.0, base + noise)), 1)

    def battery_soc(self) -> float:
        # Linear drain 85 % → 55 % over 600 ticks (~10 min), then reset
        phase = self._t % 600
        base  = 85.0 - phase * 0.05
        return round(max(0.0, min(100.0, base + noise)), 2)

    def cabin_temperature(self) -> float:
        # HVAC cycling modeled as a sinusoid
        # Range: 19.5–24.5 °C
        base  = 22.0 + 2.5 * math.sin(self._t * 0.015)
        return round(base + noise, 1)
```

Each signal uses a **different angular frequency** so the charts don't look identical.

#### Exponential Back-off Reconnect Loop

```python
retry_delay = 2.0
while True:
    try:
        with VSSClient(host, port) as client:
            retry_delay = 2.0   # reset on successful connect
            while True:
                client.set_current_values({...})
                time.sleep(UPDATE_INTERVAL)
    except Exception as exc:
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30.0)  # cap at 30 s
```

**Why not rely solely on Docker's `restart: on-failure`?** A Docker restart restarts the entire container — heavier than necessary for a transient network blip. An in-process reconnect loop recovers within milliseconds to a few seconds and is the standard pattern for cloud-native services.

---

### 5-4. Streamlit Dashboard

**Role:** A read-only consumer that polls the Databroker and renders live vehicle signals in a browser.

**URL:** `http://localhost:8501`

#### UI Layout

```
┌───────────────────────────────────────────────────────────┐
│  mini-SDV Platform                              ● Connected│
├──────────────────┬──────────────────┬─────────────────────┤
│  Vehicle Speed   │ Battery SoC      │ Cabin Temperature   │
│  87.3  km/h      │  72.40  %        │  22.1  °C           │
│  ▲ +2.1          │  ▼ -0.05         │  → 0.0              │
├──────────────────┴──────────────────┴─────────────────────┤
│  Speed   — 60-second rolling line chart                    │
│  SoC     — 60-second rolling line chart                    │
│  Temp    — 60-second rolling line chart                    │
└───────────────────────────────────────────────────────────┘
```

#### Session State Design

```python
st.session_state.history = {
    "Vehicle.Speed":             deque(maxlen=60),
    "Vehicle.Battery.SoC":       deque(maxlen=60),
    "Vehicle.Cabin.Temperature": deque(maxlen=60),
}
```

`deque(maxlen=60)` automatically drops the oldest entry once full — a rolling window with no database required.

#### Auto-Refresh Mechanism

```python
time.sleep(REFRESH_INTERVAL)   # wait 1 second
st.rerun()                     # re-execute the script from the top
```

Streamlit re-runs the entire script on each `st.rerun()` call, re-rendering the UI with the latest data. This is the idiomatic Streamlit pattern for live dashboards.

---

### 5-5. gRPC and the Kuksa VAL API

**gRPC** is a high-performance RPC framework built on HTTP/2. It uses Protocol Buffers for typed message serialization — more efficient than JSON/REST.

The two Kuksa VAL API methods used in M1:

| Method | Direction | Used By | Action |
|---|---|---|---|
| `SetCurrentValues` | ECU Sim → Databroker | ecu-simulator | Write signal values |
| `GetCurrentValues` | Dashboard → Databroker | dashboard | Request current values |

```python
# Write (ECU Simulator)
client.set_current_values({
    "Vehicle.Speed":             Datapoint(87.3),
    "Vehicle.Battery.SoC":       Datapoint(72.40),
    "Vehicle.Cabin.Temperature": Datapoint(22.1),
})

# Read (Dashboard)
response = client.get_current_values([
    "Vehicle.Speed",
    "Vehicle.Battery.SoC",
    "Vehicle.Cabin.Temperature",
])
# response: dict[str, Datapoint]
# Datapoint.value     → current value (float)
# Datapoint.timestamp → server-side timestamp assigned by the Databroker
```

**Timestamp authority (DR-05):** The Databroker's server-side timestamp is the authoritative signal time — not the ECU simulator's clock. This prevents clock drift issues in distributed systems.

---

## 6. Milestone 2: Vehicle-to-Cloud (V2C) Gateway

M2 adds a **cloud exit point** to the in-vehicle signal pipeline established in M1.

### What changes architecturally from M1 to M2

| | M1 | Added in M2 |
|---|---|---|
| Data boundary | Stays inside the vehicle network | Crosses into the cloud |
| Protocol | gRPC (typed, low-latency, internal) | MQTT (JSON, flexible, cloud-friendly) |
| Consumers | Streamlit dashboard | Any MQTT subscriber |

---

### 6-1. Eclipse Mosquitto

**Role:** MQTT broker. Receives vehicle telemetry from the MQTT bridge and distributes it to any number of subscribers.

```yaml
mosquitto:
  image: eclipse-mosquitto:2.0
  ports:
    - "1883:1883"   # exposed to host for CLI testing
```

```conf
# config/mosquitto/mosquitto.conf
listener 1883
allow_anonymous true   # no auth in M2 (known constraint)
```

**Real-world mapping:**

| Mosquitto (this project) | Production cloud MQTT broker |
|---|---|
| Plain text :1883 | TLS-encrypted :8883 |
| Anonymous access | Certificate auth / mTLS |
| Local Docker | AWS IoT Core / Azure IoT Hub / HiveMQ |

The critical point: **the MQTT Bridge requires zero code changes to connect to a real cloud broker** — only `MQTT_HOST` and `MQTT_PORT` environment variables change.

---

### 6-2. MQTT Bridge

**Role:** The **V2C gateway**. Subscribes to Kuksa Databroker via gRPC streaming and publishes each signal update to Mosquitto as a JSON payload.

This service is the **architecturally significant boundary**:

```
[ In-vehicle network ]              [ Cloud ]
  gRPC / VSS                          MQTT / JSON
  typed, low-latency                  flexible, scalable
  (authenticated in production)
         ↑                                 ↑
         │        [ mqtt-bridge ]          │
         └─── only this service crosses ───┘
                   this boundary
```

---

### 6-3. MQTT Topic Design

```python
def vss_to_topic(vss_path: str) -> str:
    # "Vehicle.Battery.SoC" → "sdv/vehicle-001/Vehicle/Battery/SoC"
    mqtt_path = vss_path.replace(".", "/")
    return f"sdv/{VEHICLE_ID}/{mqtt_path}"
```

**Why slashes instead of dots?**

MQTT topic hierarchy uses `/` as its level separator. Using slashes unlocks hierarchical wildcard subscriptions:

```bash
# All signals from vehicle-001
mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/#" -v

# Speed from any vehicle in the fleet (future)
mosquitto_sub -h localhost -p 1883 -t "sdv/+/Vehicle/Speed" -v

# All Battery-branch signals from vehicle-001
mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/Vehicle/Battery/#" -v
```

`+` is a single-level wildcard; `#` is a multi-level wildcard.

---

### 6-4. Telemetry Payload Specification

```json
{
  "signal":    "Vehicle.Speed",
  "value":     87.3,
  "unit":      "km/h",
  "timestamp": "2026-05-23T14:30:01"
}
```

**Field design rationale:**

| Field | Type | Design Intent |
|---|---|---|
| `signal` | string | Full VSS path included → self-describing without schema lookup |
| `value` | number | JSON number (not string) → receiver can do arithmetic directly |
| `unit` | string | Matches VSS catalog → cloud side can interpret without extra context |
| `timestamp` | string | ISO 8601 → parseable by any language and framework |

This format mirrors the telemetry schemas used by AWS IoT Core, Azure IoT Hub, and the COVESA VISS specification.

---

## 7. The Most Important Design Decision: Subscribe vs. Poll

This is the **most important technical concept introduced in M2**.

### Comparison

| | M1 Dashboard (Poll) | M2 MQTT Bridge (Subscribe) |
|---|---|---|
| API method | `get_current_values()` | `subscribe_current_values()` |
| Mechanism | Pull: requests values on a fixed timer | Push: server notifies the moment a value changes |
| Latency | Up to `REFRESH_INTERVAL` (1 s) | Near-zero — receives update within ms of ECU publish |
| CPU profile | gRPC call fires even when nothing changed | Active only when a signal change occurs |
| Threading | Single-threaded (fits Streamlit's rerun model) | Blocking iterator (fits long-running bridge process) |
| SDV use case | UI refresh, human-readable display | Forwarding, bridging, recording, alerting |

### Code Comparison

```python
# === Poll (Dashboard) ===
# "Give me the current values right now" — fires every 1 second
response = client.get_current_values([
    "Vehicle.Speed", "Vehicle.Battery.SoC", "Vehicle.Cabin.Temperature"
])

# === Subscribe (MQTT Bridge) ===
# "Notify me whenever a value changes" — event-driven, immediate
# Blocking iterator: yields each time one or more signals change
for updates in client.subscribe_current_values(SIGNAL_PATHS):
    for path, datapoint in updates.items():
        forward_to_mqtt(path, datapoint)
```

### Why the Bridge MUST Use Subscribe (DR-15)

A forwarding bridge must transmit data **the moment it changes**.

Using poll instead would:
- Introduce up to 1 second of artificial latency per update
- Send duplicate MQTT messages in cycles where nothing changed (wasted traffic)
- Be architecturally wrong — a bridge is a **reactive forwarder**, not a scheduled job

---

## 8. Docker Compose Orchestration

### Service Startup Order

```
Phase 1 (parallel)
  ├── databroker   → waits until TCP :55555 health check passes
  └── mosquitto    → waits until TCP :1883  health check passes

Phase 2 (depends_on: condition: service_healthy)
  ├── ecu-simulator → starts after databroker is healthy
  ├── dashboard     → starts after databroker is healthy
  └── mqtt-bridge   → starts after BOTH databroker AND mosquitto are healthy
```

**Why `condition: service_healthy` matters:** Without it, the ECU Simulator would attempt a gRPC connection before the Databroker is ready, causing connection errors. The health check condition ensures dependent services only start once the upstream is genuinely accepting connections.

### Network Design

```yaml
networks:
  sdv-net:
    driver: bridge
```

All five services share one internal bridge network. Ports exposed to the host machine:

| Port | Service | Purpose |
|---|---|---|
| `:8501` | dashboard | Streamlit UI |
| `:1883` | mosquitto | CLI testing (`mosquitto_sub`) |

The Databroker's gRPC port (`:55555`) is intentionally **not** exposed to the host. This mirrors the SDV principle that internal vehicle middleware is not directly accessible from outside.

### Environment Variable Pattern (12-Factor App, Factor III)

```yaml
ecu-simulator:
  environment:
    DATABROKER_HOST: databroker   # Docker internal DNS name
    DATABROKER_PORT: "55555"
    UPDATE_INTERVAL_SEC: "1.0"   # easy to change for experimentation
```

No hostnames are hardcoded anywhere in Python source files. Changing `DATABROKER_HOST` is all it takes to point the simulator at a different Databroker instance.

---

## 9. Deep Dive: Service Implementation Walkthroughs

### 9-1. ECU Simulator (`ecu-simulator/main.py`)

**File structure:**
```
main.py
├── VehicleState class
│   ├── speed()               → sinusoid + Gaussian noise
│   ├── battery_soc()         → linear drain + periodic reset
│   ├── cabin_temperature()   → sinusoidal HVAC cycle
│   └── advance()             → increment simulation clock
└── run() function
    ├── outer loop: reconnect logic (exponential back-off)
    └── inner loop: set_current_values() → time.sleep()
```

**Key things to notice when reading the code:**

1. `VehicleState.__init__`: `self._t = 0.0` is the simulation clock — incremented by `advance()` on every tick.
2. Each signal uses a different angular frequency (`0.04`, `0.015`). This is why the charts look different.
3. `retry_delay = min(retry_delay * 2, 30.0)`: back-off doubles each retry — 2 → 4 → 8 → 16 → 30 → 30 … — capping at 30 s to ensure periodic retries never stop.

---

### 9-2. MQTT Bridge (`mqtt-bridge/main.py`)

**File structure:**
```
main.py
├── SIGNALS dict         (path → unit metadata)
├── vss_to_topic()       → replace dots with slashes, prepend sdv/{vehicle_id}/
├── make_payload()       → build JSON string
├── connect_mqtt()       → create paho-mqtt client, connect, start loop thread
└── run() function
    ├── connect to Mosquitto
    ├── connect to Kuksa Databroker
    └── subscribe_current_values() blocking iterator loop
```

**Key things to notice when reading the code:**

1. `client.loop_start()`: paho-mqtt spawns a background thread that handles KEEPALIVE ping/pong and reconnection — the bridge itself doesn't manage this.
2. `for updates in kuksa.subscribe_current_values(SIGNAL_PATHS):`: this single line is what makes the bridge fundamentally different from the M1 dashboard.
3. `mqtt_client.publish(topic, payload, qos=0)`: QoS 0 — fire and forget. Suitable for high-frequency telemetry where an occasional lost message is acceptable.
4. The `finally:` block ensures `loop_stop()` and `disconnect()` are always called even if an exception is raised.

---

### 9-3. Dashboard (`dashboard/main.py`)

**File structure:**
```
main.py
├── SIGNALS dict              (path → display metadata: label, unit, ECU, format, color)
├── init_session_state()      → initialise st.session_state on first run
├── poll_databroker()         → one get_current_values() call per cycle
├── render_header()           → title + connection status badge
├── render_metrics()          → 3 metric cards with current value and delta
├── render_charts()           → 3 rolling line charts
├── render_sidebar()          → architecture explanation + MQTT info
└── main()                    → Streamlit entry point
```

**Key things to notice when reading the code:**

1. The `if "history" not in st.session_state:` guard in `init_session_state()`: Streamlit re-runs the entire script from top on every cycle. Without this check, the history buffer would be reset to empty every second.
2. The `SIGNALS` dict pattern: adding a new signal in a future milestone only requires one new entry here — the UI renders automatically (Open/Closed Principle).
3. `time.sleep(REFRESH_INTERVAL)` → `st.rerun()`: this two-liner is the entire auto-refresh mechanism.

---

## 10. Container Design Principles

Both custom services share these Dockerfile conventions:

### 1. `python:3.11-slim` base image

```dockerfile
FROM python:3.11-slim
```

Lighter than the full `python:3.11` image (~400 MB → ~150 MB). Smaller attack surface for production deployments.

### 2. `PYTHONUNBUFFERED=1`

```dockerfile
ENV PYTHONUNBUFFERED=1
```

**This is critical.** Without it, Python's print/logging output is buffered in memory and does not appear in `docker compose logs` in real time. Worse, if the container crashes, buffered logs may be lost entirely.

### 3. Layer cache optimization

```dockerfile
COPY requirements.txt .          ← copy only requirements.txt first
RUN pip install ...               ← install deps (this layer is cached)
COPY main.py .                    ← copy source code last
```

If `main.py` changes but `requirements.txt` does not, the `pip install` layer is served from cache. This dramatically reduces rebuild time during development.

### 4. Non-root user execution

```dockerfile
USER 1000
```

Running as a non-root user is a security baseline requirement in production container environments.

---

## 11. Known Constraints and the Road Ahead

### Items intentionally deferred in M1 and M2

| Constraint | Production SDV Equivalent | Target Milestone |
|---|---|---|
| No gRPC auth (`--insecure`) | mTLS + token-based auth | Security milestone |
| No MQTT auth (anonymous) | Certificate auth / mTLS | Security milestone |
| `Vehicle.Battery.SoC` is a non-standard path | Standard COVESA VSS path | M3 |
| TCP probe only (no gRPC app-layer health check) | `grpc-health-probe` | M3+ |
| In-memory signal history (lost on restart) | InfluxDB / TimescaleDB | M3+ |
| Direct gRPC publish (no CAN bus) | CAN bus → Gateway ECU → gRPC | M4 |
| Single vehicle ID only | Multi-vehicle fleet simulation | M4 |

### Milestone Roadmap

| Milestone | Goal | New Services | New Concepts |
|---|---|---|---|
| **M1** ✅ | Live vehicle signal dashboard | Kuksa Databroker, ECU Simulator, Dashboard | VSS, gRPC, centralized middleware |
| **M2** ✅ | Cloud connectivity | MQTT Broker, MQTT Bridge | MQTT, V2C telemetry, Subscribe vs. Poll |
| **M3** | ROS2 integration | ROS2 node | DDS, topic-based pub/sub, sensor fusion |
| **M4** | Virtual CAN bus | SocketCAN ECUs | ISO 11898, CAN frames, Gateway ECU pattern |
| **M5** | AI agent | LLM-based orchestrator | Intelligent actuation, anomaly detection |

---

## 12. Review Quiz

Use these questions to test your understanding of the project.

**Architecture**

1. Why does the Dashboard not communicate directly with the ECU Simulator?
2. What happens if you remove `depends_on: condition: service_healthy` from the ECU Simulator definition?
3. What does the Databroker's `--insecure` flag mean, and why is it acceptable in M1?

**Protocols**

4. What is the difference between `get_current_values()` and `subscribe_current_values()`? When should you use each?
5. What information can you infer from the MQTT topic `sdv/vehicle-001/Vehicle/Battery/SoC`?
6. Explain MQTT QoS levels 0, 1, and 2. Which is most appropriate for the Speed signal? For a safety-critical battery warning event?

**Implementation**

7. Why does the ECU Simulator's reconnect logic use `retry_delay = min(retry_delay * 2, 30.0)` rather than a fixed sleep?
8. What goes wrong if `PYTHONUNBUFFERED=1` is not set?
9. Why do two VSS catalog files (`vss_mini.json` and `vss_mini_covesa.json`) exist?

**Extensibility**

10. You want to add a new signal `Vehicle.FuelLevel` (fuel level as a percentage). List every file you need to modify and in what order.

---

<details>
<summary>Answer Key</summary>

1. **The SDV centralized architecture principle (DR-00).** All signal reads and writes go through the Databroker. This decoupling means ECUs can be swapped or restarted without changing any application code.

2. The ECU Simulator attempts a gRPC connection before the Databroker is ready, causing an immediate connection error. The exponential back-off loop will eventually recover, but you will see error logs at startup.

3. `--insecure` disables TLS on the gRPC interface. It is acceptable in M1 because certificate management (CA, rotation, mTLS) would distract from the SDV architecture lesson. It is explicitly documented as a known limitation with a clear migration path.

4. `get_current_values()` is Pull — it requests the current values on demand, typically on a fixed timer. `subscribe_current_values()` is Push — the server notifies the client the moment a value changes. Use Poll for UI refresh (human-readable); use Subscribe for forwarding, bridging, or recording (reactive).

5. Namespace `sdv`, vehicle ID `vehicle-001`, VSS path `Vehicle.Battery.SoC` (dots converted to slashes). The slash hierarchy enables `#` wildcard subscriptions at any level of the tree.

6. QoS 0: at most once (fire and forget, may be lost). QoS 1: at least once (guaranteed delivery, may duplicate). QoS 2: exactly once (no loss, no duplicate). Speed: QoS 0 is fine (one dropped sample is invisible to humans). A battery warning event: QoS 1, because missing a critical alert is unacceptable.

7. Fixed sleep is too aggressive when a broker is slow to restart — you'd hammer it with rapid reconnection attempts. Exponential back-off reduces load on the recovering service. The 30-second cap ensures retries never stop completely.

8. Python's stdout/stderr are buffered in memory. `docker compose logs` shows nothing in real time; if the container crashes, the last lines of output (often the error message) may be lost.

9. Kuksa Databroker requires the COVESA hierarchical (nested) JSON format for its `--vss` flag. That format is difficult for humans to read. The flat `vss_mini.json` is kept as a human-readable companion reference document.

10. In order: ① Add the signal entry to `config/vss/vss_mini_covesa.json` → ② Add the matching entry to `config/vss/vss_mini.json` (reference doc) → ③ Add simulation logic to `services/ecu-simulator/main.py` → ④ Add an entry to the `SIGNALS` dict in `services/dashboard/main.py` → ⑤ Add an entry to the `SIGNALS` dict in `services/mqtt-bridge/main.py` → ⑥ `docker compose build && docker compose up`.

</details>

---

*This document covers the artifacts produced through Milestone 1 and Milestone 2 of the `mini-sdv-platform` project.*
