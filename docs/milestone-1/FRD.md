# Functional Requirements Document (FRD)
## Milestone 1: Live Vehicle Signal Dashboard
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | FRD |
| Milestone | 1 — Live Vehicle Signal Dashboard |
| Status | Draft |
| Hypothesis Layers | Domain (L3) · Interaction (L4) |
| Created | 2026-05-23 |
| Version | 1.0 |
| Depends On | [PRD.md](PRD.md) |
| Next Layer | [TRD.md](TRD.md) |

---

## 1. System Context

```
[User: Browser]
      │
      │ HTTP :8501
      ▼
[Dashboard Service]  ──── gRPC :55555 ────▶  [Kuksa Databroker]
                                                      ▲
                                         gRPC :55555  │
                                                      │
                                           [ECU Simulator]
                                                      │
                                              loads at startup
                                                      │
                                      [VSS Catalog: vss_mini_covesa.json]
```

**Core architectural invariant (Domain Rule DR-00):**
The Databroker is the **single source of truth** for all vehicle signal state.
No service communicates vehicle data directly with another service — all reads and writes flow through the Databroker.

---

## 2. User Stories

| ID | As a… | I want to… | So that… | Acceptance Criterion |
|---|---|---|---|---|
| US-01 | learner | start the entire platform with one command | I can see a working SDV signal chain without complex setup | `docker compose up` starts all services within 60 seconds with no manual steps |
| US-02 | learner | see live vehicle signals on a dashboard | I can understand how a centralized vehicle middleware distributes data | Dashboard shows Speed, SoC, and Temperature updating at ≥ 1 Hz |
| US-03 | learner | read code comments that explain SDV concepts | I can map the simulation to real automotive systems | Every non-trivial function has a comment explaining its real-world SDV equivalent |
| US-04 | learner | see service logs in real time | I can understand what each service is doing without a debugger | All services emit structured timestamped logs; ECU signal values appear per cycle |
| US-05 | learner | see the system recover from a service restart | I can experiment without permanently breaking the stack | ECU sim reconnects to Databroker within 30 seconds after Databroker restart |

---

## 3. Domain Model (Hypothesis L3)

### 3.1 Domain Entities

```
VSSCatalog
  - defines valid signal paths, datatypes, units, and min/max constraints
  - loaded at Databroker startup; immutable at runtime

VehicleSignal
  - identified by a unique VSS path (dot-notation, e.g. "Vehicle.Speed")
  - has: datatype (float), unit (km/h / percent / celsius), min, max

Datapoint
  - the current measured value of a VehicleSignal at a point in time
  - has: value (typed), timestamp (server-side, set by Databroker on write)

DataBroker
  - stores the most recent Datapoint for each registered VehicleSignal
  - provides gRPC API for read (GetCurrentValues) and write (SetCurrentValues)

ECUSimulator
  - produces Datapoints for 3 signals at a fixed interval
  - simulates 3 distinct ECU types (Powertrain, BMS, HVAC)

Dashboard
  - read-only consumer of Datapoints
  - maintains a rolling 60-sample history per signal for chart rendering
```

### 3.2 Entity Relationships

```
VSSCatalog ──registers──▶ VehicleSignal (1 catalog : N signals)
ECUSimulator ──publishes──▶ Datapoint ──stored in──▶ DataBroker
Dashboard ──reads──▶ Datapoint ──retrieved from──▶ DataBroker
DataBroker ──validates against──▶ VSSCatalog (signal must exist before write)
```

### 3.3 Business Rules (Domain Rules)

| ID | Rule |
|---|---|
| DR-00 | The Databroker is the single source of truth. No direct ECU↔Dashboard communication. |
| DR-01 | A signal MUST be registered in the VSS catalog before it can be published or read. |
| DR-02 | Signal values MUST stay within the min/max bounds defined in the VSS catalog. Violation is enforced at the application layer (simulator clamps values). |
| DR-03 | The Dashboard is **read-only** in M1. It MUST NOT write any signal values to the Databroker. |
| DR-04 | The ECU Simulator MUST reconnect automatically on Databroker failure. It MUST NOT exit on transient connection errors. |
| DR-05 | The Databroker timestamp (not the simulator clock) is the authoritative signal timestamp. |

---

## 4. Functional Requirements

### 4.1 VSS Signal Catalog

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | MUST | The system MUST define exactly 3 vehicle signals in a VSS-compliant catalog file. |
| FR-02 | MUST | Each signal MUST define: path, datatype, type, description, unit, min, max. |
| FR-03 | MUST | The catalog file MUST conform to the COVESA hierarchical JSON format required by Kuksa Databroker's `--vss` flag. |
| FR-04 | MUST | Signal paths MUST be: `Vehicle.Speed`, `Vehicle.Battery.SoC`, `Vehicle.Cabin.Temperature`. |
| FR-05 | SHOULD | The original flat-notation `vss_mini.json` SHOULD be retained as human-readable companion documentation. |

### 4.2 ECU Simulator Service

| ID | Priority | Requirement |
|---|---|---|
| FR-10 | MUST | The ECU simulator MUST publish all 3 signals to the Databroker in a single gRPC `SetCurrentValues` request per update cycle. |
| FR-11 | MUST | The update interval MUST be configurable via the `UPDATE_INTERVAL_SEC` environment variable (default: 1.0 second). |
| FR-12 | MUST | Published signal values MUST stay within the VSS-defined min/max bounds (application-level clamping). |
| FR-13 | MUST | The simulator MUST log each published datapoint with: signal path, value, unit, and cycle timestamp. |
| FR-14 | MUST | On Databroker connection failure, the simulator MUST retry with exponential back-off: initial delay 2s, doubling each retry, capped at 30s. |
| FR-15 | MUST | The Databroker endpoint MUST be configurable via `DATABROKER_HOST` and `DATABROKER_PORT` environment variables. |
| FR-16 | MUST | `Vehicle.Speed` MUST use a sinusoidal pattern with Gaussian noise to produce realistic speed variation (range: ~10–120 km/h). |
| FR-17 | MUST | `Vehicle.Battery.SoC` MUST use a slow linear drain with periodic reset to simulate a discharge→charge cycle (range: 55–85 %). |
| FR-18 | MUST | `Vehicle.Cabin.Temperature` MUST use a sinusoidal HVAC cycling pattern (range: 19.5–24.5 °C). |
| FR-19 | SHOULD | The simulator SHOULD log a startup banner listing all configured signal paths and the Databroker endpoint. |

### 4.3 Kuksa Databroker Service

| ID | Priority | Requirement |
|---|---|---|
| FR-20 | MUST | The Databroker MUST load the VSS catalog from the mounted config file at startup. |
| FR-21 | MUST | The Databroker MUST accept gRPC connections on port 55555. |
| FR-22 | MUST | The Databroker MUST operate in insecure mode (no TLS) for M1. |
| FR-23 | MUST | The Databroker MUST reject `SetCurrentValues` requests for paths not registered in the VSS catalog. |
| FR-24 | MUST | The Databroker MUST store the most recent Datapoint (value + server timestamp) for each registered signal. |
| FR-25 | SHOULD | The Databroker SHOULD be reachable for health checking via TCP probe on port 55555. |

### 4.4 Dashboard Service

| ID | Priority | Requirement |
|---|---|---|
| FR-30 | MUST | The dashboard MUST display current values of all 3 signals as metric cards. |
| FR-31 | MUST | Each metric card MUST show: signal name, current value with unit, and delta from the previous reading. |
| FR-32 | MUST | The dashboard MUST display a rolling 60-second line chart for each signal. |
| FR-33 | MUST | The dashboard MUST refresh all signal values automatically every 1 second without user interaction. |
| FR-34 | MUST | The dashboard MUST display a Databroker connection status indicator (connected / disconnected / error). |
| FR-35 | MUST | The dashboard MUST be accessible at `http://localhost:8501` from the host machine. |
| FR-36 | MUST | The Databroker endpoint MUST be configurable via `DATABROKER_HOST` and `DATABROKER_PORT` environment variables. |
| FR-37 | MUST | The dashboard MUST display a graceful error state (not crash) when the Databroker is unreachable. |
| FR-38 | MUST | The dashboard MUST handle the case where no datapoints have been published yet (null/empty response). |
| FR-39 | SHOULD | Each signal section SHOULD include a brief tooltip or caption explaining what the signal represents in a real vehicle. |

### 4.5 System-Level Behaviors

| ID | Priority | Requirement |
|---|---|---|
| FR-40 | MUST | The ECU simulator MUST NOT begin signal publication until the Databroker passes its health check (`depends_on: condition: service_healthy`). |
| FR-41 | MUST | The dashboard MUST NOT crash if no signals have been published yet (handles null/empty Datapoint gracefully). |
| FR-42 | MUST | All services MUST be startable with `docker compose up` from the project root directory. |
| FR-43 | MUST | All services MUST be stoppable and removable with `docker compose down`. |
| FR-44 | SHOULD | All services SHOULD define a `restart: on-failure` policy to survive transient startup errors. |

---

## 5. Signal Definitions

| Signal Path | Datatype | Unit | Min | Max | Simulated Range | Simulated ECU |
|---|---|---|---|---|---|---|
| `Vehicle.Speed` | float | km/h | 0 | 250 | ~10–120 | Powertrain ECU |
| `Vehicle.Battery.SoC` | float | percent | 0 | 100 | 55–85 | Battery Management System (BMS) |
| `Vehicle.Cabin.Temperature` | float | celsius | -40 | 100 | 19.5–24.5 | HVAC Controller |

### 5.1 Real-SDV Mapping

In a production SDV system, each of these signals originates from a dedicated ECU communicating over CAN bus or Automotive Ethernet:

| Simulated ECU | Real-World Equivalent | Real Signal Origin |
|---|---|---|
| Powertrain ECU | Engine / Motor Control Unit | Wheel speed sensors → ABS ECU → CAN → Gateway |
| Battery Management System | High-Voltage Battery Controller | Cell voltage monitors → BMS ECU → CAN → Gateway |
| HVAC Controller | Climate Control Unit | Cabin NTC thermistor → HVAC ECU → LIN/CAN → Gateway |

In M1, the simulator replaces the physical bus and gateway ECU. Milestone 4 (SocketCAN) will add the virtual CAN layer to simulate the bus-level communication.

---

## 6. Interaction Design (Hypothesis L4)

### 6.1 Dashboard Layout

```
┌──────────────────────────────────────────────────────────┐
│  mini-SDV Platform · Milestone 1          ● Connected    │
├──────────────────┬───────────────────┬───────────────────┤
│  Vehicle.Speed   │ Vehicle.Battery   │ Vehicle.Cabin     │
│                  │        .SoC       │    .Temperature   │
│   87.3  km/h     │    72.40  %       │    22.1  °C       │
│   ▲ +2.1         │    ▼ -0.05        │    → 0.0          │
│  [Powertrain ECU]│  [Battery Mgmt]   │  [HVAC Controller]│
├──────────────────┴───────────────────┴───────────────────┤
│  Speed — last 60 seconds                                 │
│  ╭────────────────────────────────────────────────────╮  │
│  │   [line chart: 0–120 km/h rolling 60s]             │  │
│  ╰────────────────────────────────────────────────────╯  │
│  Battery SoC — last 60 seconds                           │
│  ╭────────────────────────────────────────────────────╮  │
│  │   [line chart: 0–100% rolling 60s]                 │  │
│  ╰────────────────────────────────────────────────────╯  │
│  Cabin Temperature — last 60 seconds                     │
│  ╭────────────────────────────────────────────────────╮  │
│  │   [line chart: 15–30°C rolling 60s]                │  │
│  ╰────────────────────────────────────────────────────╯  │
└──────────────────────────────────────────────────────────┘
```

### 6.2 Interaction Flow

```
Step 1 — Connection
  Dashboard starts → connects to Databroker via gRPC → shows "● Connected"
  On failure → shows "○ Disconnected" → retries next cycle

Step 2 — Poll Cycle (every 1 second)
  GetCurrentValues(["Vehicle.Speed", "Vehicle.Battery.SoC", "Vehicle.Cabin.Temperature"])
  → append to session_state history buffer (max 60 entries)
  → compute delta from previous reading
  → st.rerun() → re-render all UI elements

Step 3 — Error Handling
  If Databroker is unreachable → display error banner → continue polling next cycle
  If signal value is None → display "—" in metric card → skip chart update
```

### 6.3 State Model

```
st.session_state:
  history: {
    "Vehicle.Speed":             [87.3, 85.1, 89.2, ...],  # max 60 entries
    "Vehicle.Battery.SoC":       [72.40, 72.35, ...],
    "Vehicle.Cabin.Temperature": [22.1, 22.0, 22.2, ...]
  }
  connected: bool
  last_values: dict[str, float]  # for delta computation
```

---

## 7. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | End-to-end latency (ECU publish → dashboard display) MUST be ≤ 2 seconds |
| NFR-02 | Reliability | ECU simulator MUST auto-recover from Databroker restart without manual intervention |
| NFR-03 | Observability | All services MUST emit structured logs with ISO 8601 timestamps |
| NFR-04 | Portability | The entire stack MUST run on any machine with Docker Engine and Docker Compose v2 installed |
| NFR-05 | Educational | Code MUST include inline comments explaining the SDV concept behind each significant implementation choice |
| NFR-06 | Simplicity | No external databases, message queues, or Kubernetes are permitted in M1 |

---

## 8. Acceptance Criteria Summary

A Milestone 1 build is considered **PASSING** when all of the following are true:

- [ ] `docker compose up` completes without errors
- [ ] All 3 services reach healthy/running state within 60 seconds
- [ ] Dashboard at `http://localhost:8501` renders all 3 signal metric cards
- [ ] All 3 signals update at ≥ 1 Hz (observable in the dashboard)
- [ ] Signal values fall within the specified simulation ranges
- [ ] ECU simulator logs show periodic signal publication with values
- [ ] `docker compose restart databroker` causes ECU sim to log reconnection and resume publishing within 30 seconds
- [ ] `docker compose down` stops all services cleanly

---

## 9. Functional Dependency Map

```
FR-40 (startup order)
  └── depends on → FR-25 (Databroker TCP health check)

FR-30–38 (dashboard display)
  └── depends on → FR-21 (Databroker gRPC on :55555)
  └── depends on → FR-10 (ECU sim publishing signals)

FR-10–18 (ECU sim signal generation)
  └── depends on → FR-20 (Databroker loaded with VSS catalog)
  └── depends on → FR-01–04 (VSS catalog correctly defined)

FR-20 (Databroker startup)
  └── depends on → FR-03 (VSS catalog in COVESA hierarchical format)
```
