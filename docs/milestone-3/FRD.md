# Functional Requirements Document (FRD)
## Milestone 3: ROS2 Integration
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | FRD |
| Milestone | 3 — ROS2 Integration |
| Status | Draft |
| Hypothesis Layers | Domain (L3) · Interaction (L4) |
| Created | 2026-05-24 |
| Version | 1.0 |
| Depends On | [PRD.md](PRD.md) · Milestone 2 FRD |
| Next Layer | [TRD.md](TRD.md) |

---

## 1. System Context

```
[ECU Simulator] ──gRPC──▶ [Kuksa Databroker] ──gRPC──▶ [Dashboard]      (M1)
                                   │                ──gRPC──▶ [MQTT Bridge] ──MQTT──▶ [Mosquitto]  (M2)
                                   │
                         gRPC subscribe_current_values   (M3 NEW)
                                   │
                                   ▼
                           [ros2-bridge]
                                   │
                           ROS2 publish (DDS)
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
          /vehicle/speed              /vehicle/battery/soc
          /vehicle/cabin/temperature
                    │
                    ▼
           [ros2-subscriber]          (M3 NEW — Docker test service)
           docker compose logs -f ros2-subscriber
```

**Architectural invariants carried from M1/M2:**
- Databroker remains the single source of truth for all vehicle signal state (DR-00)
- All consumers are read-only — none writes back to the Databroker in M3 (DR-10)
- All services start with `docker compose up`; no host dependencies

**New M3 invariant (DR-20):**
- The `ros2-bridge` is read-only from the Kuksa side. It MUST NOT write any signal values to the Databroker.

**M3 also resolves:**
- VSS signal paths are migrated to standard COVESA VSS 4.x paths (deferred since M1).

---

## 2. User Stories

| ID | As a… | I want to… | So that… | Acceptance Criterion |
|---|---|---|---|---|
| US-20 | SDV engineer | see the ROS2 bridge forwarding Kuksa signals to ROS2 topics in the logs | I can verify the Kuksa–ROS2 bridge works end-to-end | `docker compose logs -f ros2-bridge` shows each published topic with value |
| US-21 | robotics engineer | subscribe to `/vehicle/speed` and receive live data | I can write an AD node that consumes vehicle signals without knowing Kuksa | `docker compose logs -f ros2-subscriber` shows topic data updating at ≥ 1 Hz |
| US-22 | learner | read code comments comparing DDS/ROS2 pub/sub with gRPC and MQTT | I can understand when each middleware paradigm is used in a real SDV platform | Bridge code has inline comments explaining DDS vs gRPC vs MQTT |
| US-23 | learner | restart the Databroker and watch the ROS2 bridge recover | I can experiment safely without permanently breaking the stack | Bridge reconnects within 30 s; ROS2 topics resume publishing |
| US-24 | learner | use the M1 dashboard and M2 MQTT bridge unchanged alongside M3 | I can see three consumers (UI / cloud / AD stack) reading the same Databroker | All M1 and M2 acceptance criteria continue to pass |
| US-25 | learner | see that VSS paths now match COVESA standard names | I understand the industry-standard signal naming convention | Databroker VSS catalog uses `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` and `Vehicle.Cabin.HVAC.AmbientAirTemperature` |

---

## 3. Domain Model (Hypothesis L3)

### 3.1 Updated VSS Signal Catalog (M3 Migration)

M3 migrates two of the three VSS paths from M1/M2 custom names to COVESA VSS 4.x standard paths:

| Signal | M1/M2 Path (custom) | M3 Path (COVESA standard) | Unit |
|---|---|---|---|
| Vehicle speed | `Vehicle.Speed` | `Vehicle.Speed` *(unchanged — already standard)* | km/h |
| Battery SoC | `Vehicle.Battery.SoC` | `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | percent |
| Cabin temperature | `Vehicle.Cabin.Temperature` | `Vehicle.Cabin.HVAC.AmbientAirTemperature` | celsius |

All four services that reference VSS paths (ECU Simulator, Dashboard, MQTT Bridge, ROS2 Bridge) must be updated to use the new paths. The VSS catalog files (`vss_mini_covesa.json` and `vss_mini.json`) must be updated accordingly.

### 3.2 New Domain Entities

```
ROS2Node (ros2-bridge service)
  - Subscribes to Kuksa Databroker using gRPC streaming
  - Publishes each Datapoint as a ROS2 topic message
  - Read-only from the Kuksa side (same constraint as mqtt-bridge)

ROS2Topic
  - Hierarchical address for a stream of ROS2 messages
  - Naming: /vehicle/{signal_label}  (see mapping table in §3.3)
  - Message type: std_msgs/msg/Float32

DDSParticipant
  - ROS2 runtime entity that manages topic discovery and DDS communication
  - Within the Docker network, all ROS2 nodes share one DDS domain (DOMAIN_ID=0)

ROS2SubscriberNode (ros2-subscriber service)
  - A ROS2 node that subscribes to all three vehicle topics
  - Logs each received message to stdout
  - Used exclusively for integration testing — no business logic
```

### 3.3 VSS Path → ROS2 Topic Mapping

| COVESA VSS Path | ROS2 Topic | Message Type | Notes |
|---|---|---|---|
| `Vehicle.Speed` | `/vehicle/speed` | `std_msgs/msg/Float32` | Unchanged from M1 signal |
| `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | `/vehicle/battery/soc` | `std_msgs/msg/Float32` | Renamed from M1/M2 |
| `Vehicle.Cabin.HVAC.AmbientAirTemperature` | `/vehicle/cabin/temperature` | `std_msgs/msg/Float32` | Renamed from M1/M2 |

**ROS2 topic naming rationale:**
- Snake_case, all lowercase: ROS2 naming convention
- `/vehicle/` prefix: namespace that groups all vehicle-signal topics
- Simplified leaf names (`soc`, `temperature`): balances readability with uniqueness within the `/vehicle/` namespace
- No dots: ROS2 topic names use `/` as hierarchy separator (same principle as MQTT)

### 3.4 Business Rules

| ID | Rule |
|---|---|
| DR-00 | (carried) Databroker is the single source of truth. No direct ECU↔Bridge path. |
| DR-10 | (carried) All bridge services are read-only consumers of the Databroker. |
| DR-20 | `ros2-bridge` MUST NOT write to the Databroker. Read-only consumer. |
| DR-21 | All services MUST use the COVESA VSS 4.x standard paths defined in §3.1. No service may reference the deprecated M1/M2 custom paths after M3. |
| DR-22 | ROS2 DDS communication MUST be confined to the `sdv-net` Docker network. No ROS2 traffic is exposed to the host network. |
| DR-23 | The `ros2-bridge` MUST use `subscribe_current_values()` (gRPC streaming), not polling. Same reasoning as the `mqtt-bridge` (DR-15). |
| DR-24 | All ROS2 nodes MUST run within Docker containers on the official `ros:jazzy-ros-base` image. No host ROS2 installation is required. |
| DR-25 | The ROS2 DDS domain ID MUST be set to `0` (default) for inter-container discovery within `sdv-net`. |

---

## 4. Functional Requirements

### 4.1 VSS Catalog Update (All Services)

| ID | Priority | Requirement |
|---|---|---|
| FR-100 | MUST | `config/vss/vss_mini_covesa.json` MUST be updated to use the three COVESA standard paths defined in §3.1. |
| FR-101 | MUST | `config/vss/vss_mini.json` (flat reference) MUST be updated to match. |
| FR-102 | MUST | `services/ecu-simulator/main.py` MUST be updated to publish to the new COVESA paths. |
| FR-103 | MUST | `services/dashboard/main.py` MUST be updated to read from the new COVESA paths. |
| FR-104 | MUST | `services/mqtt-bridge/main.py` MUST be updated to subscribe to and forward the new COVESA paths. |
| FR-105 | SHOULD | The new MQTT topics for the renamed signals MUST reflect the new path: `sdv/vehicle-001/Vehicle/Powertrain/TractionBattery/StateOfCharge/Current`. |

### 4.2 ROS2 Bridge Service (`ros2-bridge`)

| ID | Priority | Requirement |
|---|---|---|
| FR-110 | MUST | The bridge MUST subscribe to all three COVESA VSS signal paths using `subscribe_current_values()` gRPC streaming. |
| FR-111 | MUST | The bridge MUST publish a `std_msgs/msg/Float32` message to the corresponding ROS2 topic for every Datapoint update received. |
| FR-112 | MUST | The bridge MUST map VSS paths to ROS2 topics using the mapping defined in §3.3. |
| FR-113 | MUST | The bridge MUST log each published message with topic name and value. |
| FR-114 | MUST | The Databroker endpoint MUST be configurable via `DATABROKER_HOST` and `DATABROKER_PORT` environment variables. |
| FR-115 | MUST | The ROS2 node name MUST be `vehicle_signal_bridge` (snake_case, ROS2 convention). |
| FR-116 | MUST | The bridge MUST implement exponential back-off reconnection on Databroker failure. |
| FR-117 | MUST | The DDS domain ID MUST be configurable via `ROS_DOMAIN_ID` environment variable (default: `0`). |
| FR-118 | SHOULD | The bridge SHOULD log a startup banner listing all subscribed VSS paths and their mapped ROS2 topics. |
| FR-119 | SHOULD | The bridge SHOULD handle `None` Datapoint values gracefully (signal not yet published by ECU). |

### 4.3 ROS2 Subscriber Service (`ros2-subscriber`)

| ID | Priority | Requirement |
|---|---|---|
| FR-120 | MUST | The subscriber MUST subscribe to all three `/vehicle/*` ROS2 topics. |
| FR-121 | MUST | Each received message MUST be logged to stdout in the format: `[<topic>] value=<float>`. |
| FR-122 | MUST | The subscriber MUST remain running after startup without exiting (long-running service). |
| FR-123 | MUST | The subscriber is a test/verification service only. It MUST NOT contain any business logic. |
| FR-124 | MUST | The DDS domain ID MUST match `ros2-bridge` (`ROS_DOMAIN_ID=0`). |

### 4.4 System-Level Behaviors

| ID | Priority | Requirement |
|---|---|---|
| FR-130 | MUST | `ros2-bridge` MUST NOT start until `databroker` passes its health check. |
| FR-131 | MUST | `ros2-subscriber` MUST NOT start until `ros2-bridge` has started (depends_on: service_started). |
| FR-132 | MUST | All six services MUST start with `docker compose up` from the project root. |
| FR-133 | MUST | All M1 and M2 acceptance criteria MUST continue to pass after M3 deployment. |
| FR-134 | MUST | All services MUST be stoppable with `docker compose down`. |

---

## 5. ROS2 Topic Specification

### 5.1 Topic Details

| Topic | Message Type | Field | Value Range | Publish Rate |
|---|---|---|---|---|
| `/vehicle/speed` | `std_msgs/msg/Float32` | `data` | 0–250 (sim: 10–120) | ≥ 1 Hz |
| `/vehicle/battery/soc` | `std_msgs/msg/Float32` | `data` | 0–100 (sim: 55–85) | ≥ 1 Hz |
| `/vehicle/cabin/temperature` | `std_msgs/msg/Float32` | `data` | -40–100 (sim: 19.5–24.5) | ≥ 1 Hz |

### 5.2 Why `std_msgs/msg/Float32` and Not a Custom Message Type?

Custom ROS2 message types require generating Python/C++ bindings at build time from `.msg` IDL files — adding significant Dockerfile and build complexity. `std_msgs/msg/Float32` carries a single `float32 data` field, which is sufficient for M3's goal of demonstrating the Kuksa–ROS2 bridge pattern.

In a production system, you would define a `VehicleSignal.msg` type that includes path, value, unit, and timestamp — analogous to the `TelemetryPayload` JSON in M2. This is deferred to M4+.

### 5.3 Real-World Context: DDS vs. gRPC vs. MQTT

| | gRPC (M1) | MQTT (M2) | DDS / ROS2 (M3) |
|---|---|---|---|
| Pattern | Request/Response + Streaming | Pub/Sub (broker-mediated) | Pub/Sub (brokerless, peer-to-peer) |
| Transport | HTTP/2 | TCP (via broker) | UDP multicast + TCP (direct) |
| Discovery | Manual endpoint config | Connect to broker | Automatic (DDS discovery protocol) |
| Strengths | Typed API, low latency, well-defined schema | IoT-scale, cloud-native, widely supported | Real-time, deterministic, no single point of failure |
| SDV use case | In-vehicle middleware API | V2C cloud telemetry | Autonomous driving software stack |
| This project | Databroker ↔ ECU/Dashboard | MQTT Bridge → Mosquitto | ROS2 Bridge → ROS2 nodes |

**Key DDS distinction — brokerless architecture:**
Unlike MQTT (which requires a broker like Mosquitto), DDS performs peer-to-peer discovery over multicast. There is no central broker to restart or fail. Within the Docker `sdv-net` network, `ros2-bridge` and `ros2-subscriber` discover each other automatically via DDS without any explicit connection configuration.

---

## 6. Interaction Design (Hypothesis L4)

### 6.1 Primary Verification Flow

```
Step 1 — Start the stack
  docker compose up
  → All 6 services reach running state

Step 2 — Watch the ROS2 bridge
  docker compose logs -f ros2-bridge
  → [INFO] vehicle_signal_bridge: Published /vehicle/speed = 87.3
  → [INFO] vehicle_signal_bridge: Published /vehicle/battery/soc = 72.4
  → [INFO] vehicle_signal_bridge: Published /vehicle/cabin/temperature = 22.1

Step 3 — Watch the ROS2 subscriber
  docker compose logs -f ros2-subscriber
  → [/vehicle/speed] value=87.3
  → [/vehicle/battery/soc] value=72.4
  → [/vehicle/cabin/temperature] value=22.1

Step 4 — Confirm M1 and M2 still work
  http://localhost:8501          → Streamlit dashboard still live
  mosquitto_sub -t "sdv/vehicle-001/#"  → MQTT messages still arriving
```

### 6.2 Resilience Verification Flow

```
Step 1 — Restart Databroker
  docker compose restart databroker

Step 2 — Watch ros2-bridge logs
  docker compose logs -f ros2-bridge
  → [WARNING] Connection error: ...
  → [INFO] Retrying in 2 s …
  → [INFO] Connected to Kuksa Databroker. Starting gRPC subscribe loop.
  → [INFO] Published /vehicle/speed = 89.1   ← resumes within 30 s
```

### 6.3 Three-Consumer Verification (The M3 "Money Shot")

```
Terminal A:  docker compose logs -f dashboard       → Streamlit polling logs
Terminal B:  docker compose logs -f mqtt-bridge     → MQTT publish logs
Terminal C:  docker compose logs -f ros2-subscriber → ROS2 topic logs

All three show the same vehicle data, at the same time,
via three completely different protocols:
  gRPC polling  → Dashboard (human UI)
  MQTT pub/sub  → Mosquitto (cloud)
  DDS pub/sub   → ROS2 topics (autonomous driving stack)
```

This side-by-side view is the core learning moment of M3: one Databroker, three consumers, three protocol paradigms.

---

## 7. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-20 | Latency | ROS2 message MUST arrive at `ros2-subscriber` within 500 ms of the Databroker receiving the Datapoint |
| NFR-21 | Reliability | `ros2-bridge` MUST auto-recover from Databroker restart without manual intervention |
| NFR-22 | Portability | All ROS2 services MUST run in Docker with no host ROS2 installation required |
| NFR-23 | Observability | All services MUST emit structured logs with ISO 8601 timestamps |
| NFR-24 | Backward compatibility | All M1 and M2 acceptance criteria MUST continue to pass |
| NFR-25 | Educational | Code MUST include inline comments explaining DDS discovery, ROS2 topic naming, and how this middleware layer differs from gRPC and MQTT |

---

## 8. Acceptance Criteria Summary

A Milestone 3 build is **PASSING** when all of the following are true:

**M3 new criteria:**
- [ ] `docker compose up` starts all 6 services without errors
- [ ] `docker compose logs -f ros2-bridge` shows published messages for all 3 topics at ≥ 1 Hz
- [ ] `docker compose logs -f ros2-subscriber` shows received messages for all 3 topics at ≥ 1 Hz
- [ ] `docker compose restart databroker` → `ros2-bridge` reconnects and resumes within 30 s
- [ ] VSS catalog contains standard COVESA paths (verified in `config/vss/vss_mini_covesa.json`)
- [ ] Dashboard and MQTT bridge use the new COVESA paths (no references to old `Vehicle.Battery.SoC` or `Vehicle.Cabin.Temperature`)

**M1 + M2 regression criteria (must still pass):**
- [ ] Streamlit dashboard at `:8501` shows all 3 signals updating at ≥ 1 Hz
- [ ] `mosquitto_sub -t "sdv/vehicle-001/#"` receives JSON payloads at ≥ 1 Hz
- [ ] `docker compose down` stops all services cleanly

---

## 9. Functional Dependency Map

```
FR-130 (ros2-bridge startup order)
  └── depends on → FR-25 (Databroker health on :55555) [from M1]

FR-131 (ros2-subscriber startup)
  └── depends on → FR-110 (ros2-bridge started)

FR-110–119 (bridge signal forwarding)
  └── depends on → FR-102 (ECU sim using new COVESA paths)
  └── depends on → FR-100 (VSS catalog updated to COVESA paths)

FR-103, FR-104 (dashboard + mqtt-bridge path updates)
  └── depends on → FR-100 (VSS catalog updated to COVESA paths)
  └── required for → FR-133 (M1/M2 regression pass)
```
