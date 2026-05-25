#!/usr/bin/env python3
"""
MQTT Bridge — mini-sdv-platform  Milestone 2
=============================================
Subscribes to vehicle signals from the Kuksa Databroker using gRPC streaming
and forwards each update to a Mosquitto MQTT broker as a JSON telemetry payload.

SDV Concept:
  This bridge represents the "cloud exit point" of the in-vehicle middleware.
  In a production SDV platform:
    - The Databroker runs on the Central Vehicle Computer (on-board)
    - The MQTT bridge runs at the vehicle's cloud gateway (on-board or edge)
    - Mosquitto represents an AWS IoT Core / Azure IoT Hub endpoint (cloud)

  Any cloud service — fleet management, AI analytics, OTA orchestrator —
  subscribes to MQTT topics. It never touches the Databroker directly.
  The bridge is the only component that crosses the vehicle / cloud boundary.

Key design decision — subscribe vs. poll:
  This service uses subscribe_current_values() (gRPC streaming), NOT
  get_current_values() polling (which the M1 dashboard uses).

  Poll:      "Give me the current value right now"  — runs on a fixed timer
  Subscribe: "Notify me whenever the value changes" — event-driven, immediate

  A bridge must react to changes as they occur, not on a schedule.
  Using poll would add up to 1 second of artificial latency per update.
  Subscribe is the correct pattern for any forwarding or recording service.

MQTT Topic structure:
  sdv/{vehicle_id}/{VSS_path_with_slashes}

  Examples (COVESA VSS 4.x standard paths — updated in M3):
    sdv/vehicle-001/Vehicle/Speed
    sdv/vehicle-001/Vehicle/Powertrain/TractionBattery/StateOfCharge/Current
    sdv/vehicle-001/Vehicle/Cabin/HVAC/AmbientAirTemperature

  Wildcard subscriptions (from a cloud subscriber):
    sdv/vehicle-001/#           → all signals from this vehicle (unchanged)
    sdv/+/Vehicle/Speed         → Speed from any vehicle in the fleet
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from kuksa_client.grpc import VSSClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("mqtt-bridge")

# ── Configuration ─────────────────────────────────────────────────────────────
DATABROKER_HOST  = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT  = int(os.environ.get("DATABROKER_PORT", "55555"))
MQTT_HOST        = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT        = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID       = os.environ.get("VEHICLE_ID", "vehicle-001")
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
OTEL_ENABLED     = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
OTEL_ENDPOINT    = os.environ.get("OTEL_ENDPOINT", "http://localhost:4317")

# ── Signal metadata (COVESA VSS 4.x standard paths — migrated in M3) ─────────
# Unit strings match the VSS catalog definitions in vss_mini_covesa.json.
# Keeping unit metadata here (not fetching from Databroker) avoids an extra
# RPC call and makes the payload self-contained for cloud consumers.
#
# MQTT topic change from M2 → M3 (dot-to-slash conversion of new paths):
#   Vehicle.Powertrain.TractionBattery.StateOfCharge.Current
#     → sdv/vehicle-001/Vehicle/Powertrain/TractionBattery/StateOfCharge/Current
#   Vehicle.Cabin.HVAC.AmbientAirTemperature
#     → sdv/vehicle-001/Vehicle/Cabin/HVAC/AmbientAirTemperature
#   Wildcard sdv/vehicle-001/# continues to capture all signals unchanged.
SIGNALS: dict[str, dict] = {
    "Vehicle.Speed":                                                    {"unit": "km/h"},
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current":        {"unit": "percent"},
    "Vehicle.Cabin.HVAC.AmbientAirTemperature":                        {"unit": "celsius"},
}

SIGNAL_PATHS = list(SIGNALS.keys())


# ── Helper functions ──────────────────────────────────────────────────────────

def vss_to_topic(vss_path: str) -> str:
    """
    Convert a VSS dot-notation path to an MQTT topic.

    VSS uses dots as hierarchy separators:  "Vehicle.Battery.SoC"
    MQTT uses slashes:  "sdv/vehicle-001/Vehicle/Battery/SoC"

    The slash-based hierarchy unlocks MQTT wildcard subscriptions:
      sdv/vehicle-001/#                → all signals from this vehicle
      sdv/vehicle-001/Vehicle/Battery/# → all Battery branch signals
      sdv/+/Vehicle/Speed              → Speed from any vehicle in the fleet
    """
    return f"sdv/{VEHICLE_ID}/{vss_path.replace('.', '/')}"


def make_payload(vss_path: str, value: float) -> str:
    """
    Build a self-describing JSON telemetry payload for one signal observation.

    The payload includes the signal path and unit so a cloud subscriber can
    parse it without needing to look up a schema or know the topic hierarchy.

    This format mirrors production V2C telemetry schemas:
      AWS IoT Core, Azure IoT Hub, COVESA VISS (Vehicle Information Service Spec)
    """
    return json.dumps({
        "signal":    vss_path,
        "value":     value,
        "unit":      SIGNALS[vss_path]["unit"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    })


# ── MQTT connection ───────────────────────────────────────────────────────────

def setup_tracing(service_name: str) -> trace.Tracer:
    """
    Returns a configured OTLP tracer when OTEL_ENABLED=true, NoOp otherwise.
    NoOp tracer: start_as_current_span() is a zero-overhead context manager.
    This lets instrumentation code stay in place without any runtime cost
    when tracing is disabled (e.g., in plain development mode).
    """
    if not OTEL_ENABLED:
        return trace.get_tracer(service_name)
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    log.info(f"OpenTelemetry tracing enabled → {OTEL_ENDPOINT}")
    return trace.get_tracer(service_name)


def apply_tls(client: mqtt.Client) -> None:
    if not MQTT_TLS:
        return
    client.tls_set(
        ca_certs=MQTT_CA_CERT,
        certfile=MQTT_CLIENT_CERT,
        keyfile=MQTT_CLIENT_KEY,
    )


def connect_mqtt() -> mqtt.Client:
    """
    Create a paho-mqtt client and connect to Mosquitto.

    loop_start() spawns a background thread that handles:
      - MQTT keepalive PING/PONG (prevents broker from dropping idle connections)
      - Automatic reconnect on network disruption (paho built-in)
      - Incoming message callbacks (not needed here — bridge is publish-only)

    The bridge is publish-only: it never subscribes to MQTT topics.
    In a bidirectional system (M5: AI commands), this would also subscribe
    to a command topic and write actuator values back to the Databroker.
    """
    client = mqtt.Client(client_id="sdv-bridge")

    # Callbacks for observability
    client.on_connect = lambda c, u, f, rc: log.info(
        f"MQTT connected (rc={rc})"
    )
    client.on_disconnect = lambda c, u, rc: log.warning(
        f"MQTT disconnected (rc={rc})"
    )

    apply_tls(client)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


# ── Main bridge loop ──────────────────────────────────────────────────────────

def run() -> None:
    """
    Outer reconnect loop.

    Establishes connections to both the Kuksa Databroker (gRPC subscribe)
    and Mosquitto (MQTT publish), then forwards every signal update.

    If either connection fails, waits with exponential back-off and retries.

    Cloud-native pattern: handle transient failures inside the process so
    Docker's restart policy is a last resort, not the primary recovery path.
    """
    tracer = setup_tracing("mqtt-bridge")
    retry_delay = 2.0

    while True:
        mqtt_client = None
        try:
            # ── Connect to MQTT broker ────────────────────────────────────────
            log.info(f"Connecting to Mosquitto at {MQTT_HOST}:{MQTT_PORT} …")
            mqtt_client = connect_mqtt()
            log.info("Connected to Mosquitto.")

            # ── Connect to Kuksa Databroker and subscribe ─────────────────────
            log.info(
                f"Connecting to Kuksa Databroker at "
                f"{DATABROKER_HOST}:{DATABROKER_PORT} …"
            )
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as kuksa:
                log.info(
                    "Connected to Kuksa Databroker. "
                    "Starting gRPC subscribe loop."
                )
                retry_delay = 2.0  # reset back-off on successful connect

                # subscribe_current_values() returns a blocking iterator.
                # It yields whenever one or more signals change — immediately,
                # without waiting for a poll timer.
                #
                # Compare with M1 dashboard's get_current_values() polling:
                #   Poll:       requests values every 1 second regardless
                #   Subscribe:  receives values within milliseconds of change
                #
                # The bridge uses subscribe because it is a reactive forwarder,
                # not a scheduled reader.
                for updates in kuksa.subscribe_current_values(SIGNAL_PATHS):
                    for path, datapoint in updates.items():
                        if datapoint is None or datapoint.value is None:
                            # Signal registered in VSS but not yet published
                            # by the ECU simulator (first cycle).
                            continue

                        value   = float(datapoint.value)
                        topic   = vss_to_topic(path)
                        payload = make_payload(path, value)

                        # One trace span per signal update — captures the
                        # full forward latency from Kuksa receive to MQTT publish.
                        with tracer.start_as_current_span("signal.forward") as span:
                            span.set_attribute("vehicle.id",   VEHICLE_ID)
                            span.set_attribute("signal.path",  path)
                            span.set_attribute("signal.value", value)
                            span.set_attribute("mqtt.topic",   topic)

                            # QoS 0 — at most once (fire and forget).
                            # Suitable for high-frequency telemetry where an
                            # occasional lost message is acceptable.
                            # QoS 1 (at least once) would be appropriate for
                            # safety-relevant signals in a production system.
                            mqtt_client.publish(topic, payload, qos=0)

                        log.info(
                            f"Published → {topic} "
                            f"= {value} {SIGNALS[path]['unit']}"
                        )

        except KeyboardInterrupt:
            log.info("Shutdown requested — stopping MQTT bridge.")
            return

        except Exception as exc:
            log.warning(f"Connection error: {exc}")
            log.info(f"Retrying in {retry_delay:.0f} s …")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)  # cap at 30 s

        finally:
            if mqtt_client is not None:
                try:
                    mqtt_client.loop_stop()
                    mqtt_client.disconnect()
                except Exception:
                    pass


def main() -> None:
    log.info("=" * 60)
    log.info("  mini-SDV Platform — MQTT Bridge  (Milestone 2)")
    log.info(f"  Databroker : {DATABROKER_HOST}:{DATABROKER_PORT}")
    log.info(f"  MQTT       : {MQTT_HOST}:{MQTT_PORT}")
    log.info(f"  Vehicle ID : {VEHICLE_ID}")
    log.info(f"  Topics     : sdv/{VEHICLE_ID}/Vehicle/Speed")
    log.info(f"             : sdv/{VEHICLE_ID}/Vehicle/Battery/SoC")
    log.info(f"             : sdv/{VEHICLE_ID}/Vehicle/Cabin/Temperature")
    log.info("  Subscribe  : mosquitto_sub -h localhost "
             f"-p {MQTT_PORT} -t 'sdv/{VEHICLE_ID}/#' -v")
    log.info("=" * 60)

    run()


if __name__ == "__main__":
    main()
