# Product Requirements Document (PRD)
## Milestone 3: ROS2 Integration
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | PRD |
| Milestone | 3 — ROS2 Integration |
| Status | Draft |
| Hypothesis Layers | Value (L1) · Behavior (L2) |
| Created | 2026-05-24 |
| Version | 1.0 |
| Depends On | Milestone 2 (stable, deployed) |
| Next Layer | [FRD.md](FRD.md) |

---

## 1. Overview

Milestone 3 adds a **ROS2 node and a Kuksa–ROS2 bridge** to the mini-sdv-platform. Vehicle signals flowing through the Kuksa Databroker are re-published as ROS2 topics, making them consumable by any ROS2-native component such as a perception node, a path planner, or a safety monitor.

M1 established the in-vehicle middleware (gRPC / VSS). M2 added the cloud exit point (MQTT). M3 adds the **autonomous driving middleware layer** — the interface between the vehicle's signal backbone and the robotics/autonomy software stack that increasingly defines what an SDV actually *does*.

All M1 and M2 services remain unchanged and fully operational.

---

## 2. Problem Statement

### 2.1 The Gap M3 Closes

Modern SDV platforms are not a single middleware — they are a layered stack of two distinct paradigms that must coexist:

| Layer | Technology | Strength |
|---|---|---|
| Vehicle Signal Layer | Kuksa Databroker / VSS | Standardized naming, automotive-grade, ECU integration |
| Autonomous Driving Layer | ROS2 / DDS | Robotics ecosystem, perception/planning/control, sensor fusion |

After M2, a learner understands how vehicle data moves from ECUs to cloud. But they have no model for how that same data reaches an **autonomous driving stack** — a path planner that needs vehicle speed, or an emergency braking node that monitors battery state.

Real SDV platforms (Tier 1 suppliers, OEMs building ADAS/AD) run both simultaneously. Without M3, this critical architectural layer remains invisible.

### 2.2 Why ROS2?

ROS2 (Robot Operating System 2) is the dominant open-source framework for autonomous vehicle software:

- Used by Autoware (the leading open-source AD stack), Apollo (Baidu), and TierIV
- DDS (Data Distribution Service) as its transport layer is mandated by several AUTOSAR Adaptive profiles
- The Eclipse SDV ecosystem already maintains an official `kuksa-ros2-bridge` — making this a production-relevant integration, not an academic exercise
- A learner who understands this bridge has a concrete mental model of how OEMs structure their vehicle software stacks

---

## 3. Target Users

### Primary: SDV / Automotive Software Engineer (same as M1–M2)

**New learning goal for M3:**
Understand how VSS-based vehicle middleware (Kuksa) and robotics middleware (ROS2/DDS) coexist in the same vehicle platform, and how a bridge service connects the two worlds.

### Secondary: Robotics / Autonomous Driving Engineer

**Profile:**
- Strong background in ROS2 and robotics
- New to VSS, Kuksa, or automotive signal architectures
- Goal: understand where ROS2 fits in a full SDV platform

M3 is the first milestone where a **robotics engineer can enter the project from their own domain** and follow the integration outward into the vehicle signal layer.

---

## 4. Value Proposition (Hypothesis L1)

> "Run `docker compose up`, then in a separate terminal run `ros2 topic echo /vehicle/speed` — and watch the same vehicle speed signal that flows through Kuksa appear as a ROS2 topic, ready to be consumed by any autonomous driving node."

**What makes this valuable:**

| Attribute | Description |
|---|---|
| Dual-middleware model | Learner sees VSS/Kuksa and ROS2/DDS as complementary layers, not competitors |
| Industry-realistic | The Kuksa–ROS2 bridge pattern is used in production AD stacks built on Eclipse SDV |
| DDS contrast | Introduces DDS pub/sub semantics and compares them to gRPC (RPC) and MQTT (IoT) |
| True subscribe pattern | ROS2 nodes use event-driven subscription — reinforcing and extending the M2 Subscribe concept |
| Autonomous driving context | Grounds abstract SDV concepts in the ADAS/AD use case that motivates most SDV investment |

### 4.1 Success Metrics (Acceptance Criteria — Falsifiable)

| Criterion | Threshold | Verification |
|---|---|---|
| ROS2 topic receives vehicle signals | ≥ 1 Hz per signal | `ros2 topic echo /vehicle/speed` on host |
| ROS2 topic names follow a clear convention | `/vehicle/{signal_name}` pattern | Manual inspection |
| All M1 + M2 acceptance criteria continue to pass | Zero regressions | Full stack smoke test |
| ROS2 bridge reconnects on Databroker restart | Reconnects within 30 s | `docker compose restart databroker` |
| VSS path migration to standard COVESA paths | `Vehicle.Battery.SoC` → `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | Databroker VSS catalog inspection |

---

## 5. User Journey (Hypothesis L2 — Behavior)

### Journey A: Vehicle / SDV Engineer

```
Step 1: docker compose up
         → All services start (M1 + M2 stack + ros2-bridge)

Step 2: docker compose logs -f ros2-bridge
         → See: "Subscribed to Kuksa: Vehicle.Speed"
         → See: "Published ROS2 topic: /vehicle/speed = 87.3"

Step 3: Read services/ros2-bridge/main.py
         → Understand: Kuksa subscribe → ROS2 publish bridge
         → Understand: VSS path → ROS2 topic name convention
         → Understand: ROS2 message type for vehicle signals (Float32 / custom msg)
```

### Journey B: Robotics / Autonomous Driving Engineer

```
Step 1: (Assumes docker compose is running)

Step 2: ros2 topic list
         → /vehicle/speed
         → /vehicle/battery/soc
         → /vehicle/cabin/temperature

Step 3: ros2 topic echo /vehicle/speed
         → data: 87.3  (updates at 1 Hz)

Step 4: Understands:
         → How to write an AD node that subscribes to /vehicle/speed
         → Where that data originates (Kuksa Databroker ← ECU Simulator)
         → The bridge architecture between vehicle middleware and AD stack
```

### Journey C: Learning the Two Middleware Paradigms Side-by-Side

```
Step 1: docker compose up
Step 2: Terminal A — mosquitto_sub -t "sdv/vehicle-001/#"   (M2 MQTT, cloud side)
Step 3: Terminal B — ros2 topic echo /vehicle/speed          (M3 ROS2, AD side)
Step 4: Both terminals show the same vehicle data,
         arriving via two completely different middleware stacks,
         both ultimately sourced from the same Kuksa Databroker.
```

**Desired Interaction Model:**
> One Databroker — three consumers (Dashboard, MQTT Bridge, ROS2 Bridge) — three different protocols and use cases. The same SDV principle (centralized middleware) scales to every consumer type.

---

## 6. Milestone Context

```
Milestone 1 ✅  ECU Simulator → Kuksa Databroker → Dashboard
                (gRPC / VSS / centralized in-vehicle middleware)

Milestone 2 ✅  + MQTT Bridge → Mosquitto Broker
                (MQTT / V2C telemetry / cloud exit point)

Milestone 3 ◀── We are here
                + ROS2 Bridge → ROS2 topics
                (DDS / distributed middleware / AD software integration)

Milestone 4 (future)
                + Virtual CAN ECUs → CAN Gateway → Kuksa
                (ISO 11898 / CAN frames / gateway ECU pattern)

Milestone 5 (future)
                + AI Agent monitoring Databroker
                (LLM / anomaly detection / intelligent actuation)
```

M3 completes the **three-layer consumer model** of the Databroker:
- Layer 1: Human UI (Dashboard — M1)
- Layer 2: Cloud (MQTT Bridge — M2)
- Layer 3: Autonomous Driving Stack (ROS2 Bridge — M3)

---

## 7. Proposed M3 Architecture (High Level)

```
Kuksa Databroker (:55555)
       │
       │  gRPC subscribe_current_values()   (same pattern as M2 bridge)
       ▼
  ros2-bridge service
       │
       │  ROS2 publish (DDS / rclpy)
       ▼
  ROS2 topic: /vehicle/speed
  ROS2 topic: /vehicle/battery/soc
  ROS2 topic: /vehicle/cabin/temperature
       │
       │  ros2 topic echo  (from host or another ROS2 node)
       ▼
  Any ROS2-native node (path planner, safety monitor, etc.)
```

Key design principle: the `ros2-bridge` is structurally identical to the `mqtt-bridge` from M2 — it subscribes to the Databroker and forwards. The only difference is the output protocol (ROS2/DDS instead of MQTT). This reinforces the architectural pattern rather than introducing an entirely new one.

---

## 8. Out of Scope (Milestone 3)

| Item | Reason |
|---|---|
| Full Autoware or AD stack integration | Too complex; out of educational scope |
| ROS2 → Kuksa write-back (actuation) | Read-only in M3; actuation is M5 |
| ROS2 bag recording | Useful but not core to the architectural lesson |
| Custom ROS2 message types (beyond std_msgs) | `std_msgs/Float32` is sufficient for M3; custom msgs add IDL complexity |
| ROS2 lifecycle nodes | Advanced ROS2 topic; deferred |
| Multi-node ROS2 graph with sensor fusion | Milestone 4+ scope |

---

## 9. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| Q1 | Should the ROS2 bridge run inside Docker (using the official ROS2 Docker image), or should it require a host ROS2 installation? | Team | ✅ **Resolved: Docker** — maintains the zero-configuration `docker compose up` principle |
| Q2 | Which ROS2 distribution? Humble (LTS, EOL 2027) or Jazzy (LTS, EOL 2029)? | Team | ✅ **Resolved: Jazzy Jalisco** — see note below |
| Q3 | Should M3 also migrate VSS signal paths to standard COVESA paths (`Vehicle.Powertrain.TractionBattery.StateOfCharge.Current`)? | Team | ✅ **Resolved: Yes** — see note below |
| Q4 | ROS2 topic name convention: `/vehicle/speed` (simplified) or `/vehicle/battery/state_of_charge` (COVESA-aligned)? | Team | Open — to be decided in FRD (L3 Domain) |
| Q5 | Should the `ros2 topic echo` test require a host ROS2 installation, or ship a `ros2-subscriber` Docker service for testing? | Team | ⏳ **Pending host ROS2 check** — see note below |

### Q2 Resolution — ROS2 Jazzy Jalisco

**Decision: ROS2 Jazzy Jalisco (LTS, May 2024 – May 2029)**

Evidence:
- Autoware Universe — the leading open-source autonomous driving stack and the primary real-world reference for SDV/AD software — completed **Jazzy full support in April 2026** and has scheduled Humble soft-freeze for January 2027 and Jazzy-exclusive mode for May 2027. Any new project targeting the current AD ecosystem should target Jazzy.
- Jazzy is an LTS release with 5 years of support (to May 2029), giving this project a longer shelf life than Humble (EOL May 2027).
- Eclipse Cyclone DDS (the default RMW for Eclipse SDV tooling) fully supports Jazzy.
- As of May 2026, new SDV programs starting today standardize on Jazzy; Humble is in maintenance mode.

### Q3 Resolution — VSS Path Migration to COVESA Standard Paths

**Decision: Yes — migrate in M3**

The following custom paths used in M1/M2 will be replaced with their COVESA VSS 4.x equivalents:

| M1/M2 Custom Path | COVESA VSS 4.x Standard Path |
|---|---|
| `Vehicle.Speed` | `Vehicle.Speed` *(already standard)* |
| `Vehicle.Battery.SoC` | `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` |
| `Vehicle.Cabin.Temperature` | `Vehicle.Cabin.HVAC.AmbientAirTemperature` |

Rationale: M3 introduces ROS2 topic naming that mirrors the VSS hierarchy. Aligning VSS paths with the COVESA standard at this point ensures ROS2 topic names are also industry-standard, and removes the "known limitation" documented since M1.

### Q5 Resolution — Docker-based Test Subscriber

**Decision: Add a lightweight `ros2-subscriber` Docker service**

Host ROS2 is **not installed**. Docker is available.

M3 will ship a `ros2-subscriber` container (based on the official `ros:jazzy-ros-base` image) that runs `ros2 topic echo` on all three vehicle topics and prints the output to its container log. Verification is performed with:

```bash
docker compose logs -f ros2-subscriber
```

This keeps the zero-configuration `docker compose up` guarantee consistent with M1 and M2. A user who later installs ROS2 (native or WSL2) can also subscribe from the host — the two approaches are fully compatible.
