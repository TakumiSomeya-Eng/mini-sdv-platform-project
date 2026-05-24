# Product Requirements Document (PRD)
## Milestone 2: MQTT Cloud Bridge
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | PRD |
| Milestone | 2 — MQTT Cloud Bridge |
| Status | Draft |
| Hypothesis Layers | Value (L1) · Behavior (L2) |
| Created | 2026-05-23 |
| Version | 1.0 |
| Depends On | Milestone 1 (stable, deployed) |
| Next Layer | [FRD.md](FRD.md) |

---

## 1. Overview

Milestone 2 adds a **Vehicle-to-Cloud (V2C) connectivity layer** to the mini-sdv-platform. A new MQTT Bridge service subscribes to vehicle signals from the Kuksa Databroker and forwards them to a Mosquitto MQTT broker, simulating how real SDV platforms export telemetry to cloud backends.

M1 established the in-vehicle middleware. M2 establishes the **cloud exit point** — the boundary where vehicle data leaves the on-board system and becomes accessible to external services.

All M1 services remain unchanged and fully operational.

---

## 2. Problem Statement

### 2.1 The Gap M2 Closes

M1 demonstrated that vehicle signals flow from ECUs to a centralized Databroker. However, that data stays entirely within the vehicle's network boundary. Modern SDV platforms derive significant value from cloud connectivity:

| Cloud Use Case | Requires V2C |
|---|---|
| Fleet management (track 10,000 vehicles) | ✅ |
| Predictive maintenance (SoC degradation analysis) | ✅ |
| OTA trigger conditions (speed = 0, parked) | ✅ |
| AI model training from real driving data | ✅ |
| Remote diagnostics | ✅ |

Without M2, a learner understands the in-vehicle side but cannot see how that data reaches the cloud systems that consume it.

### 2.2 Why MQTT?

MQTT (Message Queuing Telemetry Transport) is the dominant protocol for IoT and V2C telemetry:

- **AWS IoT Core**, **Azure IoT Hub**, and **Google Cloud IoT** all use MQTT as their device-facing interface
- Designed for constrained networks (low bandwidth, high latency) — perfect for cellular vehicle connections
- Pub/sub model: the vehicle publishes; any number of cloud subscribers can consume independently
- Eclipse Mosquitto is the reference open-source implementation used across the embedded and automotive industries

---

## 3. Target Users

### Primary: SDV / Automotive Software Engineer (same as M1)

**New learning goal for M2:**
Understand how in-vehicle middleware (Kuksa) connects to cloud-side infrastructure (MQTT broker) and what the architectural boundary between them looks like.

### Secondary: Cloud / Backend Engineer

**Profile:**
- Knows MQTT, cloud IoT platforms, and message-driven architectures
- New to the vehicle side (does not know what ECUs or VSS are)
- Goal: understand what vehicle data looks like when it arrives at the cloud

M2 is the first milestone where a **cloud engineer can participate** without touching the vehicle-side code — they only need to subscribe to MQTT topics.

---

## 4. Value Proposition (Hypothesis L1)

> "Run `docker compose up`, then in a separate terminal run `mosquitto_sub -h localhost -p 1883 -t 'sdv/vehicle-001/#' -v` — and watch live vehicle telemetry arrive from the simulated car, formatted as the same JSON payload you'd receive from a real connected vehicle on AWS IoT Core."

**What makes this valuable:**

| Attribute | Description |
|---|---|
| Dual-role learning | Vehicle engineer sees the publish side; cloud engineer sees the subscribe side |
| Industry-realistic | MQTT + JSON is the actual pattern used in production V2C platforms |
| Protocol contrast | Demonstrates gRPC (internal, typed, low-latency) vs. MQTT (external, flexible, cloud-friendly) |
| Subscribe pattern | M2 bridge uses gRPC streaming subscribe — the correct pattern a real bridge uses |

---

## 5. Success Metrics

### 5.1 Primary Acceptance Criteria (Hypothesis L1 — Value)

| Criterion | Threshold | Verification |
|---|---|---|
| MQTT messages arrive for all 3 signals | ≥ 1 Hz per signal | `mosquitto_sub -t "sdv/vehicle-001/#"` on host |
| Payload is valid JSON with required fields | `signal`, `value`, `unit`, `timestamp` all present | Manual inspection or `jq` |
| M1 dashboard continues to work unchanged | All 3 signals display at ≥ 1 Hz | Browser observation |
| MQTT bridge reconnects on Databroker restart | Reconnects within 30 s | `docker compose restart databroker` |

### 5.2 Secondary Quality Criteria

| Criterion | Threshold |
|---|---|
| MQTT bridge logs each forwarded message | Visible in `docker compose logs -f mqtt-bridge` |
| Mosquitto receives messages from bridge only (no unauthenticated writes from host needed) | Functional |
| Code comments explain gRPC subscribe vs. poll distinction | Every key section documented |

---

## 6. User Journey (Hypothesis L2 — Behavior)

### Journey A: Vehicle Engineer

```
Step 1: docker compose up
         → All 5 services start (databroker, ecu-simulator, dashboard, mosquitto, mqtt-bridge)

Step 2: docker compose logs -f mqtt-bridge
         → See: "Published → sdv/vehicle-001/Vehicle/Speed = 87.3 km/h"
         → See: "Published → sdv/vehicle-001/Vehicle/Battery/SoC = 72.4 %"

Step 3: Read services/mqtt-bridge/main.py
         → Understand: gRPC subscribe (streaming) vs. polling
         → Understand: VSS path → MQTT topic conversion
         → Understand: JSON telemetry payload structure
```

### Journey B: Cloud Engineer

```
Step 1: (Assumes docker compose is running — vehicle side is a black box)

Step 2: mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/#" -v
         → Receives:
            sdv/vehicle-001/Vehicle/Speed
            {"signal": "Vehicle.Speed", "value": 87.3, "unit": "km/h", "timestamp": "..."}

Step 3: Understands:
         → Topic hierarchy encodes vehicle ID + signal path
         → Payload is self-describing (no schema lookup needed)
         → Any number of cloud subscribers can consume independently
```

**Desired Interaction Model:**
> Two engineers with different backgrounds (vehicle / cloud) can collaborate using MQTT as the shared interface contract — without either needing to understand the other's implementation.

---

## 7. Milestone Context

```
Milestone 1 ✅  ECU Simulator → Kuksa Databroker → Dashboard
                (gRPC / VSS / centralized in-vehicle middleware)

Milestone 2 ◀── We are here
                + MQTT Bridge → Mosquitto Broker
                (MQTT / V2C telemetry / cloud exit point)

Milestone 3 (future)
                + ROS2 Node → Kuksa Bridge
                (DDS / distributed middleware / sensor pub/sub)

Milestone 4 (future)
                + Virtual CAN ECUs → CAN Gateway → Kuksa
                (ISO 11898 / CAN frames / gateway ECU pattern)

Milestone 5 (future)
                + AI Agent monitoring Databroker
                (LLM / anomaly detection / intelligent actuation)
```

M2 completes the **core data pipeline**: ECU → Middleware → Cloud. Every subsequent milestone enriches either the vehicle side (M3, M4) or the intelligence layer (M5).

---

## 8. Out of Scope (Milestone 2)

| Item | Reason |
|---|---|
| TLS / MQTT over TLS (port 8883) | Adds certificate management complexity; deferred |
| MQTT authentication (username/password) | Educational simplicity; anonymous mode documented as known limitation |
| Cloud-side dashboard (MQTT subscriber UI) | CLI `mosquitto_sub` is sufficient for M2; visual cloud UI is M3+ |
| Actuation commands (cloud → vehicle write-back) | Dashboard and bridge are read-only in M2 |
| QoS 1 / QoS 2 delivery guarantees | QoS 0 used; QoS levels explained in code comments |
| MQTT retained messages | Out of scope; explained conceptually in TRD |
| Multiple vehicle IDs | Single `vehicle-001` for M2 simplicity |

---

## 9. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| Q1 | Should the dashboard show a live MQTT message feed (last N messages), or just a connection status badge? | Team | Minimum: status badge only (M2); live feed deferred to M3 |
| Q2 | Should MQTT topic use slash-converted VSS path (`Vehicle/Speed`) or dot-notation (`Vehicle.Speed`)? | Team | Slash convention adopted (MQTT hierarchy semantics) |
| Q3 | Should a `cloud-subscriber` Docker service be added, or is CLI sufficient? | Team | CLI sufficient for M2; avoids adding a service for passive consumption |
