#!/usr/bin/env python3
"""
Fleet Simulator — mini-sdv-platform  Milestone 8
=================================================
Simulates vehicle-002 and vehicle-003 in parallel threads.
Each vehicle independently generates Speed / BatterySoC / CabinTemp
signals and publishes them to MQTT and InfluxDB.

Architecture note (DR-81):
  vehicle-001 flows through the full CAN → Databroker pipeline.
  vehicle-002/003 write directly to MQTT + InfluxDB, mirroring how
  a real fleet management backend receives telemetry from remote
  vehicles over a cloud MQTT endpoint — without needing a separate
  CAN bus or Databroker instance per vehicle.

SDV Concept:
  In production, each physical vehicle runs its own Databroker and
  Telemetry Agent (TCU). The cloud side sees only MQTT/HTTPS streams
  tagged with vehicle_id. This service simulates that cloud-side view.
"""

import json
import logging
import math
import os
import random
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt_client
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("fleet-simulator")

# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_HOST        = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT        = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
INFLUXDB_URL    = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN", "sdv-token-local")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG", "sdv-org")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "sdv")
INTERVAL_SEC    = float(os.environ.get("INTERVAL_SEC", "2"))

VEHICLES = ["vehicle-002", "vehicle-003"]


# ── Vehicle state ─────────────────────────────────────────────────────────────

class VehicleState:
    """Independent signal state per vehicle with smooth random walk."""

    def __init__(self, vehicle_id: str) -> None:
        self.vehicle_id = vehicle_id
        seed = int(vehicle_id.split("-")[-1])  # reproducible but distinct
        self._rng = random.Random(seed * 1000 + int(time.time()) % 1000)

        # Initial values (v1.0.0 parameter range)
        self.speed     = self._rng.uniform(30, 90)
        self.soc       = self._rng.uniform(60, 95)
        self.cabin     = self._rng.uniform(20, 23)

    def step(self) -> None:
        # Speed: random walk within 10–120 km/h
        self.speed = max(10.0, min(120.0,
            self.speed + self._rng.uniform(-5, 5)))

        # SoC: slowly draining with occasional charging spike
        drain = 0.05 + self._rng.uniform(-0.01, 0.01)
        self.soc = max(5.0, min(100.0, self.soc - drain))
        if self.soc < 20.0:
            self.soc = self._rng.uniform(80, 95)  # simulate charging

        # Cabin temp: small fluctuation around 21 °C
        self.cabin = max(19.5, min(24.5,
            self.cabin + self._rng.uniform(-0.3, 0.3)))

    def signals(self) -> dict:
        return {
            "Speed":      round(self.speed, 1),
            "BatterySoC": round(self.soc, 1),
            "CabinTemp":  round(self.cabin, 1),
        }

    def mqtt_payload(self) -> str:
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vehicle_id": self.vehicle_id,
            **self.signals(),
        })


# ── Connection helpers ────────────────────────────────────────────────────────

def apply_tls(client: mqtt_client.Client) -> None:
    if not MQTT_TLS:
        return
    client.tls_set(
        ca_certs=MQTT_CA_CERT,
        certfile=MQTT_CLIENT_CERT,
        keyfile=MQTT_CLIENT_KEY,
    )


def connect_mqtt() -> mqtt_client.Client:
    retry = 2.0
    while True:
        try:
            client = mqtt_client.Client(client_id="sdv-fleet")
            apply_tls(client)
            client.connect(MQTT_HOST, MQTT_PORT)
            client.loop_start()
            log.info(f"MQTT connected → {MQTT_HOST}:{MQTT_PORT}")
            return client
        except Exception as exc:
            log.warning(f"MQTT connect failed: {exc}. Retrying in {retry:.0f}s...")
            time.sleep(retry)
            retry = min(retry * 2, 30.0)


def connect_influxdb() -> InfluxDBClient:
    retry = 2.0
    while True:
        try:
            client = InfluxDBClient(
                url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
            client.ping()
            log.info(f"InfluxDB connected → {INFLUXDB_URL}")
            return client
        except Exception as exc:
            log.warning(f"InfluxDB connect failed: {exc}. Retrying in {retry:.0f}s...")
            time.sleep(retry)
            retry = min(retry * 2, 30.0)


# ── Per-vehicle simulation loop ───────────────────────────────────────────────

def simulate_vehicle(
    vehicle_id: str,
    mqtt: mqtt_client.Client,
    write_api,
) -> None:
    state = VehicleState(vehicle_id)
    topic = f"sdv/{vehicle_id}/telemetry"
    log.info(f"[{vehicle_id}] simulation started")

    while True:
        state.step()
        signals = state.signals()

        # ── MQTT publish ──────────────────────────────────────────────
        mqtt.publish(topic, state.mqtt_payload(), qos=0)

        # ── InfluxDB write ────────────────────────────────────────────
        points = [
            Point("vehicle_signals")
            .tag("vehicle_id", vehicle_id)
            .tag("signal", sig)
            .field("value", val)
            for sig, val in signals.items()
        ]
        try:
            write_api.write(bucket=INFLUXDB_BUCKET, record=points)
        except Exception as exc:
            log.warning(f"[{vehicle_id}] InfluxDB write error: {exc}")

        log.debug(
            f"[{vehicle_id}] speed={signals['Speed']} "
            f"soc={signals['BatterySoC']} cabin={signals['CabinTemp']}"
        )
        time.sleep(INTERVAL_SEC)


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    log.info("Fleet Simulator starting...")
    log.info(f"  Vehicles:  {', '.join(VEHICLES)}")
    log.info(f"  MQTT:      {MQTT_HOST}:{MQTT_PORT}")
    log.info(f"  InfluxDB:  {INFLUXDB_URL}  bucket={INFLUXDB_BUCKET}")
    log.info(f"  Interval:  {INTERVAL_SEC}s")

    mqtt   = connect_mqtt()
    influx = connect_influxdb()
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    threads = []
    for vid in VEHICLES:
        t = threading.Thread(
            target=simulate_vehicle,
            args=(vid, mqtt, write_api),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Main thread blocks until all daemon threads finish (never, unless error)
    for t in threads:
        t.join()


if __name__ == "__main__":
    run()
