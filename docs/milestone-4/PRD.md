# Product Requirements Document (PRD)
## Milestone 4: Virtual CAN Bus (SocketCAN)
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | PRD |
| Milestone | 4 — Virtual CAN Bus (SocketCAN) |
| Status | Draft |
| Hypothesis Layers | Value (L1) · Behavior (L2) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | Milestone 3 (stable, deployed) |
| Next Layer | [FRD.md](FRD.md) |

---

## 1. Overview

Milestone 4 inserts a **virtual CAN bus (vcan0)** between the ECU Simulator and the Kuksa Databroker. A new **CAN Gateway** service reads CAN frames from the virtual bus and translates them into VSS signals, mirroring the Central Gateway ECU pattern used in production vehicles.

M1–M3 demonstrated how signals move from ECU → Databroker → Cloud / ROS2. M4 closes the gap between simulation and reality by adding the **physical communication layer** that real ECUs use — the CAN bus.

---

## 2. Problem Statement

### 2.1 The Gap M4 Closes

In M1–M3, the ECU Simulator publishes signals directly to the Databroker via gRPC. This is architecturally clean but skips the vehicle's physical communication layer:

| Layer | M1–M3 | Real Vehicle |
|---|---|---|
| ECU output | gRPC to Databroker (direct) | CAN frames over ISO 11898 bus |
| Signal translation | None (ECU writes VSS directly) | Gateway ECU reads CAN, writes gRPC/SOME-IP |
| Bus topology | None | CAN bus (multi-ECU shared medium) |

Without M4, a learner has no model for:
- How ECUs actually communicate (CAN frame structure, arbitration IDs)
- What a Gateway ECU does (protocol translation, signal mapping)
- Why the Gateway pattern exists (decoupling physical bus from middleware API)

### 2.2 Why SocketCAN?

SocketCAN is the Linux kernel's native CAN interface:
- The same API used in real automotive Linux ECUs (e.g., Yocto-based IVI systems)
- Tooled by `can-utils` (`candump`, `cansend`, `canplayer`) — the standard diagnostic CLI
- `vcan` (virtual CAN) allows full SocketCAN semantics without physical hardware
- `python-can` with `socketcan` backend gives Python services direct access to the same kernel API

---

## 3. Target Users

Same as M1–M3: SDV / Automotive Software Engineer learning vehicle platform architecture.

**New learning goal for M4:**
> Understand CAN bus communication, frame encoding, and the Gateway ECU pattern that bridges physical bus protocols to higher-level vehicle middleware.

---

## 4. Value Hypothesis (L1)

**Hypothesis:**
> Adding a virtual CAN bus layer between the ECU Simulator and the Kuksa Databroker provides concrete, hands-on understanding of the CAN communication pattern that underlies all real-world ECU integration — value that cannot be achieved by gRPC-direct simulation alone.

**Evidence:**
- All production ECUs communicate over CAN (ISO 11898), not gRPC. The M1–M3 architecture, while valid for middleware learning, omits this foundational layer.
- `candump vcan0` gives a direct, real-time view of the "wire" — a debugging experience identical to using a CAN analyser on a real vehicle bus.
- The Gateway ECU pattern (translate CAN → middleware API) is explicitly defined in AUTOSAR Classic and Adaptive platform architectures.

**Acceptance Criteria:**
- AC-1: ECU Simulator publishes vehicle signals as CAN frames to vcan0 — no gRPC
- AC-2: CAN Gateway reads vcan0 frames and publishes VSS signals to Databroker via gRPC
- AC-3: Dashboard, MQTT Bridge, and ROS2 Bridge continue to work without modification
- AC-4: A learner can run `candump vcan0` and observe raw CAN frames in real time

---

## 5. Behavior Hypothesis (L2)

### 5.1 Architecture Change

```
【M1–M3】
ECU Simulator ──────────────gRPC──────────────▶ Kuksa Databroker

【M4】
ECU Simulator ──CAN frames──▶ vcan0 ──CAN frames──▶ CAN Gateway ──gRPC──▶ Kuksa Databroker
```

All downstream consumers (Dashboard, MQTT Bridge, ROS2 Bridge) are unchanged — they still read from the Databroker. Only the path from ECU to Databroker changes.

### 5.2 CAN Frame Flow

```
Powertrain ECU sim  → CAN ID 0x100 → [4 bytes: speed as float32-LE]
Battery Mgmt sim    → CAN ID 0x200 → [4 bytes: SoC as float32-LE]
HVAC Controller sim → CAN ID 0x300 → [4 bytes: cabin temp as float32-LE]
                              │
                           vcan0  (Linux kernel virtual CAN bus)
                              │
                        CAN Gateway
                              │
                     VSS signal mapping:
                       0x100 → Vehicle.Speed
                       0x200 → Vehicle.Powertrain.TractionBattery.StateOfCharge.Current
                       0x300 → Vehicle.Cabin.HVAC.AmbientAirTemperature
                              │
                           gRPC SetCurrentValues
                              │
                        Kuksa Databroker
```

### 5.3 New Concepts Demonstrated

| Concept | Description |
|---|---|
| CAN Frame | Arbitration ID (11-bit) + DLC + up to 8 data bytes |
| ISO 11898 | Standard governing the physical and data link layers of CAN |
| SocketCAN | Linux kernel CAN subsystem — same API as real automotive Linux ECUs |
| CAN arbitration ID | Acts as signal identity on the bus (not an address — all nodes see all frames) |
| Gateway ECU pattern | Dedicated ECU that translates between bus protocols and higher-level middleware |
| `candump` | Real-time CAN bus monitor — equivalent to Wireshark for CAN |

### 5.4 User Interaction

| Action | Observable Result |
|---|---|
| `docker compose up` | All 8 services start; ECU sim sends CAN frames; gateway translates |
| `candump vcan0` (host WSL2) | Live CAN frames: `vcan0 100 [4] xx xx xx xx` at 1 Hz per signal |
| `http://localhost:8501` | Dashboard unchanged — signals still flow via Databroker |
| `mosquitto_sub -t 'sdv/#'` | MQTT stream unchanged |
| `docker compose logs -f can-gateway` | Gateway logs: `CAN 0x100 → Vehicle.Speed = 87.3 km/h` |

### 5.5 Prerequisite (Host Setup)

vcan0 must exist on the WSL2 host before `docker compose up`:

```bash
sudo modprobe vcan can can_raw
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

This is a one-time host setup (survives WSL2 session; requires re-run after WSL2 restart).

---

## 6. Out of Scope for M4

- Multi-node CAN topology (multiple virtual buses)
- CAN error frame handling
- CAN FD (Flexible Data Rate)
- TLS / authentication on gRPC (inherited insecure mode from M1)
- Physical CAN hardware (Peak, Kvaser) — vcan only
