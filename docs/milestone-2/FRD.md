# Functional Requirements Document (FRD)
## Milestone 2: MQTT Cloud Bridge
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | FRD |
| Milestone | 2 — MQTT Cloud Bridge |
| Status | Draft |
| Hypothesis Layers | Domain (L3) · Interaction (L4) |
| Created | 2026-05-23 |
| Version | 1.0 |
| Depends On | [PRD.md](PRD.md) · Milestone 1 FRD |
| Next Layer | [TRD.md](TRD.md) |

---

## 1. System Context

```
[ECU Simulator] ──gRPC──▶ [Kuksa Databroker] ──gRPC──▶ [Dashboard]  (M1, unchanged)
                                   │
                         gRPC subscribe_current_values   (M2 NEW)
                                   │
                                   ▼
                           [MQTT Bridge]  ──MQTT publish──▶  [Mosquitto]
                                                                  │
                                                            MQTT subscribe
                                                                  │
                                                                  ▼
                                                     [Cloud Subscriber / CLI]
```

**Architectural invariants carried from M1:**
- Databroker remains the single source of truth for all vehicle signal state (DR-00)
- Dashboard is read-only and unchanged
- All services communicate through well-defined interfaces (no direct ECU → Dashboard path)

**New M2 invariant (DR-10):**
- The MQTT Bridge is read-only from the Kuksa side — it MUST NOT write any signal values to the Databroker

---

## 2. User Stories

| ID | As a… | I want to… | So that… | Acceptance Criterion |
|---|---|---|---|---|
| US-10 | vehicle engineer | see the MQTT bridge forwarding signals in logs | I can verify V2C data flow end-to-end | `docker compose logs -f mqtt-bridge` shows each forwarded message with topic and value |
| US-11 | cloud engineer | subscribe to MQTT topics without knowing the vehicle internals | I can consume vehicle data using standard cloud tooling | `mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/#" -v` receives JSON payloads at ≥ 1 Hz |
| US-12 | learner | read code comments explaining gRPC subscribe vs. polling | I can understand when to use each pattern in a real SDV system | Bridge code has inline comments comparing both approaches |
| US-13 | learner | restart the Databroker and watch the bridge recover | I can understand service resilience in an SDV platform | Bridge reconnects and resumes forwarding within 30 seconds |
| US-14 | learner | still use the M1 dashboard unchanged | I can confirm M2 does not break existing functionality | Streamlit dashboard at :8501 continues to display live signals |

---

## 3. Domain Model (Hypothesis L3)

### 3.1 New Domain Entities

```
MQTTBroker (Mosquitto)
  - Receives MQTT messages on port 1883
  - Routes messages to all subscribers matching the topic pattern
  - Stateless routing (no persistence in M2)

CloudBridge (mqtt-bridge service)
  - Subscribes to Kuksa Databroker using gRPC streaming
  - Converts Datapoint → TelemetryPayload (JSON)
  - Publishes TelemetryPayload to MQTTBroker on the corresponding MQTTTopic
  - Read-only from the Kuksa side

MQTTTopic
  - Hierarchical address for a stream of MQTT messages
  - Naming: sdv/{vehicle_id}/{signal_path_with_slashes}
  - Example: sdv/vehicle-001/Vehicle/Speed

TelemetryPayload
  - JSON envelope wrapping a single signal observation
  - Fields: signal (str), value (float), unit (str), timestamp (ISO 8601 str)
```

### 3.2 Updated Domain Relationships

```
VSSCatalog ──registers──▶ VehicleSignal
ECUSimulator ──publishes──▶ Datapoint ──stored in──▶ DataBroker
Dashboard ──reads──▶ Datapoint ──from──▶ DataBroker          (M1)
CloudBridge ──subscribes──▶ Datapoint ──from──▶ DataBroker   (M2 NEW)
CloudBridge ──publishes──▶ TelemetryPayload ──to──▶ MQTTBroker
CloudBridge ──maps──▶ VehicleSignal.path → MQTTTopic
```

### 3.3 Business Rules

| ID | Rule |
|---|---|
| DR-00 | (carried) Databroker is the single source of truth. No ECU↔Bridge direct path. |
| DR-10 | CloudBridge MUST NOT write to the Databroker. It is a read-only consumer. |
| DR-11 | Each VehicleSignal maps to exactly one MQTTTopic. One-to-one mapping. |
| DR-12 | Every TelemetryPayload MUST include: `signal`, `value`, `unit`, `timestamp`. No partial payloads. |
| DR-13 | MQTTBroker operates without authentication in M2. Anonymous access is explicitly documented as a known limitation. |
| DR-14 | CloudBridge MUST reconnect to the Databroker on connection failure using exponential back-off. |
| DR-15 | CloudBridge MUST use gRPC `subscribe_current_values()` (streaming), not polling. A bridge forwards changes as they occur — not on a fixed schedule. |

**Rationale for DR-15:**
The dashboard polls because Streamlit reruns the entire script on each `st.rerun()`. A bridge service has no such constraint. Using subscribe means the bridge forwards each signal update immediately when the Databroker receives it from the ECU — no artificial 1-second delay.

---

## 4. Functional Requirements

### 4.1 Mosquitto Broker Service

| ID | Priority | Requirement |
|---|---|---|
| FR-50 | MUST | Mosquitto MUST accept MQTT connections on port 1883. |
| FR-51 | MUST | Mosquitto MUST allow anonymous publish and subscribe (no credentials required in M2). |
| FR-52 | MUST | Mosquitto MUST be configured via a mounted `mosquitto.conf` file. |
| FR-53 | MUST | Port 1883 MUST be exposed to the host machine to enable CLI testing with `mosquitto_sub`. |
| FR-54 | SHOULD | Mosquitto SHOULD log incoming connections and published messages at INFO level. |

### 4.2 MQTT Bridge Service

| ID | Priority | Requirement |
|---|---|---|
| FR-60 | MUST | The bridge MUST subscribe to all 3 VSS signal paths using `subscribe_current_values()` gRPC streaming. |
| FR-61 | MUST | The bridge MUST publish a TelemetryPayload JSON message to Mosquitto for every Datapoint update received from the Databroker. |
| FR-62 | MUST | The bridge MUST map each VSS path to an MQTT topic using the pattern `sdv/{VEHICLE_ID}/{path_with_dots_replaced_by_slashes}`. |
| FR-63 | MUST | The MQTT payload MUST be valid JSON containing: `signal` (str), `value` (float), `unit` (str), `timestamp` (ISO 8601 str). |
| FR-64 | MUST | The bridge MUST log each published message with topic, value, and unit. |
| FR-65 | MUST | The Databroker endpoint MUST be configurable via `DATABROKER_HOST` and `DATABROKER_PORT` environment variables. |
| FR-66 | MUST | The Mosquitto endpoint MUST be configurable via `MQTT_HOST` and `MQTT_PORT` environment variables. |
| FR-67 | MUST | The vehicle ID MUST be configurable via `VEHICLE_ID` environment variable (default: `vehicle-001`). |
| FR-68 | MUST | The bridge MUST implement exponential back-off reconnection on Databroker or MQTT broker failure. |
| FR-69 | MUST | The bridge MUST NOT start publishing until both the Databroker and Mosquitto are reachable. |
| FR-70 | SHOULD | The bridge SHOULD log a startup banner listing all subscribed signal paths, MQTT topic patterns, and endpoint addresses. |

### 4.3 Signal-to-Topic Mapping

| VSS Signal Path | MQTT Topic |
|---|---|
| `Vehicle.Speed` | `sdv/vehicle-001/Vehicle/Speed` |
| `Vehicle.Battery.SoC` | `sdv/vehicle-001/Vehicle/Battery/SoC` |
| `Vehicle.Cabin.Temperature` | `sdv/vehicle-001/Vehicle/Cabin/Temperature` |

Wildcard subscription for all signals: `sdv/vehicle-001/#`

### 4.4 Dashboard Update (Minimum Scope)

| ID | Priority | Requirement |
|---|---|---|
| FR-80 | SHOULD | The dashboard sidebar SHOULD display an MQTT Bridge status indicator (connected / disconnected). |
| FR-81 | MUST NOT | The dashboard MUST NOT directly connect to Mosquitto. Status is inferred from a shared environment variable or probe — not a live MQTT connection. |

### 4.5 System-Level Behaviors

| ID | Priority | Requirement |
|---|---|---|
| FR-90 | MUST | The MQTT Bridge MUST NOT start until both `databroker` and `mosquitto` pass their health checks (`depends_on: condition: service_healthy`). |
| FR-91 | MUST | All M1 services (databroker, ecu-simulator, dashboard) MUST remain functional and unchanged in behaviour. |
| FR-92 | MUST | All 5 services MUST start with `docker compose up` from the project root. |

---

## 5. TelemetryPayload Specification

### 5.1 Schema

```json
{
  "signal":    "Vehicle.Speed",
  "value":     87.3,
  "unit":      "km/h",
  "timestamp": "2026-05-23T14:30:01"
}
```

### 5.2 Field Rules

| Field | Type | Rule |
|---|---|---|
| `signal` | string | MUST be the full VSS dot-notation path (e.g., `Vehicle.Speed`) |
| `value` | number | MUST be a JSON number (float). MUST NOT be a string. |
| `unit` | string | MUST match the VSS catalog unit (km/h, percent, celsius) |
| `timestamp` | string | MUST be ISO 8601 format `YYYY-MM-DDTHH:MM:SS` (UTC, no timezone suffix for M2 simplicity) |

### 5.3 Real-World Context

This payload format mirrors the telemetry schemas used in production V2C platforms:
- **AWS IoT Core:** device → MQTT topic → Lambda / S3 / DynamoDB
- **Azure IoT Hub:** device → MQTT → Stream Analytics → Cosmos DB
- **COVESA VISS (Vehicle Information Service Specification):** defines similar JSON envelope for VSS data over WebSocket/REST

---

## 6. MQTT Topic Design Rationale

### 6.1 Topic Hierarchy

```
sdv / vehicle-001 / Vehicle / Speed
 │        │             │        │
 │        │             └── VSS path (dots → slashes)
 │        └── Vehicle identifier (configurable)
 └── Namespace prefix (all mini-sdv-platform topics)
```

### 6.2 Why Slashes Instead of Dots?

MQTT topic hierarchy uses `/` as the level separator. Using slashes for the VSS path portion aligns with MQTT conventions and enables hierarchical wildcard subscriptions:

```bash
sdv/vehicle-001/#               # All signals from vehicle-001
sdv/vehicle-001/Vehicle/Battery/#  # All Battery branch signals
sdv/+/Vehicle/Speed             # Speed from any vehicle
```

The `+` (single-level) and `#` (multi-level) wildcards only work with slash-delimited hierarchies.

---

## 7. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-10 | Latency | MQTT message MUST arrive at Mosquitto within 500 ms of the Databroker receiving the Datapoint from the ECU |
| NFR-11 | Reliability | MQTT bridge MUST auto-recover from Databroker or Mosquitto restart without manual intervention |
| NFR-12 | Observability | All services MUST emit structured logs with ISO 8601 timestamps |
| NFR-13 | Backward compatibility | All M1 acceptance criteria MUST continue to pass after M2 deployment |
| NFR-14 | Educational | gRPC subscribe vs. polling distinction MUST be explained in code comments |

---

## 8. Acceptance Criteria Summary

A Milestone 2 build is **PASSING** when all of the following are true:

**M2 new criteria:**
- [ ] `mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/#" -v` receives messages for all 3 signals at ≥ 1 Hz
- [ ] Each MQTT payload is valid JSON with `signal`, `value`, `unit`, `timestamp` fields
- [ ] `docker compose logs -f mqtt-bridge` shows each forwarded message
- [ ] `docker compose restart databroker` → bridge reconnects and resumes within 30 seconds

**M1 regression criteria (must still pass):**
- [ ] `docker compose up` starts all 5 services without errors
- [ ] Streamlit dashboard at `:8501` shows all 3 signals updating at ≥ 1 Hz
- [ ] `docker compose down` stops all services cleanly

---

## 9. Functional Dependency Map

```
FR-90 (bridge startup order)
  └── depends on → FR-50 (Mosquitto health on :1883)
  └── depends on → FR-25 (Databroker health on :55555) [from M1]

FR-60–70 (bridge signal forwarding)
  └── depends on → FR-10–18 (ECU sim publishing signals) [from M1]
  └── depends on → FR-50–53 (Mosquitto accepting connections)

FR-80 (dashboard MQTT badge)
  └── depends on → FR-50 (Mosquitto running)
  └── MUST NOT depend on → live MQTT connection from dashboard
```
