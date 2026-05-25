# Mini SDV Platform — Architecture Review & Study Guide
## Milestone 5: AI Signal Monitoring Agent (Claude API + Observe→Reason→Act)

> **Date:** 2026-05-25

---

## Table of Contents

1. [What M5 Adds and Why](#1-what-m5-adds-and-why)
2. [Why LLM-Based Monitoring, Not Rule-Based Thresholds](#2-why-llm-based-monitoring-not-rule-based-thresholds)
3. [The Observe → Reason → Act Pattern](#3-the-observe--reason--act-pattern)
4. [Anthropic Messages API Deep Dive](#4-anthropic-messages-api-deep-dive)
   - 4-1. API Structure (system / user / assistant turns)
   - 4-2. Model Selection: claude-haiku-4-5
   - 4-3. max_tokens and Cost Control
   - 4-4. Structured Output via Prompt Engineering
5. [AI Monitor Service Deep Dive](#5-ai-monitor-service-deep-dive)
   - 5-1. Configuration and Environment Variables
   - 5-2. Agent Loop Walkthrough
   - 5-3. Rolling History Window
   - 5-4. MQTT Alert Publishing
6. [Prompt Engineering for Vehicle Signals](#6-prompt-engineering-for-vehicle-signals)
   - 6-1. System Prompt Design
   - 6-2. User Turn: Signal History as JSON
   - 6-3. Output Schema Enforcement
7. [JSON Parsing and the Code Fence Problem](#7-json-parsing-and-the-code-fence-problem)
8. [Dashboard AI Alert Panel](#8-dashboard-ai-alert-panel)
   - 8-1. MQTT Background Thread (paho loop_start)
   - 8-2. st.session_state for Alert State
   - 8-3. Severity Rendering
9. [Full M5 Architecture Walkthrough](#9-full-m5-architecture-walkthrough)
10. [LLM vs Rule-Based Anomaly Detection](#10-llm-vs-rule-based-anomaly-detection)
11. [Docker Compose Changes in M5](#11-docker-compose-changes-in-m5)
12. [Security: API Key Handling](#12-security-api-key-handling)
13. [Known Constraints and Trade-offs](#13-known-constraints-and-trade-offs)
14. [M5 in the Context of Real SDV AI Layers](#14-m5-in-the-context-of-real-sdv-ai-layers)
15. [Review Quiz](#15-review-quiz)

---

## 1. What M5 Adds and Why

### The gap M5 closes

After M4, the platform has a complete signal pipeline:

```
ECU → CAN → vcan0 → CAN Gateway → Databroker → Dashboard
                                              → MQTT Bridge → Cloud
                                              → ROS2 Bridge → AD Stack
```

Every signal flows correctly. But no component **understands** what those signals mean. The dashboard shows a number; a human must judge whether 68.2 % SoC with Speed = 0 is normal or a parasitic drain failure.

M5 adds the **intelligence layer**:

```
                   Databroker
                       │
               ┌───────┘ poll every 10s
               ▼
          ai-monitor
               │ signal history
               ▼
          Claude API ──── "Is this an anomaly?"
               │
               ▼ JSON response
          anomaly? → MQTT alert → Dashboard AI panel
```

**What M5 teaches:**

| Concept | Description |
|---|---|
| Observe→Reason→Act | The agent loop pattern used in all LLM-based autonomous agents |
| Anthropic Messages API | How to call an LLM with structured context from a Python service |
| Prompt engineering | How to instruct an LLM to return machine-parseable JSON |
| Multi-signal reasoning | Why LLMs can detect anomalies that threshold rules miss |
| MQTT background thread | How to add real-time MQTT subscription to a Streamlit dashboard |
| API key security | How to pass secrets via environment variables in Docker Compose |

---

## 2. Why LLM-Based Monitoring, Not Rule-Based Thresholds

### Rule-based approach (traditional)

```python
# Typical threshold monitor
if speed > 130:
    alert("Speed over limit")
if soc < 20:
    alert("Battery critical")
if cabin_temp > 35:
    alert("Cabin overheating")
```

This works for known, single-signal conditions. But it fails on:

| Problem | Example |
|---|---|
| **Cross-signal correlation** | Speed=0 AND SoC declining → parasitic drain. No single threshold catches this. |
| **Trend patterns** | SoC declining at 2%/sample normally. Declining at 0.1%/sample → charging anomaly. |
| **Context-dependence** | Speed=120 at 100% SoC is normal. Speed=120 at 12% SoC is a critical range-anxiety event. |
| **Unknown unknowns** | Rules only catch what an engineer anticipated. LLMs reason over patterns engineers didn't predict. |

### LLM-based approach (M5)

Instead of hard-coded rules, the LLM receives a **window of signal history** and applies its training knowledge about vehicle physics and typical failure modes:

```json
{
  "signal_history": {
    "Vehicle.Speed": [0.0, 0.0, 0.0, 0.0, 0.0],
    "Vehicle...SoC": [78.2, 77.8, 77.3, 76.9, 76.4],
    "Vehicle...Temp": [22.1, 22.3, 22.6, 22.9, 23.2]
  }
}
```

The LLM sees: **vehicle is stationary, battery declining, cabin temp rising** → produces: `"Possible parasitic drain with HVAC running while engine off"`.

No rule needed. The LLM reasons from first principles.

---

## 3. The Observe → Reason → Act Pattern

The **Observe→Reason→Act (ORA)** loop is the foundational pattern for all LLM-based agents, from vehicle monitors to software development agents (like Claude Code).

```
┌─────────────────────────────────────────────────────┐
│                   Agent Loop                        │
│                                                     │
│  ┌─────────┐     ┌─────────┐     ┌─────────┐       │
│  │ OBSERVE │────▶│ REASON  │────▶│   ACT   │       │
│  │         │     │         │     │         │       │
│  │ Read    │     │ Call    │     │ Publish │       │
│  │ signals │     │ Claude  │     │ alert   │       │
│  │ from    │     │ API     │     │ to MQTT │       │
│  │ Broker  │     │         │     │ (if     │       │
│  └────┬────┘     └─────────┘     │  anomaly│       │
│       │                          │  =true) │       │
│       │    sleep(10s)            └─────────┘       │
│       └──────────────────────────────────┐         │
│                                          │         │
└──────────────────────────────────────────┘─────────┘
```

### M5 implementation phases

| Phase | Code | Input | Output |
|---|---|---|---|
| OBSERVE | `poll_databroker()` | Databroker gRPC response | `dict[path → float]` |
| REASON | `call_llm()` | Signal history JSON | Structured JSON assessment |
| ACT | `mqtt.publish()` | Parsed JSON with `anomaly=true` | MQTT message on alerts topic |

### Why sleep-based, not event-driven?

An event-driven agent would subscribe to Databroker changes and call the LLM on every signal update. This would mean 3 LLM calls per second (one per CAN frame) — expensive and unnecessary.

The 10-second polling cycle (`MONITOR_INTERVAL_SEC=10`) is a deliberate trade-off:
- **Latency:** ≤ 10s to detect an anomaly (acceptable for monitoring, not safety-critical control)
- **Cost:** ~360 API calls/hour vs 10,800 event-driven calls/hour
- **History:** 10-second cycles accumulate 10 readings over 100 seconds — enough trend context for the LLM

---

## 4. Anthropic Messages API Deep Dive

### 4-1. API Structure (system / user / assistant turns)

The Claude API follows the **Chat Completions** pattern. A request contains:

```python
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    system=SYSTEM_PROMPT,       # ← persistent instruction context
    messages=[
        {"role": "user", "content": user_content}   # ← current input
    ],
)
```

| Parameter | Purpose in M5 |
|---|---|
| `system` | Defines the agent's role, signal semantics, and required JSON output schema. Sent on every call. |
| `messages[user]` | Current signal history snapshot (JSON). Changes every 10 seconds. |
| `model` | Which Claude model to use. |
| `max_tokens` | Maximum output tokens. Set to 256 — enough for the JSON alert, prevents runaway responses. |

The response is accessed via:
```python
raw = response.content[0].text   # the raw text output from Claude
```

### 4-2. Model Selection: claude-haiku-4-5

M5 uses `claude-haiku-4-5-20251001` — the smallest and fastest Claude model family.

| Model | Speed | Cost | Best for |
|---|---|---|---|
| claude-haiku-4-5 | ~1–2s | Lowest | High-frequency monitoring, structured JSON output |
| claude-sonnet-4-6 | ~3–5s | Medium | Complex multi-step reasoning |
| claude-opus-4-7 | ~8–15s | Highest | Deep analysis, novel reasoning |

For a 10-second monitoring loop, Haiku's ~1–2 second latency leaves 8 seconds of slack. Opus would consume most of the polling window and cost 10–20× more per call.

**Latency observed in M5:**
```
18:01:00 [REASON] Sending signal history to Claude API...
18:01:02 HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
```
~2 seconds end-to-end — fits comfortably in a 10-second cycle.

### 4-3. max_tokens and Cost Control

`max_tokens=256` limits the response length. The expected JSON output is approximately:

```json
{
  "anomaly": false,
  "severity": "info",
  "explanation": "All signals nominal. Speed follows expected sinusoidal...",
  "signals": { "Vehicle.Speed": 44.6, "...SoC": 80.69, "...Temp": 21.5 }
}
```

This is roughly 80–120 tokens. Setting `max_tokens=256` gives 2× headroom for verbose explanations while preventing the model from generating paragraphs of commentary.

### 4-4. Structured Output via Prompt Engineering

The system prompt instructs Claude to return **only JSON** with a specific schema:

```
Respond ONLY with a JSON object matching this schema — no markdown, no commentary:
{
  "anomaly": <bool>,
  "severity": "<info|warning|critical>",
  "explanation": "<1-3 sentences, plain English>",
  "signals": { ... }
}
```

This is called **prompt-based structured output**. The model is instructed to constrain its output format through natural language instructions. An alternative is **tool use / function calling**, where the API enforces a JSON schema at the protocol level — more reliable but requires a different API call pattern.

---

## 5. AI Monitor Service Deep Dive

### 5-1. Configuration and Environment Variables

```python
DATABROKER_HOST      = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT      = int(os.environ.get("DATABROKER_PORT", "55555"))
MQTT_HOST            = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT            = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID           = os.environ.get("VEHICLE_ID", "vehicle-001")
MONITOR_INTERVAL_SEC = float(os.environ.get("MONITOR_INTERVAL_SEC", "10"))
HISTORY_WINDOW       = int(os.environ.get("HISTORY_WINDOW", "10"))
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]   # no default — required
```

`ANTHROPIC_API_KEY` uses `os.environ["KEY"]` (dict access, not `.get()`). This raises `KeyError` at startup if the key is missing — a deliberate **fail-fast** design. Better to crash immediately with a clear error than to run silently and fail on the first API call 10 seconds later.

### 5-2. Agent Loop Walkthrough

```python
while True:
    # OBSERVE
    values = poll_databroker()
    for path, val in values.items():
        if val is not None:
            history[path].append(round(val, 3))

    # Wait for minimum history
    if any(len(history[p]) == 0 for p in SIGNAL_PATHS):
        time.sleep(MONITOR_INTERVAL_SEC)
        continue

    # REASON
    result = call_llm(ai_client, history)
    if result is None:
        time.sleep(MONITOR_INTERVAL_SEC)
        continue

    # ACT
    if result.get("anomaly"):
        mqtt.publish(ALERT_TOPIC, json.dumps(alert), qos=0)

    time.sleep(MONITOR_INTERVAL_SEC)
```

Key design decisions:
- **`round(val, 3)`** — Limits float precision in the history to 3 decimal places. Prevents floating-point noise (`80.6900000001`) from confusing the LLM's trend analysis.
- **Early `continue` on missing data** — The ECU simulator might not be running yet. Rather than calling the LLM with empty data (which would produce nonsense), the agent waits until all signals have at least one reading.
- **`result is None` guard** — If the API call fails or JSON parse fails, skip the ACT phase entirely. The loop continues on the next cycle.

### 5-3. Rolling History Window

```python
history = {path: deque(maxlen=HISTORY_WINDOW) for path in SIGNAL_PATHS}
```

`collections.deque(maxlen=N)` is a double-ended queue with a fixed maximum length. When full, appending a new item automatically discards the oldest one — O(1) both operations.

With `HISTORY_WINDOW=10` and `MONITOR_INTERVAL_SEC=10`:
- History covers the last **100 seconds** of signal readings
- Each LLM call sees 10 data points per signal (30 numbers total)
- This is enough for the LLM to detect trends (SoC declining, Temp rising) and plateau patterns (signals frozen)

The history is passed to the LLM as:
```json
{
  "signal_history": {
    "Vehicle.Speed": [31.7, 44.6, 87.3, 116.0, 102.1, 78.4, 44.6, 18.0, 10.2, 16.4],
    "Vehicle...SoC": [81.2, 80.9, 80.5, 80.1, 79.7, 79.4, 79.1, 78.8, 78.5, 78.2],
    "Vehicle...Temp": [19.0, 19.5, 20.1, 21.0, 21.5, 22.1, 22.5, 22.1, 21.5, 21.0]
  }
}
```

### 5-4. MQTT Alert Publishing

```python
alert = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "anomaly":     result["anomaly"],
    "severity":    result.get("severity", "warning"),
    "explanation": result.get("explanation", ""),
    "signals":     result.get("signals", latest),
}
mqtt.publish(ALERT_TOPIC, json.dumps(alert), qos=0)
```

**Why QoS 0?**
- QoS 0 = fire-and-forget (no acknowledgment)
- Monitoring alerts are **informational, not safety-critical** in this simulation
- If an alert is lost, the next cycle will reassess and publish again if the anomaly persists
- QoS 1 (at-least-once delivery) would be appropriate for safety-critical alerts in production

**Alert topic:** `sdv/vehicle-001/alerts/ai`
- Follows the existing `sdv/{vehicle_id}/{path}` pattern from M2
- Subscribers can filter by vehicle ID: `sdv/+/alerts/ai` for fleet monitoring

---

## 6. Prompt Engineering for Vehicle Signals

### 6-1. System Prompt Design

The system prompt has four sections:

**1. Role definition**
```
You are an AI vehicle signal monitoring agent for a Software Defined Vehicle platform.
```
Sets the LLM's persona and domain. "Vehicle signal monitoring" primes the model's automotive knowledge.

**2. Signal semantics**
```
Signals and normal operating ranges:
- Vehicle.Speed: 0–130 km/h (simulation: sinusoidal 10–120 km/h)
- Vehicle.Powertrain.TractionBattery.StateOfCharge.Current: 20–100 %
- Vehicle.Cabin.HVAC.AmbientAirTemperature: 15–30 °C
```
Without this, the LLM would have to infer what "normal" means from the data alone. Providing expected ranges reduces hallucination and false positives.

The `(simulation: sinusoidal 10–120 km/h)` note is critical — it tells the LLM that oscillating speed is **expected**, not anomalous. Without it, the LLM might flag the sinusoidal pattern as a "speed fluctuation anomaly".

**3. Anomaly pattern examples**
```
Anomaly patterns to detect (examples — not exhaustive):
- All signals frozen (same value for 5+ consecutive readings): sensor/ECU failure
- Speed > 100 km/h with SoC dropping rapidly: high-load discharge anomaly
```
The phrase "not exhaustive" is important — it permits the LLM to detect patterns not listed. Exhaustive enumeration would make this equivalent to rule-based detection.

**4. Output schema**
```
Respond ONLY with a JSON object matching this schema — no markdown, no commentary:
{ "anomaly": <bool>, "severity": "...", ... }
```
The `ONLY` and `no markdown` constraints are essential. Without them, Claude will often wrap JSON in explanatory text or code fences. (Despite this instruction, Claude Haiku sometimes still adds code fences — see Section 7.)

### 6-2. User Turn: Signal History as JSON

Each agent cycle sends a fresh user turn with the current timestamp and rolling history:

```python
user_content = json.dumps({
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "signal_history": {path: list(history[path]) for path in SIGNAL_PATHS},
})
```

Passing history as **a JSON array of numbers** (not prose) is deliberate:
- Numbers are unambiguous — no parsing ambiguity
- The LLM can compute trends (differences, rates of change) directly from the array
- JSON format matches how the LLM would expect structured sensor data

### 6-3. Output Schema Enforcement

M5 uses **prompt-based** schema enforcement: the model is told what to output via text instructions. The alternative is **tool use** (function calling):

```python
# Alternative: tool use / function calling
response = client.messages.create(
    model="...",
    tools=[{
        "name": "report_anomaly",
        "input_schema": {
            "type": "object",
            "properties": {
                "anomaly": {"type": "boolean"},
                "severity": {"enum": ["info", "warning", "critical"]},
                ...
            }
        }
    }],
    tool_choice={"type": "tool", "name": "report_anomaly"},
    ...
)
```

Tool use enforces the schema at the protocol level — the API will reject responses that don't conform. For production use, tool use is more reliable. M5 uses prompt-based output because it's simpler to implement and sufficient for educational purposes.

---

## 7. JSON Parsing and the Code Fence Problem

### The bug

After M5 launched, the logs showed:
```
[WARNING] ai-monitor: LLM JSON parse failed: ```json
{
  "anomaly": false,
  "severity": "info",
  "explanation": "All signals nominal...
```

Claude Haiku returned the JSON wrapped in a **markdown code fence** (` ```json ... ``` `) — despite the system prompt saying `no markdown`. This is a known behavior: smaller/faster models are less reliably instruction-following than larger models.

```python
# Before fix — fails on code-fenced responses
result = json.loads(raw)   # raises json.JSONDecodeError
```

### The fix

Strip code fences before parsing:

```python
raw = response.content[0].text.strip()

# Strip markdown code fences the model sometimes adds despite instructions
if raw.startswith("```"):
    raw = raw.split("```", 2)[1]   # split on first ``` → take middle part
    if raw.startswith("json"):
        raw = raw[4:]              # remove "json" language tag
    raw = raw.strip()

result = json.loads(raw)
```

**How `split("```", 2)` works:**

```
Input:  "```json\n{...}\n```"
split("```", 2) → ["", "json\n{...}\n", ""]
[1]             → "json\n{...}\n"
[4:]            → "\n{...}\n"   (after removing "json")
strip()         → "{...}"       (clean JSON)
```

`maxsplit=2` ensures only the first two occurrences of ` ``` ` are split — leaving the JSON body intact if it happens to contain backticks.

### Lesson

**Never trust LLM output format unconditionally.** Even with explicit instructions, models may:
- Add code fences (most common)
- Add prose before/after the JSON
- Return slightly malformed JSON (trailing comma, single quotes)
- Return valid JSON with different key names than specified

Defensive parsing (strip, fallback, error logging) is essential for production LLM integrations.

---

## 8. Dashboard AI Alert Panel

### 8-1. MQTT Background Thread (paho loop_start)

The Streamlit dashboard needs to receive MQTT messages while also running its 1-second poll loop. This requires **two concurrent operations**:

1. Streamlit's `st.rerun()` main loop (single-threaded, runs in the Streamlit runtime)
2. MQTT network I/O (needs its own thread to receive messages asynchronously)

paho-mqtt's `loop_start()` launches a background daemon thread that handles all MQTT I/O:

```python
client = mqtt_client.Client(client_id="dashboard-alert-sub")
client.on_message = on_message    # callback invoked in the background thread
client.connect(MQTT_HOST, MQTT_PORT)
client.subscribe(AI_ALERT_TOPIC)
client.loop_start()               # ← starts background thread; returns immediately
```

vs `loop_forever()` which blocks:
```python
client.loop_forever()   # ← blocks here; Streamlit would freeze
```

The `on_message` callback writes to `st.session_state`:
```python
def on_message(_client, _userdata, msg):
    try:
        st.session_state.ai_alert = json.loads(msg.payload.decode())
    except Exception:
        pass
```

This is safe because Streamlit reruns the entire script on each cycle, reading `session_state` fresh each time. The background thread writes, the main thread reads on the next rerun — no lock needed.

### 8-2. st.session_state for Alert State

```python
if "ai_alert" not in st.session_state:
    st.session_state.ai_alert = None
if "mqtt_subscribed" not in st.session_state:
    st.session_state.mqtt_subscribed = False
```

`mqtt_subscribed` flag prevents re-creating the MQTT client on every Streamlit rerun (which happens every second). Without it, a new paho client would be created 60 times per minute, each starting its own background thread — a resource leak.

### 8-3. Severity Rendering

```python
severity = alert.get("severity", "info")
if severity == "critical":
    st.error(f"**CRITICAL** — {explanation}")
elif severity == "warning":
    st.warning(f"**WARNING** — {explanation}")
else:
    st.info(f"**INFO** — {explanation}")
```

Streamlit's `st.error()` / `st.warning()` / `st.info()` render colored banners:
- `st.error` → red (critical)
- `st.warning` → yellow/orange (warning)
- `st.info` → blue (info)

This gives an immediate visual severity indicator without any custom CSS.

---

## 9. Full M5 Architecture Walkthrough

Tracing a single anomaly alert from ECU to Dashboard:

```
Step 1 — ECU Simulator (WSL2, python-can)
  Speed simulation: sinusoidal function returns 0.0 km/h (bottom of sine wave)
  TX CAN 0x100 [00 00 00 00]  → vcan0

Step 2 — CAN Gateway (WSL2, kuksa-client)
  RX CAN 0x100 → Vehicle.Speed = 0.0 km/h
  gRPC SetCurrentValues({Vehicle.Speed: Datapoint(0.0)}) → Databroker :55555

Step 3 — Kuksa Databroker (Docker, host network)
  Stores Vehicle.Speed = 0.0 (overwrites previous value)
  SoC has been draining: ...78.2, 77.8, 77.3, 76.9 (declining while speed=0)

Step 4 — AI Monitor: OBSERVE (Docker, host network)
  VSSClient.get_current_values([...]) → {Speed: 0.0, SoC: 76.9, Temp: 22.8}
  Appends to history deques: Speed history now [87.3, 102.1, 0.0]

Step 5 — AI Monitor: REASON
  Builds user_content JSON with 10-reading history per signal
  POST https://api.anthropic.com/v1/messages
    model: claude-haiku-4-5-20251001
    system: SYSTEM_PROMPT (signal definitions, anomaly patterns, JSON schema)
    user: {"signal_history": {"Vehicle.Speed": [87.3, 102.1, 0.0, ...], "...SoC": [...]}}
  Response (HTTP 200, ~2s):
    {
      "anomaly": true,
      "severity": "warning",
      "explanation": "Vehicle speed dropped to zero while battery SoC continues
                      declining at ~0.7% per reading, suggesting a parasitic load
                      or HVAC running with the vehicle stationary.",
      "signals": {"Vehicle.Speed": 0.0, "...SoC": 76.9, "...Temp": 22.8}
    }

Step 6 — AI Monitor: ACT
  anomaly=true → publish to MQTT
  mqtt.publish("sdv/vehicle-001/alerts/ai", json.dumps(alert), qos=0)

Step 7 — Mosquitto (Docker, host network)
  Receives alert message, holds for subscribers

Step 8 — Dashboard: MQTT background thread
  paho on_message callback fires
  st.session_state.ai_alert = alert_dict

Step 9 — Dashboard: next Streamlit rerun (1s cycle)
  render_ai_alert() reads st.session_state.ai_alert
  Renders: st.warning("WARNING — Vehicle speed dropped to zero while...")
  Signal metrics display: Speed=0.0 km/h | SoC=76.9% | Temp=22.8°C
```

**End-to-end latency budget:**

| Step | Latency |
|---|---|
| CAN TX + Gateway gRPC | ~5 ms |
| Databroker write | ~1 ms |
| AI Monitor poll cycle | 0 – 10 s (depends on cycle position) |
| Claude API call | ~1–2 s |
| MQTT publish + deliver | ~1 ms |
| Dashboard rerun pickup | 0 – 1 s |
| **Total worst case** | **~13 s** |

---

## 10. LLM vs Rule-Based Anomaly Detection

| Property | Rule-Based (threshold) | LLM-Based (M5) |
|---|---|---|
| **Detection of known patterns** | ✅ Fast, deterministic | ✅ Can detect (but slower) |
| **Detection of unknown patterns** | ❌ Only what was programmed | ✅ Generalizes from training |
| **Cross-signal correlation** | ❌ Requires explicit rule per combination | ✅ Automatic (prompt gives all signals) |
| **Trend analysis** | ⚠️ Possible but requires sliding-window code | ✅ Built-in (history array in prompt) |
| **Explanation** | ❌ "Threshold exceeded" only | ✅ Natural-language explanation |
| **Latency** | ~1 ms | ~1–2 s (API call) |
| **Cost** | Zero (local code) | API cost per call |
| **Determinism** | ✅ Same input → same output | ❌ LLM outputs vary slightly |
| **Auditability** | ✅ Rule is explicit in code | ⚠️ Reasoning is opaque (LLM black box) |
| **False positive rate** | Low (tuned thresholds) | Medium (prompt-dependent) |

**Production reality:** Modern SDV monitoring uses **both**:
- Rule-based for hard safety limits (speed > 200 km/h → immediate alert, no API call)
- LLM-based for soft pattern detection, root-cause explanation, operator guidance

---

## 11. Docker Compose Changes in M5

### New service: ai-monitor

```yaml
ai-monitor:
  build:
    context: ./services/ai-monitor
    dockerfile: Dockerfile
  environment:
    DATABROKER_HOST: localhost
    DATABROKER_PORT: "55555"
    MQTT_HOST: localhost
    MQTT_PORT: "1883"
    VEHICLE_ID: "vehicle-001"
    MONITOR_INTERVAL_SEC: "10"
    HISTORY_WINDOW: "10"
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}   # from host env or .env
  depends_on:
    - databroker
    - mosquitto
  network_mode: host
  restart: on-failure
```

**Why `network_mode: host`?**
- Consistent with all M4 services (ip_tables.ko unavailable on custom kernel)
- Needs to reach both `localhost:55555` (Databroker) and `localhost:1883` (Mosquitto)

**`${ANTHROPIC_API_KEY}` syntax:**
Docker Compose reads this from:
1. The shell environment (`export ANTHROPIC_API_KEY=...`)
2. A `.env` file in the project root

If neither is set, Docker Compose warns and passes an empty string — which causes the `KeyError` in `main.py` and a clean startup failure.

### Dashboard changes

`services/dashboard/requirements.txt`:
```
paho-mqtt==1.6.1   ← added for AI alert MQTT subscription
```

`services/dashboard/main.py` additions:
- `init_mqtt_alert_listener()` — creates paho client, subscribes to alerts topic, calls `loop_start()`
- `render_ai_alert()` — renders the alert panel below the signal charts
- `st.session_state.ai_alert` and `mqtt_subscribed` — new session state keys

---

## 12. Security: API Key Handling

### The threat: API key leakage

An Anthropic API key grants access to the Claude API on your behalf. If leaked (committed to git, logged, or included in a Docker image), attackers can make API calls that are billed to your account.

### M5 protection layers

**Layer 1: Never hardcode**
```python
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]   # read from environment
```
The key never appears in source code.

**Layer 2: .gitignore**
```
# .gitignore
.env
.env.*
!.env.example
```
`.env` files (which hold the key) are excluded from git. `.env.example` (which contains only the key name, not the value) is committed as documentation.

**Layer 3: Docker Compose variable substitution**
```yaml
ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
```
Docker Compose reads the value from the host environment at runtime — never baked into the image.

**Layer 4: Docker image contains no secret**
The Dockerfile copies only `requirements.txt` and `main.py`. The API key is injected at container start via the environment, not during build. Running `docker image inspect` reveals no key.

### What M5 does NOT protect against

- Secrets in `docker inspect` of a running container (`docker inspect <container>` shows env vars)
- Container logs that accidentally print the key
- Host environment exposure

For production: use Docker secrets, AWS Secrets Manager, or Vault — not environment variables.

---

## 13. Known Constraints and Trade-offs

| ID | Constraint | Impact | Mitigation |
|---|---|---|---|
| CON-50 | Requires `ANTHROPIC_API_KEY` | Agent won't start without it | Fail-fast KeyError at startup |
| CON-51 | Requires internet access from WSL2 | LLM calls fail if WSL2 has no outbound HTTPS | nftables masquerade (setup-wsl2.sh) provides NAT |
| CON-52 | LLM latency ~1–2s per cycle | Anomaly detection latency ≤ ~13s worst case | Acceptable for monitoring; not for safety-critical control |
| CON-53 | LLM output non-deterministic | Same signals may produce slightly different explanations | Parse `anomaly` bool (deterministic enough); treat explanation as advisory |
| CON-54 | paho-mqtt pinned at 1.6.1 | paho 2.x has breaking API changes (`on_message` signature changed) | Pin explicitly; test before upgrading |
| CON-55 | Prompt-based JSON vs tool use | Model occasionally adds code fences (see Section 7) | Strip code fences before parse |
| CON-56 | No alert deduplication | If anomaly persists, alert published every 10s | Downstream consumers should deduplicate by timestamp |

---

## 14. M5 in the Context of Real SDV AI Layers

Modern production SDV platforms have multiple AI layers:

```
┌─────────────────────────────────────────────────────────┐
│            Vehicle AI Architecture                      │
│                                                         │
│  L4: Fleet AI (Cloud)                                   │
│    • Cross-vehicle anomaly pattern learning             │
│    • OTA model updates                                  │
│    • Long-term trend analysis (weeks, months)           │
│                          ↑ MQTT / HTTPS                 │
│  L3: In-Vehicle Cloud Agent  ← M5 approximates this    │
│    • LLM-based signal monitoring                        │
│    • Natural-language alerts                            │
│    • Operator-facing explanations                       │
│                          ↑ VSS / Databroker             │
│  L2: ADAS / AD Stack (ROS2)    ← M3                    │
│    • Perception, planning, control                      │
│    • Rule-based + ML safety monitors                    │
│                          ↑ DDS                          │
│  L1: Vehicle Signal Layer      ← M1–M4                 │
│    • Kuksa Databroker (VSS)                             │
│    • CAN Gateway, ECU signals                           │
└─────────────────────────────────────────────────────────┘
```

**M5 maps to L3** — an in-vehicle or cloud-side AI agent that:
- Reads structured VSS signals (not raw camera/LiDAR data)
- Applies LLM reasoning (not deterministic control algorithms)
- Produces human-readable outputs (not actuator commands)

Real-world equivalents:
- **Mercedes MBUX Hyperscreen AI Assistant** — uses vehicle signal context to provide natural-language guidance
- **VW CARIAD AI Safety Monitor** — cloud-side LLM analyzes fleet anomalies
- **BMW Intelligent Personal Assistant** — queries VSS-equivalent signals for context-aware responses

M5's architecture — VSS signals → LLM → alert → dashboard — is the same pattern at educational scale.

---

## 15. Review Quiz

**Q1.** The Observe→Reason→Act loop polls the Databroker every 10 seconds instead of subscribing to changes. What is the main reason?

> **A:** Cost and history accumulation. Event-driven would trigger LLM calls at 3 Hz (one per CAN signal update), costing ~10,800 API calls/hour vs ~360/hour for polling. Polling also accumulates history naturally — each 10-second reading adds one point to the window used for trend analysis.

---

**Q2.** Why does `ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]` use dict access instead of `.get()`?

> **A:** Dict access raises `KeyError` immediately at startup if the key is missing (fail-fast). `.get()` would return `None`, which would only fail when the first API call is made 10 seconds later — harder to diagnose. Fail-fast is the correct behavior for required configuration.

---

**Q3.** The system prompt includes `(simulation: sinusoidal 10–120 km/h)` in the Speed signal description. Why?

> **A:** Without it, the LLM might classify the sinusoidal speed oscillation as an anomaly ("speed fluctuating erratically"). The note tells the LLM that oscillation is expected behavior for this simulation, preventing false positives on normal operation.

---

**Q4.** What is the difference between `paho loop_start()` and `paho loop_forever()`? Which does M5 use and why?

> **A:** `loop_start()` launches a background daemon thread for MQTT I/O and returns immediately. `loop_forever()` blocks the calling thread forever. M5 uses `loop_start()` because the dashboard's main thread must continue running the Streamlit rerun loop — blocking it would freeze the dashboard UI.

---

**Q5.** Claude Haiku returned JSON wrapped in ` ```json ``` ` code fences despite the system prompt saying "no markdown". How does M5 fix this?

> **A:** Strip code fences before `json.loads()`:
> ```python
> if raw.startswith("```"):
>     raw = raw.split("```", 2)[1]
>     if raw.startswith("json"):
>         raw = raw[4:]
>     raw = raw.strip()
> ```
> The `maxsplit=2` argument ensures only the outer fences are split, leaving the JSON body intact.

---

**Q6.** Why is the alert published with `qos=0` (fire-and-forget) rather than `qos=1` (at-least-once)?

> **A:** Monitoring alerts in this simulation are informational, not safety-critical. If one alert is lost, the next 10-second cycle will reassess and re-publish if the anomaly persists. QoS 0 is lower overhead and sufficient here. QoS 1 would be appropriate for safety-critical alerts in production.

---

**Q7.** The `mqtt_subscribed` flag in `st.session_state` prevents re-creating the MQTT client on each Streamlit rerun. What would happen without this flag?

> **A:** A new paho client would be created every second (Streamlit reruns at 1 Hz). Each client calls `loop_start()`, launching a new background thread. After 60 seconds, there would be 60 background threads all subscribed to the same topic — a resource leak that would eventually exhaust system resources.

---

**Q8.** What is the key architectural difference between prompt-based structured output (M5) and tool use / function calling?

> **A:** Prompt-based: the model is instructed via text to return a specific JSON format; compliance is not guaranteed at the protocol level (the model may deviate). Tool use: the API enforces a JSON schema at the protocol level — the model must invoke the specified tool with conforming arguments or the API rejects the response. Tool use is more reliable for production; prompt-based is simpler to implement.

---

**Q9.** A rule-based monitor would flag `Speed > 130 km/h` as an anomaly. Give an example of an anomaly pattern that rule-based monitoring cannot detect but the LLM-based approach can.

> **A:** Speed = 0 AND SoC declining at the same rate as when driving → parasitic drain while parked. This requires correlating two signals over time — no single threshold captures it. The LLM sees the history array and reasons: "Speed has been zero for 5 readings while SoC continues declining at 0.7%/reading, suggesting a load is active without the vehicle moving."

---

**Q10.** Where in the Docker Compose file is the API key injected, and why is it NOT baked into the Docker image at build time?

> **A:** `ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}` — injected at container runtime from the host environment. Not baked in because Dockerfiles are often committed to version control, pushed to registries, and inspected by third parties. A secret baked into a layer would be extractable by anyone with access to the image. Runtime injection via environment variables means the secret only exists in the running container's process environment.

---

**Q11.** The history deque uses `maxlen=HISTORY_WINDOW` and values are rounded to 3 decimal places before appending. Why round?

> **A:** Floating-point arithmetic produces values like `80.6900000001` due to IEEE 754 representation. Including many such values in the LLM prompt wastes tokens and may confuse trend analysis (the LLM sees noise as meaningful precision). Rounding to 3 decimal places (`round(val, 3)`) keeps the data clean and compact without losing physically meaningful precision (0.001 km/h, %, or °C is below sensor accuracy anyway).

---

**Q12.** M5's ai-monitor is described as "read-only" (DR-50). What would happen architecturally if the agent were allowed to call `set_current_values()` on the Databroker?

> **A:** The agent would become an **actuator** — it could override ECU-reported signal values with LLM-generated ones. This would corrupt the Databroker's state (other consumers like the MQTT bridge and dashboard would receive LLM-generated values instead of real ECU values), break the single-writer invariant (only the CAN Gateway should write), and introduce a feedback loop where the AI reads its own writes on the next cycle. Read-only monitoring is the correct pattern for an observer that must not influence the system it observes.
