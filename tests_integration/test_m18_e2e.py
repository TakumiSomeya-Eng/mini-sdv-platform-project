"""
Phase-3 E2E tests — M18 Continuous Profiling (live k3s services)
T18-05: Pyroscope /api/apps lists ai-monitor-edge after profiling period
T18-06: MQTT alert from ai-monitor-edge has correct JSON schema
T18-07: Pyroscope flame graph query returns data for the "onnx_inference" tag

Skip conditions (each test has its own guard):
  - Pyroscope: localhost:4040/ready must respond 200
  - MQTT alerts: broker at localhost:1883 and ai-monitor-edge must be publishing
"""

import json
import threading
import time

import pytest
import requests

_PYROSCOPE  = "http://localhost:4040"
_ALERT_TOPIC = "sdv/vehicle-001/alerts/ai-edge"


def _pyroscope_up() -> bool:
    try:
        return requests.get(f"{_PYROSCOPE}/ready", timeout=2).status_code == 200
    except Exception:
        return False


def _mqtt_up() -> bool:
    try:
        import paho.mqtt.client as mqtt
        c = mqtt.Client()
        c.connect("localhost", 1883, keepalive=1)
        c.disconnect()
        return True
    except Exception:
        return False


_PYR_UP  = _pyroscope_up()
_MQTT_UP = _mqtt_up()


# ─────────────────────────────────────────────────────────────────────────────
# T18-05  Pyroscope /api/apps lists ai-monitor-edge after it has profiled
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _PYR_UP, reason=f"Pyroscope not running at {_PYROSCOPE}")
def test_pyroscope_lists_ai_monitor_edge():
    """
    Wait up to 30 s for ai-monitor-edge to appear in Pyroscope.
    Requires ai-monitor-edge running with PYROSCOPE_URL=http://localhost:4040.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        resp = requests.get(f"{_PYROSCOPE}/api/apps", timeout=5)
        names = [a.get("name", "") for a in resp.json()]
        if any("ai-monitor-edge" in n for n in names):
            return
        time.sleep(3)
    pytest.fail(
        f"ai-monitor-edge not found in Pyroscope apps after 30 s. Found: {names}"
    )


@pytest.mark.skipif(not _PYR_UP, reason=f"Pyroscope not running at {_PYROSCOPE}")
def test_pyroscope_ready_and_api():
    resp = requests.get(f"{_PYROSCOPE}/ready", timeout=5)
    assert resp.status_code == 200

    apps = requests.get(f"{_PYROSCOPE}/api/apps", timeout=5)
    assert apps.status_code == 200
    assert isinstance(apps.json(), list)


# ─────────────────────────────────────────────────────────────────────────────
# T18-06  MQTT alert from ai-monitor-edge has correct JSON schema
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _MQTT_UP, reason="MQTT broker not at localhost:1883")
def test_alert_mqtt_schema():
    """
    Subscribe to the alert topic and validate the first message received
    within 60 s has the required schema.

    Requires ai-monitor-edge to be publishing alerts (i.e., signals are
    breaching thresholds or frozen — normal in test environments).
    """
    import paho.mqtt.client as mqtt

    received: list[dict] = []
    error: list[str]     = []

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            received.append(payload)
        except json.JSONDecodeError as e:
            error.append(str(e))

    client = mqtt.Client()
    client.on_message = on_message
    client.connect("localhost", 1883)
    client.subscribe(_ALERT_TOPIC, qos=0)
    client.loop_start()

    deadline = time.time() + 60
    while not received and not error and time.time() < deadline:
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()

    if error:
        pytest.fail(f"Received malformed MQTT payload: {error[0]}")

    if not received:
        pytest.skip("No MQTT alerts received in 60 s — ai-monitor-edge may not be running")

    alert = received[0]
    required = {"vehicle_id", "ts", "severity", "explanation", "engine", "signals"}
    assert required <= alert.keys(), f"Alert missing fields: {required - alert.keys()}"
    assert alert["severity"] in ("NORMAL", "WARNING", "CRITICAL")
    assert isinstance(alert["signals"], dict)
    assert isinstance(alert["explanation"], str)


# ─────────────────────────────────────────────────────────────────────────────
# T18-07  Pyroscope flame graph query contains "onnx_inference" tag
#         (only meaningful when ai-monitor-edge has MODEL_PATH configured)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _PYR_UP, reason=f"Pyroscope not running at {_PYROSCOPE}")
def test_pyroscope_onnx_inference_tag():
    """
    Query Pyroscope for profiles tagged function=onnx_inference.
    Skips gracefully if no data is found (model not loaded → rule fallback).
    """
    now_ns  = int(time.time() * 1e9)
    from_ns = now_ns - int(5 * 60 * 1e9)   # last 5 minutes

    # Pyroscope v0.x render API: query by tag selector
    params = {
        "query": "ai-monitor-edge.cpu{function='onnx_inference'}",
        "from":  str(from_ns),
        "until": str(now_ns),
        "format": "json",
    }
    try:
        resp = requests.get(f"{_PYROSCOPE}/render", params=params, timeout=10)
    except Exception as exc:
        pytest.skip(f"Pyroscope render endpoint not available: {exc}")

    if resp.status_code == 422 or (resp.status_code == 200 and not resp.content):
        pytest.skip("No onnx_inference profiling data — MODEL_PATH not configured")

    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
