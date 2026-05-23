#!/usr/bin/env python3
"""
ECU Simulator — mini-sdv-platform  Milestone 1
===============================================
Simulates three vehicle Electronic Control Units (ECUs):

  ECU                   Signal                   Unit
  ─────────────────     ────────────────────────  ─────
  Powertrain ECU     →  Vehicle.Speed             km/h
  Battery Mgmt Sys   →  Vehicle.Battery.SoC       %
  HVAC Controller    →  Vehicle.Cabin.Temperature  °C

SDV concept:
  In a real vehicle, ECUs communicate over CAN bus (ISO 11898).
  A central gateway ECU reads those CAN frames and forwards them
  to the in-vehicle middleware (here: Kuksa Databroker) via gRPC.
  This simulator replaces the physical bus + gateway by writing
  directly to the Databroker — Milestone 2 adds SocketCAN.

Design decisions:
  • Single Python process — keeps M1 simple; real ECUs are separate hardware.
  • Direct gRPC publish — removes CAN dependency for now.
  • Sinusoidal + Gaussian noise — produces smooth, realistic-looking telemetry.
  • Reconnect loop — handles the race condition at container startup.
"""

import logging
import math
import os
import random
import time

from kuksa_client.grpc import Datapoint, VSSClient

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ecu-simulator")

# ── Configuration (override via environment variables) ────────────────────────
DATABROKER_HOST  = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT  = int(os.environ.get("DATABROKER_PORT", "55555"))
UPDATE_INTERVAL  = float(os.environ.get("UPDATE_INTERVAL_SEC", "1.0"))

# ── VSS Signal Paths ──────────────────────────────────────────────────────────
# VSS (Vehicle Signal Specification) defines a standardised, hierarchical
# naming tree for all vehicle data.  Using a shared catalog lets any app or
# service discover and subscribe to signals without prior negotiation.
# Paths here must match config/vss/vss_mini.json mounted into the Databroker.
SIGNAL_SPEED = "Vehicle.Speed"             # km/h  — Powertrain ECU
SIGNAL_SOC   = "Vehicle.Battery.SoC"       # %     — Battery Management System
SIGNAL_TEMP  = "Vehicle.Cabin.Temperature" # °C    — HVAC Controller


# ─────────────────────────────────────────────────────────────────────────────
# Vehicle physics simulation
# ─────────────────────────────────────────────────────────────────────────────

class VehicleState:
    """
    Produces smooth, realistic-looking telemetry using periodic functions
    and Gaussian noise — no real physics engine needed for M1.

    Each signal has a different period so the charts don't look identical.
    """

    def __init__(self) -> None:
        self._t: float = 0.0  # simulation clock (ticks)

    # ── Powertrain ECU ────────────────────────────────────────────────────────
    def speed(self) -> float:
        """
        Sinusoidal cruise pattern (city driving cycle approximation).
        Range: ~10 – 120 km/h with ±1.5 km/h Gaussian noise.
        """
        base  = 65.0 + 50.0 * math.sin(self._t * 0.04)
        noise = random.gauss(0.0, 1.5)
        return round(max(0.0, min(250.0, base + noise)), 1)

    # ── Battery Management System ─────────────────────────────────────────────
    def battery_soc(self) -> float:
        """
        Slow linear drain from 85 % to 55 %, then reset (charge cycle).
        600-tick period ≈ 10 minutes at 1 s update rate.
        """
        phase = self._t % 600
        base  = 85.0 - phase * 0.05
        noise = random.gauss(0.0, 0.05)
        return round(max(0.0, min(100.0, base + noise)), 2)

    # ── HVAC Controller ───────────────────────────────────────────────────────
    def cabin_temperature(self) -> float:
        """
        Slow warm-up toward setpoint with sinusoidal HVAC cycling.
        Range: 19.5 – 24.5 °C with ±0.15 °C noise.
        """
        base  = 22.0 + 2.5 * math.sin(self._t * 0.015)
        noise = random.gauss(0.0, 0.15)
        return round(base + noise, 1)

    def advance(self) -> None:
        """Increment the simulation clock by one tick."""
        self._t += 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def run(vehicle: VehicleState) -> None:
    """
    Outer reconnect loop — establishes a gRPC connection to the Databroker
    and publishes signals continuously.  If the connection drops (e.g. during
    a Databroker restart), it waits and reconnects automatically.

    Cloud-native pattern: prefer reconnect loops over crash-and-exit so that
    Docker's restart policy is a last resort, not the primary recovery path.
    """
    retry_delay = 2.0

    while True:
        log.info(f"Connecting to Kuksa Databroker at {DATABROKER_HOST}:{DATABROKER_PORT} …")
        try:
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as client:
                log.info("Connected — starting signal publication loop.")
                retry_delay = 2.0  # reset back-off on successful connect

                while True:
                    vehicle.advance()

                    speed = vehicle.speed()
                    soc   = vehicle.battery_soc()
                    temp  = vehicle.cabin_temperature()

                    # Publish all three signals in a single gRPC SetRequest.
                    # The Databroker atomically stores the new Datapoint values
                    # with a server-side timestamp.
                    client.set_current_values({
                        SIGNAL_SPEED: Datapoint(speed),
                        SIGNAL_SOC:   Datapoint(soc),
                        SIGNAL_TEMP:  Datapoint(temp),
                    })

                    log.info(
                        "Published → "
                        f"Speed={speed:6.1f} km/h | "
                        f"SoC={soc:5.2f} % | "
                        f"Temp={temp:5.1f} °C"
                    )

                    time.sleep(UPDATE_INTERVAL)

        except KeyboardInterrupt:
            log.info("Shutdown requested — stopping ECU simulator.")
            return

        except Exception as exc:
            log.warning(f"Connection error: {exc}")
            log.info(f"Retrying in {retry_delay:.0f} s …")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)  # exponential back-off, cap 30 s


def main() -> None:
    log.info("=" * 60)
    log.info("  mini-SDV Platform — ECU Simulator  (Milestone 1)")
    log.info(f"  Databroker  : {DATABROKER_HOST}:{DATABROKER_PORT}")
    log.info(f"  Interval    : {UPDATE_INTERVAL} s")
    log.info(f"  Signals     : {SIGNAL_SPEED}")
    log.info(f"              : {SIGNAL_SOC}")
    log.info(f"              : {SIGNAL_TEMP}")
    log.info("=" * 60)

    vehicle = VehicleState()
    run(vehicle)


if __name__ == "__main__":
    main()
