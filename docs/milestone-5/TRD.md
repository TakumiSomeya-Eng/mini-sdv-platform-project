# Technical Requirements Document (TRD)
## Milestone 5: AI Signal Monitoring Agent
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | TRD |
| Milestone | 5 — AI Signal Monitoring Agent |
| Status | Draft |
| Hypothesis Layer | Implementation (L5) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | [FRD.md](FRD.md) |

---

## 1. Implementation Hypothesis (L5)

> A Python service using the `anthropic` SDK can be integrated as a new Docker Compose service (`ai-monitor`) that polls the Kuksa Databroker every 10 seconds, constructs a structured prompt from signal history, and publishes JSON alerts to Mosquitto when the LLM detects an anomaly — requiring zero changes to any M1–M4 service.

---

## 2. Technology Decisions

| Component | Choice | Rationale |
|---|---|---|
| LLM API | `anthropic` SDK (`claude-haiku-4-5-20251001`) | Fastest Haiku model; low cost per call; structured JSON output reliable |
| Databroker client | `kuksa-client==0.4.3` | Consistent with all other services |
| MQTT client | `paho-mqtt==1.6.1` | Consistent with mqtt-bridge; avoids paho 2.x breaking changes |
| Base image | `python:3.11-slim` | Consistent with M1–M4 services |
| Docker network | `network_mode: host` | Reaches both Databroker (localhost:55555) and Mosquitto (localhost:1883) |
| Response parsing | `json.loads()` on raw response text | Haiku reliably returns JSON when schema is in system prompt |

---

## 3. File Changes

### New Files

```
services/ai-monitor/
├── Dockerfile
├── main.py
└── requirements.txt
```

### Modified Files

```
services/dashboard/main.py   ← add AI Alert panel (FR-52)
docker-compose.yml            ← add ai-monitor service (FR-53)
README.md                     ← M5 architecture + quick start
```

---

## 4. services/ai-monitor/requirements.txt

```
anthropic>=0.40.0
kuksa-client==0.4.3
paho-mqtt==1.6.1
```

---

## 5. services/ai-monitor/Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["python", "-u", "main.py"]
```

---

## 6. services/ai-monitor/main.py — Implementation Plan

### 6.1 Configuration

```python
DATABROKER_HOST     = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT     = int(os.environ.get("DATABROKER_PORT", "55555"))
MQTT_HOST           = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT           = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID          = os.environ.get("VEHICLE_ID", "vehicle-001")
MONITOR_INTERVAL_SEC = float(os.environ.get("MONITOR_INTERVAL_SEC", "10"))
HISTORY_WINDOW      = int(os.environ.get("HISTORY_WINDOW", "10"))
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]   # required — no default

ALERT_TOPIC = f"sdv/{VEHICLE_ID}/alerts/ai"

SIGNAL_PATHS = [
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",
    "Vehicle.Cabin.HVAC.AmbientAirTemperature",
]
```

### 6.2 System Prompt (FR-51)

```python
SYSTEM_PROMPT = """
You are an AI vehicle signal monitoring agent for a Software Defined Vehicle platform.
You receive a rolling history of vehicle sensor readings and assess whether the current
state represents a normal operating condition or an anomaly.

Signals and normal operating ranges:
- Vehicle.Speed: 0–130 km/h (simulation: sinusoidal 10–120 km/h)
- Vehicle.Powertrain.TractionBattery.StateOfCharge.Current: 20–100 % (simulation: 55–85 %, slow drain)
- Vehicle.Cabin.HVAC.AmbientAirTemperature: 15–30 °C (simulation: sinusoidal 19.5–24.5 °C)

Anomaly patterns to detect (examples — not exhaustive):
- All signals frozen (same value for 5+ consecutive readings): sensor/ECU failure
- Speed > 100 km/h with SoC dropping rapidly (>2%/reading): high-load discharge anomaly
- Cabin Temp consistently rising above 25 °C: HVAC failure
- Speed = 0 with SoC declining: parasitic drain while parked

Respond ONLY with a JSON object matching this schema — no markdown, no commentary:
{
  "anomaly": <bool>,
  "severity": "<info|warning|critical>",
  "explanation": "<1-3 sentences, plain English>",
  "signals": {
    "Vehicle.Speed": <latest float>,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": <latest float>,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": <latest float>
  }
}
If no anomaly is detected, set anomaly=false, severity="info", explanation="All signals nominal."
"""
```

### 6.3 Agent Loop

```python
def run():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    mqtt = connect_mqtt()  # paho with reconnect loop
    history = {path: deque(maxlen=HISTORY_WINDOW) for path in SIGNAL_PATHS}

    while True:
        # ── OBSERVE ─────────────────────────────────────────────────────
        log.info("[OBSERVE] Polling Databroker...")
        values = poll_databroker()           # get_current_values() → dict
        for path, val in values.items():
            if val is not None:
                history[path].append(round(val, 3))

        if any(len(h) == 0 for h in history.values()):
            log.info("Waiting for signal history to fill...")
            time.sleep(MONITOR_INTERVAL_SEC)
            continue

        # ── REASON ──────────────────────────────────────────────────────
        log.info("[REASON] Calling Claude API...")
        user_content = json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "signal_history": {path: list(history[path]) for path in SIGNAL_PATHS},
        })
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"[REASON] JSON parse failed: {raw[:120]}")
            time.sleep(MONITOR_INTERVAL_SEC)
            continue

        # ── ACT ─────────────────────────────────────────────────────────
        if result.get("anomaly"):
            alert = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                **result,
            }
            payload = json.dumps(alert)
            mqtt.publish(ALERT_TOPIC, payload, qos=0)
            log.warning(f"[ACT] ALERT published → {result['severity'].upper()}: "
                        f"{result['explanation']}")
        else:
            log.info(f"[ACT] OK — {result.get('explanation', 'All signals nominal.')}")

        time.sleep(MONITOR_INTERVAL_SEC)
```

---

## 7. Dashboard Changes (FR-52)

### 7.1 MQTT subscription for AI alerts

```python
import paho.mqtt.client as mqtt_client

def init_mqtt_listener():
    """Subscribe to AI alert topic; store latest alert in session_state."""
    if "ai_alert" not in st.session_state:
        st.session_state.ai_alert = None
    if "mqtt_client" not in st.session_state:
        client = mqtt_client.Client()
        def on_message(c, userdata, msg):
            try:
                st.session_state.ai_alert = json.loads(msg.payload.decode())
            except Exception:
                pass
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.subscribe(f"sdv/{VEHICLE_ID}/alerts/ai")
        client.loop_start()
        st.session_state.mqtt_client = client
```

### 7.2 render_ai_alert()

```python
def render_ai_alert() -> None:
    st.subheader("AI Signal Monitor")
    alert = st.session_state.get("ai_alert")
    if alert is None:
        st.info("No anomaly detected.")
        return
    severity = alert.get("severity", "info")
    color_fn = {"info": st.info, "warning": st.warning, "critical": st.error}
    color_fn.get(severity, st.info)(
        f"**{severity.upper()}** — {alert.get('explanation', '')}"
    )
    st.caption(f"Timestamp: {alert.get('timestamp', '—')}")
```

---

## 8. docker-compose.yml Changes

```yaml
ai-monitor:
  build:
    context: ./services/ai-monitor
  network_mode: host
  environment:
    DATABROKER_HOST: localhost
    DATABROKER_PORT: "55555"
    MQTT_HOST: localhost
    MQTT_PORT: "1883"
    VEHICLE_ID: vehicle-001
    MONITOR_INTERVAL_SEC: "10"
    HISTORY_WINDOW: "10"
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}   # from host environment or .env
  depends_on:
    - databroker
    - mosquitto
  restart: on-failure
```

---

## 9. Environment Variable Setup

```bash
# WSL2 terminal — set before docker compose up
export ANTHROPIC_API_KEY="sk-ant-..."

# Or create .env in project root (add .env to .gitignore):
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

`.env` MUST be added to `.gitignore` to prevent API key leakage.

---

## 10. Constraints

| ID | Constraint |
|---|---|
| CON-50 | `ANTHROPIC_API_KEY` must be set in environment before `docker compose up` |
| CON-51 | Claude API requires HTTPS access from WSL2 — outbound internet must be available |
| CON-52 | `paho-mqtt==1.6.1` pinned — paho 2.x has breaking API changes |
| CON-53 | Dashboard MQTT loop runs in background thread — must use `loop_start()`, not `loop_forever()` |
| CON-54 | `ai-monitor` is read-only; adding Databroker write calls violates DR-50 |

---

## 11. Quick Test (Post-Implementation)

```bash
# 1. Set API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 2. Start all services (including ai-monitor)
docker compose up -d

# 3. Watch ai-monitor logs (observe → reason → act loop)
docker compose logs -f ai-monitor
# Expected every 10s:
# [OBSERVE] Polling Databroker...
# [REASON] Calling Claude API...
# [ACT] OK — All signals nominal.

# 4. Subscribe to AI alerts
mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/alerts/ai" -v

# 5. Open dashboard
# → http://localhost:8501  (AI Signal Monitor panel at bottom)
```
