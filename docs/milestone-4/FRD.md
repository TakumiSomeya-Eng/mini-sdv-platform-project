# Functional Requirements Document (FRD)
## Milestone 4: Virtual CAN Bus (SocketCAN)
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | FRD |
| Milestone | 4 — Virtual CAN Bus (SocketCAN) |
| Status | Draft |
| Hypothesis Layers | Domain (L3) · Interaction (L4) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | [PRD.md](PRD.md) · Milestone 3 FRD |
| Next Layer | [TRD.md](TRD.md) |

---

## 1. System Context

```
【M4 Full Architecture】

  WSL2 Host (Linux kernel)
  ┌────────────────────────────────────────────────────────────────────┐
  │  vcan0  (virtual CAN bus — SocketCAN kernel interface)             │
  │    ↑ CAN frames          ↓ CAN frames                              │
  │    │                     │                                         │
  │  ┌─────────────────┐   ┌──────────────────┐                       │
  │  │  ecu-simulator  │   │   can-gateway    │ ──gRPC──▶ Databroker  │
  │  │  (host network) │   │  (host network)  │           (sdv-net)   │
  │  └─────────────────┘   └──────────────────┘                       │
  │                                                                    │
  │  Docker Compose: sdv-net                                           │
  │  ┌────────────┐  ┌──────────┐  ┌────────────┐  ┌───────────────┐ │
  │  │ databroker │  │dashboard │  │mqtt-bridge │  │  ros2-bridge  │ │
  │  │  :55555    │  │  :8501   │  │            │  │               │ │
  │  └────────────┘  └──────────┘  └────────────┘  └───────────────┘ │
  └────────────────────────────────────────────────────────────────────┘
```

**Architectural invariants carried from M1–M3:**
- Databroker remains the single source of truth for all vehicle signal state (DR-00)
- Dashboard, MQTT Bridge, ROS2 Bridge are read-only consumers — no change required (DR-10)
- All services start with `docker compose up` (after host vcan0 setup)

**New M4 invariant (DR-40):**
- The ECU Simulator MUST NOT write directly to the Databroker via gRPC in M4.
  All signal data exits the ECU via CAN frames on vcan0 only.

---

## 2. User Stories

| ID | As a… | I want to… | So that… | Acceptance Criterion |
|---|---|---|---|---|
| US-40 | SDV engineer | run `candump vcan0` and see CAN frames in real time | I can observe raw ECU communication as on a real vehicle bus | 3 distinct CAN IDs appear at ~1 Hz each |
| US-41 | SDV engineer | see the CAN Gateway log translating CAN ID to VSS path | I understand the Gateway ECU translation pattern | `can-gateway` logs show `CAN 0x100 → Vehicle.Speed = X km/h` |
| US-42 | SDV engineer | open the dashboard and see the same signals as M1–M3 | I confirm the CAN→Databroker→Dashboard chain works end-to-end | Dashboard shows Speed, SoC, Temperature with live values |
| US-43 | SDV engineer | understand the CAN frame structure from the logs | I can map a raw byte sequence to a physical signal value | Gateway logs show hex bytes alongside decoded float values |

---

## 3. Domain Rules (L3)

### DR-40: ECU publishes CAN frames only
The ECU Simulator MUST use python-can with `socketcan` backend to publish to `vcan0`.
It MUST NOT import or call any Kuksa gRPC client.

### DR-41: CAN ID to VSS path mapping (immutable in M4)

| CAN ID | VSS Path (COVESA 4.x) | Unit | Byte encoding |
|---|---|---|---|
| `0x100` | `Vehicle.Speed` | km/h | `struct.pack('<f', value)` — float32 little-endian |
| `0x200` | `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | percent | `struct.pack('<f', value)` |
| `0x300` | `Vehicle.Cabin.HVAC.AmbientAirTemperature` | celsius | `struct.pack('<f', value)` |

### DR-42: CAN frame format
- Standard frame (not extended): `is_extended_id=False`
- DLC: 4 bytes (float32)
- Arbitration ID: 11-bit (valid range 0x000–0x7FF)

### DR-43: Gateway is the only Databroker writer in M4
The CAN Gateway MUST be the sole service that calls `set_current_values()` on the Databroker.
This preserves the single-writer invariant and mirrors the Gateway ECU role in real vehicles.

### DR-44: vcan0 must exist before container start
Both `ecu-simulator` and `can-gateway` depend on `/dev/vcan0` (or the vcan0 socket).
If vcan0 does not exist, both services MUST fail with a clear error message and retry with exponential back-off.

### DR-45: Signal update rate preserved
The ECU Simulator MUST maintain the 1 Hz update rate per signal (UPDATE_INTERVAL_SEC=1.0),
matching M1–M3 behaviour so downstream consumers (Dashboard, MQTT) are unaffected.

---

## 4. Functional Requirements (L4)

### FR-40: ECU Simulator — CAN publisher

| ID | Requirement |
|---|---|
| FR-40-1 | Opens `vcan0` socket using python-can `socketcan` backend on startup |
| FR-40-2 | Publishes CAN ID 0x100 (Speed) at UPDATE_INTERVAL_SEC interval |
| FR-40-3 | Publishes CAN ID 0x200 (SoC) at UPDATE_INTERVAL_SEC interval |
| FR-40-4 | Publishes CAN ID 0x300 (Cabin Temp) at UPDATE_INTERVAL_SEC interval |
| FR-40-5 | Encodes each value as float32 little-endian (4 bytes) |
| FR-40-6 | Implements exponential back-off retry if vcan0 not available (2 s → 30 s cap) |
| FR-40-7 | Logs each transmitted frame: `TX CAN 0x100 [4] xx xx xx xx  → 87.3 km/h` |

### FR-41: CAN Gateway — bridge service

| ID | Requirement |
|---|---|
| FR-41-1 | Opens `vcan0` socket for reading (python-can socketcan, recv_own_msgs=False) |
| FR-41-2 | Connects to Kuksa Databroker via gRPC (VSSClient) |
| FR-41-3 | On each received CAN frame, decodes float32 LE from data bytes |
| FR-41-4 | Maps CAN ID to VSS path using DR-41 table |
| FR-41-5 | Calls `set_current_values()` for the mapped VSS path |
| FR-41-6 | Ignores frames with unknown CAN IDs (logs warning, does not crash) |
| FR-41-7 | Implements exponential back-off retry for both vcan0 and gRPC connections |
| FR-41-8 | Logs each translated frame: `RX CAN 0x100 → Vehicle.Speed = 87.3 km/h` |

### FR-42: Docker Compose — host network for CAN services

| ID | Requirement |
|---|---|
| FR-42-1 | `ecu-simulator` uses `network_mode: host` to access vcan0 |
| FR-42-2 | `can-gateway` uses `network_mode: host` to access vcan0 |
| FR-42-3 | `databroker` exposes port 55555 to host (for can-gateway gRPC) |
| FR-42-4 | `ecu-simulator` env: `CAN_INTERFACE=vcan0`, `DATABROKER_HOST` removed |
| FR-42-5 | `can-gateway` env: `CAN_INTERFACE=vcan0`, `DATABROKER_HOST=localhost` |

### FR-43: Host setup script

| ID | Requirement |
|---|---|
| FR-43-1 | `scripts/setup-vcan.sh` automates vcan0 initialisation |
| FR-43-2 | Script is idempotent — safe to run multiple times |
| FR-43-3 | Script prints confirmation: `vcan0 is up` |

---

## 5. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-40 | CAN frame publish latency < 10 ms (vcan0 is in-kernel, negligible overhead) |
| NFR-41 | Gateway translation adds < 5 ms additional latency over direct gRPC |
| NFR-42 | No change to Dashboard, MQTT Bridge, or ROS2 Bridge container images |
| NFR-43 | `candump vcan0` output readable without additional tools — frames visible at 1 Hz |

---

## 6. Out of Scope

- Multi-bus topology (vcan1, vcan2)
- CAN FD support
- DBC file-based signal decoding
- Physical CAN hardware adapters
