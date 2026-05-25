#!/usr/bin/env python3
"""
ECU Simulator — mini-sdv-platform  Milestone 4 / M6 OTA
=========================================================
Simulates three vehicle ECUs publishing signals as CAN frames over vcan0.

  ECU                   CAN ID   Signal                                                     Unit
  ─────────────────     ───────  ──────────────────────────────────────────────────────────  ────
  Powertrain ECU     →  0x100    Vehicle.Speed                                               km/h
  Battery Mgmt Sys   →  0x200    Vehicle.Powertrain.TractionBattery.StateOfCharge.Current   %
  HVAC Controller    →  0x300    Vehicle.Cabin.HVAC.AmbientAirTemperature                   °C

M6 addition:
  Watches ECU_CONFIG_PATH for changes written by ota-manager.
  When the config file is updated, reloads simulation parameters
  (speed range, SoC drain rate, cabin temp range) without restarting.
  This mirrors how a real ECU applies a parameter update without a full
  firmware flash.
"""

import json
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
ECU_CONFIG_PATH = os.environ.get("ECU_CONFIG_PATH", "/tmp/sdv-ota/ecu_config.json")

# ── Default ECU parameters (baseline v1.0.0) ──────────────────────────────────
DEFAULT_CONFIG = {
    "version":        "1.0.0",
    "speed_min":      10.0,
    "speed_max":      120.0,
    "soc_start":      85.0,
    "soc_drain_rate": 0.05,
    "cabin_temp_min": 19.5,
    "cabin_temp_max": 24.5,
}

# ── CAN ID → VSS Signal mapping ───────────────────────────────────────────────
CAN_IDS: dict[str, int] = {
    "Vehicle.Speed":                                                  0x100,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current":       0x200,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature":                       0x300,
}


# ── Config loading ────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(ECU_CONFIG_PATH):
        try:
            with open(ECU_CONFIG_PATH) as f:
                cfg = json.load(f)
            log.info(
                f"[OTA] Config loaded: version={cfg.get('version','?')} | "
                f"speed={cfg.get('speed_min')}–{cfg.get('speed_max')} km/h | "
                f"drain={cfg.get('soc_drain_rate')}%/cycle"
            )
            return {**DEFAULT_CONFIG, **cfg}
        except Exception as exc:
            log.warning(f"[OTA] Config read failed: {exc} — using defaults")
    return dict(DEFAULT_CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# Vehicle physics simulation
# ─────────────────────────────────────────────────────────────────────────────

class VehicleState:
    """Produces smooth, realistic-looking telemetry using periodic functions."""

    def __init__(self, config: dict) -> None:
        self._t:   float = 0.0
        self._soc: float = config["soc_start"]
        self.cfg         = config

    def speed(self) -> float:
        cfg   = self.cfg
        mid   = (cfg["speed_min"] + cfg["speed_max"]) / 2.0
        amp   = (cfg["speed_max"] - cfg["speed_min"]) / 2.0
        base  = mid + amp * math.sin(self._t * 0.04)
        noise = random.gauss(0.0, 1.5)
        return round(max(0.0, min(250.0, base + noise)), 1)

    def battery_soc(self) -> float:
        noise = random.gauss(0.0, 0.05)
        self._soc -= self.cfg["soc_drain_rate"]
        if self._soc < 55.0:
            self._soc = self.cfg["soc_start"]   # periodic reset (simulate charge)
        return round(max(0.0, min(100.0, self._soc + noise)), 2)

    def cabin_temperature(self) -> float:
        cfg  = self.cfg
        mid  = (cfg["cabin_temp_min"] + cfg["cabin_temp_max"]) / 2.0
        amp  = (cfg["cabin_temp_max"] - cfg["cabin_temp_min"]) / 2.0
        base = mid + amp * math.sin(self._t * 0.015)
        return round(base + random.gauss(0.0, 0.15), 1)

    def advance(self) -> None:
        self._t += 1.0

    def reload_config(self, config: dict) -> None:
        self.cfg = config
        log.info(f"[OTA] VehicleState config reloaded: version={config.get('version')}")


# ─────────────────────────────────────────────────────────────────────────────
# CAN publisher
# ─────────────────────────────────────────────────────────────────────────────

def send_signal(bus: can.BusABC, path: str, value: float) -> None:
    can_id = CAN_IDS[path]
    data   = struct.pack('<f', value)
    msg    = can.Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=False,
    )
    bus.send(msg)
    log.info(
        f"TX CAN 0x{can_id:03X} [{len(data)}] "
        f"{' '.join(f'{b:02X}' for b in data)}"
        f"  → {path.split('.')[-1]} = {value:.2f}"
    )


def run(vehicle: VehicleState) -> None:
    retry_delay  = 2.0
    config_mtime = os.path.getmtime(ECU_CONFIG_PATH) if os.path.exists(ECU_CONFIG_PATH) else 0.0

    while True:
        bus = None
        try:
            log.info(f"Opening CAN bus on interface '{CAN_INTERFACE}' …")
            bus = can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
            log.info("CAN bus open — starting signal publication loop.")
            retry_delay = 2.0

            while True:
                # ── OTA config file watch ────────────────────────────────
                if os.path.exists(ECU_CONFIG_PATH):
                    mtime = os.path.getmtime(ECU_CONFIG_PATH)
                    if mtime != config_mtime:
                        config_mtime = mtime
                        new_cfg = load_config()
                        vehicle.reload_config(new_cfg)

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
    log.info("  mini-SDV Platform — ECU Simulator  (M4 / M6 OTA)")
    log.info(f"  CAN interface : {CAN_INTERFACE}")
    log.info(f"  Interval      : {UPDATE_INTERVAL} s")
    log.info(f"  Config path   : {ECU_CONFIG_PATH}")
    log.info(f"  CAN ID 0x100  → Vehicle.Speed          (km/h)")
    log.info(f"  CAN ID 0x200  → Battery SoC            (%)")
    log.info(f"  CAN ID 0x300  → Cabin Temperature      (°C)")
    log.info("=" * 60)

    config  = load_config()
    vehicle = VehicleState(config)
    run(vehicle)


if __name__ == "__main__":
    main()
