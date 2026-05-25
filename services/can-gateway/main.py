#!/usr/bin/env python3
"""
CAN Gateway — mini-sdv-platform  Milestone 4
=============================================
Reads CAN frames from vcan0 and translates them into VSS signals,
publishing each decoded value to the Kuksa Databroker via gRPC.

SDV concept:
  This service represents the Central Gateway ECU in a real vehicle.
  In production SDV platforms (AUTOSAR Adaptive, COVESA VSOMEIP):
    - ECUs publish raw sensor data as CAN frames (CAN ID + bytes)
    - A dedicated Gateway ECU reads every frame off the bus
    - The Gateway translates CAN IDs → signal names and byte offsets → values
    - It then writes those values to the in-vehicle middleware (Databroker)
    - No ECU knows the Databroker exists — the Gateway is the only bridge

  This decoupling is architecturally significant:
    - ECU firmware can be frozen while the middleware evolves
    - A new consumer (cloud backend, AI agent) requires no ECU change
    - Signal ownership is centralised in the Gateway's CAN ID mapping table

CAN frame format (matches ecu-simulator/main.py):
  Arbitration ID: 11-bit standard frame (is_extended_id=False)
  Data length:    4 bytes
  Encoding:       float32 little-endian (struct.pack('<f', value))

CAN ID → VSS mapping:
  0x100 → Vehicle.Speed                                                  km/h
  0x200 → Vehicle.Powertrain.TractionBattery.StateOfCharge.Current       %
  0x300 → Vehicle.Cabin.HVAC.AmbientAirTemperature                       °C
"""

import logging
import os
import struct
import time

import can
from kuksa_client.grpc import Datapoint, VSSClient

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("can-gateway")

# ── Configuration ─────────────────────────────────────────────────────────────
CAN_INTERFACE   = os.environ.get("CAN_INTERFACE", "vcan0")
DATABROKER_HOST = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT = int(os.environ.get("DATABROKER_PORT", "55555"))

# ── CAN ID → VSS mapping ──────────────────────────────────────────────────────
# This table is the Gateway's signal map — equivalent to a DBC file entry.
# Adding a new signal requires only a new row here and a new ECU sender.
CAN_TO_VSS: dict[int, tuple[str, str]] = {
    0x100: ("Vehicle.Speed",                                                 "km/h"),
    0x200: ("Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",      "percent"),
    0x300: ("Vehicle.Cabin.HVAC.AmbientAirTemperature",                      "celsius"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Main gateway loop
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    """
    Outer reconnect loop.

    1. Opens a SocketCAN socket on vcan0 (read-only — gateway never transmits).
    2. Connects to the Kuksa Databroker via gRPC.
    3. Enters a blocking frame-receive loop: for each frame, decode the float32
       value, map the CAN ID to a VSS path, and call set_current_values().

    If either connection drops, waits with exponential back-off and retries.
    This mirrors the resilience pattern used in M1–M3 services.
    """
    retry_delay = 2.0

    while True:
        bus = None
        try:
            log.info(f"Opening CAN bus on interface '{CAN_INTERFACE}' …")
            bus = can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
            log.info("CAN bus open.")

            log.info(
                f"Connecting to Kuksa Databroker at "
                f"{DATABROKER_HOST}:{DATABROKER_PORT} …"
            )
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as kuksa:
                log.info(
                    "Connected to Databroker. "
                    "Starting CAN → VSS translation loop."
                )
                retry_delay = 2.0

                # bus is a blocking iterator: yields each received CAN frame.
                # Unlike polling (get_current_values), this is purely event-driven —
                # the gateway reacts to every frame as it arrives on the bus,
                # with sub-millisecond latency from CAN receive to gRPC write.
                for msg in bus:
                    can_id = msg.arbitration_id

                    if can_id not in CAN_TO_VSS:
                        log.debug(f"Unknown CAN ID 0x{can_id:03X} — skipping")
                        continue

                    if len(msg.data) < 4:
                        log.warning(
                            f"CAN 0x{can_id:03X}: expected 4 bytes, "
                            f"got {len(msg.data)} — skipping"
                        )
                        continue

                    path, unit = CAN_TO_VSS[can_id]
                    value      = struct.unpack('<f', bytes(msg.data[:4]))[0]
                    value      = round(value, 3)

                    kuksa.set_current_values({path: Datapoint(value)})

                    log.info(
                        f"RX CAN 0x{can_id:03X} "
                        f"[{' '.join(f'{b:02X}' for b in msg.data[:4])}] "
                        f"→ {path.split('.')[-1]} = {value:.2f} {unit}"
                    )

        except KeyboardInterrupt:
            log.info("Shutdown requested — stopping CAN gateway.")
            return

        except Exception as exc:
            log.warning(f"Connection error: {exc}")
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
    log.info("  mini-SDV Platform — CAN Gateway  (Milestone 4)")
    log.info(f"  CAN interface : {CAN_INTERFACE}")
    log.info(f"  Databroker    : {DATABROKER_HOST}:{DATABROKER_PORT}")
    log.info(f"  CAN ID 0x100  → Vehicle.Speed")
    log.info(f"  CAN ID 0x200  → Vehicle.Powertrain...StateOfCharge.Current")
    log.info(f"  CAN ID 0x300  → Vehicle.Cabin.HVAC.AmbientAirTemperature")
    log.info("=" * 60)

    run()


if __name__ == "__main__":
    main()
