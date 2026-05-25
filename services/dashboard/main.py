#!/usr/bin/env python3
"""
Vehicle Signal Dashboard — mini-sdv-platform  Milestone 1
==========================================================
A Streamlit dashboard that polls the Kuksa Databroker every second
and displays live vehicle signals from three simulated ECUs.

SDV Concept:
  This dashboard represents the *consumer* side of the Vehicle
  Abstraction Layer (VAL). In a real SDV platform, any application
  that needs vehicle data — an instrument cluster, a fleet backend,
  an AI safety monitor — connects to the central Databroker and reads
  signals by VSS path. No application ever talks to an ECU directly.

  This decoupling is the core architectural benefit of centralized
  vehicle middleware: ECUs can be replaced, updated, or restarted
  without changing any application code.

Design decisions:
  • Polling (get_current_values) over subscribe — Streamlit reruns the
    entire script on each st.rerun(), making a persistent gRPC stream
    hard to manage without threads. Polling is the right fit for M1.
    True subscribe with ROS2 pub/sub is introduced in Milestone 3.
  • st.session_state for history — 60-entry rolling buffer per signal.
    No database needed; state survives Streamlit reruns within a session.
  • New gRPC connection per poll — avoids threading complexity. The
    overhead (~1 ms) is negligible at a 1-second poll interval.
  • st.rerun() at the end — Streamlit's idiomatic pattern for a live
    dashboard that auto-refreshes without user interaction.
"""

import json
import logging
import os
import time
from collections import deque

import paho.mqtt.client as mqtt_client
import streamlit as st
from kuksa_client.grpc import VSSClient

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("dashboard")

# ── Configuration ─────────────────────────────────────────────────────────────
DATABROKER_HOST  = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT  = int(os.environ.get("DATABROKER_PORT", "55555"))
REFRESH_INTERVAL = 1.0   # seconds between each Databroker poll
HISTORY_MAX      = 60    # rolling window size (60 samples ≈ 60 s at 1 Hz)

# M2: MQTT bridge display config.
# The dashboard does not connect to Mosquitto directly (FR-81).
# These vars are used to show the bridge endpoint in the sidebar only.
MQTT_HOST        = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT        = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID       = os.environ.get("VEHICLE_ID", "vehicle-001")
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")

# ── Signal Metadata (COVESA VSS 4.x standard paths — migrated in M3) ─────────
# Maps each VSS path to its display properties.
# Keeping this as a dict (not hard-coded in the UI functions) means adding
# a new signal in a future milestone only requires one entry here.
SIGNALS: dict[str, dict] = {
    "Vehicle.Speed": {
        "label": "Vehicle Speed",
        "unit": "km/h",
        "ecu": "Powertrain ECU",
        "format": "{:.1f}",
        "chart_color": "#1f77b4",
    },
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": {
        "label": "Battery State of Charge",
        "unit": "%",
        "ecu": "Battery Management System",
        "format": "{:.2f}",
        "chart_color": "#2ca02c",
    },
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": {
        "label": "Cabin Temperature",
        "unit": "°C",
        "ecu": "HVAC Controller",
        "format": "{:.1f}",
        "chart_color": "#d62728",
    },
}

SIGNAL_PATHS = list(SIGNALS.keys())

# M5: AI monitor alert subscription
AI_ALERT_TOPIC = f"sdv/{VEHICLE_ID}/alerts/ai"

# M6: OTA status subscription
OTA_STATUS_TOPIC = f"sdv/{VEHICLE_ID}/ota/status"


# ── Session State ─────────────────────────────────────────────────────────────

def init_session_state() -> None:
    """
    Initialise persistent session state on the very first Streamlit run.

    st.session_state values survive st.rerun() within the same browser
    session, giving us an in-memory signal history buffer without a database.
    Each signal gets its own deque with a fixed maximum length — when full,
    appending a new value automatically discards the oldest entry.
    """
    if "history" not in st.session_state:
        st.session_state.history = {
            path: deque(maxlen=HISTORY_MAX) for path in SIGNAL_PATHS
        }
    if "prev_values" not in st.session_state:
        st.session_state.prev_values = {path: None for path in SIGNAL_PATHS}
    if "connected" not in st.session_state:
        st.session_state.connected = False
    if "ai_alert" not in st.session_state:
        st.session_state.ai_alert = None
    if "ota_status" not in st.session_state:
        st.session_state.ota_status = None
    if "mqtt_subscribed" not in st.session_state:
        st.session_state.mqtt_subscribed = False


# ── Databroker Poll ───────────────────────────────────────────────────────────

def poll_databroker() -> dict[str, float | None]:
    """
    Open a short-lived gRPC connection to the Kuksa Databroker, read the
    current value of all three signals, and return them.

    Returns:
        dict mapping VSS path → float value, or None if not yet published
        or if the Databroker is unreachable.
    """
    values: dict[str, float | None] = {path: None for path in SIGNAL_PATHS}

    try:
        with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
            response = client.get_current_values(SIGNAL_PATHS)

        for path in SIGNAL_PATHS:
            datapoint = response.get(path)
            # A Datapoint with value=None means the signal is registered in
            # the VSS catalog but has not been published yet (ECU sim not
            # started, or first cycle not complete).
            if datapoint is not None and datapoint.value is not None:
                values[path] = float(datapoint.value)

        st.session_state.connected = True
        log.info(
            "Poll → "
            f"Speed={values['Vehicle.Speed']} km/h | "
            f"SoC={values['Vehicle.Powertrain.TractionBattery.StateOfCharge.Current']} % | "
            f"Temp={values['Vehicle.Cabin.HVAC.AmbientAirTemperature']} °C"
        )

    except Exception as exc:
        st.session_state.connected = False
        log.warning(f"Databroker poll failed: {exc}")

    return values


# ── UI Components ─────────────────────────────────────────────────────────────

def render_header() -> None:
    """Title bar with Databroker connection status."""
    col_title, col_status = st.columns([5, 1])

    with col_title:
        st.title("mini-SDV Platform")
        st.caption("Milestone 1 · ECU Simulator → Kuksa Databroker → Dashboard")

    with col_status:
        st.write("")  # push the badge down to align with the title
        if st.session_state.connected:
            st.success("● Connected")
        else:
            st.error("○ Disconnected")


def render_metrics(values: dict[str, float | None]) -> None:
    """
    One metric card per signal showing the current value and delta.

    Delta = difference from the previous poll reading.
    A positive delta on Speed means the vehicle is accelerating;
    a negative delta on SoC means the battery is discharging.
    This mirrors the delta concept used in automotive telemetry displays.
    """
    cols = st.columns(len(SIGNAL_PATHS))

    for col, path in zip(cols, SIGNAL_PATHS):
        meta = SIGNALS[path]
        current = values[path]
        previous = st.session_state.prev_values[path]

        with col:
            if current is None:
                st.metric(
                    label=f"{meta['label']}  ({meta['unit']})",
                    value="—",
                    help=f"VSS path: {path}\nSource: {meta['ecu']}\nNo data yet.",
                )
            else:
                delta_str = None
                if previous is not None:
                    delta = round(current - previous, 3)
                    delta_str = f"{delta:+.2f} {meta['unit']}"

                st.metric(
                    label=f"{meta['label']}  ({meta['unit']})",
                    value=meta["format"].format(current),
                    delta=delta_str,
                    help=f"VSS path: {path}\nSource ECU: {meta['ecu']}",
                )


def render_charts() -> None:
    """
    Rolling 60-second line chart for each signal.

    The chart shows the last HISTORY_MAX readings (60 samples = 60 seconds
    at a 1 Hz poll rate). This gives a short-term trend view — useful for
    spotting oscillations, drift, or anomalies in vehicle signals.

    In a real SDV cloud backend, this role would be filled by InfluxDB +
    Grafana or a cloud time-series store. For M1 the in-memory deque is
    sufficient and avoids adding a database service.
    """
    for path in SIGNAL_PATHS:
        meta = SIGNALS[path]
        history = list(st.session_state.history[path])

        st.subheader(f"{meta['label']}")
        st.caption(
            f"VSS path: `{path}` · Source: {meta['ecu']} · "
            f"Last {HISTORY_MAX} readings"
        )

        if not history:
            st.info("Waiting for signal data from ECU simulator…")
        else:
            # Pass a dict so the series label matches the unit name.
            # st.line_chart renders the dict key as the legend entry.
            st.line_chart(
                {meta["unit"]: history},
                height=160,
                use_container_width=True,
            )

        st.divider()


def apply_tls(client: mqtt_client.Client) -> None:
    if not MQTT_TLS:
        return
    client.tls_set(
        ca_certs=MQTT_CA_CERT,
        certfile=MQTT_CLIENT_CERT,
        keyfile=MQTT_CLIENT_KEY,
    )


def init_mqtt_alert_listener() -> None:
    """
    Subscribe to the AI monitor alert topic in a background MQTT thread.

    paho loop_start() runs the network loop in a daemon thread. The on_message
    callback writes directly to st.session_state — safe because Streamlit
    reruns the entire script on each cycle, so the next rerun picks up the
    latest alert without needing locks.
    """
    if st.session_state.mqtt_subscribed:
        return
    try:
        client = mqtt_client.Client(client_id="dashboard-alert-sub")
        def on_message(_client, _userdata, msg):
            try:
                data = json.loads(msg.payload.decode())
                if msg.topic == AI_ALERT_TOPIC:
                    st.session_state.ai_alert = data
                elif msg.topic == OTA_STATUS_TOPIC:
                    st.session_state.ota_status = data
            except Exception:
                pass
        client.on_message = on_message
        apply_tls(client)
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.subscribe(AI_ALERT_TOPIC)
        client.subscribe(OTA_STATUS_TOPIC)
        client.loop_start()
        st.session_state.mqtt_subscribed = True
        log.info(f"Subscribed to AI alert topic: {AI_ALERT_TOPIC}")
    except Exception as exc:
        log.warning(f"AI alert MQTT subscription failed: {exc}")


def render_ota_status() -> None:
    """
    OTA update status panel (M6).

    Displays the latest OTA phase received from ota-manager via MQTT.
    Phase colours: check/complete=green, downloading/verifying/installing=orange, error=red.
    """
    st.subheader("OTA Update Manager")
    st.caption(f"Topic: {OTA_STATUS_TOPIC} · Poll interval: 30s")

    status = st.session_state.ota_status
    if status is None:
        st.info("Waiting for OTA manager status…")
        st.divider()
        return

    phase   = status.get("phase", "unknown")
    version = status.get("version") or status.get("installed_version", "—")
    ts      = status.get("timestamp", "—")

    phase_config = {
        "check":       ("✓ Up to date",   "success"),
        "downloading": ("⬇ Downloading",  "warning"),
        "verifying":   ("🔍 Verifying",   "warning"),
        "installing":  ("⚙ Installing",   "warning"),
        "complete":    ("✅ Complete",     "success"),
        "error":       ("✗ Error",        "error"),
    }
    label, kind = phase_config.get(phase, (phase.upper(), "info"))

    render_fn = {"success": st.success, "warning": st.warning,
                 "error": st.error, "info": st.info}
    render_fn.get(kind, st.info)(f"**{label}**  — version: `{version}`")

    if phase == "complete":
        prev = status.get("previous_version", "—")
        changelog = status.get("changelog", "")
        st.caption(f"Updated: {prev} → {version}")
        if changelog:
            st.caption(f"Changes: {changelog}")
    elif phase == "error":
        st.caption(f"Reason: {status.get('reason', '—')}  |  Rollback: {status.get('rollback', False)}")
    elif phase == "downloading":
        to_ver = status.get("to_version", "?")
        st.caption(f"Fetching version {to_ver}…")

    st.caption(f"Last update: {ts}")
    st.divider()


def render_ai_alert() -> None:
    """
    AI Signal Monitor alert panel (M5).

    Displays the latest anomaly assessment from the ai-monitor agent.
    Severity maps to Streamlit alert colour: info→blue, warning→yellow, critical→red.
    """
    st.subheader("AI Signal Monitor")
    st.caption("Powered by claude-haiku-4-5 · Updates every 10 s · Topic: " + AI_ALERT_TOPIC)

    alert = st.session_state.ai_alert

    if alert is None:
        st.info("No anomaly detected — waiting for AI monitor data…")
        return

    severity = alert.get("severity", "info")
    explanation = alert.get("explanation", "—")
    timestamp = alert.get("timestamp", "—")

    if severity == "critical":
        st.error(f"**CRITICAL** — {explanation}")
    elif severity == "warning":
        st.warning(f"**WARNING** — {explanation}")
    else:
        st.info(f"**INFO** — {explanation}")

    signals = alert.get("signals", {})
    if signals:
        cols = st.columns(3)
        labels = [
            ("Speed", "Vehicle.Speed", "km/h"),
            ("SoC", "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current", "%"),
            ("Temp", "Vehicle.Cabin.HVAC.AmbientAirTemperature", "°C"),
        ]
        for col, (name, path, unit) in zip(cols, labels):
            val = signals.get(path)
            with col:
                st.metric(label=f"{name} ({unit})", value=f"{val:.1f}" if val is not None else "—")

    st.caption(f"Assessment timestamp: {timestamp}")
    st.divider()


def render_sidebar() -> None:
    """
    Educational sidebar explaining the SDV architecture visible in this demo.
    """
    with st.sidebar:
        st.header("SDV Architecture")
        st.markdown(f"""
**Signal Flow (this demo)**
```
Powertrain ECU ──┐
Battery Mgmt Sys ┼──▶ Kuksa      ──▶ This
HVAC Controller ─┘    Databroker      Dashboard
      ↑                    ↑
   (gRPC)              (gRPC)
```

**Real-World Equivalent**
| Simulation | Production SDV |
|---|---|
| ECU Simulator | Physical ECU on CAN bus |
| gRPC publish | CAN frame → Gateway → gRPC |
| Kuksa Databroker | Central Vehicle Computer |
| Dashboard | HMI / Cloud Backend |

**Protocol:** gRPC (Kuksa VAL API)
**Signal Standard:** COVESA VSS
        """)

        st.divider()
        st.caption(f"Databroker: `{DATABROKER_HOST}:{DATABROKER_PORT}`")
        st.caption(f"Poll interval: {REFRESH_INTERVAL} s")
        st.caption(f"History window: {HISTORY_MAX} samples")

        # ── M2: MQTT Cloud Bridge info ────────────────────────────────────
        # The dashboard does not connect to Mosquitto directly (FRD FR-81).
        # This section shows the configured endpoint and the CLI command
        # a cloud engineer would use to subscribe to vehicle telemetry.
        st.divider()
        st.subheader("MQTT Cloud Bridge")
        st.markdown(
            f"**Broker:** `{MQTT_HOST}:{MQTT_PORT}`  \n"
            f"**Vehicle ID:** `{VEHICLE_ID}`\n\n"
            "Subscribe from host terminal:\n"
            "```\n"
            f"mosquitto_sub -h localhost \\\\\n"
            f"  -p {MQTT_PORT} \\\\\n"
            f"  -t \"sdv/{VEHICLE_ID}/#\" -v\n"
            "```"
        )
        st.caption("M2 — Vehicle-to-Cloud telemetry via MQTT")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="mini-SDV Platform",
        page_icon="🚗",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_session_state()
    init_mqtt_alert_listener()

    render_header()
    st.divider()

    # ── Poll current values from Databroker ──────────────────────────────────
    values = poll_databroker()

    # Append to rolling history buffers (None values are skipped so the chart
    # does not display gaps when the ECU sim hasn't published yet)
    for path in SIGNAL_PATHS:
        if values[path] is not None:
            st.session_state.history[path].append(values[path])

    # ── Render UI ────────────────────────────────────────────────────────────
    render_metrics(values)
    st.divider()
    render_charts()
    render_ota_status()
    render_ai_alert()
    render_sidebar()

    # Update previous values for delta calculation in the next cycle
    for path in SIGNAL_PATHS:
        if values[path] is not None:
            st.session_state.prev_values[path] = values[path]

    # ── Schedule next refresh ────────────────────────────────────────────────
    # time.sleep + st.rerun() is the idiomatic Streamlit pattern for a live
    # dashboard. The sleep keeps the UI responsive and the poll rate bounded.
    time.sleep(REFRESH_INTERVAL)
    st.rerun()


if __name__ == "__main__":
    main()
