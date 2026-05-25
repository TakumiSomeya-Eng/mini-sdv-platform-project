# Technical Requirements Document (TRD)
## Milestone 4: Virtual CAN Bus (SocketCAN)
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | TRD |
| Milestone | 4 — Virtual CAN Bus (SocketCAN) |
| Status | Draft |
| Hypothesis Layer | Implementation (L5) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | [FRD.md](FRD.md) |

---

## 1. Implementation Hypothesis (L5)

> The ECU Simulator and CAN Gateway can be implemented as Python services using `python-can==4.x` with `socketcan` backend, running with `network_mode: host` in Docker Compose to share the WSL2 host's `vcan0` interface, while all other M1–M3 services remain unchanged on `sdv-net`.

---

## 2. Technology Decisions

| Component | Choice | Rationale |
|---|---|---|
| CAN library | `python-can==4.3.1` | Latest stable; socketcan backend built-in; same API as physical hardware |
| CAN backend | `socketcan` | Native Linux kernel CAN socket — identical to real ECU Linux environment |
| CAN encoding | `struct.pack('<f', value)` | float32 little-endian — 4 bytes; standard for automotive signal packing |
| Docker network | `network_mode: host` for CAN services | Required to access host's vcan0; CAN sockets are kernel-level, not Docker-bridgeable |
| Databroker port | expose `55555:55555` to host | Allows `can-gateway` (on host network) to reach databroker (on sdv-net) |
| Base image | `python:3.11-slim` | Consistent with M1–M3 |

---

## 3. File Changes

### New Files

```
services/can-gateway/
├── Dockerfile
├── main.py
└── requirements.txt

scripts/
└── setup-vcan.sh
```

### Modified Files

```
services/ecu-simulator/main.py      ← replace gRPC publish with CAN send
services/ecu-simulator/requirements.txt  ← add python-can==4.3.1
docker-compose.yml                  ← network_mode: host + port expose
README.md                           ← M4 architecture + setup steps
```

---

## 4. CAN Frame Specification (TDR-40)

| Signal | CAN ID | DLC | Encoding | Example (87.3 km/h) |
|---|---|---|---|---|
| Vehicle.Speed | `0x100` | 4 | `struct.pack('<f', v)` | `AE 47 AE 42` |
| Vehicle.Powertrain.TractionBattery.StateOfCharge.Current | `0x200` | 4 | `struct.pack('<f', v)` | |
| Vehicle.Cabin.HVAC.AmbientAirTemperature | `0x300` | 4 | `struct.pack('<f', v)` | |

```python
# Encode
import struct
data = struct.pack('<f', 87.3)   # b'\xae\x47\xae\x42'

# Decode
value = struct.unpack('<f', bytes(msg.data[:4]))[0]   # 87.3
```

---

## 5. ecu-simulator/main.py — Implementation Plan

```python
import can
import struct

CAN_INTERFACE = os.environ.get("CAN_INTERFACE", "vcan0")

CAN_IDS = {
    "Vehicle.Speed": 0x100,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": 0x200,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": 0x300,
}

def connect_can() -> can.Bus:
    return can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')

def publish_signals(bus: can.Bus, state: VehicleState) -> None:
    signals = {
        "Vehicle.Speed": state.speed,
        "Vehicle.Powertrain...": state.soc,
        "Vehicle.Cabin...": state.cabin_temp,
    }
    for path, value in signals.items():
        data = struct.pack('<f', value)
        msg = can.Message(
            arbitration_id=CAN_IDS[path],
            data=data,
            is_extended_id=False,
        )
        bus.send(msg)
        log.info(f"TX CAN 0x{CAN_IDS[path]:03X} [{len(data)}] "
                 f"{data.hex(' ').upper()}  → {value:.2f}")
```

**Reconnect pattern:** identical to M1–M3 exponential back-off (2s → 30s cap).

---

## 6. can-gateway/main.py — Implementation Plan

```python
import can
import struct
from kuksa_client.grpc import VSSClient, Datapoint

CAN_TO_VSS = {
    0x100: ("Vehicle.Speed", "km/h"),
    0x200: ("Vehicle.Powertrain.TractionBattery.StateOfCharge.Current", "percent"),
    0x300: ("Vehicle.Cabin.HVAC.AmbientAirTemperature", "celsius"),
}

def run():
    while True:
        try:
            bus = can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as kuksa:
                for msg in bus:   # blocking iterator — yields each received frame
                    if msg.arbitration_id not in CAN_TO_VSS:
                        continue
                    path, unit = CAN_TO_VSS[msg.arbitration_id]
                    value = struct.unpack('<f', bytes(msg.data[:4]))[0]
                    kuksa.set_current_values({path: Datapoint(value)})
                    log.info(f"RX CAN 0x{msg.arbitration_id:03X} "
                             f"→ {path} = {value:.2f} {unit}")
        except Exception as exc:
            log.warning(f"Error: {exc}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)
```

---

## 7. docker-compose.yml Changes

```yaml
# Databroker: expose port to host for can-gateway
databroker:
  ports:
    - "55555:55555"   # NEW — enables can-gateway (host network) to reach it

# ECU Simulator: switch to host network + CAN env
ecu-simulator:
  network_mode: host    # NEW — accesses vcan0
  environment:
    CAN_INTERFACE: vcan0
    UPDATE_INTERVAL_SEC: "1.0"
  # remove: depends_on databroker (no longer connects to databroker)
  # remove: networks: sdv-net

# CAN Gateway: new service
can-gateway:
  build:
    context: ./services/can-gateway
  network_mode: host    # accesses both vcan0 and localhost:55555
  environment:
    CAN_INTERFACE: vcan0
    DATABROKER_HOST: localhost
    DATABROKER_PORT: "55555"
  depends_on:
    databroker:
      condition: service_healthy
```

---

## 8. scripts/setup-vcan.sh

```bash
#!/usr/bin/env bash
set -e
sudo modprobe vcan
sudo modprobe can
sudo modprobe can_raw

if ip link show vcan0 &>/dev/null; then
  echo "vcan0 already exists"
else
  sudo ip link add dev vcan0 type vcan
fi

sudo ip link set up vcan0
echo "vcan0 is up"
ip link show vcan0
```

---

## 9. Constraints

| ID | Constraint |
|---|---|
| CON-40 | vcan0 must be created on the WSL2 host before `docker compose up` |
| CON-41 | `network_mode: host` is incompatible with `networks: sdv-net` — CAN services cannot be on both |
| CON-42 | Databroker port 55555 must be exposed to host for CAN gateway access |
| CON-43 | python-can socketcan backend requires Linux kernel SocketCAN support (vcan module) |
| CON-44 | WSL2 custom kernel (6.18 with vcan enabled) is prerequisite for CON-43 |

---

## 10. Quick Test (Post-Implementation)

```bash
# 1. Setup vcan0 (WSL2)
bash scripts/setup-vcan.sh

# 2. Start all services
docker compose up -d

# 3. Observe raw CAN frames (WSL2 terminal)
candump vcan0
# Expected: vcan0  100  [4]  xx xx xx xx   (at 1 Hz)
#           vcan0  200  [4]  xx xx xx xx
#           vcan0  300  [4]  xx xx xx xx

# 4. Check gateway translation
docker compose logs -f can-gateway
# Expected: RX CAN 0x100 → Vehicle.Speed = 87.3 km/h

# 5. Check dashboard
# → http://localhost:8501   (same as M1)
```
