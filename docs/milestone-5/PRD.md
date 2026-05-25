# Product Requirements Document (PRD)
## Milestone 5: AI Signal Monitoring Agent
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | PRD |
| Milestone | 5 — AI Signal Monitoring Agent |
| Status | Draft |
| Hypothesis Layers | Value (L1) · Behavior (L2) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | Milestone 4 (stable, deployed) |
| Next Layer | [FRD.md](FRD.md) |

---

## 1. Overview

Milestone 5 adds an **AI-powered signal monitoring agent** that continuously watches vehicle signals from the Kuksa Databroker and uses a Large Language Model (Claude API) to detect anomalies, reason about their significance, and generate natural-language alerts.

M1–M4 established the complete signal pipeline: ECU → CAN → Databroker → Dashboard / MQTT / ROS2. M5 closes the intelligence gap by adding a layer that can **interpret** what those signals mean — not just display or forward them.

---

## 2. Problem Statement

### 2.1 The Gap M5 Closes

The current platform moves vehicle signals faithfully from ECU to dashboard, but no component understands what those signals mean. A human looking at the dashboard must judge whether a value is normal or concerning.

| Capability | M1–M4 | Real SDV Platform |
|---|---|---|
| Signal delivery | ✅ ECU → Databroker → consumers | ✅ Same |
| Anomaly detection | ❌ None | ✅ Rule-based or ML-based monitor |
| Natural-language explanation | ❌ None | ✅ ADAS / safety layer or cloud AI |
| Actuation / alerting | ❌ None | ✅ Alert to OEM backend or HMI |

Without M5, a learner has no model for how an SDV platform would respond intelligently to signal data — a gap that matters as AI becomes a standard layer in modern vehicle architectures.

### 2.2 Why an LLM Agent?

Rule-based anomaly detection (threshold checks) is straightforward to implement but teaches nothing about AI integration patterns. An LLM-based agent demonstrates:

- How to connect a vehicle middleware signal stream to a foundation model API
- How to structure multi-signal context for an LLM prompt
- How to act on LLM output (structured JSON → actionable alert)
- The architectural pattern of an "AI agent" in an SDV context: observe → reason → act

---

## 3. Target Users

Same as M1–M4: SDV / Automotive Software Engineer learning vehicle platform architecture.

**New learning goal for M5:**
> Understand how an AI agent is integrated into a vehicle signal pipeline — from subscribing to the Databroker, constructing context for an LLM, interpreting structured responses, and publishing alerts downstream.

---

## 4. Value Hypothesis (L1)

**Hypothesis:**
> Connecting an LLM-based monitoring agent to the existing Kuksa Databroker signal stream provides a concrete, runnable example of the "AI in the vehicle" pattern that is increasingly central to SDV platform design — value that static dashboards and rule-based thresholds cannot deliver.

**Evidence:**
- OEMs (VW CARIAD, BMW, Mercedes) are actively integrating LLM-based co-pilots and safety monitors into their SDV stacks (2024–2025 public roadmaps).
- The Claude API (function calling + structured output) provides a stable, documented interface that mirrors how production AI agents consume structured sensor data.
- M1–M4 already delivers the signal pipeline; M5 reuses it without modification, demonstrating the extensibility of the Databroker abstraction.

**Acceptance Criteria:**
- AC-1: The AI agent subscribes to all three vehicle signals from the Databroker in real time
- AC-2: The agent calls the Claude API with structured signal context and receives a structured anomaly assessment
- AC-3: When the LLM detects an anomaly, a natural-language alert is published to an MQTT topic (`sdv/vehicle-001/alerts/ai`)
- AC-4: The Streamlit dashboard displays the latest AI alert alongside the signal charts
- AC-5: The agent runs as a Docker service with no manual steps after `docker compose up`

---

## 5. Behavior Hypothesis (L2)

### 5.1 Architecture Addition

```
【M4 (unchanged)】
ECU Simulator → vcan0 → CAN Gateway → Kuksa Databroker
                                              │
                              ┌───────────────┼────────────────┐
                              ▼               ▼                ▼
                          Dashboard      MQTT Bridge      ROS2 Bridge

【M5 addition】
                        Kuksa Databroker
                              │
                              ▼  gRPC subscribe
                       ┌─────────────┐
                       │  ai-monitor │  ← NEW
                       │   agent     │
                       └──────┬──────┘
                              │ Claude API (HTTPS)
                              ▼
                       ┌─────────────┐
                       │  Claude API │  (claude-haiku-4-5)
                       └──────┬──────┘
                              │ structured JSON response
                              ▼
                       ┌─────────────┐       ┌──────────┐
                       │  Mosquitto  │◀──────│ ai-alert │
                       │  :1883      │       │  topic   │
                       └─────────────┘       └──────────┘
                              │
                              ▼  (dashboard subscribes)
                         Dashboard
                       (alert panel)
```

### 5.2 Agent Observe → Reason → Act Loop

```
Every N seconds:
  1. OBSERVE  — read latest values of all 3 signals from Databroker
  2. REASON   — send signal snapshot + history context to Claude API
                 with structured output schema (anomaly: bool, severity, explanation, signals)
  3. ACT      — if anomaly detected:
                   publish JSON alert to sdv/vehicle-001/alerts/ai (MQTT)
                   log alert to stdout
```

### 5.3 What Counts as an Anomaly (examples the LLM will reason about)

| Signal Combination | Anomaly Pattern |
|---|---|
| Speed > 100 km/h + Battery SoC dropping > 2%/sample | High-load discharge anomaly |
| Cabin Temp > 24°C and rising for 5+ consecutive samples | HVAC failure pattern |
| Speed = 0 but Battery SoC still draining | Parasitic drain while parked |
| All signals frozen (same value for 10+ samples) | Sensor loss / ECU failure |

The LLM reasons over these patterns without hard-coded rules — the prompt provides signal history and asks for interpretation.

### 5.4 User-Observable Behavior

| Action | Observable Result |
|---|---|
| `docker compose up` | All services including `ai-monitor` start |
| `mosquitto_sub -t 'sdv/vehicle-001/alerts/ai'` | JSON alerts appear when anomaly detected |
| `http://localhost:8501` | Dashboard shows AI Alert panel with latest alert text |
| `docker compose logs -f ai-monitor` | Agent logs: `[OK] Speed=87.3, SoC=72.4, Temp=22.1` or `[ALERT] HVAC anomaly detected…` |

### 5.5 Alert JSON Schema (illustrative)

```json
{
  "timestamp": "2026-05-25T12:34:56Z",
  "anomaly": true,
  "severity": "warning",
  "signals": {
    "Vehicle.Speed": 0.0,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": 71.2,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": 25.8
  },
  "explanation": "Vehicle is stationary but battery SoC is declining and cabin temperature is above normal range. Possible parasitic load or HVAC running with engine off."
}
```

---

## 6. Out of Scope for M5

- Actuation / vehicle control commands (read-only monitoring only)
- Fine-tuned or locally-hosted LLM (Claude API via HTTPS only)
- Persistent alert database / time-series storage
- Multi-vehicle fleet monitoring
- TLS on gRPC or MQTT (inherited insecure mode from M1)
- Alert routing beyond MQTT (email, PagerDuty, etc.)
