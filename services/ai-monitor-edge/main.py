#!/usr/bin/env python3
"""
AI Monitor Edge — mini-sdv-platform  Milestone 17
==================================================
Replaces the Claude Haiku (cloud API) ai-monitor with a fully local
inference stack:

  Phi-4-mini (Microsoft, MIT) + ONNX Runtime (MIT)
  → CPU inference on WSL2 Surface, no GPU, no API cost

Model loading strategy:
  1. If MODEL_PATH points to an ONNX model directory:
     - Load via optimum.onnxruntime.ORTModelForCausalLM
     - Tokenize with the bundled AutoTokenizer
     - Run generation on CPU (INT4 ONNX, ~2 GB, ~3–8 s/inference)
  2. Otherwise: rule-based detection (same thresholds, zero latency)
     This keeps the service functional during the model bootstrap phase.

M18 Pyroscope hook:
  If PYROSCOPE_URL is set, every inference is wrapped in a pyroscope
  span so CPU flamegraphs appear in Grafana Pyroscope.

MQTT output format is identical to the original ai-monitor (M5) so
existing Grafana alert rules require no changes.

SDV Concept:
  Edge AI replaces cloud round-trips for safety-critical detection.
  Phi-4-mini INT4 fits in ~4 GB RAM, suitable for an automotive-grade
  central compute unit (e.g. NVIDIA Orin NX 8 GB).
"""

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as mqtt_client
from kuksa_client.grpc import VSSClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ai-monitor-edge")

DATABROKER_HOST      = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT      = int(os.environ.get("DATABROKER_PORT", "55555"))
MQTT_HOST            = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT            = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID           = os.environ.get("VEHICLE_ID", "vehicle-001")
MQTT_TLS             = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT         = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT     = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY      = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
OTEL_ENABLED         = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
OTEL_ENDPOINT        = os.environ.get("OTEL_ENDPOINT", "http://localhost:4318/v1/traces")
MONITOR_INTERVAL_SEC = float(os.environ.get("MONITOR_INTERVAL_SEC", "10"))
HISTORY_WINDOW       = int(os.environ.get("HISTORY_WINDOW", "10"))
MODEL_PATH           = os.environ.get("MODEL_PATH", "/models/phi4-mini-onnx")
PYROSCOPE_URL        = os.environ.get("PYROSCOPE_URL", "")
MAX_NEW_TOKENS       = int(os.environ.get("MAX_NEW_TOKENS", "200"))

ALERT_TOPIC = f"sdv/{VEHICLE_ID}/alerts/ai-edge"
SIGNAL_PATHS = [
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",
    "Vehicle.Cabin.HVAC.AmbientAirTemperature",
]

SYSTEM_PROMPT = (
    "You are an AI vehicle signal monitoring agent for a Software Defined Vehicle. "
    "Analyze the rolling sensor history and respond with a JSON object containing: "
    "severity (NORMAL/WARNING/CRITICAL), anomaly (bool), explanation (str). "
    "Normal ranges: Speed 0–130 km/h, SoC 20–100%, CabinTemp 15–30°C."
)

# Rule-based thresholds (fallback when ONNX model is absent)
_RULES = {
    "Vehicle.Speed":                                          (0, 130),
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": (20, 100),
    "Vehicle.Cabin.HVAC.AmbientAirTemperature":              (15, 30),
}


# ── Pyroscope (optional, M18) ─────────────────────────────────────────────────

def _setup_pyroscope():
    if not PYROSCOPE_URL:
        return
    try:
        import pyroscope
        pyroscope.configure(
            application_name="ai-monitor-edge",
            server_address=PYROSCOPE_URL,
            tags={"vehicle_id": VEHICLE_ID},
        )
        log.info(f"Pyroscope profiling → {PYROSCOPE_URL}")
    except ImportError:
        log.warning("pyroscope-io not installed; profiling disabled")


# ── OTel tracing ──────────────────────────────────────────────────────────────

def _setup_otel():
    if not OTEL_ENABLED:
        return trace.get_tracer("ai-monitor-edge")
    resource = Resource({"service.name": "ai-monitor-edge", "vehicle.id": VEHICLE_ID})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT))
    )
    trace.set_tracer_provider(provider)
    log.info(f"OTel tracing → {OTEL_ENDPOINT}")
    return trace.get_tracer("ai-monitor-edge")


# ── ONNX model (Phi-4-mini) ───────────────────────────────────────────────────

_model = None
_tokenizer = None


def _load_onnx_model():
    global _model, _tokenizer
    if not os.path.isdir(MODEL_PATH):
        log.warning(f"MODEL_PATH {MODEL_PATH!r} not found — using rule-based fallback")
        return
    try:
        from optimum.onnxruntime import ORTModelForCausalLM
        from transformers import AutoTokenizer
        log.info(f"Loading Phi-4-mini ONNX from {MODEL_PATH} …")
        t0 = time.time()
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
        _model = ORTModelForCausalLM.from_pretrained(MODEL_PATH)
        log.info(f"Model loaded in {time.time()-t0:.1f} s")
    except Exception as exc:
        log.error(f"ONNX model load failed: {exc} — using rule-based fallback")


def _infer_onnx(history: list[dict]) -> dict:
    """Run Phi-4-mini ONNX inference and parse JSON output."""
    prompt = (
        f"<|system|>{SYSTEM_PROMPT}<|end|>"
        f"<|user|>Signal history (last {len(history)} readings):\n"
        + json.dumps(history, indent=2)
        + "\nRespond with JSON only.<|end|><|assistant|>"
    )
    try:
        import pyroscope
        ctx = pyroscope.tag_wrapper({"function": "onnx_inference"})
    except ImportError:
        from contextlib import nullcontext as ctx

    with ctx:
        inputs = _tokenizer(prompt, return_tensors="pt")
        out = _model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        decoded = _tokenizer.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)

    decoded = decoded.strip()
    # Extract JSON block if wrapped in markdown
    if "```" in decoded:
        decoded = decoded.split("```")[1].lstrip("json").strip()
    result = json.loads(decoded)
    return {
        "severity":    result.get("severity", "NORMAL"),
        "anomaly":     bool(result.get("anomaly", False)),
        "explanation": result.get("explanation", ""),
        "engine":      "phi4-mini-onnx",
    }


def _infer_rules(history: list[dict]) -> dict:
    """Deterministic rule-based fallback."""
    if not history:
        return {"severity": "NORMAL", "anomaly": False, "explanation": "No data", "engine": "rules"}

    latest = history[-1]
    violations = []
    for path, (lo, hi) in _RULES.items():
        val = latest.get(path)
        if val is not None and not (lo <= val <= hi):
            violations.append(f"{path}={val:.1f} out of [{lo}, {hi}]")

    # Frozen-signal detection (all values identical across entire window)
    if len(history) >= 5:
        for path in _RULES:
            vals = [r.get(path) for r in history if r.get(path) is not None]
            if len(vals) >= 5 and len(set(round(v, 2) for v in vals[-5:])) == 1:
                violations.append(f"{path} frozen at {vals[-1]:.2f}")

    severity = "NORMAL"
    if violations:
        severity = "CRITICAL" if len(violations) > 1 else "WARNING"

    return {
        "severity":    severity,
        "anomaly":     bool(violations),
        "explanation": "; ".join(violations) if violations else "All signals within normal range.",
        "engine":      "rules",
    }


def _analyze(history: list[dict]) -> dict:
    if _model is not None:
        try:
            return _infer_onnx(history)
        except Exception as exc:
            log.warning(f"ONNX inference failed ({exc}), falling back to rules")
    return _infer_rules(history)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    _setup_pyroscope()
    tracer = _setup_otel()
    _load_onnx_model()

    mc = mqtt_client.Client(client_id=f"ai-monitor-edge-{VEHICLE_ID}", protocol=mqtt_client.MQTTv5)
    if MQTT_TLS:
        mc.tls_set(ca_certs=MQTT_CA_CERT, certfile=MQTT_CLIENT_CERT, keyfile=MQTT_CLIENT_KEY)
    mc.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    mc.loop_start()

    history: deque[dict] = deque(maxlen=HISTORY_WINDOW)

    log.info(f"Monitoring started (engine={'phi4-mini-onnx' if _model else 'rules'}, interval={MONITOR_INTERVAL_SEC}s)")

    with VSSClient(DATABROKER_HOST, DATABROKER_PORT, insecure=True) as vss:
        while True:
            with tracer.start_as_current_span("monitor_cycle") as span:
                try:
                    vals = vss.get_current_values(SIGNAL_PATHS)
                    reading = {
                        p: (vals[p].value if vals.get(p) and vals[p].value is not None else None)
                        for p in SIGNAL_PATHS
                    }
                    reading["ts"] = datetime.now(timezone.utc).isoformat()
                    history.append(reading)

                    assessment = _analyze(list(history))
                    span.set_attribute("anomaly.severity", assessment["severity"])
                    span.set_attribute("inference.engine", assessment["engine"])

                    if assessment["anomaly"]:
                        alert = {
                            "vehicle_id":  VEHICLE_ID,
                            "ts":          reading["ts"],
                            "severity":    assessment["severity"],
                            "explanation": assessment["explanation"],
                            "engine":      assessment["engine"],
                            "signals":     reading,
                        }
                        mc.publish(ALERT_TOPIC, json.dumps(alert), qos=1)
                        log.warning(f"ALERT [{assessment['severity']}] {assessment['explanation']}")
                    else:
                        log.info(f"OK [{assessment['engine']}] {assessment['explanation'][:80]}")

                except Exception as exc:
                    log.error(f"Monitor cycle error: {exc}")
                    span.record_exception(exc)

            time.sleep(MONITOR_INTERVAL_SEC)


if __name__ == "__main__":
    run()
