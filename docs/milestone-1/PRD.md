# Product Requirements Document (PRD)
## Milestone 1: Live Vehicle Signal Dashboard
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | PRD |
| Milestone | 1 — Live Vehicle Signal Dashboard |
| Status | Draft |
| Hypothesis Layers | Value (L1) · Behavior (L2) |
| Created | 2026-05-23 |
| Version | 1.0 |
| Next Layer | [FRD.md](FRD.md) |

---

## 1. Overview

Milestone 1 delivers the foundational end-to-end signal pipeline of the mini-sdv-platform: a simulated ECU publishes vehicle signals to a centralized databroker, which a live dashboard visualizes in real-time.

This milestone is intentionally minimal. Its purpose is to make the core SDV signal flow **tangible and runnable with a single command**, before any additional architectural layers (MQTT, ROS2, CAN) are introduced.

---

## 2. Problem Statement

### 2.1 The Learning Gap in SDV Education

Modern Software Defined Vehicles (SDVs) are built around a **centralized compute architecture** where dozens of Electronic Control Units (ECUs) publish signals to a shared vehicle middleware. Applications — HMI, cloud connectors, AI agents — consume signals from this middleware rather than communicating directly with individual ECUs.

This pattern is fundamentally different from traditional automotive software, where each ECU owns its data and communicates peer-to-peer over CAN bus. Engineers new to SDV architecture (coming from embedded systems, robotics, or backend software) cannot easily experience or experiment with the centralized model without access to real vehicle hardware.

### 2.2 The Gap This Milestone Closes

| Without M1 | With M1 |
|---|---|
| Architecture exists only as diagrams | A running system can be started and observed |
| Signal flow is abstract | Signal flow is visible in real-time on a dashboard |
| SDV concepts are hard to map to code | Inline code comments explain each SDV concept |
| Learning requires hardware access | Learning requires only Docker |

---

## 3. Target Users

### Primary: Junior-to-Mid SDV / Automotive Software Engineer

**Profile:**
- Background in embedded systems, robotics, or backend software
- New to AUTOSAR Adaptive, VSS, or centralized vehicle compute architectures
- Goal: understand how modern SDV platforms structure and distribute vehicle data

**Needs:**
- A running system to inspect, not just diagrams to read
- Code comments that explain SDV concepts in their natural context
- A clear mapping from simulation components to real-world SDV equivalents

### Secondary: Engineering Educator / Technical Interviewer

**Profile:**
- Teaching automotive software architecture to engineers or students
- Uses this project as a reference implementation or assignment scaffold

---

## 4. Value Proposition (Hypothesis L1)

> "Run `docker compose up`, open a browser, and watch a simulated vehicle's speed, battery level, and cabin temperature update in real-time — then read the code to understand how production SDV platforms like CARIAD and Bosch SDV use the same architectural pattern."

**What makes this valuable:**

| Attribute | Description |
|---|---|
| Executable | Not just a diagram — a real running system with observable behavior |
| Industry-relevant | Uses Eclipse Kuksa, a production SDV technology adopted by COVESA members |
| Self-documenting | Code comments map each implementation choice to its real-world SDV counterpart |
| Extensible | M1 is the stable foundation every future milestone (MQTT, ROS2, CAN) builds upon |

---

## 5. Success Metrics

### 5.1 Primary Acceptance Criteria (Hypothesis L1 — Value)

All three must be true for M1 to be considered validated:

| Criterion | Threshold | Verification Method |
|---|---|---|
| `docker compose up` produces a running dashboard | < 60 seconds on a clean machine | Manual timing |
| All 3 signals visible and updating | ≥ 1 Hz update rate observed in dashboard | Visual inspection |
| Signal values match ECU simulation model | Speed 10–120 km/h · SoC 55–85 % · Temp 19.5–24.5 °C | Dashboard observation |

### 5.2 Secondary Quality Criteria

| Criterion | Threshold |
|---|---|
| Code readability | Every service has inline comments explaining the SDV concept behind each key decision |
| Observability | All services emit structured, timestamped logs with signal values |
| Resilience | ECU simulator auto-reconnects to Databroker within 30 seconds after restart |
| Documentation | README covers architecture, quickstart, and a real-SDV mapping section |

---

## 6. User Journey (Hypothesis L2 — Behavior)

```
Step 1: Clone
  git clone <repo>
  cd mini-sdv-platform

Step 2: Start
  docker compose up

Step 3: Observe
  Open http://localhost:8501
  → See 3 live signal metric cards (Speed / SoC / Temperature)
  → See 3 rolling 60-second line charts updating in real time

Step 4: Explore the Code
  → services/ecu-simulator/main.py
    → understand: ECU → gRPC → Databroker signal flow
  → services/dashboard/main.py
    → understand: Databroker subscription → UI rendering
  → README.md
    → understand: how each component maps to a real SDV system

Step 5: Experiment
  → Change UPDATE_INTERVAL_SEC env var → observe faster/slower updates
  → docker compose restart databroker → observe ECU sim auto-reconnect in logs
  → Read config/vss/vss_mini.json → understand what a VSS catalog defines
```

**Desired Interaction Model:**
> Zero-configuration startup → immediate visual feedback → self-documenting code teaches SDV concepts in context.

---

## 7. Milestone Context

M1 establishes the core architectural pattern that all future milestones extend. It must be stable and well-documented before M2 begins.

```
Milestone 1  ◀── We are here
  ECU Simulator → Kuksa Databroker → Streamlit Dashboard
  (gRPC direct publish)

Milestone 2  (future)
  + MQTT Bridge → cloud-style signal forwarding over publish/subscribe

Milestone 3  (future)
  + ROS2 integration → distributed vehicle middleware and true pub/sub

Milestone 4  (future)
  + SocketCAN virtual CAN → physical-layer ECU simulation over virtual bus

Milestone 5  (future)
  + AI agent integration → intelligent vehicle orchestration and actuation
```

The Databroker-as-central-hub pattern established in M1 is the architectural constant. Every future service connects to it, not to each other directly.

---

## 8. Out of Scope (Milestone 1)

Explicitly excluded to preserve focus and educational clarity:

| Item | Reason for Exclusion |
|---|---|
| SocketCAN / virtual CAN bus | Adds Linux kernel-level complexity; Milestone 4 concern |
| MQTT cloud bridge | Milestone 2 concern |
| ROS2 integration | Milestone 3 concern |
| TLS / mTLS on gRPC | Educational simplicity; `--insecure` mode documents the gap explicitly |
| Persistent signal history (database) | In-memory rolling buffer is sufficient for M1 |
| Kubernetes / Helm | Explicitly out of scope per project principles |
| Actuator commands / write-back to ECU | Dashboard is read-only in M1 |
| Authentication / authorization | Known limitation; documented in TRD |
| OTA update simulation | Milestone 4+ concern |

---

## 9. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| Q1 | Should `Vehicle.Battery.SoC` use the standard COVESA VSS path `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` instead of the custom path? | Team | Open — custom path retained for M1 simplicity; revisit in M3 |
| Q2 | Should the dashboard support a dark mode for HMI visual resemblance? | Team | Deferred to M2 |
| Q3 | Should signal range warnings (e.g., SoC < 20%) be shown in M1? | Team | Deferred to M2 |
