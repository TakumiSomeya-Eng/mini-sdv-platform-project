# mini-sdv-platform

> An educational simulation of a modern **Software Defined Vehicle (SDV)** platform built entirely with open-source tools and Docker Compose.

This project teaches SDV architecture by making it runnable. Every component maps to a real pattern used in production automotive software organizations.

---

## What Is a Software Defined Vehicle?

A traditional vehicle has dozens of ECUs (Electronic Control Units) communicating peer-to-peer over CAN bus. Each ECU owns its data. Adding a new feature (e.g., cloud telemetry) requires wiring into each relevant ECU individually.

A **Software Defined Vehicle** flips this model:

```
Traditional:  ECU-A ←──CAN──→ ECU-B ←──CAN──→ ECU-C
                ↓                                  ↓
           (tightly coupled, hard to update or extend)

SDV:          ECU-A ─┐
              ECU-B ─┼──▶  Central Vehicle Middleware  ──▶  Any App
              ECU-C ─┘         (Databroker / VAL)
                ↓
           (decoupled — apps subscribe to signals, not ECUs)
```

All vehicle data flows through a central **Vehicle Abstraction Layer (VAL)**. Applications — the instrument cluster, a cloud backend, an AI safety agent — subscribe to named signals without knowing which ECU produces them.

This project simulates that architecture using Eclipse Kuksa as the VAL.

---

## Current Architecture (Milestone 2)

```
┌──────────────────────────────────────────────────────────────────┐
│                    Docker Compose: sdv-net                        │
│                                                                  │
│  ┌─────────────────────┐   gRPC SetCurrentValues                 │
│  │   ecu-simulator     │──────────────────────────┐             │
│  │  Powertrain ECU     │                          │             │
│  │  Battery Mgmt Sys   │                          ▼             │
│  │  HVAC Controller    │            ┌─────────────────────────┐ │
│  └─────────────────────┘            │   kuksa-databroker      │ │
│           ↑ 1 s interval            │   :55555 (gRPC)         │ │
│    VehicleState physics sim         │                         │ │
│                                     │  • Vehicle.Speed        │ │
│                                     │  • Vehicle.Battery.SoC  │ │
│                                     │  • Vehicle.Cabin.Temp   │ │
│                                     └─────────────────────────┘ │
│                                        │              │          │
│                               gRPC Get │    gRPC      │ Subscribe│
│                               (poll)   │    (stream)  │          │
│                                        ▼              ▼          │
│                          ┌──────────────┐  ┌───────────────────┐ │
│                          │  dashboard   │  │   mqtt-bridge     │ │
│                          │  :8501       │  │                   │ │
│                          │  [M1 charts] │  │ MQTT publish      │ │
│                          └──────────────┘  └───────────────────┘ │
│                                                      │           │
│                                              MQTT :1883          │
│                                                      ▼           │
│                                           ┌────────────────────┐ │
│                                           │   mosquitto        │ │
│                                           │   :1883            │ │
│                                           └────────────────────┘ │
└───────────────────────────────────────────────────┬──────────────┘
         http://localhost:8501  (dashboard)          │ :1883 (MQTT)
                                                     ▼
                                        mosquitto_sub (host CLI)
```

---

## Quick Start

**Requirements:** Docker Engine + Docker Compose v2

```bash
# 1. Clone the repository
git clone <repo-url>
cd mini-sdv-platform

# 2. Start all services
docker compose up

# 3. Open the dashboard
# → http://localhost:8501
```

To run in the background:
```bash
docker compose up -d
docker compose logs -f          # tail all logs
docker compose logs -f dashboard # tail one service
```

To stop:
```bash
docker compose down
```

To rebuild after code changes:
```bash
docker compose build --no-cache
docker compose up
```

---

## Quick Test for Milestone 2 (MQTT Cloud Bridge)

After `docker compose up`, open a second terminal on your host machine:

```bash
# Subscribe to all vehicle signals in real time
mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/#" -v
```

Expected output (1 Hz per signal):
```
sdv/vehicle-001/Vehicle/Speed {"signal": "Vehicle.Speed", "value": 87.3, "unit": "km/h", "timestamp": "2026-05-23T14:30:01"}
sdv/vehicle-001/Vehicle/Battery/SoC {"signal": "Vehicle.Battery.SoC", "value": 72.4, "unit": "percent", "timestamp": "2026-05-23T14:30:01"}
sdv/vehicle-001/Vehicle/Cabin/Temperature {"signal": "Vehicle.Cabin.Temperature", "value": 22.1, "unit": "celsius", "timestamp": "2026-05-23T14:30:01"}
```

> **No `mosquitto_sub` installed?** On Ubuntu: `sudo apt install mosquitto-clients` · On macOS: `brew install mosquitto`

---

## Services

### 1. `databroker` — Eclipse Kuksa Databroker

**Image:** `ghcr.io/eclipse/kuksa.val/databroker:0.4.4`

The central vehicle middleware. It loads a **VSS (Vehicle Signal Specification)** catalog at startup and exposes a gRPC API for reading and writing typed, named vehicle signals.

**Real-world equivalent:** The Vehicle Abstraction Layer (VAL) running on the Central Vehicle Computer (CVC) in platforms like Volkswagen CARIAD E3 or Bosch SDV.

**Why VSS?**
VSS defines a standardized, hierarchical naming tree for all vehicle data, maintained by COVESA. Using a shared catalog means any service can discover and subscribe to signals without prior negotiation between teams — the same benefit as a well-designed API contract.

```
config/vss/vss_mini_covesa.json   ← loaded at startup by the Databroker
config/vss/vss_mini.json          ← human-readable companion reference
```

---

### 2. `ecu-simulator` — Python ECU Simulator

**Source:** `services/ecu-simulator/`

Simulates three Electronic Control Units publishing vehicle signals to the Databroker every second via gRPC.

| Simulated ECU | Signal | Simulation Model |
|---|---|---|
| Powertrain ECU | `Vehicle.Speed` | Sinusoidal 10–120 km/h + Gaussian noise |
| Battery Management System | `Vehicle.Battery.SoC` | Linear drain 85→55 %, periodic reset |
| HVAC Controller | `Vehicle.Cabin.Temperature` | Sinusoidal HVAC cycling 19.5–24.5 °C |

**Real-world equivalent:** Physical ECUs communicating over ISO 11898 CAN bus → Central Gateway ECU → gRPC to Databroker. In Milestone 4 (SocketCAN), the virtual CAN layer between the ECU simulator and the Databroker will be added.

**Resilience pattern:** The simulator implements an exponential back-off reconnect loop (2 s → 4 s → 8 s … capped at 30 s). If the Databroker restarts, the simulator reconnects automatically without container restart. This is a cloud-native service pattern, not a Docker restart policy dependency.

---

### 4. `mosquitto` — Eclipse Mosquitto MQTT Broker

**Image:** `eclipse-mosquitto:2.0`

The cloud-side message broker. Receives vehicle telemetry from the MQTT bridge and distributes it to any number of subscribers.

**Real-world equivalent:** AWS IoT Core, Azure IoT Hub, or HiveMQ Cloud — all expose an MQTT endpoint that vehicles publish to. Mosquitto is a local drop-in replacement. The `mqtt-bridge` service requires zero code changes to point at a real cloud broker; only `MQTT_HOST` and `MQTT_PORT` environment variables change.

| Port | Protocol | Access |
|---|---|---|
| 1883 | MQTT (plain text) | Host machine (for CLI testing) · All services on sdv-net |

---

### 5. `mqtt-bridge` — MQTT Bridge (V2C Gateway)

**Source:** `services/mqtt-bridge/`

The Vehicle-to-Cloud gateway. Subscribes to the Kuksa Databroker using **gRPC streaming** and publishes each signal update to Mosquitto as a JSON payload.

**Key SDV concept — subscribe vs. poll:**

| Pattern | Used by | When to use |
|---|---|---|
| `get_current_values()` (poll) | M1 Dashboard | Periodic UI refresh; Streamlit single-thread model |
| `subscribe_current_values()` (subscribe) | M2 Bridge | Forwarding; reacts to changes immediately, not on a timer |

The bridge uses subscribe because it is a **reactive forwarder** — it must transmit data the moment it changes, not on a fixed schedule.

**MQTT topic pattern:** `sdv/{vehicle_id}/{VSS_path_with_slashes}`

---

### 3. `dashboard` — Streamlit Dashboard

**Source:** `services/dashboard/`

A live browser dashboard that polls the Databroker every second and visualises the three vehicle signals.

**Real-world equivalent:** The instrument cluster HMI app, a fleet telematics cloud backend, or an AI safety monitor — all of which consume signals from the Databroker, never from ECUs directly.

| URL | `http://localhost:8501` |
|---|---|
| Poll interval | 1 second |
| History window | 60 seconds |
| Display | 3 metric cards + 3 rolling line charts |

---

## Vehicle Signals

| VSS Path | Unit | Range | Source ECU |
|---|---|---|---|
| `Vehicle.Speed` | km/h | 0 – 250 (simulated: 10–120) | Powertrain ECU |
| `Vehicle.Battery.SoC` | % | 0 – 100 (simulated: 55–85) | Battery Management System |
| `Vehicle.Cabin.Temperature` | °C | -40 – 100 (simulated: 19.5–24.5) | HVAC Controller |

> **Note on VSS paths:** `Vehicle.Battery.SoC` and `Vehicle.Cabin.Temperature` are simplified paths for M1 clarity. The standard COVESA VSS 4.x equivalents are `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` and `Vehicle.Cabin.HVAC.AmbientAirTemperature`. Migration to standard paths is planned for Milestone 3.

---

## How This Maps to Real SDV Systems

| This Project | Production SDV System |
|---|---|
| ECU Simulator (Python) | Physical ECU (NXP S32, Renesas R-Car) |
| Sinusoidal signal model | Real sensor data (wheel speed sensors, NTC thermistors) |
| Direct gRPC publish | CAN frame → Automotive Gateway ECU → gRPC |
| Kuksa Databroker | Central Vehicle Computer running AUTOSAR Adaptive / Android Automotive |
| Docker bridge network `sdv-net` | In-vehicle Ethernet (SOME/IP, DDS, or raw Ethernet) |
| Streamlit dashboard | HMI app / Cloud telematics backend / AI monitoring agent |
| VSS catalog file | VSS catalog managed by the COVESA consortium and OEM R&D teams |

---

## Experimenting

**Change signal update rate:**
```yaml
# docker-compose.yml → ecu-simulator → environment
UPDATE_INTERVAL_SEC: "0.5"    # 2 Hz — faster updates
UPDATE_INTERVAL_SEC: "5.0"    # 0.2 Hz — slower, easier to observe
```
Then: `docker compose up -d --force-recreate ecu-simulator`

**Restart the Databroker to test ECU resilience:**
```bash
docker compose restart databroker
# Watch the ECU simulator reconnect automatically in the logs:
docker compose logs -f ecu-simulator
```

**Add a new signal (conceptual steps):**
1. Add the signal to `config/vss/vss_mini_covesa.json`
2. Add simulation logic in `services/ecu-simulator/main.py`
3. Add the signal path to the `SIGNALS` dict in `services/dashboard/main.py`
4. `docker compose build && docker compose up`

---

## Project Structure

```
mini-sdv-platform/
├── docker-compose.yml                  ← orchestrates all services
├── README.md                           ← this file
│
├── config/
│   └── vss/
│       ├── vss_mini_covesa.json        ← VSS catalog (COVESA format, loaded by Databroker)
│       └── vss_mini.json              ← VSS catalog (flat format, human-readable reference)
│
├── services/
│   ├── ecu-simulator/
│   │   ├── Dockerfile
│   │   ├── main.py                     ← ECU simulation logic + gRPC publisher
│   │   └── requirements.txt
│   └── dashboard/
│       ├── Dockerfile
│       ├── main.py                     ← Streamlit dashboard
│       └── requirements.txt
│
└── docs/
    ├── templates/                      ← Hypothesis Hierarchy Model templates
    └── milestone-1/
        ├── PRD.md                      ← Product Requirements (Value + Behavior hypotheses)
        ├── FRD.md                      ← Functional Requirements (Domain + Interaction)
        └── TRD.md                      ← Technical Requirements (Implementation hypothesis)
```

---

## Milestone Roadmap

| Milestone | Goal | New Services | New Concepts |
|---|---|---|---|
| **M1** ✅ | Live vehicle signal dashboard | Kuksa Databroker, ECU Simulator, Dashboard | VSS, gRPC, centralized middleware |
| **M2** ✅ | Cloud connectivity | MQTT Broker, MQTT Bridge | MQTT, V2C telemetry, subscribe vs. poll |
| **M3** | ROS2 integration | ROS2 node | DDS, topic-based pub/sub, sensor fusion |
| **M4** | Virtual CAN bus | SocketCAN ECUs | ISO 11898, CAN frames, Gateway ECU pattern |
| **M5** | AI agent | LLM-based orchestrator | Intelligent actuation, anomaly detection |

---

## Key SDV Concepts Demonstrated in M1

**Vehicle Signal Specification (VSS)**
A COVESA-maintained hierarchical naming standard for all vehicle data. Using VSS means any tool or service that understands VSS can consume your signals without custom integration.

**Centralized Vehicle Middleware**
The Databroker acts as a pub/sub hub for the entire vehicle. ECUs write; apps read. This enables over-the-air (OTA) updates to individual services without touching the hardware layer.

**Service-Oriented Architecture in Vehicles**
Each service (ECU sim, Databroker, Dashboard) is an independent container. In a real SDV, these would be independent software components deployed on the Central Vehicle Computer, each with its own lifecycle.

**Exponential Back-off Reconnect**
The ECU simulator's reconnect loop mirrors how cloud-native vehicle services handle transient middleware restarts — without crashing and relying on the container orchestrator as the only recovery mechanism.

---

## License

MIT — built for learning. Fork it, break it, extend it.
