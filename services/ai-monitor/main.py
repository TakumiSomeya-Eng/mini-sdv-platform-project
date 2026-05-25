#!/usr/bin/env python3
"""
AI Signal Monitoring Agent — mini-sdv-platform  Milestone 5
============================================================
Observe → Reason → Act loop:
  OBSERVE  Poll Kuksa Databroker for latest vehicle signal values.
  REASON   Send signal history to Claude API; receive structured anomaly assessment.
  ACT      Publish JSON alert to Mosquitto if anomaly detected.

SDV Concept:
  This agent represents the "AI layer" increasingly present in modern SDV platforms.
  Rather than hard-coded threshold rules, an LLM reasons over multi-signal context
  and trend history, then produces a natural-language explanation alongside a
  machine-readable severity classification.

  The architectural pattern (read Databroker → call AI → publish alert) mirrors
  how OEM cloud safety monitors and in-vehicle AI assistants consume VSS signals.
"""

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import anthropic
import paho.mqtt.client as mqtt_client
from kuksa_client.grpc import VSSClient

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ai-monitor")

# ── Configuration ─────────────────────────────────────────────────────────────
DATABROKER_HOST      = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT      = int(os.environ.get("DATABROKER_PORT", "55555"))
MQTT_HOST            = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT            = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID           = os.environ.get("VEHICLE_ID", "vehicle-001")
MONITOR_INTERVAL_SEC = float(os.environ.get("MONITOR_INTERVAL_SEC", "10"))
HISTORY_WINDOW       = int(os.environ.get("HISTORY_WINDOW", "10"))
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]

ALERT_TOPIC = f"sdv/{VEHICLE_ID}/alerts/ai"

SIGNAL_PATHS = [
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",
    "Vehicle.Cabin.HVAC.AmbientAirTemperature",
]

# ── LLM System Prompt ─────────────────────────────────────────────────────────
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
- Speed > 100 km/h with SoC dropping rapidly (>2 % per reading): high-load discharge anomaly
- Cabin Temp consistently rising above 25 °C: possible HVAC failure
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
""".strip()


# ── Databroker Poll ───────────────────────────────────────────────────────────

def poll_databroker() -> dict[str, float | None]:
    values: dict[str, float | None] = {path: None for path in SIGNAL_PATHS}
    try:
        with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
            response = client.get_current_values(SIGNAL_PATHS)
        for path in SIGNAL_PATHS:
            dp = response.get(path)
            if dp is not None and dp.value is not None:
                values[path] = float(dp.value)
    except Exception as exc:
        log.warning(f"Databroker poll failed: {exc}")
    return values


# ── MQTT Connection ───────────────────────────────────────────────────────────

def connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(client_id="ai-monitor")
    retry_delay = 2.0
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT)
            client.loop_start()
            log.info(f"MQTT connected → {MQTT_HOST}:{MQTT_PORT}")
            return client
        except Exception as exc:
            log.warning(f"MQTT connect failed: {exc}. Retrying in {retry_delay:.0f}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)


# ── Claude API Call ───────────────────────────────────────────────────────────

def call_llm(client: anthropic.Anthropic, history: dict) -> dict | None:
    user_content = json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_history": {path: list(history[path]) for path in SIGNAL_PATHS},
    })
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences the model sometimes adds despite instructions
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning(f"LLM JSON parse failed: {raw[:120]}")
        return None
    except Exception as exc:
        log.warning(f"Claude API call failed: {exc}")
        return None


# ── Main Agent Loop ───────────────────────────────────────────────────────────

def run() -> None:
    log.info("AI Signal Monitor starting...")
    log.info(f"  Databroker: {DATABROKER_HOST}:{DATABROKER_PORT}")
    log.info(f"  MQTT:       {MQTT_HOST}:{MQTT_PORT}  topic={ALERT_TOPIC}")
    log.info(f"  Interval:   {MONITOR_INTERVAL_SEC}s  |  History: {HISTORY_WINDOW} readings")

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    mqtt       = connect_mqtt()
    history    = {path: deque(maxlen=HISTORY_WINDOW) for path in SIGNAL_PATHS}

    while True:
        # ── OBSERVE ─────────────────────────────────────────────────────
        log.info("[OBSERVE] Polling Databroker for current signal values...")
        values = poll_databroker()

        for path, val in values.items():
            if val is not None:
                history[path].append(round(val, 3))

        has_data = all(len(history[p]) > 0 for p in SIGNAL_PATHS)
        if not has_data:
            log.info("[OBSERVE] Waiting for signal data — ECU simulator not running yet?")
            time.sleep(MONITOR_INTERVAL_SEC)
            continue

        latest = {p: history[p][-1] for p in SIGNAL_PATHS}
        log.info(
            f"[OBSERVE] Speed={latest['Vehicle.Speed']} km/h | "
            f"SoC={latest['Vehicle.Powertrain.TractionBattery.StateOfCharge.Current']} % | "
            f"Temp={latest['Vehicle.Cabin.HVAC.AmbientAirTemperature']} °C"
        )

        # ── REASON ──────────────────────────────────────────────────────
        log.info("[REASON] Sending signal history to Claude API...")
        result = call_llm(ai_client, history)

        if result is None:
            time.sleep(MONITOR_INTERVAL_SEC)
            continue

        log.info(
            f"[REASON] anomaly={result.get('anomaly')} "
            f"severity={result.get('severity')} — {result.get('explanation', '')}"
        )

        # ── ACT ─────────────────────────────────────────────────────────
        if result.get("anomaly"):
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "anomaly":     result["anomaly"],
                "severity":    result.get("severity", "warning"),
                "explanation": result.get("explanation", ""),
                "signals":     result.get("signals", latest),
            }
            payload = json.dumps(alert)
            mqtt.publish(ALERT_TOPIC, payload, qos=0)
            log.warning(
                f"[ACT] ALERT published → {alert['severity'].upper()}: {alert['explanation']}"
            )
        else:
            log.info("[ACT] No anomaly — no MQTT publish.")

        time.sleep(MONITOR_INTERVAL_SEC)


if __name__ == "__main__":
    run()
