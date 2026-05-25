# mini-sdv-platform

> An educational simulation of a modern **Software Defined Vehicle (SDV)** platform built entirely with open-source tools, Docker Compose, and SocketCAN.

This project teaches SDV architecture by making it runnable. Every component maps to a real pattern used in production automotive software organizations.

---

## What Is a Software Defined Vehicle?

A traditional vehicle has dozens of ECUs (Electronic Control Units) communicating peer-to-peer over CAN bus. Each ECU owns its data. Adding a new feature (e.g., cloud telemetry) requires wiring into each relevant ECU individually.

A **Software Defined Vehicle** flips this model:

```
Traditional:  ECU-A ←──CAN──→ ECU-B ←──CAN──→ ECU-C
                ↓                                  ↓
           (tightly coupled, hard to update or extend)

SDV:          ECU-A ─CAN─┐
              ECU-B ─CAN─┼──▶  CAN Gateway  ──▶  Central Middleware  ──▶  Any App
              ECU-C ─CAN─┘      (new M4)           (Databroker / VAL)
                ↓
           (decoupled — apps subscribe to signals, not ECUs)
```

All vehicle data flows through a central **Vehicle Abstraction Layer (VAL)**. Applications — the instrument cluster, a cloud backend, an AI safety agent — subscribe to named signals without knowing which ECU produces them.

This project simulates that architecture using Eclipse Kuksa as the VAL and SocketCAN (Linux kernel) as the virtual CAN bus.

---

## Current Architecture (Milestone 4)

```
  WSL2 Ubuntu (custom 6.18 kernel — SocketCAN support)
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │  ┌─────────────────────┐    CAN frames (float32 LE)               │
  │  │   ecu-simulator     │──────────────────────────┐               │
  │  │  Powertrain ECU     │    vcan0 (SocketCAN)     │               │
  │  │  Battery Mgmt Sys   │    0x100 → Speed         │               │
  │  │  HVAC Controller    │    0x200 → Battery SoC   │               │
  │  └─────────────────────┘    0x300 → Cabin Temp    │               │
  │                                                   ▼               │
  │                             ┌─────────────────────────┐           │
  │                             │      can-gateway        │           │
  │                             │  CAN ID → VSS path      │           │
  │                             │  float32 → Datapoint    │           │
  │                             └────────────┬────────────┘           │
  │                                          │ gRPC SetCurrentValues  │
  │                                          ▼                        │
  │  ┌─────────────────────────────────────────────────────────────┐  │
  │  │                 Docker services (host networking)           │  │
  │  │                                                             │  │
  │  │  ┌─────────────────────────────────────────────────────┐   │  │
  │  │  │              kuksa-databroker  :55555               │   │  │
  │  │  │  • Vehicle.Speed                                    │   │  │
  │  │  │  • Vehicle.Powertrain.TractionBattery               │   │  │
  │  │  │    .StateOfCharge.Current                           │   │  │
  │  │  │  • Vehicle.Cabin.HVAC.AmbientAirTemperature         │   │  │
  │  │  └──────────────┬──────────────┬──────────────┐        │   │  │
  │  │                 │ gRPC (poll)  │ gRPC (stream)│        │   │  │
  │  │                 ▼              ▼              ▼         │   │  │
  │  │  ┌──────────┐  ┌────────────┐  ┌────────────┐         │   │  │
  │  │  │dashboard │  │mqtt-bridge │  │ ros2-bridge│         │   │  │
  │  │  │  :8501   │  │            │  │            │         │   │  │
  │  │  └──────────┘  └─────┬──────┘  └─────┬──────┘         │   │  │
  │  │                      │ MQTT           │ DDS (loopback) │   │  │
  │  │                      ▼               ▼                 │   │  │
  │  │               ┌──────────┐  ┌───────────────┐          │   │  │
  │  │               │mosquitto │  │ros2-subscriber│          │   │  │
  │  │               │  :1883   │  │ (verification)│          │   │  │
  │  │               └──────────┘  └───────────────┘          │   │  │
  │  └─────────────────────────────────────────────────────────┘  │  │
  └────────────────────────────────────────────────────────────────┘
              ↓ localhost (WSL2 auto-forwards to Windows)
    http://localhost:8501   (Dashboard)
    localhost:1883          (MQTT test)
```

**Signal flow summary:**
```
ECU Simulator → [CAN frame 0x100] → vcan0 → CAN Gateway → gRPC → Databroker
                                                                      ↓
Dashboard (poll) ◀─────────────────────────────────────────── Vehicle.Speed
MQTT Bridge (subscribe) → mosquitto :1883 → sdv/vehicle-001/Vehicle/Speed
ROS2 Bridge (subscribe) → DDS → /vehicle/speed → ros2-subscriber
```

---

## Prerequisites (M4)

- **Windows 11** with WSL2
- **WSL2 Ubuntu** (Ubuntu 24.04 or later)
- **Custom WSL2 kernel 6.18** with SocketCAN support (see [M4 setup guide](docs/milestone-4/TRD.md))
- **Python venv** in WSL2 home (`~/sdv-venv`) with `python-can` and `kuksa-client`

> M1–M3 only required Docker Desktop. M4 adds SocketCAN which requires a custom WSL2 Linux kernel and Docker Engine running directly inside WSL2 Ubuntu.

---

## Quick Start (M4)

All commands run in a **WSL2 Ubuntu terminal** unless noted.

```bash
# 0. Clone (run once, from WSL2)
git clone <repo-url>
cd mini-sdv-platform

# 1. Bootstrap WSL2 session (run once after every wsl --shutdown)
#    Loads CAN kernel modules, creates vcan0, starts Docker Engine
bash scripts/setup-wsl2.sh

# 2. Start all Docker services
docker compose up -d

# 3. Start the CAN Gateway (WSL2 terminal 1)
~/sdv-venv/bin/python services/can-gateway/main.py

# 4. Start the ECU Simulator (WSL2 terminal 2)
~/sdv-venv/bin/python services/ecu-simulator/main.py

# 5. Open the Dashboard (Windows browser)
#    http://localhost:8501

# Optional: Monitor raw CAN frames (WSL2 terminal 3)
candump vcan0
```

**Stop everything:**
```bash
# Kill CAN gateway and ECU simulator (Ctrl+C in their terminals), then:
docker compose down
```

---

## Quick Test: M4 (SocketCAN)

```bash
# Monitor raw CAN frames on vcan0
candump vcan0
```

Expected output (1 Hz):
```
vcan0  100   [4]  66 66 82 42    # Speed = 65.2 km/h (float32 LE)
vcan0  200   [4]  29 DC A9 42    # Battery SoC = 84.9 %
vcan0  300   [4]  CD CC B0 41    # Cabin Temp  = 22.1 °C
```

```bash
# Verify CAN → Databroker translation in the gateway
tail -f /tmp/gateway.log
```

Expected output:
```
RX CAN 0x100 [66 66 82 42] → Speed = 65.20 km/h
RX CAN 0x200 [29 DC A9 42] → Current = 84.93 percent
RX CAN 0x300 [CD CC B0 41] → AmbientAirTemperature = 22.10 celsius
```

---

## Quick Test: M3 (ROS2 Integration)

```bash
# Watch the ROS2 subscriber receiving DDS messages
docker compose logs -f ros2-subscriber
```

Expected output:
```
[/vehicle/speed] value=87.3
[/vehicle/battery/soc] value=72.4
[/vehicle/cabin/temperature] value=22.1
```

---

## Quick Test: M2 (MQTT Cloud Bridge)

```bash
# Subscribe to all vehicle signals in real time (WSL2 or Windows with mosquitto-clients)
mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/#" -v
```

Expected output (1 Hz per signal):
```
sdv/vehicle-001/Vehicle/Speed {"signal": "Vehicle.Speed", "value": 87.3, "unit": "km/h", ...}
sdv/vehicle-001/Vehicle/Powertrain/TractionBattery/StateOfCharge/Current {"value": 72.4, ...}
sdv/vehicle-001/Vehicle/Cabin/HVAC/AmbientAirTemperature {"value": 22.1, ...}
```

---

## Services

### `databroker` — Eclipse Kuksa Databroker

**Image:** `ghcr.io/eclipse-kuksa/kuksa-databroker:0.4.4`

The central vehicle middleware. Loads a VSS catalog at startup and exposes a gRPC API for reading and writing typed, named vehicle signals.

**Real-world equivalent:** The Vehicle Abstraction Layer (VAL) running on the Central Vehicle Computer (CVC) in platforms like VW CARIAD E3 or Bosch SDV.

---

### `ecu-simulator` — Virtual ECU (M4: SocketCAN)

**Source:** `services/ecu-simulator/` · **Runs in WSL2 directly (not Docker)**

Simulates three ECUs publishing vehicle signals over the virtual CAN bus (`vcan0`).

| Simulated ECU | CAN ID | Signal | Simulation Model |
|---|---|---|---|
| Powertrain ECU | `0x100` | `Vehicle.Speed` | Sinusoidal 10–120 km/h + Gaussian noise |
| Battery Management System | `0x200` | Battery SoC | Linear drain 85→55 %, periodic reset |
| HVAC Controller | `0x300` | Cabin Temperature | Sinusoidal HVAC cycling 19.5–24.5 °C |

**CAN frame format:** 4-byte little-endian float32 (`struct.pack('<f', value)`) — standard automotive signal encoding.

**M4 change from M1–M3:** Previously published directly to Databroker via gRPC. Now publishes CAN frames to `vcan0` (SocketCAN kernel interface), mirroring how real ECUs communicate.

---

### `can-gateway` — CAN Gateway ECU (M4: new)

**Source:** `services/can-gateway/` · **Runs in WSL2 directly (not Docker)**

Reads CAN frames from `vcan0`, decodes the float32 payload, and publishes to the Databroker via gRPC.

| CAN ID | VSS Path | Unit |
|---|---|---|
| `0x100` | `Vehicle.Speed` | km/h |
| `0x200` | `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | % |
| `0x300` | `Vehicle.Cabin.HVAC.AmbientAirTemperature` | celsius |

**Real-world equivalent:** The **Central Gateway ECU** present in every modern vehicle. Physical ECUs publish raw CAN frames; the gateway translates them into structured data for the Central Vehicle Computer. This translation layer is what makes the SDV abstraction possible.

---

### `dashboard` — Streamlit Dashboard

**Source:** `services/dashboard/`

Live browser dashboard polling the Databroker every second.

| URL | `http://localhost:8501` |
|---|---|
| Poll interval | 1 second |
| History window | 60 seconds |
| Display | 3 metric cards + 3 rolling line charts |

---

### `mosquitto` — Eclipse Mosquitto MQTT Broker

**Image:** `eclipse-mosquitto:2.0`

Local MQTT broker. Receives vehicle telemetry from the MQTT bridge. In production, replace with AWS IoT Core or Azure IoT Hub — the `mqtt-bridge` requires zero code changes.

---

### `mqtt-bridge` — MQTT Bridge (V2C Gateway)

**Source:** `services/mqtt-bridge/`

Subscribes to Databroker via gRPC streaming and forwards each signal update to Mosquitto as JSON. Demonstrates the Vehicle-to-Cloud (V2C) pattern.

---

### `ros2-bridge` + `ros2-subscriber` — ROS2 Integration

**Source:** `services/ros2-bridge/`, `services/ros2-subscriber/`

Bridges Kuksa signals to ROS2 DDS topics (`/vehicle/speed`, `/vehicle/battery/soc`, `/vehicle/cabin/temperature`). Demonstrates the coexistence of Kuksa/VSS (vehicle layer) and ROS2/DDS (autonomous driving layer).

---

## Vehicle Signals (COVESA VSS 4.x)

| VSS Path | CAN ID | Unit | Simulated Range |
|---|---|---|---|
| `Vehicle.Speed` | `0x100` | km/h | 10 – 120 |
| `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | `0x200` | % | 55 – 85 |
| `Vehicle.Cabin.HVAC.AmbientAirTemperature` | `0x300` | °C | 19.5 – 24.5 |

---

## How This Maps to Real SDV Systems

| This Project | Production SDV System |
|---|---|
| `vcan0` (SocketCAN kernel interface) | Physical CAN bus (ISO 11898, 500 kbps) |
| ECU Simulator (Python + python-can) | Physical ECU (NXP S32, Renesas R-Car) |
| CAN frame (float32 little-endian) | Real CAN signal encoding per DBC file |
| CAN Gateway (Python) | Central Gateway ECU (AUTOSAR BSW) |
| Kuksa Databroker | Central Vehicle Computer — VAL layer |
| VSS catalog | COVESA VSS maintained by OEM + Tier-1 consortium |
| Mosquitto | AWS IoT Core / Azure IoT Hub / HiveMQ Cloud |
| ROS2 Bridge + DDS | Autoware or Apollo autonomous driving middleware |
| Docker host networking in WSL2 | In-vehicle Ethernet (SOME/IP / Ethernet AVB) |

---

## Project Structure

```
mini-sdv-platform/
├── docker-compose.yml                  ← orchestrates 6 Docker services (host networking)
├── README.md
│
├── config/
│   ├── mosquitto/mosquitto.conf
│   └── vss/
│       ├── vss_mini_covesa.json        ← VSS catalog (COVESA format, loaded by Databroker)
│       └── vss_mini.json              ← flat format reference
│
├── scripts/
│   ├── setup-wsl2.sh                   ← M4: WSL2 session bootstrap (modules + vcan0 + dockerd)
│   └── setup-vcan.sh                   ← legacy: vcan0 setup only
│
├── services/
│   ├── ecu-simulator/                  ← M4: CAN TX via python-can (runs in WSL2)
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── requirements.txt            ← python-can==4.3.1
│   ├── can-gateway/                    ← M4: NEW — CAN RX → Databroker gRPC (runs in WSL2)
│   │   ├── main.py
│   │   └── requirements.txt            ← python-can==4.3.1 + kuksa-client==0.4.3
│   ├── dashboard/                      ← M1: Streamlit live dashboard
│   ├── mqtt-bridge/                    ← M2: Kuksa → MQTT forwarder
│   ├── ros2-bridge/                    ← M3: Kuksa → ROS2 DDS forwarder
│   └── ros2-subscriber/                ← M3: ROS2 verification subscriber
│
└── docs/
    ├── milestone-1/  PRD.md  FRD.md  TRD.md
    ├── milestone-2/  PRD.md  FRD.md  TRD.md
    ├── milestone-3/  PRD.md  FRD.md  TRD.md
    └── milestone-4/  PRD.md  FRD.md  TRD.md
```

---

## Milestone Roadmap

| Milestone | Goal | New Components | New Concepts |
|---|---|---|---|
| **M1** ✅ | Live vehicle signal dashboard | Databroker, ECU Simulator, Dashboard | VSS, gRPC, centralized middleware |
| **M2** ✅ | Cloud connectivity | MQTT Broker, MQTT Bridge | MQTT, V2C telemetry, subscribe vs. poll |
| **M3** ✅ | ROS2 integration | ROS2 Bridge, ROS2 Subscriber | DDS, brokerless pub/sub, COVESA VSS 4.x |
| **M4** ✅ | Virtual CAN bus | CAN Gateway, SocketCAN ECUs | ISO 11898, CAN frames, Gateway ECU pattern |
| **M5** | AI agent | LLM-based orchestrator | Intelligent actuation, anomaly detection |

---

## License

MIT — built for learning. Fork it, break it, extend it.
