# Technical Requirements Document (TRD)
## Milestone 2: MQTT Cloud Bridge
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | TRD |
| Milestone | 2 — MQTT Cloud Bridge |
| Status | Draft |
| Hypothesis Layer | Implementation (L5) |
| Created | 2026-05-23 |
| Version | 1.0 |
| Depends On | [FRD.md](FRD.md) · Milestone 1 TRD |

---

## 1. Architecture Overview

### 1.1 Updated Service Map

| Service | Type | Image / Build | Internal Port | Host Port | Role |
|---|---|---|---|---|---|
| `databroker` | Upstream | `ghcr.io/eclipse/kuksa.val/databroker:0.4.4` | 55555 (gRPC) | — | Vehicle signal middleware (M1) |
| `ecu-simulator` | Custom | `./services/ecu-simulator` | — | — | Signal producer (M1) |
| `dashboard` | Custom | `./services/dashboard` | 8501 (HTTP) | 8501 | Signal visualizer (M1) |
| `mosquitto` | Upstream | `eclipse-mosquitto:2.0` | 1883 (MQTT) | **1883** | MQTT broker (M2 NEW) |
| `mqtt-bridge` | Custom | `./services/mqtt-bridge` | — | — | Kuksa → MQTT forwarder (M2 NEW) |

### 1.2 Network Topology

All services remain on `sdv-net` (bridge network). M2 adds one new exposed port: `1883` for host-side CLI testing.

```
Host Machine
  └── :8501 ──▶ dashboard (Streamlit UI)       [M1]
  └── :1883 ──▶ mosquitto (MQTT, CLI access)   [M2 NEW]

Docker network: sdv-net
  ├── databroker    :55555  (gRPC internal)
  ├── ecu-simulator         (outbound gRPC → databroker)
  ├── dashboard     :8501   (outbound gRPC → databroker)
  ├── mosquitto     :1883   (inbound MQTT from mqtt-bridge; exposed to host)
  └── mqtt-bridge           (outbound gRPC subscribe → databroker)
                            (outbound MQTT publish → mosquitto)
```

### 1.3 Startup Order

```
Phase 1 ── databroker      health: TCP :55555
Phase 1 ── mosquitto       health: TCP :1883

Phase 2 ── ecu-simulator   depends_on: databroker healthy    [M1]
           dashboard        depends_on: databroker healthy    [M1]
           mqtt-bridge      depends_on: databroker healthy
                                      + mosquitto healthy    [M2]
```

---

## 2. The Core M2 Technical Decision: Subscribe vs. Poll

This is the most important technical concept introduced in Milestone 2.

### 2.1 Comparison

| | M1 Dashboard (poll) | M2 MQTT Bridge (subscribe) |
|---|---|---|
| Method | `client.get_current_values()` | `client.subscribe_current_values()` |
| Mechanism | Pull: requests current values on a fixed timer | Push: server sends updates as they arrive |
| Latency | Up to `REFRESH_INTERVAL` (1 s) | Near-zero — receives update within milliseconds of ECU publish |
| CPU profile | Constant gRPC calls even if no signal changed | Only active when signal changes occur |
| Threading | Single-threaded (fits Streamlit rerun model) | Blocking iterator — runs in its own thread or process |
| SDV use case | UI refresh, human-readable display | Forwarding, bridging, recording, alerting |

### 2.2 Why the Bridge MUST Use Subscribe (DR-15)

A forwarding bridge must transmit data as it changes. Using polling would introduce an artificial maximum latency equal to the poll interval (1 s), which:
1. Breaks the near-real-time requirement for safety-relevant signals
2. Is architecturally incorrect — a bridge is a reactive component, not a scheduled job
3. Would send duplicate payloads when no signal changes occur (unnecessary MQTT traffic)

### 2.3 subscribe_current_values() API (kuksa-client 0.4.3)

```python
from kuksa_client.grpc import VSSClient

with VSSClient(host, port) as client:
    # Returns a blocking iterator.
    # Each iteration yields a dict[str, Datapoint] containing the signals
    # that changed in that update batch.
    for updates in client.subscribe_current_values(signal_paths):
        for path, datapoint in updates.items():
            if datapoint is not None and datapoint.value is not None:
                # Process the update immediately
                forward_to_mqtt(path, datapoint)
```

The iterator blocks until an update arrives, then yields. This is the correct pattern for a long-running bridge service.

---

## 3. Service Specifications

### 3.1 Mosquitto Broker

| Property | Value |
|---|---|
| Docker Image | `eclipse-mosquitto:2.0` |
| MQTT Port | 1883 (exposed to host) |
| Config | Loaded from `./config/mosquitto/mosquitto.conf` |
| Auth Mode | Anonymous (no credentials required) |
| Persistence | Disabled (in-memory only for M2) |
| Restart Policy | `on-failure` |

**Mosquitto configuration (`mosquitto.conf`):**
```conf
listener 1883
allow_anonymous true
```

**Health check:**
```yaml
healthcheck:
  test: ["CMD-SHELL", "timeout 1 bash -c 'cat < /dev/null > /dev/tcp/localhost/1883'"]
  interval: 5s
  timeout: 3s
  retries: 10
  start_period: 5s
```

### 3.2 MQTT Bridge

| Property | Value |
|---|---|
| Base Image | `python:3.11-slim` |
| Key Dependencies | `kuksa-client==0.4.3` · `paho-mqtt==1.6.1` |
| Entry Point | `python main.py` |
| Depends On | `databroker` (healthy) · `mosquitto` (healthy) |
| Restart Policy | `on-failure` |
| Environment Variables | `DATABROKER_HOST` · `DATABROKER_PORT` · `MQTT_HOST` · `MQTT_PORT` · `VEHICLE_ID` |

**Environment variable defaults:**

| Variable | Default | Description |
|---|---|---|
| `DATABROKER_HOST` | `databroker` | Kuksa Databroker hostname (Docker DNS) |
| `DATABROKER_PORT` | `55555` | Kuksa gRPC port |
| `MQTT_HOST` | `mosquitto` | Mosquitto hostname (Docker DNS) |
| `MQTT_PORT` | `1883` | Mosquitto MQTT port |
| `VEHICLE_ID` | `vehicle-001` | Vehicle identifier used in MQTT topic prefix |

---

## 4. Communication Protocol Details

### 4.1 gRPC → MQTT Data Flow

```
┌──────────────────────────────────────────────────────┐
│                    mqtt-bridge                        │
│                                                      │
│  gRPC subscribe thread          MQTT publish         │
│  ┌─────────────────────┐        ┌─────────────────┐ │
│  │ subscribe_current   │        │ paho-mqtt        │ │
│  │ _values(paths)      │──────▶ │ client.publish() │ │
│  │                     │        │                 │ │
│  │ yields on change:   │        │ topic:           │ │
│  │ {"Vehicle.Speed":   │  map   │  sdv/vehicle-001/│ │
│  │   Datapoint(87.3)}  │──────▶ │  Vehicle/Speed   │ │
│  │                     │        │                 │ │
│  │                     │        │ payload (JSON):  │ │
│  │                     │        │  {"signal":...,  │ │
│  │                     │        │   "value":87.3,  │ │
│  │                     │        │   "unit":"km/h", │ │
│  │                     │        │   "timestamp":.. │ │
│  │                     │        │  }               │ │
│  └─────────────────────┘        └─────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### 4.2 VSS Path → MQTT Topic Conversion

```python
def vss_to_topic(vss_path: str, vehicle_id: str) -> str:
    """
    Convert a VSS dot-notation path to an MQTT topic.

    "Vehicle.Speed"           → "sdv/vehicle-001/Vehicle/Speed"
    "Vehicle.Battery.SoC"     → "sdv/vehicle-001/Vehicle/Battery/SoC"
    "Vehicle.Cabin.Temperature" → "sdv/vehicle-001/Vehicle/Cabin/Temperature"
    """
    mqtt_path = vss_path.replace(".", "/")
    return f"sdv/{vehicle_id}/{mqtt_path}"
```

### 4.3 MQTT QoS Levels (Educational Reference)

M2 uses **QoS 0** for all messages. The three MQTT QoS levels:

| QoS | Name | Guarantee | Use Case in SDV |
|---|---|---|---|
| 0 | At most once | Fire and forget — may be lost | High-frequency telemetry (Speed, Temp) |
| 1 | At least once | Delivered ≥ 1 time — may duplicate | Safety events (SoC below threshold) |
| 2 | Exactly once | Delivered exactly once | Actuation commands (open window) |

In a production V2C pipeline, you would use QoS 1 for battery and safety signals and QoS 0 for high-frequency kinematic signals. QoS 2 is typically reserved for command channels.

---

## 5. Dockerfile Design

### 5.1 MQTT Bridge Dockerfile

Same conventions as M1 (python:3.11-slim, layer caching, non-root user):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
USER 1000
CMD ["python", "main.py"]
```

### 5.2 MQTT Bridge requirements.txt

```
kuksa-client==0.4.3
paho-mqtt==1.6.1
```

**Why paho-mqtt 1.6.1?**
paho-mqtt 2.x introduced breaking API changes (callback signatures, `MQTTv5` default). 1.6.1 is the last stable 1.x release with the widely-documented API. Pinning avoids silent breakage on `pip install paho-mqtt`.

---

## 6. docker-compose.yml Changes

Two new service blocks added. All M1 blocks unchanged.

### 6.1 New: mosquitto

```yaml
mosquitto:
  image: eclipse-mosquitto:2.0
  volumes:
    - ./config/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
  ports:
    - "1883:1883"
  networks:
    - sdv-net
  healthcheck:
    test: ["CMD-SHELL", "timeout 1 bash -c 'cat < /dev/null > /dev/tcp/localhost/1883'"]
    interval: 5s
    timeout: 3s
    retries: 10
    start_period: 5s
  restart: on-failure
```

### 6.2 New: mqtt-bridge

```yaml
mqtt-bridge:
  build:
    context: ./services/mqtt-bridge
    dockerfile: Dockerfile
  environment:
    DATABROKER_HOST: databroker
    DATABROKER_PORT: "55555"
    MQTT_HOST: mosquitto
    MQTT_PORT: "1883"
    VEHICLE_ID: "vehicle-001"
  depends_on:
    databroker:
      condition: service_healthy
    mosquitto:
      condition: service_healthy
  networks:
    - sdv-net
  restart: on-failure
```

---

## 7. Logging Strategy

MQTT bridge follows the same log format as M1 services:

```
2026-05-23T14:30:01 [INFO    ] mqtt-bridge: Connected to Kuksa Databroker at databroker:55555
2026-05-23T14:30:01 [INFO    ] mqtt-bridge: Connected to Mosquitto at mosquitto:1883
2026-05-23T14:30:02 [INFO    ] mqtt-bridge: Published → sdv/vehicle-001/Vehicle/Speed = 87.3 km/h
2026-05-23T14:30:02 [INFO    ] mqtt-bridge: Published → sdv/vehicle-001/Vehicle/Battery/SoC = 72.4 %
2026-05-23T14:30:02 [INFO    ] mqtt-bridge: Published → sdv/vehicle-001/Vehicle/Cabin/Temperature = 22.1 °C
```

---

## 8. Technology Decision Records (TDRs)

### TDR-10: subscribe_current_values() over Polling in the Bridge

| | |
|---|---|
| **Decision** | Use gRPC streaming subscribe in the MQTT bridge (not polling) |
| **Alternatives** | `get_current_values()` polling at 1-second interval |
| **Rationale** | A bridge is a reactive component — it must forward data as it arrives, not on a schedule. Subscribe delivers updates within milliseconds of the ECU publishing. Polling introduces an artificial 1-second maximum latency and wastes resources when signals are stable. |
| **Tradeoff** | Subscribe requires a blocking iterator or thread management. The bridge runs as a simple long-lived process, so this is not a constraint. |
| **Educational Value** | M2 deliberately demonstrates both patterns: poll (M1 dashboard) and subscribe (M2 bridge), teaching learners when each is appropriate. |

### TDR-11: Eclipse Mosquitto over a Cloud MQTT Broker

| | |
|---|---|
| **Decision** | Run Mosquitto locally in Docker rather than connecting to AWS IoT Core / Azure IoT Hub |
| **Alternatives** | AWS IoT Core, HiveMQ Cloud, EMQX Cloud |
| **Rationale** | Cloud broker services require account setup, credentials, and internet access — all of which break the zero-configuration `docker compose up` experience. Mosquitto is a drop-in replacement with identical MQTT semantics. The bridge code requires zero changes to connect to a real cloud broker later. |
| **Tradeoff** | Mosquitto is not cloud infrastructure. Production V2C uses managed cloud brokers with TLS, auth, and global scale. |
| **Migration Path** | Change `MQTT_HOST`, `MQTT_PORT`, and add TLS config to connect the bridge to a real cloud broker — bridge code unchanged. |

### TDR-12: paho-mqtt 1.6.1 over 2.x

| | |
|---|---|
| **Decision** | Pin `paho-mqtt==1.6.1` |
| **Alternatives** | `paho-mqtt>=2.0` |
| **Rationale** | paho-mqtt 2.x changed callback signatures and connection API in breaking ways. 1.6.1 is the stable, widely-documented version matching most tutorials and reference implementations. Avoids silent API breakage when rebuilding containers with newer pip versions. |

### TDR-13: QoS 0 for All Signals in M2

| | |
|---|---|
| **Decision** | Use MQTT QoS 0 (at most once) for all signal publications |
| **Alternatives** | QoS 1 for safety-relevant signals (SoC), QoS 0 for kinematic signals (Speed) |
| **Rationale** | Differentiated QoS adds broker-side state management and retry logic that distracts from the core V2C architecture lesson. QoS 0 is sufficient to demonstrate the publish/subscribe pattern. |
| **Educational Note** | QoS levels are explained in code comments. Production systems would use QoS 1 for battery and safety signals. |

---

## 9. Known Constraints and Limitations

| ID | Constraint | Impact | Resolution Path |
|---|---|---|---|
| C10 | No TLS on Mosquitto (port 1883, plain text) | MQTT traffic is unencrypted | Document as known; add TLS in a security-focused milestone |
| C11 | Anonymous MQTT access (no auth) | Any process can publish/subscribe to any topic | Add username/password or token auth in future milestone |
| C12 | QoS 0 — messages may be lost on network disruption | Not suitable for safety-critical actuation | Increase to QoS 1/2 when actuator commands are introduced (M5) |
| C13 | No MQTT retained messages | New subscribers miss historical values until next ECU update | Add retain flag when persistence matters (M5+) |
| C14 | Single vehicle ID hardcoded (`vehicle-001`) | Cannot simulate multi-vehicle fleet from one Compose stack | Multi-vehicle simulation is M4+ scope |

---

## 10. Non-Functional Requirements (Technical)

| ID | Category | Requirement | Implementation |
|---|---|---|---|
| TNF-10 | Latency | Signal update → MQTT publish latency MUST be ≤ 500 ms | gRPC subscribe eliminates poll delay; paho publish is synchronous |
| TNF-11 | Image Size | mqtt-bridge image MUST be ≤ 300 MB | python:3.11-slim + kuksa-client + paho-mqtt |
| TNF-12 | Startup Time | All 5 services healthy within 90 seconds of `docker compose up` | Mosquitto health check: start_period 5s |
| TNF-13 | Backward Compatibility | All M1 acceptance criteria MUST pass without modification to M1 service code | M1 files are not changed except `docker-compose.yml` (additive only) |

---

## 11. File Structure (Implementation Target)

```
02_mini-sdv-platform project/
├── docker-compose.yml                   ← UPDATED: add mosquitto + mqtt-bridge
├── README.md                            ← UPDATED: M2 architecture + CLI test command
├── config/
│   ├── mosquitto/
│   │   └── mosquitto.conf               ← NEW [1]: Mosquitto config
│   └── vss/                             ← unchanged
├── services/
│   ├── mqtt-bridge/                     ← NEW
│   │   ├── Dockerfile                   ← NEW [2]
│   │   ├── requirements.txt             ← NEW [2]
│   │   └── main.py                      ← NEW [3] ← M2 core implementation
│   ├── ecu-simulator/                   ← unchanged
│   └── dashboard/
│       └── main.py                      ← UPDATED [4]: MQTT status badge in sidebar
└── docs/
    ├── milestone-1/                     ← unchanged
    └── milestone-2/
        ├── PRD.md                       ← this document set
        ├── FRD.md
        └── TRD.md
```

### 11.1 Implementation Priority Order

| Order | File | Validates |
|---|---|---|
| 1 | `config/mosquitto/mosquitto.conf` | Mosquitto starts and accepts connections |
| 2 | `docker-compose.yml` (updated) | All 5 services start; mosquitto health passes |
| 3 | `services/mqtt-bridge/Dockerfile` + `requirements.txt` | Bridge container builds |
| 4 | `services/mqtt-bridge/main.py` | End-to-end: ECU → Databroker → Bridge → Mosquitto → CLI |
| 5 | `services/dashboard/main.py` (sidebar update) | MQTT status badge visible |
| 6 | `README.md` (updated) | Learner can test V2C flow from documentation |
