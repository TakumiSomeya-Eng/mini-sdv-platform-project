#!/usr/bin/env python3
"""
ECU Simulator — mini-sdv-platform  Milestone 4
===============================================
Simulates three vehicle ECUs publishing signals as CAN frames over vcan0.

  ECU                   CAN ID   Signal                                                     Unit
  ─────────────────     ───────  ──────────────────────────────────────────────────────────  ────
  Powertrain ECU     →  0x100    Vehicle.Speed                                               km/h
  Battery Mgmt Sys   →  0x200    Vehicle.Powertrain.TractionBattery.StateOfCharge.Current   %
  HVAC Controller    →  0x300    Vehicle.Cabin.HVAC.AmbientAirTemperature                   °C

M4 change:
  M1–M3 published signals directly to the Kuksa Databroker via gRPC.
  M4 publishes CAN frames to vcan0. A separate CAN Gateway service
  reads these frames and forwards them to the Databroker — mirroring
  the real vehicle architecture (ECU → CAN bus → Gateway ECU → Middleware).

SDV concept:
  In a real vehicle, an ECU never knows the Databroker exists.
  It simply puts a CAN frame on the bus (ID + bytes).
  The Gateway ECU is responsible for protocol translation.
  This decoupling is what allows the middleware to be upgraded
  without touching ECU firmware.

CAN frame encoding:
  Each signal value is packed as a 32-bit IEEE 754 float, little-endian.
  This is a common encoding used in real automotive ECUs and is the
  format expected by the CAN Gateway (services/can-gateway/main.py).

  struct.pack('<f', 87.3) → b'\\xae\\x47\\xae\\x42'
"""

import logging
import math
import os
import random
import struct
import time

import can

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ecu-simulator")

# ── Configuration ─────────────────────────────────────────────────────────────
CAN_INTERFACE   = os.environ.get("CAN_INTERFACE", "vcan0")
UPDATE_INTERVAL = float(os.environ.get("UPDATE_INTERVAL_SEC", "1.0"))

# ── CAN ID → VSS Signal mapping ───────────────────────────────────────────────
# Arbitration IDs identify the signal on the CAN bus.
# In a real vehicle these IDs are defined in a DBC (database CAN) file
# maintained by the vehicle's signal architect.
CAN_IDS: dict[str, int] = {
    "Vehicle.Speed":                                                  0x100,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current":       0x200,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature":                       0x300,
}


# ─────────────────────────────────────────────────────────────────────────────
# Vehicle physics simulation (unchanged from M1–M3)
# ─────────────────────────────────────────────────────────────────────────────

class VehicleState:
    """Produces smooth, realistic-looking telemetry using periodic functions."""

    def __init__(self) -> None:
        self._t: float = 0.0

    def speed(self) -> float:
        base  = 65.0 + 50.0 * math.sin(self._t * 0.04)
        noise = random.gauss(0.0, 1.5)
        return round(max(0.0, min(250.0, base + noise)), 1)

    def battery_soc(self) -> float:
        phase = self._t % 600
        base  = 85.0 - phase * 0.05
        noise = random.gauss(0.0, 0.05)
        return round(max(0.0, min(100.0, base + noise)), 2)

    def cabin_temperature(self) -> float:
        base  = 22.0 + 2.5 * math.sin(self._t * 0.015)
        noise = random.gauss(0.0, 0.15)
        return round(base + noise, 1)

    def advance(self) -> None:
        self._t += 1.0


# ─────────────────────────────────────────────────────────────────────────────
# CAN publisher
# ─────────────────────────────────────────────────────────────────────────────

def send_signal(bus: can.BusABC, path: str, value: float) -> None:
    """Pack a float32 value into a 4-byte CAN frame and send it."""
    can_id = CAN_IDS[path]
    data   = struct.pack('<f', value)          # float32 little-endian
    msg    = can.Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=False,                  # standard 11-bit ID
    )
    bus.send(msg)
    log.info(
        f"TX CAN 0x{can_id:03X} [{len(data)}] "
        f"{' '.join(f'{b:02X}' for b in data)}"
        f"  → {path.split('.')[-1]} = {value:.2f}"
    )


def run(vehicle: VehicleState) -> None:
    """
    Outer reconnect loop.

    Opens a SocketCAN socket on vcan0 and sends CAN frames continuously.
    If vcan0 is not available (e.g. modprobe vcan not run yet), waits
    with exponential back-off and retries — same resilience pattern as M1–M3.
    """
    retry_delay = 2.0

    while True:
        bus = None
        try:
            log.info(f"Opening CAN bus on interface '{CAN_INTERFACE}' …")
            bus = can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
            log.info("CAN bus open — starting signal publication loop.")
            retry_delay = 2.0

            while True:
                vehicle.advance()

                send_signal(bus, "Vehicle.Speed",
                            vehicle.speed())
                send_signal(bus, "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",
                            vehicle.battery_soc())
                send_signal(bus, "Vehicle.Cabin.HVAC.AmbientAirTemperature",
                            vehicle.cabin_temperature())

                time.sleep(UPDATE_INTERVAL)

        except KeyboardInterrupt:
            log.info("Shutdown requested — stopping ECU simulator.")
            return

        except Exception as exc:
            log.warning(f"CAN error: {exc}")
            log.info(f"Retrying in {retry_delay:.0f} s …")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)

        finally:
            if bus is not None:
                try:
                    bus.shutdown()
                except Exception:
                    pass


def main() -> None:
    log.info("=" * 60)
    log.info("  mini-SDV Platform — ECU Simulator  (Milestone 4)")
    log.info(f"  CAN interface : {CAN_INTERFACE}")
    log.info(f"  Interval      : {UPDATE_INTERVAL} s")
    log.info(f"  CAN ID 0x100  → Vehicle.Speed          (km/h)")
    log.info(f"  CAN ID 0x200  → Battery SoC            (%)")
    log.info(f"  CAN ID 0x300  → Cabin Temperature      (°C)")
    log.info("=" * 60)

    vehicle = VehicleState()
    run(vehicle)


if __name__ == "__main__":
    main()
