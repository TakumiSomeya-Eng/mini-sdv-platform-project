# Functional Requirements Document (FRD)
## Milestone 5: AI Signal Monitoring Agent
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | FRD |
| Milestone | 5 — AI Signal Monitoring Agent |
| Status | Draft |
| Hypothesis Layers | Domain (L3) · Interaction (L4) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | [PRD.md](PRD.md) · Milestone 4 FRD |
| Next Layer | [TRD.md](TRD.md) |

---

## 1. System Context

```
【M5 Full Architecture (additions highlighted with ★)】

  WSL2 Ubuntu
  ┌─────────────────────────────────────────────────────────────────┐
  │  vcan0 → CAN Gateway → Kuksa Databroker :55555                  │
  │                               │                                 │
  │           ┌───────────────────┼──────────────────────┐          │
  │           ▼                   ▼                      ▼          │
  │       dashboard           mqtt-bridge          ros2-bridge      │
  │        :8501               → Mosquitto         → DDS            │
  │           │ ★ AI Alert         │                                │
  │           │   panel            │ ★ sdv/.../alerts/ai            │
  │           │                    │                                │
  │       ★ ai-monitor  ──────────▶ Mosquitto :1883                 │
  │         (poll 10s)   MQTT pub                                   │
  │             │                                                   │
  │             │ HTTPS                                             │
  │             ▼                                                   │
  │       Claude API (claude-haiku-4-5)                             │
  └─────────────────────────────────────────────────────────────────┘
```

**Architectural invariants carried from M1–M4:**
- Databroker remains the single source of truth for all vehicle signal state (DR-00)
- Dashboard, MQTT Bridge, ROS2 Bridge, CAN Gateway are unchanged (DR-10)
- `ai-monitor` is read-only — it MUST NOT write to the Databroker (DR-50)

---

## 2. User Stories

| ID | As a… | I want to… | So that… | Acceptance Criterion |
|---|---|---|---|---|
| US-50 | SDV engineer | see natural-language anomaly alerts when signals behave abnormally | I understand how an LLM agent interprets vehicle telemetry | Alerts appear within 15s of anomaly onset |
| US-51 | SDV engineer | subscribe to `sdv/vehicle-001/alerts/ai` and receive JSON alerts | I can integrate AI alerts into any MQTT consumer | Valid JSON with `anomaly`, `severity`, `explanation` fields |
| US-52 | SDV engineer | see an AI alert panel on the dashboard | I have a single view of signals + AI interpretation | Dashboard shows latest alert text and timestamp |
| US-53 | SDV engineer | read the agent logs to understand the Observe-Reason-Act loop | I can learn the LLM integration pattern | Logs show OBSERVE/REASON/ACT phases clearly |

---

## 3. Domain Rules (L3)

### DR-50: ai-monitor is read-only
`ai-monitor` MUST only call `get_current_values()` on the Databroker.
It MUST NOT call `set_current_values()` or any write API.

### DR-51: Signal polling interval
`ai-monitor` MUST poll the Databroker at a fixed interval (default 10 seconds).
This interval is configurable via `MONITOR_INTERVAL_SEC` environment variable.

### DR-52: History window
`ai-monitor` MUST maintain a rolling history of the last `HISTORY_WINDOW` readings
(default: 10) per signal, passed to the LLM for trend analysis.

### DR-53: LLM call gating
The Claude API MUST be called on every poll cycle, regardless of whether values changed.
(The LLM assesses trend context, not just point-in-time values.)

### DR-54: Alert publish condition
`ai-monitor` MUST publish to MQTT if and only if the LLM response contains `"anomaly": true`.
Normal state (`"anomaly": false`) is logged but NOT published to MQTT.

### DR-55: Alert schema (immutable in M5)

```json
{
  "timestamp": "<ISO 8601>",
  "anomaly": true,
  "severity": "info | warning | critical",
  "signals": {
    "Vehicle.Speed": <float>,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": <float>,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": <float>
  },
  "explanation": "<natural language, 1–3 sentences>"
}
```

### DR-56: LLM model
MUST use `claude-haiku-4-5-20251001` (fast, low-cost; appropriate for monitoring loop).

### DR-57: Prompt structure
The system prompt MUST define the agent's role, the signal semantics (units, normal ranges),
and the required JSON output schema. Signal history MUST be included in the user turn.

---

## 4. Functional Requirements (L4)

### FR-50: ai-monitor — core agent loop

| ID | Requirement |
|---|---|
| FR-50-1 | On startup, connects to Databroker via gRPC and Mosquitto via MQTT |
| FR-50-2 | Every `MONITOR_INTERVAL_SEC` seconds, calls `get_current_values()` for all 3 signals |
| FR-50-3 | Appends current values to per-signal rolling history deque (maxlen=HISTORY_WINDOW) |
| FR-50-4 | Calls Claude API with system prompt + signal history in user turn |
| FR-50-5 | Parses Claude response as JSON (DR-55 schema) |
| FR-50-6 | If `anomaly: true`, publishes JSON alert to `sdv/{VEHICLE_ID}/alerts/ai` (QoS 0) |
| FR-50-7 | Logs OBSERVE / REASON / ACT phase markers for educational clarity |
| FR-50-8 | On Databroker or MQTT connection failure, retries with exponential back-off (2s → 30s cap) |

### FR-51: ai-monitor — LLM prompt contract

| ID | Requirement |
|---|---|
| FR-51-1 | System prompt specifies: agent role, signal definitions (name, unit, normal range), output schema |
| FR-51-2 | User turn includes: current timestamp, last N readings per signal as JSON array |
| FR-51-3 | Claude API called with `max_tokens=256` (sufficient for JSON alert; constrains cost) |
| FR-51-4 | Response is parsed with `json.loads()`; parse errors are logged and skipped (no crash) |

### FR-52: Dashboard — AI Alert panel

| ID | Requirement |
|---|---|
| FR-52-1 | Dashboard subscribes to `sdv/{VEHICLE_ID}/alerts/ai` via MQTT on startup |
| FR-52-2 | Latest alert is stored in `st.session_state.ai_alert` |
| FR-52-3 | Alert panel renders below the signal charts: severity badge + explanation text + timestamp |
| FR-52-4 | If no alert has been received, panel shows "No anomaly detected" |
| FR-52-5 | Alert panel is updated on every dashboard rerun cycle (1 Hz) |

### FR-53: Docker Compose — ai-monitor service

| ID | Requirement |
|---|---|
| FR-53-1 | New service `ai-monitor` with `network_mode: host` |
| FR-53-2 | `ANTHROPIC_API_KEY` passed via environment variable (never hardcoded) |
| FR-53-3 | `restart: on-failure` policy |
| FR-53-4 | `depends_on` databroker and mosquitto |

---

## 5. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-50 | Alert latency ≤ 15 seconds from anomaly onset to MQTT publish (one poll cycle + API call) |
| NFR-51 | Claude API call timeout = 10 seconds; on timeout, log warning and skip cycle |
| NFR-52 | No new dependencies added to dashboard, mqtt-bridge, ros2-bridge, can-gateway |
| NFR-53 | `ANTHROPIC_API_KEY` MUST be sourced from environment, never from source code or config files |
| NFR-54 | ai-monitor logs MUST clearly label OBSERVE / REASON / ACT phases for educational reading |

---

## 6. Out of Scope

- Write actuation to Databroker (speed limit, HVAC override)
- Persistent alert storage (database, file)
- Multi-model comparison (single model per DR-56)
- Streaming Claude API responses
- Alert deduplication / suppression window
