#!/usr/bin/env python3
"""
InfluxDB Writer — mini-sdv-platform  Milestone 7
=================================================
Subscribes to Kuksa Databroker via gRPC (same pattern as mqtt-bridge) and
writes each vehicle signal update as an InfluxDB data point.

Data schema:
  measurement : vehicle_signals
  tags        : vehicle_id, signal
  field       : value (float64)
  timestamp   : server-side (InfluxDB assigns on write)

SDV Concept:
  In production vehicles, a telemetry agent (e.g., embedded in the Central
  Vehicle Computer or a dedicated TCU) streams VSS signal updates to a
  time-series database in the cloud (InfluxDB, TimescaleDB, AWS Timestream).
  Grafana — or a proprietary analytics layer — queries that DB to visualise
  fleet-wide trends and detect degradation over time.
"""

import logging
import os
import time

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from kuksa_client.grpc import VSSClient

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("influxdb-writer")

# ── Configuration ─────────────────────────────────────────────────────────────
DATABROKER_HOST  = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT  = int(os.environ.get("DATABROKER_PORT", "55555"))
INFLUXDB_URL     = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN   = os.environ.get("INFLUXDB_TOKEN", "sdv-token-local")
INFLUXDB_ORG     = os.environ.get("INFLUXDB_ORG", "sdv-org")
INFLUXDB_BUCKET  = os.environ.get("INFLUXDB_BUCKET", "sdv")
VEHICLE_ID       = os.environ.get("VEHICLE_ID", "vehicle-001")

SIGNAL_PATHS = [
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",
    "Vehicle.Cabin.HVAC.AmbientAirTemperature",
]

# Short label stored as InfluxDB tag (avoids long VSS paths in Flux queries)
SIGNAL_LABELS = {
    "Vehicle.Speed": "Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": "BatterySoC",
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": "CabinTemp",
}


# ── InfluxDB connection ───────────────────────────────────────────────────────

def connect_influxdb() -> InfluxDBClient:
    retry = 2.0
    while True:
        try:
            client = InfluxDBClient(
                url=INFLUXDB_URL,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG,
            )
            client.ping()
            log.info(f"InfluxDB connected → {INFLUXDB_URL}  bucket={INFLUXDB_BUCKET}")
            return client
        except Exception as exc:
            log.warning(f"InfluxDB connect failed: {exc}. Retrying in {retry:.0f}s...")
            time.sleep(retry)
            retry = min(retry * 2, 30.0)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    log.info("InfluxDB Writer starting...")
    log.info(f"  Databroker: {DATABROKER_HOST}:{DATABROKER_PORT}")
    log.info(f"  InfluxDB:   {INFLUXDB_URL}  org={INFLUXDB_ORG}  bucket={INFLUXDB_BUCKET}")

    influx = connect_influxdb()
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    retry = 2.0
    while True:
        try:
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
                log.info("Kuksa connected. Subscribing to signals...")
                retry = 2.0
                for updates in client.subscribe_current_values(SIGNAL_PATHS):
                    for path, datapoint in updates.items():
                        if datapoint is None or datapoint.value is None:
                            continue
                        label = SIGNAL_LABELS.get(path, path.split(".")[-1])
                        point = (
                            Point("vehicle_signals")
                            .tag("vehicle_id", VEHICLE_ID)
                            .tag("signal", label)
                            .field("value", float(datapoint.value))
                        )
                        write_api.write(bucket=INFLUXDB_BUCKET, record=point)
                        log.debug(f"[WRITE] {label}={datapoint.value:.2f}")

        except Exception as exc:
            log.warning(f"Kuksa error: {exc}. Retrying in {retry:.0f}s...")
            time.sleep(retry)
            retry = min(retry * 2, 30.0)


if __name__ == "__main__":
    run()
