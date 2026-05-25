# Mini SDV Platform — Architecture Review & Study Guide
## Milestone 4: Virtual CAN Bus (SocketCAN + CAN Gateway ECU)

> **Date:** 2026-05-25

---

## Table of Contents

1. [What M4 Adds and Why](#1-what-m4-adds-and-why)
2. [CAN Bus Fundamentals](#2-can-bus-fundamentals)
   - 2-1. Why CAN Exists in Vehicles
   - 2-2. CAN Frame Format
   - 2-3. CAN ID and Arbitration
   - 2-4. Signal Encoding Inside a Frame
3. [SocketCAN — Linux Kernel CAN Subsystem](#3-socketcan--linux-kernel-can-subsystem)
   - 3-1. The AF_CAN Address Family
   - 3-2. Kernel Modules: can, can_raw, vcan
   - 3-3. vcan0 — Virtual CAN Interface
   - 3-4. candump — Live Frame Monitor
4. [python-can — CAN in Python](#4-python-can--can-in-python)
   - 4-1. Bus Abstraction
   - 4-2. Sending Frames (ecu-simulator)
   - 4-3. Receiving Frames (can-gateway)
   - 4-4. Float32 Little-Endian Encoding
5. [ECU Simulator Deep Dive (M4 Rewrite)](#5-ecu-simulator-deep-dive-m4-rewrite)
6. [CAN Gateway Deep Dive (New Service)](#6-can-gateway-deep-dive-new-service)
   - 6-1. CAN → VSS Mapping Table
   - 6-2. Blocking Iterator Pattern
   - 6-3. Exponential Back-off Reconnect
7. [The Gateway ECU Pattern](#7-the-gateway-ecu-pattern)
8. [WSL2 Custom Kernel — Why and How](#8-wsl2-custom-kernel--why-and-how)
   - 8-1. The SocketCAN + Docker Desktop Problem
   - 8-2. Building a Custom WSL2 Kernel
   - 8-3. Kernel Module Architecture
9. [Docker Host Networking and iptables](#9-docker-host-networking-and-iptables)
   - 9-1. Why iptables Failed
   - 9-2. network_mode: host
   - 9-3. nftables Masquerade Workaround
10. [Full M4 Architecture Walkthrough](#10-full-m4-architecture-walkthrough)
11. [Protocol Comparison: CAN vs gRPC vs MQTT vs DDS](#11-protocol-comparison-can-vs-grpc-vs-mqtt-vs-dds)
12. [Docker Compose Changes in M4](#12-docker-compose-changes-in-m4)
13. [Known Constraints and M5 Preview](#13-known-constraints-and-m5-preview)
14. [Review Quiz](#14-review-quiz)

---

## 1. What M4 Adds and Why

### The gap M4 closes

After M3, the SDV platform demonstrates centralized vehicle middleware (Kuksa), cloud connectivity (MQTT), and autonomous driving integration (ROS2/DDS). But ECUs still publish directly to the Databroker via gRPC — skipping the entire **hardware communication layer** that exists in every real vehicle.

In production, ECUs never talk to software middleware directly. They emit raw **CAN frames** onto a shared bus. A dedicated **Gateway ECU** reads those frames and translates them into the abstraction layer (VSS signals in the Databroker). M4 adds this missing layer:

```
Before M4:                        After M4:
──────────                        ─────────
ecu-simulator                     ecu-simulator
  │ gRPC SetCurrentValues            │ CAN frame TX (python-can)
  ▼                                  ▼
Kuksa Databroker               vcan0 (SocketCAN kernel interface)
                                     │ CAN frame RX
                                     ▼
                               can-gateway  ← NEW: Gateway ECU
                                     │ gRPC SetCurrentValues
                                     ▼
                               Kuksa Databroker
```

**What M4 teaches:**

| Concept | Description |
|---|---|
| ISO 11898 CAN bus | The physical/logical standard for ECU communication |
| CAN frame format | Arbitration ID, DLC, payload — the atoms of vehicle data |
| SocketCAN | Linux kernel's CAN subsystem — same API real automotive Linux ECUs use |
| Gateway ECU pattern | The architectural boundary between hardware CAN and software middleware |
| WSL2 kernel customization | How to extend the Linux kernel for automotive simulation |

---

## 2. CAN Bus Fundamentals

### 2-1. Why CAN Exists in Vehicles

**CAN (Controller Area Network)**, standardized as **ISO 11898**, was developed by Bosch in 1986 specifically for automotive use. Compared to general-purpose networking (Ethernet, TCP/IP), CAN has properties that make it ideal for vehicles:

| Property | CAN | Ethernet (TCP) |
|---|---|---|
| **Topology** | Single shared bus (two wires) | Point-to-point or switched |
| **Speed** | 125 kbps – 1 Mbps (CAN FD: up to 8 Mbps) | 100 Mbps – 10 Gbps |
| **Cost** | ~€0.50/node | ~€3–10/node |
| **Fault tolerance** | Two-wire differential; works if one wire breaks | Single fault → full failure |
| **Determinism** | Hard real-time; worst-case latency provable | Non-deterministic |
| **Protocol overhead** | Minimal (11-bit ID + max 8 bytes data) | Large (TCP/IP headers, handshakes) |

A modern production vehicle contains **40–100 ECUs** communicating over 4–8 separate CAN networks (powertrain, body, chassis, infotainment, diagnostics, …). Each ECU only needs to connect to two wires.

### 2-2. CAN Frame Format

```
┌────────────┬─────┬──────────────────────┬─────┐
│  ID        │ DLC │      Data (payload)   │ CRC │
│ (11 bits)  │(4 b)│  (0 – 8 bytes)        │     │
└────────────┴─────┴──────────────────────┴─────┘
  Arbitration  Data  Actual signal values
  ID = who     Length
  sent it      Code
```

- **Arbitration ID (11-bit standard / 29-bit extended):** Identifies the **message type**, not the sender. Lower ID = higher bus priority. When two ECUs transmit simultaneously, the one with the lower ID wins without collision.
- **DLC (Data Length Code):** Number of payload bytes, 0–8.
- **Data:** The raw signal payload — typically multiple signals packed into the 8 bytes using a DBC (Database CAN) file that specifies start bit, length, scale, and offset for each signal.
- **CRC:** Error detection; CAN hardware handles this automatically.

### 2-3. CAN ID and Arbitration

CAN is a **multi-master bus** — any ECU can transmit at any time. Collisions are resolved by **bitwise arbitration**:

1. Every transmitter simultaneously sends its ID bit-by-bit.
2. A dominant bit (0) always overwrites a recessive bit (1).
3. Any transmitter that sees its recessive bit overwritten stops transmitting.
4. The node with the **lowest ID** wins and continues transmitting.
5. Losers retry on the next bus-idle period.

This is why low IDs = high priority: `0x000` (engine speed) will always win over `0x7FF` (infotainment equalizer settings).

**M4 CAN ID mapping:**

| CAN ID | Priority | Signal | Real-world parallel |
|---|---|---|---|
| `0x100` | Medium-high | Vehicle.Speed | Wheel speed sensor broadcast |
| `0x200` | Medium | Battery SoC | BMS periodic broadcast |
| `0x300` | Medium-low | Cabin Temperature | HVAC ECU periodic broadcast |

### 2-4. Signal Encoding Inside a Frame

Real CAN signals use a **DBC (Database CAN) file** to define how bits inside the 8-byte payload map to physical signals (with scale, offset, min/max, unit). A single CAN frame often carries multiple signals packed at specific bit positions.

M4 uses a simplified encoding: **one signal per frame, float32 little-endian**.

```python
# Encode: Python float → 4 bytes (LE float32)
import struct
data = struct.pack('<f', 87.3)      # b'\x9a\x99\xae\x42'

# Decode: 4 bytes → Python float
value = struct.unpack('<f', data)[0]  # 87.3
```

`'<f'` = little-endian (`<`) 32-bit float (`f`). Little-endian is the standard byte order for CAN signals in Motorola/Intel signal encoding used in DBC files.

**Why float32, not integer?**

Real CAN signals use integers with scale+offset (e.g., raw value 0–255 → 0–255 km/h with scale=1.0, offset=0). Float32 is simpler for simulation — no scale/offset tables needed — and accurate to ~7 significant digits (sufficient for km/h, %, °C).

---

## 3. SocketCAN — Linux Kernel CAN Subsystem

### 3-1. The AF_CAN Address Family

**SocketCAN** is Linux's native CAN framework, introduced in kernel 2.6.25. It exposes CAN interfaces using the standard **BSD socket API** — the same API used for TCP/IP sockets (`socket()`, `bind()`, `recv()`, `send()`).

```c
// Opening a CAN socket in C (identical to a TCP socket, different family)
int sock = socket(AF_CAN, SOCK_RAW, CAN_RAW);   // AF_CAN: CAN address family
struct sockaddr_can addr = {
    .can_family  = AF_CAN,
    .can_ifindex = if_nametoindex("vcan0"),
};
bind(sock, (struct sockaddr*)&addr, sizeof(addr));
```

This design decision — using standard BSD sockets rather than a device-file API — means:
- Any existing socket-aware tool works with CAN (select/poll, sendmsg/recvmsg, epoll)
- Python's `socket` module can read CAN frames without any library
- `python-can` provides a higher-level interface on top of this

**Real-world significance:** Every automotive-grade Linux ECU (running Yocto/OpenEmbedded, for example) uses SocketCAN. Code written against SocketCAN runs on production hardware with zero changes — it's the same kernel interface whether the hardware is a virtual interface (vcan0), a USB CAN adapter (slcan), or a hardware CAN controller (can0 via PEAK, Kvaser, etc.).

### 3-2. Kernel Modules: can, can_raw, vcan

SocketCAN is implemented as loadable kernel modules:

| Module | Role |
|---|---|
| `can` | Core CAN protocol implementation; required by all other modules |
| `can_raw` | `CAN_RAW` socket support — raw frame read/write |
| `can_bcm` | `CAN_BCM` socket support — Broadcast Manager (periodic sends, filters) |
| `vcan` | Virtual CAN driver — creates `vcanN` interfaces backed by loopback, not hardware |

Loading order:
```bash
sudo modprobe can        # core first
sudo modprobe can_raw    # raw socket support
sudo modprobe vcan       # virtual interface support
```

These modules are **volatile** — they are unloaded when the kernel is shut down (or in WSL2, when `wsl --shutdown` is run). `scripts/setup-wsl2.sh` re-loads them at the start of each session.

### 3-3. vcan0 — Virtual CAN Interface

After loading the `vcan` module, a virtual CAN interface is created with standard Linux network commands:

```bash
sudo ip link add dev vcan0 type vcan   # create the interface
sudo ip link set vcan0 up              # bring it up
```

From the kernel's perspective, `vcan0` is identical to a hardware CAN interface (`can0`, `can1`, …) — it appears in `ip link show`, accepts the same socket calls, and shows in `candump`. The only difference: frames are looped back in-kernel rather than transmitted over physical wires.

```
ip link show vcan0:
  48: vcan0: <NOARP,UP,LOWER_UP> mtu 2060 qdisc noqueue state UNKNOWN
      link/can
```

`mtu 2060` = maximum CAN FD frame size (64 data bytes × variable). State `UNKNOWN` is normal for CAN interfaces — unlike Ethernet, CAN has no link-state negotiation.

### 3-4. candump — Live Frame Monitor

`candump` (from the `can-utils` package) displays raw CAN frames in real time:

```bash
candump vcan0
```

Output format:
```
  vcan0  100   [4]  66 66 82 42
  │       │     │    └──────────── payload (4 bytes, hex)
  │       │     └──────────────── DLC (data length)
  │       └────────────────────── CAN ID (hex, no 0x prefix)
  └────────────────────────────── interface name
```

Decoding `66 66 82 42` as float32 little-endian:
```python
import struct
struct.unpack('<f', bytes([0x66, 0x66, 0x82, 0x42]))[0]  # → 65.2 km/h
```

Other useful `can-utils` commands:

| Command | Purpose |
|---|---|
| `candump vcan0` | Monitor all frames |
| `cansend vcan0 100#6666 8242` | Inject a single frame manually |
| `cangen vcan0 -g 100 -I 100` | Generate random frames for testing |
| `canplayer -I logfile.log` | Replay a recorded CAN log |

---

## 4. python-can — CAN in Python

### 4-1. Bus Abstraction

`python-can` provides a unified Python API for CAN communication across many hardware backends:

| Interface name | Hardware |
|---|---|
| `socketcan` | Linux SocketCAN (`vcan0`, `can0`, `slcan0`, …) |
| `pcan` | PEAK PCAN USB/PCI adapters |
| `kvaser` | Kvaser hardware |
| `vector` | Vector VN/CANalyzer hardware |
| `virtual` | In-process loopback (no kernel, for unit tests) |

M4 uses `socketcan` — same API, same code, whether the interface is virtual (`vcan0`) or hardware (`can0`).

```python
import can

bus = can.interface.Bus(channel="vcan0", interface="socketcan")
```

The `channel` matches the Linux interface name; `interface` selects the backend. This single line is the only change needed to switch from virtual testing to real hardware.

### 4-2. Sending Frames (ecu-simulator)

```python
import can, struct

def send_signal(bus: can.BusABC, can_id: int, value: float) -> None:
    data = struct.pack('<f', value)          # encode float → 4 bytes LE
    msg = can.Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=False,               # use 11-bit standard ID
    )
    bus.send(msg)
```

`is_extended_id=False` → 11-bit standard CAN ID (range 0x000–0x7FF).
`is_extended_id=True` → 29-bit extended CAN ID (range 0x00000000–0x1FFFFFFF), used in CAN FD and J1939.

### 4-3. Receiving Frames (can-gateway)

```python
bus = can.interface.Bus(channel="vcan0", interface="socketcan")

for msg in bus:          # blocking iterator — yields on every received frame
    can_id = msg.arbitration_id
    payload = bytes(msg.data[:4])
    value = struct.unpack('<f', payload)[0]
```

`for msg in bus:` is a **blocking iterator** — it calls `recv()` internally and yields control to the loop body only when a frame arrives. There is no busy-wait or polling; the thread sleeps in the kernel until a frame appears. This is efficient and appropriate for a gateway that must process every frame.

### 4-4. Float32 Little-Endian Encoding

```
Physical value: 65.2 km/h

struct.pack('<f', 65.2):
  ┌────────────────────────────────────────┐
  │  Byte 0  │  Byte 1  │  Byte 2  │  Byte 3  │
  │  0x66    │  0x66    │  0x82    │  0x42    │
  └────────────────────────────────────────┘
  ← LSB (Least Significant Byte first = little-endian)

CAN frame payload (as seen in candump):
  100  [4]  66 66 82 42
```

IEEE 754 float32 representation of 65.2:
- Sign: 0
- Exponent: 10000101 (133 − 127 = 6, so 2^6 = 64)
- Mantissa: 00000101000111101011100...
- Full binary: 0 10000101 0000010111000010100011...

The exact byte values can be verified:
```python
>>> import struct
>>> struct.pack('<f', 65.2).hex()
'6666824 2'  # 66 66 82 42
>>> struct.unpack('<f', bytes.fromhex('66668242'))[0]
65.19999694824219  # float32 precision limit: ~7 significant digits
```

Note the tiny precision loss (65.2 → 65.19999694824219). This is inherent to float32 and acceptable for vehicle signals (0.001 km/h precision error is irrelevant for vehicle speed control).

---

## 5. ECU Simulator Deep Dive (M4 Rewrite)

The M4 ECU Simulator is a complete rewrite of the M1–M3 version. The physics simulation (sinusoidal signals, VehicleState class) is unchanged; only the **output mechanism** changed.

### M1–M3 vs M4 comparison

| Aspect | M1–M3 | M4 |
|---|---|---|
| Output protocol | gRPC `SetCurrentValues()` | SocketCAN CAN frame TX |
| Dependency | `kuksa-client==0.4.3` | `python-can==4.3.1` |
| Middleware awareness | Yes — spoke VSS paths | No — only knows CAN IDs |
| Runs in | Docker container | WSL2 directly (SocketCAN not available in Docker Desktop) |

### M4 CAN ID assignment

```python
CAN_IDS: dict[str, int] = {
    "Vehicle.Speed":                                              0x100,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current":  0x200,
    "Vehicle.Cabin.HVAC.AmbientAirTemperature":                  0x300,
}
```

These IDs are arbitrary simulation IDs. In a real vehicle, CAN IDs are defined in the **network database (DBC file)** which is maintained by the OEM's systems engineering team. Every ECU supplier receives the DBC file during project kick-off and hard-codes the IDs in firmware.

### Main loop

```python
def run(vehicle: VehicleState) -> None:
    while True:
        try:
            bus = can.interface.Bus(channel=CAN_INTERFACE, interface="socketcan")
            while True:
                vehicle.advance()
                send_signal(bus, 0x100, vehicle.speed())
                send_signal(bus, 0x200, vehicle.battery_soc())
                send_signal(bus, 0x300, vehicle.cabin_temperature())
                time.sleep(UPDATE_INTERVAL)
        except Exception as exc:
            log.warning(f"CAN error: {exc}")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)
```

The outer `while True` is the reconnect loop. If `vcan0` disappears (e.g., `wsl --shutdown` without re-running setup), the simulator retries with exponential back-off rather than crashing. The inner `while True` is the 1 Hz signal publication loop.

**Why exponential back-off for a local kernel interface?**

SocketCAN interfaces are transient — they disappear on kernel module unload. An ECU in a real vehicle may also lose its CAN hardware connection during a power cycle or a cable fault. The back-off pattern is defensive and consistent with the rest of the project.

---

## 6. CAN Gateway Deep Dive (New Service)

### 6-1. CAN → VSS Mapping Table

```python
CAN_TO_VSS: dict[int, tuple[str, str]] = {
    0x100: ("Vehicle.Speed",                                              "km/h"),
    0x200: ("Vehicle.Powertrain.TractionBattery.StateOfCharge.Current",  "percent"),
    0x300: ("Vehicle.Cabin.HVAC.AmbientAirTemperature",                  "celsius"),
}
```

The gateway knows nothing about physics — it is a **pure translator**. For each CAN ID it knows: which VSS path it maps to, and which unit label to log. Adding a new signal requires only one entry here.

This is the same single-source-of-truth pattern used in `SIGNALS` (dashboard/mqtt-bridge) and `SIGNAL_MAP` (ros2-bridge).

### 6-2. Blocking Iterator Pattern

```python
def run() -> None:
    while True:
        try:
            bus = can.interface.Bus(channel=CAN_INTERFACE, interface="socketcan")
            with VSSClient(DATABROKER_HOST, DATABROKER_PORT) as kuksa:
                for msg in bus:                                    # ← blocks here
                    if msg.arbitration_id not in CAN_TO_VSS:
                        continue                                   # unknown ID: skip
                    path, unit = CAN_TO_VSS[msg.arbitration_id]
                    value = round(struct.unpack('<f', bytes(msg.data[:4]))[0], 3)
                    kuksa.set_current_values({path: Datapoint(value)})
        except Exception as exc:
            log.warning(f"Connection error: {exc}")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)
```

**Key design decisions:**

1. **`for msg in bus` as the event loop.** The gateway is entirely event-driven — no timer, no polling. The thread wakes only when a CAN frame arrives. This is CPU-efficient and means the gateway processes frames as fast as the CAN bus delivers them.

2. **Skip unknown IDs.** `if msg.arbitration_id not in CAN_TO_VSS: continue` — in a real vehicle, a CAN network carries hundreds of different message IDs. The gateway only translates the signals it is responsible for.

3. **Single `VSSClient` connection for the lifetime of the CAN session.** A new gRPC connection is opened after each reconnect. This is a deliberate trade-off: establishing a gRPC connection per frame would be extremely expensive; holding one persistent connection is the right pattern for a streaming translator.

4. **`round(..., 3)`** — float32 unpacking produces values like `65.19999694824219`. Rounding to 3 decimal places avoids storing meaningless precision in the Databroker and keeps log output readable.

### 6-3. Exponential Back-off Reconnect

The gateway must reconnect to **two** systems: SocketCAN (vcan0) and the Databroker. Both are handled by the same outer `while True` + exponential back-off:

```
Failure → log warning → sleep(2) → retry
Second failure → sleep(4) → retry
Third failure → sleep(8) → retry
...
Nth failure → sleep(min(2^N × 2, 30)) → retry
Success → reset delay to 2
```

The unified retry loop means both a missing `vcan0` and a crashed Databroker are handled identically, without duplicating reconnect logic.

---

## 7. The Gateway ECU Pattern

This is the most important real-world concept introduced in M4.

### What is a Gateway ECU?

In every modern vehicle, there is at least one dedicated **Central Gateway ECU** (CGW). Its responsibilities:

1. **Protocol translation:** Receive raw CAN frames; translate to higher-level signals (SOME/IP, VSS, proprietary formats)
2. **Network isolation:** Connect multiple CAN networks (powertrain, body, chassis, diagnostics) that must not directly communicate
3. **Security boundary:** Filter, validate, and rate-limit messages crossing domain boundaries
4. **Firewall:** Block unauthorized remote access (OBD-II diagnostic CAN should not be able to write to the powertrain CAN)

### M4 Gateway vs. production Gateway ECU

| Feature | M4 `can-gateway` | Production CGW (e.g., Bosch CGW, NXP S32G) |
|---|---|---|
| Protocol translation | CAN frame → VSS gRPC | CAN → SOME/IP → VSS → OTA cloud |
| Multi-network support | One vcan0 | 4–8 physical CAN networks |
| Security filtering | None (simulation) | Firewall rules, AUTOSAR SecOC |
| Hardware | Python on x86 WSL2 | Dedicated MCU, AUTOSAR BSW |
| COVESA VSS awareness | Yes (educational) | Increasingly yes (SDV migration) |

### Why Gateway ECUs are the key to SDV migration

In a traditional vehicle, application software talks directly to ECUs. In an SDV, all traffic passes through the Central Gateway → Central Vehicle Computer → VAL (Databroker). This gives OEMs the ability to:

- **OTA-update any ECU** without changing application software
- **Add new features** (new app subscribes to existing VSS signals) without touching ECU firmware
- **Replace a physical ECU** with a software-defined equivalent running in the cloud

The Gateway ECU is the hardware bridge that makes this architectural decoupling possible.

---

## 8. WSL2 Custom Kernel — Why and How

### 8-1. The SocketCAN + Docker Desktop Problem

M4 requires SocketCAN (`AF_CAN` sockets and `vcan0` interface). This creates two problems on Windows:

**Problem 1: Docker containers cannot access vcan0**

Docker Desktop for Windows runs containers inside a separate Linux VM (HyperV). The `vcan0` interface created in WSL2 Ubuntu lives in WSL2's kernel namespace — completely isolated from Docker Desktop's VM. No amount of port mapping or volume mounting can bridge a kernel-level network interface between two separate VMs.

**Solution:** Run the CAN-dependent services (ecu-simulator, can-gateway) directly in WSL2 Ubuntu — same kernel, same namespace as vcan0.

**Problem 2: WSL2 default kernel lacks vcan module**

The default WSL2 kernel shipped with Docker Desktop (6.6.x) is compiled without `CONFIG_CAN`, `CONFIG_CAN_RAW`, and `CONFIG_CAN_VCAN`. `modprobe vcan` fails because the module simply does not exist.

**Solution:** Build a custom WSL2 kernel from Microsoft's source tree with CAN support enabled.

### 8-2. Building a Custom WSL2 Kernel

Microsoft publishes the WSL2 kernel source at `github.com/microsoft/WSL2-Linux-Kernel`. M4 uses tag `linux-msft-wsl-6.18.26.1` — chosen over 6.6 because it is compatible with **GCC 15** (Ubuntu 26.04's default compiler). The 6.6 kernel fails to compile under GCC 15 due to C23 incompatibilities.

Required kernel configuration changes (over the default WSL2 config):

```bash
scripts/config --enable  CONFIG_CAN
scripts/config --enable  CONFIG_CAN_RAW
scripts/config --enable  CONFIG_CAN_BCM
scripts/config --module  CONFIG_CAN_VCAN   # compile as loadable module
```

The compiled kernel binary is placed at `C:\Users\takum\wsl_kernel` and activated via `C:\Users\takum\.wslconfig`:

```ini
[wsl2]
kernel=C:\\Users\\takum\\wsl_kernel
```

After `wsl --shutdown` and restart, WSL2 boots with the custom kernel:

```bash
uname -r
# → 6.18.26.1-microsoft-standard-WSL2+
```

### 8-3. Kernel Module Architecture

```
Linux kernel (6.18.26.1-microsoft-standard-WSL2+)
├── Built-in drivers
│   ├── nf_tables          ← nftables (used for Docker networking workaround)
│   ├── x_tables           ← iptables/nftables shared infrastructure
│   └── ...
└── Loadable modules (/lib/modules/6.18.26.1-microsoft-standard-WSL2+/)
    ├── kernel/net/can/can.ko            ← CAN core
    ├── kernel/net/can/can_raw.ko        ← CAN_RAW socket
    ├── kernel/net/can/vcan.ko           ← virtual CAN interface  ← KEY
    └── ... (921 total modules)
```

**Why is vcan a module rather than built-in?**

Kernel modules can be loaded/unloaded at runtime without rebooting. Building vcan as a module (`=m`) rather than built-in (`=y`) means:
- The kernel binary is smaller (module code is loaded only when needed)
- You can use `modprobe vcan` / `rmmod vcan` without rebooting
- Standard practice for optional/hardware-specific drivers

---

## 9. Docker Host Networking and iptables

### 9-1. Why iptables Failed

The custom 6.18 kernel, while having CAN modules, is missing `ip_tables.ko` — the kernel module for the legacy iptables NAT subsystem. Docker's bridge networking relies on iptables NAT (specifically, the `MASQUERADE` rule) to give containers internet access and map host ports to container ports.

Without `ip_tables.ko`, `dockerd` fails at startup:

```
Module ip_tables not found in directory /lib/modules/6.18.26.1-microsoft-standard-WSL2+
iptables v1.8.11: can't initialize iptables table `nat': Table does not exist
```

Root cause: during the custom kernel build, `ip_tables` was configured as `CONFIG_IP_NF_IPTABLES=m` (module) but the module was not installed into `/lib/modules/`. The modules directory has `nf_tables.ko` (nftables, the modern replacement) but not `ip_tables.ko` (the legacy system).

### 9-2. network_mode: host

The solution is to use `network_mode: host` in Docker Compose and start `dockerd` with `--iptables=false`.

With host networking:

| Aspect | Bridge networking (default) | Host networking (M4) |
|---|---|---|
| Container network namespace | Isolated (own IP, docker0 bridge) | Shared with WSL2 host |
| Inter-container communication | Via container hostname DNS (`databroker`, `mosquitto`) | Via `localhost` |
| Port mapping (ports:) | Required, uses iptables NAT | Not needed — services bind to host directly |
| Host access | Via mapped ports | Direct — container port = host port |
| Windows access | Via WSL2 port forwarding + iptables | Via WSL2 port forwarding (auto, no iptables needed) |

**Environment variable changes:**

```yaml
# M1–M3 (bridge networking):
DATABROKER_HOST: databroker     # Docker DNS resolves container name
MQTT_HOST: mosquitto

# M4 (host networking):
DATABROKER_HOST: localhost      # all services on same loopback
MQTT_HOST: localhost
```

**Why Windows localhost:8501 still works:**

WSL2 has a built-in port forwarding feature that automatically forwards any port listening on `127.0.0.1` in WSL2 to `127.0.0.1` on Windows. Since the dashboard container (host networking) binds to WSL2's `127.0.0.1:8501`, Windows `localhost:8501` works without any explicit port mapping or iptables rules.

### 9-3. nftables Masquerade Workaround

With `--iptables=false`, containers can communicate with each other and with the WSL2 host, but they cannot reach the internet (needed for `docker build` to run `apt-get`, `pip install`, etc.).

Docker normally handles internet access for containers by installing an iptables `MASQUERADE` rule. Without that, container traffic has a private source IP (172.17.x.x) that the internet cannot route back to.

The fix: manually install the equivalent nftables masquerade rule:

```bash
sudo nft add table ip nat
sudo nft add chain ip nat POSTROUTING '{ type nat hook postrouting priority 100; policy accept; }'
sudo nft add rule ip nat POSTROUTING masquerade
```

This rule says: any packet leaving the host (outgoing), rewrite its source IP to the host's external IP. Replies come back to the host, which reverses the translation and delivers them to the container.

This is handled automatically by `scripts/setup-wsl2.sh`.

---

## 10. Full M4 Architecture Walkthrough

Trace a single speed update from the physics simulation through the complete M4 stack:

```
1. ECU Simulator (services/ecu-simulator/main.py — WSL2)
   ├─ Physics: speed = 65 + 27.5 × sin(2π × t / 60) + Gaussian(0, 2) = 65.2 km/h
   ├─ Encode: struct.pack('<f', 65.2) = b'\x66\x66\x82\x42'
   └─ CAN TX: can.Message(arbitration_id=0x100, data=b'\x66\x66\x82\x42')
                    │
                    ▼ SocketCAN kernel loopback
2. vcan0 (Linux kernel virtual CAN interface)
   └─ Frame: 0x100 [4] 66 66 82 42
                    │
                    ▼ for msg in bus: (blocking recv)
3. CAN Gateway (services/can-gateway/main.py — WSL2)
   ├─ Receive: msg.arbitration_id = 0x100, msg.data = b'\x66\x66\x82\x42'
   ├─ Lookup: CAN_TO_VSS[0x100] = ("Vehicle.Speed", "km/h")
   ├─ Decode: struct.unpack('<f', msg.data[:4]) = (65.19999...,)
   ├─ Round: round(65.1999..., 3) = 65.2
   └─ gRPC: kuksa.set_current_values({"Vehicle.Speed": Datapoint(65.2)})
                    │
                    ▼ gRPC SetCurrentValues
4. Kuksa Databroker (:55555 — Docker, host networking)
   ├─ Stores: Vehicle.Speed = 65.2 km/h
   └─ Notifies all subscribers
                    │
         ┌──────────┼───────────────────────┐
         ▼          ▼                       ▼
5a. dashboard    5b. mqtt-bridge        5c. ros2-bridge
    gRPC poll        gRPC subscribe         gRPC subscribe
    → :8501          → MQTT publish         → DDS /vehicle/speed
    Speed: 65.2      sdv/vehicle-001/       Float32.data = 65.2
                     Vehicle/Speed
                     {"value": 65.2}
                     → Mosquitto :1883
```

Steps 5a, 5b, 5c happen concurrently — same as M3. M4's contribution is steps 1–3: the physical CAN layer between the ECU and the middleware.

---

## 11. Protocol Comparison: CAN vs gRPC vs MQTT vs DDS

| Feature | CAN (ISO 11898) | gRPC | MQTT | DDS (ROS2) |
|---|---|---|---|---|
| **Architecture** | Multi-master shared bus | Client–server | Broker hub | Brokerless P2P |
| **Transport** | Differential serial (hardware) | HTTP/2 (TCP) | TCP | UDP (+ TCP) |
| **Addressing** | Message ID (not node address) | Host:port | Topic string | Topic + Domain ID |
| **Typing** | Raw bytes (DBC defines meaning) | Protobuf | None (byte payload) | IDL strongly typed |
| **Speed** | 125 kbps – 8 Mbps | Unlimited (TCP) | Unlimited (TCP) | Very low latency (UDP) |
| **Latency** | Deterministic, sub-ms | ms–10 ms | ms–100 ms | Sub-ms real-time |
| **Reliability** | CRC + ACK, automatic retransmit | TCP guarantees | QoS 0/1/2 | DDS QoS policies |
| **Max payload** | 8 bytes (64 bytes CAN FD) | Unlimited | Unlimited | Unlimited |
| **Discovery** | None (static IDs in DBC) | Manual | Manual | Automatic (SDP) |
| **Primary role in SDV** | ECU-to-ECU hardware layer | In-vehicle VAL API | V2C cloud telemetry | AD stack middleware |
| **AUTOSAR Adaptive** | Yes (com stack) | Yes (ara::com) | No | Yes (DDS profile) |

### The SDV protocol stack

```
Physical vehicle:                This project (M1–M4):
─────────────────                ──────────────────────
ECUs ──CAN──▶ Gateway ECU        ecu-simulator ──CAN──▶ can-gateway
                │                                           │
                │ proprietary / SOME/IP / VSS              │ gRPC
                ▼                                           ▼
          Central Vehicle Computer                   Kuksa Databroker
          (VAL / Databroker)                              │
                │                                    ┌────┼────┐
           ┌────┼────┐                               │    │    │
           │    │    │                            gRPC  MQTT  DDS
           │    │    │                            poll  pub  pub
          HMI  Cloud  AD                        dashboard mqtt ros2
         (M1)  (M2)  (M3)
```

M4 closes the loop: every layer of the real SDV stack — hardware CAN → Gateway ECU → Middleware → Applications — is now represented in the simulation.

---

## 12. Docker Compose Changes in M4

### Removed from Docker Compose

| Service | Reason |
|---|---|
| `ecu-simulator` | Now runs in WSL2 directly (needs SocketCAN kernel access) |

### Services unchanged but reconfigured

All remaining services (`databroker`, `dashboard`, `mosquitto`, `mqtt-bridge`, `ros2-bridge`, `ros2-subscriber`) are now configured with **host networking**:

```yaml
# M3 (bridge networking):
dashboard:
  ports:
    - "8501:8501"
  environment:
    DATABROKER_HOST: databroker
  networks:
    - sdv-net

# M4 (host networking):
dashboard:
  network_mode: host
  environment:
    DATABROKER_HOST: localhost
  # no ports: needed — binds directly to host loopback
```

### CycloneDDS configuration change

With bridge networking (M3), DDS needed explicit unicast peers (container names):

```yaml
# M3: unicast peer workaround for Docker bridge
x-cyclonedds-config: &cyclonedds-config
  CYCLONEDDS_URI: >-
    <CycloneDDS><Domain>
    <General><AllowMulticast>false</AllowMulticast></General>
    <Discovery><Peers>
    <Peer Address="ros2-bridge"/>
    <Peer Address="ros2-subscriber"/>
    </Peers></Discovery>
    </Domain></CycloneDDS>
```

With host networking (M4), all containers share the same loopback interface — DDS multicast works:

```yaml
# M4: multicast works on shared loopback (host networking)
x-cyclonedds-config: &cyclonedds-config
  CYCLONEDDS_URI: >-
    <CycloneDDS><Domain>
    <General><AllowMulticast>true</AllowMulticast></General>
    </Domain></CycloneDDS>
```

### WSL2 session bootstrap script

`scripts/setup-wsl2.sh` automates the per-session setup that the previous M3 `docker compose up` handled automatically:

```bash
# 1. Load SocketCAN kernel modules
sudo modprobe can && sudo modprobe can_raw && sudo modprobe vcan

# 2. Create and bring up vcan0
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up

# 3. Load netfilter modules Docker needs (ip_tables unavailable)
sudo modprobe nf_tables xt_addrtype nf_nat nft_nat nft_chain_nat br_netfilter overlay

# 4. Set nftables masquerade for container internet access
sudo nft add table ip nat
sudo nft add chain ip nat POSTROUTING '{ type nat hook postrouting priority 100; }'
sudo nft add rule ip nat POSTROUTING masquerade

# 5. Start Docker Engine (without iptables)
sudo dockerd --iptables=false > /tmp/dockerd.log 2>&1 &
```

---

## 13. Known Constraints and M5 Preview

### M4 Known Constraints

| Constraint | Description | Planned resolution |
|---|---|---|
| One signal per CAN frame | Real DBC files pack multiple signals into one frame | DBC parsing with `cantools` library in a future milestone |
| No CAN FD | Standard CAN (8-byte payload); CAN FD allows 64 bytes | Extend encoding format |
| No signal scaling/offset | Float32 direct; real CAN uses integer + scale + offset | Introduce DBC-based decoding |
| `ip_tables.ko` missing | Custom kernel built without legacy iptables NAT module | Rebuild kernel with `CONFIG_IP_NF_IPTABLES=y`; for now workaround via nftables |
| Session-only modules | `modprobe` results lost on `wsl --shutdown`; must re-run `setup-wsl2.sh` | Could automate via WSL2 `/etc/rc.local` equivalent |
| Read-only gateway | `can-gateway` only writes to Databroker; no reverse (Databroker → CAN TX) | Bi-directional gateway: Databroker actuator signals → CAN TX → ECU |

### M5 Preview: AI Agent Integration

M5 will add an AI monitoring agent that subscribes to the Databroker (same pattern as `mqtt-bridge`), interprets signal patterns using the Claude API, and publishes natural-language alerts to MQTT:

```
M5 Architecture (proposed):

Kuksa Databroker
      │
      │ gRPC subscribe (same as mqtt-bridge)
      ▼
 ai-agent (new service)
      │
      │ Anthropic Claude API call
      │ "Speed=87 km/h, SoC=23% — Battery critically low at highway speed"
      ▼
 MQTT: sdv/vehicle-001/alerts/critical
      │
      ▼
 Cloud subscribers (monitoring dashboards, fleet management)
```

This demonstrates **AI-native SDV patterns** being explored by companies like Continental (AI-powered predictive maintenance), Bosch (in-vehicle AI for anomaly detection), and Volkswagen (CARIAD AI platform).

---

## 14. Review Quiz

**Q1.** What problem does M4 solve that M1–M3 leave unaddressed?

**Q2.** What is a CAN Arbitration ID, and what determines which ECU wins when two transmit simultaneously?

**Q3.** Explain the difference between `can.Message(is_extended_id=False)` and `is_extended_id=True`. When would you use each?

**Q4.** Why does `for msg in bus:` not consume CPU at 100% while waiting for a CAN frame?

**Q5.** A CAN frame arrives with payload `9A 99 8B 42`. What vehicle speed does this represent? Show your calculation.

**Q6.** Why can't the ECU Simulator and CAN Gateway run as Docker containers in M4? What architectural constraint prevents it?

**Q7.** What is a Gateway ECU, and why is it the key architectural boundary in an SDV platform?

**Q8.** The M4 custom kernel has `CONFIG_CAN_VCAN=m` (module) rather than `=y` (built-in). What is the difference, and why is module preferred for device drivers?

**Q9.** Why did switching from bridge networking to `network_mode: host` require changing `DATABROKER_HOST` from `databroker` to `localhost`?

**Q10.** Why did adding `.wslconfig` with the custom kernel break Docker Desktop, and how was it resolved?

**Q11.** What two nftables rules replace the iptables NAT masquerade that Docker normally manages, and why are they needed?

**Q12.** M3 required `AllowMulticast: false` in CycloneDDS config. M4 uses `AllowMulticast: true`. Why does the same ROS2 setup work with multicast in M4 but not M3?

---

### Answers

**A1.** M1–M3 had the ECU Simulator publish signals directly to Kuksa Databroker via gRPC — bypassing the entire hardware CAN layer that exists in real vehicles. Real ECUs communicate over ISO 11898 CAN bus; a Gateway ECU translates CAN frames to middleware signals. M4 adds this layer: ECU Simulator → CAN frames → vcan0 → CAN Gateway → gRPC → Databroker.

**A2.** The Arbitration ID identifies the message type (not the sender). When two ECUs transmit simultaneously, each sends its ID bit-by-bit. A dominant bit (0) overwrites a recessive bit (1) on the bus. Any transmitter that observes its recessive bit overwritten stops transmitting. The node with the **lowest ID** always wins because it has the most leading 0-bits.

**A3.** `is_extended_id=False` uses the 11-bit standard CAN ID (range 0x000–0x7FF), the most common format in automotive CAN. `is_extended_id=True` uses the 29-bit extended CAN ID (range 0–0x1FFFFFFF), used in CAN FD, J1939 (heavy trucks), and OBD-II diagnostic messages. M4 uses standard 11-bit IDs for simplicity.

**A4.** `for msg in bus:` calls the SocketCAN `recv()` system call internally, which puts the thread into a **blocking sleep** in the kernel. The OS scheduler does not wake the thread until a CAN frame arrives on the socket. There is no busy-wait or polling loop — the CPU is free to run other processes while waiting.

**A5.** `9A 99 8B 42` → `struct.unpack('<f', bytes([0x9A, 0x99, 0x8B, 0x42]))[0]` = 69.8 km/h. (Little-endian: byte order is reversed for IEEE 754 interpretation; the exponent field `0x42` encodes 2^6=64 ≈ 70 range.)

**A6.** SocketCAN interfaces (`vcan0`) are Linux kernel network interfaces — they exist only within the kernel's network namespace. Docker Desktop for Windows runs containers inside a separate HyperV VM with its own isolated kernel. There is no mechanism to share a kernel-level network interface between WSL2 Ubuntu's kernel namespace and Docker Desktop's VM kernel namespace. Running the CAN services directly in WSL2 gives them the same kernel as `vcan0`.

**A7.** A Gateway ECU sits at the boundary between physical CAN networks and software middleware (VAL/Databroker). It receives raw CAN frames (hardware layer), translates signal IDs and byte encodings to higher-level named signals (VSS, SOME/IP), and filters/validates messages crossing network domain boundaries. It is the key architectural boundary because it enables the SDV abstraction: application software only knows VSS paths, never CAN IDs or byte encodings. OEMs can replace ECU hardware or add new signals without changing application code.

**A8.** `CONFIG_CAN_VCAN=m` compiles vcan as a `.ko` file that is loaded on demand with `modprobe vcan`. `=y` compiles it directly into the kernel binary. Modules are preferred for device drivers because: (1) the kernel binary is smaller, (2) modules can be loaded/unloaded at runtime without rebooting, (3) optional or hardware-specific drivers are naturally optional. The trade-off: modules must be re-loaded after `wsl --shutdown`.

**A9.** With bridge networking, Docker creates a private network (`sdv-net`) where containers communicate using Docker's internal DNS — the hostname `databroker` resolves to the container's bridge IP. With `network_mode: host`, all containers share WSL2's network namespace and loopback — there is no Docker DNS, and no container-specific IPs. All services bind to `127.0.0.1` (localhost), so `DATABROKER_HOST: localhost` is the correct address.

**A10.** Docker Desktop for Windows uses a separate HyperV VM (`docker-desktop` WSL distro). When `.wslconfig` specified the custom kernel, all WSL2 distros — including `docker-desktop` — booted with it. The custom kernel was missing `ip_tables.ko` (legacy iptables) and had issues with the DirectX Graphics Kernel (`dxgk`) interface that Docker Desktop depends on. The `docker_engine` named pipe never appeared, meaning Docker Desktop's engine couldn't start. Resolution: switched from Docker Desktop to Docker Engine installed directly in WSL2 Ubuntu, using `dockerd --iptables=false` and nftables masquerade instead of iptables NAT.

**A11.** Two nftables commands: (1) `nft add chain ip nat POSTROUTING { type nat hook postrouting priority 100; }` — creates a NAT chain in the postrouting hook (fires after routing decision, on outgoing packets). (2) `nft add rule ip nat POSTROUTING masquerade` — rewrites the source IP of outgoing packets to the host's external IP. These are needed because Docker containers have private IPs (172.17.x.x) that are not routable on the internet. Without masquerade, container responses would never arrive (the internet doesn't know how to route to 172.17.0.0/16).

**A12.** In M3, `ros2-bridge` and `ros2-subscriber` ran in isolated Docker bridge network containers. DDS multicast UDP packets sent by one container were not forwarded to the other by Docker's bridge driver — multicast routing on Docker bridge networks is unreliable. In M4, both services use `network_mode: host` and share WSL2's loopback interface. Multicast on `lo` (loopback) works correctly — the kernel forwards multicast packets within the same interface, so both ROS2 nodes discover each other automatically.

---

*This document is part of the mini-sdv-platform living documentation. See also: `docs/learning/architecture_review_m3.md` for M3 concepts (ROS2, DDS, COVESA VSS).*
