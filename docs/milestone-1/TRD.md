# Technical Requirements Document (TRD)
## Milestone 1: Live Vehicle Signal Dashboard
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | TRD |
| Milestone | 1 — Live Vehicle Signal Dashboard |
| Status | Draft |
| Hypothesis Layer | Implementation (L5) |
| Created | 2026-05-23 |
| Version | 1.0 |
| Depends On | [FRD.md](FRD.md) |

---

## 1. Architecture Overview

### 1.1 Service Map

| Service Name | Type | Image / Build | Internal Port | Host Port | Role |
|---|---|---|---|---|---|
| `databroker` | Upstream image | `ghcr.io/eclipse/kuksa.val/databroker:0.4.4` | 55555 (gRPC) | — (internal only) | Vehicle signal middleware |
| `ecu-simulator` | Custom build | `./services/ecu-simulator` (Python 3.11-slim) | — | — | Signal producer |
| `dashboard` | Custom build | `./services/dashboard` (Python 3.11-slim + Streamlit) | 8501 (HTTP) | 8501 | Signal consumer + visualizer |

### 1.2 Network Topology

All services share a single Docker bridge network named `sdv-net`. Services reference each other by Docker Compose service name (internal DNS resolution). Only port `8501` is exposed to the host machine.

```
Host Machine
  └── :8501 ──────────────────────────────────────────────▶ dashboard (HTTP / Streamlit UI)

Docker network: sdv-net (bridge)
  ├── databroker     :55555  ← gRPC (internal only, not exposed to host)
  ├── ecu-simulator          → outbound gRPC to databroker:55555
  └── dashboard      :8501   → outbound gRPC to databroker:55555
                             ← inbound HTTP from host :8501
```

### 1.3 Startup Order

```
Phase 1 ── databroker
            └── loads VSS catalog from mounted vss_mini_covesa.json
            └── opens gRPC listener on :55555
            └── health check: TCP probe :55555 passes

Phase 2 ── ecu-simulator  (depends_on: databroker healthy)
           dashboard       (depends_on: databroker healthy)
            └── both connect to databroker:55555 via gRPC
            └── ecu-simulator begins publishing signal loop
            └── dashboard begins polling loop
```

---

## 2. Service Specifications

### 2.1 Kuksa Databroker

| Property | Value |
|---|---|
| Docker Image | `ghcr.io/eclipse/kuksa.val/databroker:0.4.4` |
| gRPC Port | 55555 |
| Security Mode | Insecure (no TLS) — `--insecure` flag |
| VSS Load | `--vss /vss.json` (file mounted from `config/vss/vss_mini_covesa.json`) |
| Startup Command | `databroker --vss /vss.json --insecure` |
| Restart Policy | `on-failure` |

**Why insecure mode?**
TLS requires certificate management (CA, cert rotation, mTLS), which distracts from the SDV architecture lesson M1 is teaching. In production, all inter-service gRPC in a vehicle uses mTLS. This is documented as a known limitation (see Section 9, C4).

### 2.2 ECU Simulator

| Property | Value |
|---|---|
| Base Image | `python:3.11-slim` |
| Working Directory | `/app` |
| Key Dependency | `kuksa-client==0.4.3` |
| Entry Point | `python main.py` |
| Depends On | `databroker` (condition: `service_healthy`) |
| Restart Policy | `on-failure` |
| Environment Variables | `DATABROKER_HOST=databroker` · `DATABROKER_PORT=55555` · `UPDATE_INTERVAL_SEC=1.0` |

**Connection resilience strategy:**
The service implements an outer reconnect loop with exponential back-off rather than relying solely on Docker's restart policy. This mirrors production cloud-native services where brief network interruptions (e.g., during a Databroker rolling update) should be handled gracefully within the process. The Docker restart policy is the last resort, not the primary recovery path.

### 2.3 Dashboard

| Property | Value |
|---|---|
| Base Image | `python:3.11-slim` |
| Working Directory | `/app` |
| Key Dependencies | `kuksa-client==0.4.3` · `streamlit==1.35.0` |
| Entry Point | `streamlit run main.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true` |
| Depends On | `databroker` (condition: `service_healthy`) |
| Restart Policy | `on-failure` |
| Exposed Port | container 8501 → host 8501 |
| Environment Variables | `DATABROKER_HOST=databroker` · `DATABROKER_PORT=55555` |

---

## 3. Communication Protocol

### 3.1 gRPC / Kuksa VAL API

The Databroker exposes the Kuksa Vehicle Abstraction Layer (VAL) gRPC API:

| Operation | gRPC Method | Direction | Used By |
|---|---|---|---|
| Publish signal value | `SetCurrentValues` | ECU Sim → Databroker | ecu-simulator |
| Read current value | `GetCurrentValues` | Dashboard → Databroker | dashboard |

Proto definition reference: `kuksa/val/v1/val.proto` (bundled in the `kuksa-client` Python package).

### 3.2 Signal Write (ECU Simulator → Databroker)

Per update cycle, the ECU simulator sends a single batched `SetCurrentValues` request:

```python
client.set_current_values({
    "Vehicle.Speed":             Datapoint(87.3),   # float km/h
    "Vehicle.Battery.SoC":       Datapoint(72.40),  # float percent
    "Vehicle.Cabin.Temperature": Datapoint(22.1),   # float celsius
})
```

The Databroker adds a server-side ISO 8601 timestamp to each `Datapoint` upon storage. This timestamp is the authoritative time for the signal value (Domain Rule DR-05).

### 3.3 Signal Read (Dashboard → Databroker)

Per refresh cycle, the dashboard polls all 3 signals in a single `GetCurrentValues` request:

```python
response = client.get_current_values([
    "Vehicle.Speed",
    "Vehicle.Battery.SoC",
    "Vehicle.Cabin.Temperature",
])
# response: dict[str, Datapoint]
# Datapoint.value     → current typed value (float)
# Datapoint.timestamp → server-side datetime
```

### 3.4 Polling vs. Subscribe — Design Decision

M1 uses `get_current_values()` polling (1-second interval) rather than `subscribe_current_values()` streaming.

| Approach | Latency | Complexity | M1 Choice |
|---|---|---|---|
| Polling (`GetCurrentValues`) | ~1s | Low — fits Streamlit's single-threaded model | ✅ Used |
| Subscribe (`SubscribeCurrentValues`) | ~10ms | High — requires background thread + `st.session_state` sync | ❌ Deferred to M3 |

The polling approach is sufficient for a 1 Hz visual update and avoids threading complexity that would distract from the SDV architecture lesson.

---

## 4. VSS Integration

### 4.1 Format Problem

The existing `config/vss/vss_mini.json` uses a flat dot-notation format (human-readable but not compatible with Kuksa Databroker's `--vss` flag):

```json
{
  "Vehicle.Speed": { "datatype": "float", "unit": "km/h", ... }
}
```

Kuksa Databroker requires COVESA hierarchical JSON format:

```json
{
  "Vehicle": {
    "type": "branch",
    "children": {
      "Speed": { "datatype": "float", "type": "sensor", ... }
    }
  }
}
```

### 4.2 Solution

A new file `config/vss/vss_mini_covesa.json` is created in COVESA hierarchical format. The original `vss_mini.json` is retained as human-readable documentation (companion reference, not loaded by Databroker).

### 4.3 Target COVESA Format Structure

```
Vehicle (branch)
├── Speed (sensor, float, km/h, 0–250)
├── Battery (branch)
│   └── SoC (sensor, float, percent, 0–100)
└── Cabin (branch)
    └── Temperature (sensor, float, celsius, -40–100)
```

### 4.4 Volume Mount

```yaml
databroker:
  volumes:
    - ./config/vss/vss_mini_covesa.json:/vss.json:ro
```

The `:ro` (read-only) mount reflects that the VSS catalog is immutable at runtime. A Databroker cannot modify its own catalog during operation.

---

## 5. Docker Compose Design

### 5.1 Service Dependencies

```yaml
ecu-simulator:
  depends_on:
    databroker:
      condition: service_healthy   # wait for TCP :55555 probe to pass

dashboard:
  depends_on:
    databroker:
      condition: service_healthy   # same condition
```

### 5.2 Health Check Strategy

Kuksa Databroker does not expose an HTTP health endpoint. A TCP port probe is used:

```yaml
healthcheck:
  test: ["CMD-SHELL", "timeout 1 bash -c 'cat < /dev/null > /dev/tcp/localhost/55555'"]
  interval: 5s
  timeout: 3s
  retries: 10
  start_period: 10s
```

**Why TCP probe over grpc-health-probe?**
`grpc-health-probe` requires installing a binary into the container or building a custom image. The TCP probe is sufficient to confirm the Databroker is accepting connections, avoiding the image modification. This is documented as a known limitation (C3 below).

### 5.3 Environment Variable Pattern

All service endpoints are injected via environment variables (12-Factor App, Factor III). Hard-coded hostnames are not used anywhere in Python source files.

```yaml
environment:
  DATABROKER_HOST: databroker   # Docker DNS name of the databroker service
  DATABROKER_PORT: "55555"
  UPDATE_INTERVAL_SEC: "1.0"   # ECU simulator only
```

---

## 6. Dockerfile Design Principles

Both custom services share the following Dockerfile conventions:

| Convention | Reason |
|---|---|
| `python:3.11-slim` base | Minimal attack surface; faster pull than `python:3.11` |
| `PYTHONUNBUFFERED=1` | Critical for real-time log visibility in `docker compose logs` |
| `PYTHONDONTWRITEBYTECODE=1` | Prevents unnecessary `.pyc` files in the container layer |
| `COPY requirements.txt` before `COPY .` | Maximizes Docker layer cache hits on source-only changes |
| `USER 1000` | Non-root execution; security baseline |

### 6.1 ECU Simulator Dockerfile

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

### 6.2 Dashboard Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
USER 1000
EXPOSE 8501
CMD ["streamlit", "run", "main.py",
     "--server.port=8501",
     "--server.address=0.0.0.0",
     "--server.headless=true"]
```

---

## 7. Logging Strategy

All Python services use a shared log format to enable consistent log aggregation in future milestones (e.g., Grafana Loki, ELK):

```python
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
```

| Service | Key Log Events |
|---|---|
| `databroker` | Startup, VSS catalog load, signal registration, SetRequest received |
| `ecu-simulator` | Startup banner, each publish cycle (with signal values), connection error, reconnect attempt |
| `dashboard` | Startup, connection status change, poll error |

---

## 8. Technology Decision Records (TDRs)

### TDR-01: Kuksa Databroker as Vehicle Middleware

| | |
|---|---|
| **Decision** | Use Eclipse Kuksa Databroker as the centralized vehicle signal store |
| **Alternatives** | Redis pub/sub · MQTT broker · raw gRPC service |
| **Rationale** | Kuksa is a production SDV technology adopted by COVESA members (Bosch, Continental, Mercedes-Benz). Learning it has direct industry applicability. VSS compliance enforces automotive signal naming conventions that are essential SDV knowledge. |
| **Tradeoff** | Higher initial setup complexity vs. Redis. COVESA VSS format adds a one-time conversion step. |

### TDR-02: Polling over gRPC Subscribe in Dashboard

| | |
|---|---|
| **Decision** | Use `get_current_values()` 1-second polling in the Streamlit dashboard |
| **Alternatives** | `subscribe_current_values()` gRPC streaming |
| **Rationale** | Streamlit's execution model re-runs the entire script on each `st.rerun()` call. A gRPC streaming subscription lives in a background thread and requires `st.session_state` synchronization. This threading model adds complexity that distracts from the SDV signal flow lesson M1 is teaching. |
| **Tradeoff** | 1-second polling latency vs. near-zero latency with subscribe. Acceptable for M1 visual dashboard. |
| **Future Path** | M3 (ROS2) introduces true pub/sub with proper async/threading support. |

### TDR-03: In-Memory Signal History over a Database

| | |
|---|---|
| **Decision** | Store signal history as a Python list (max 60 entries) in `st.session_state` |
| **Alternatives** | SQLite · InfluxDB · Redis TimeSeries |
| **Rationale** | No additional service means simpler `docker-compose.yml` and no data modeling. The 60-second rolling window is sufficient to show signal trends. The SDV architectural lesson is in the signal flow, not the storage layer. |
| **Tradeoff** | History lost on dashboard container restart. Not suitable for production analytics. |
| **Future Path** | M2 may introduce a persistent store when cloud connectivity requires signal history. |

### TDR-04: TCP Health Check over grpc-health-probe

| | |
|---|---|
| **Decision** | Use a bash TCP port probe for Databroker health check |
| **Alternatives** | `grpc-health-probe` sidecar binary · Custom HTTP health sidecar |
| **Rationale** | Avoids modifying the upstream Databroker image or installing additional binaries. TCP probe is sufficient to confirm the port is accepting connections before dependent services start. |
| **Tradeoff** | Does not verify gRPC application-layer health — only network availability. A healthy TCP connection does not guarantee the VSS catalog has loaded correctly. Acceptable for M1. |

### TDR-05: Custom VSS Paths over Standard COVESA Paths

| | |
|---|---|
| **Decision** | Use custom paths (`Vehicle.Battery.SoC`, `Vehicle.Cabin.Temperature`) rather than full COVESA standard paths |
| **Rationale** | The standard COVESA paths are deeply nested (`Vehicle.Powertrain.TractionBattery.StateOfCharge.Current`), which adds cognitive overhead when the M1 goal is to teach the signal flow concept, not VSS path taxonomy. |
| **Tradeoff** | Code diverges from industry-standard paths. Cannot be used directly with tools that expect standard COVESA VSS without modification. |
| **Future Path** | Migrate to standard paths in M3 when VSS conformance becomes architecturally significant. Document the divergence clearly in code comments. |

---

## 9. Known Constraints and Limitations

| ID | Constraint | Impact | Resolution Path |
|---|---|---|---|
| C1 | `Vehicle.Battery.SoC` is not a standard COVESA VSS 4.0 path | Path diverges from industry standard | Documented as custom path; migrate in M3 |
| C2 | Dashboard polling interval minimum ~1s (Streamlit rerun overhead) | Signal updates capped at 1 Hz display rate | M3 introduces async subscribe with ROS2 |
| C3 | TCP health check does not verify VSS catalog loaded correctly | Databroker may pass health check before catalog is fully indexed | `start_period: 10s` provides buffer; acceptable for M1 |
| C4 | No authentication on Databroker (insecure mode) | Any process on `sdv-net` can read or write any signal | Document as known limitation; real SDV uses mTLS + token-based auth |
| C5 | In-memory signal history resets on dashboard container restart | Loss of recent signal history | Acceptable for M1; persistent store considered for M2 |
| C6 | SocketCAN not available on Windows hosts | M4 virtual CAN requires Linux kernel | M4 will require Linux or WSL2; document clearly |

---

## 10. Non-Functional Requirements (Technical)

| ID | Category | Requirement | Implementation |
|---|---|---|---|
| TNF-01 | Image Size | Each custom image MUST be ≤ 500 MB | `python:3.11-slim` + minimal deps |
| TNF-02 | Build Time | `docker compose build` MUST complete in < 3 minutes on standard hardware | Layer caching: `requirements.txt` copied before source code |
| TNF-03 | Startup Time | All services healthy within 60 seconds of `docker compose up` | `start_period: 10s`, `interval: 5s`, `retries: 10` |
| TNF-04 | CPU Usage | ECU simulator MUST NOT exceed 5% CPU in steady state | 1-second `time.sleep()` between publish cycles |
| TNF-05 | Memory Usage | All services combined MUST NOT exceed 1 GB RAM | Python slim + 60-entry in-memory buffer only |
| TNF-06 | Portability | Stack MUST run on Linux, macOS, and Windows (Docker Desktop) | No host-level kernel features (SocketCAN deferred to M4) |

---

## 11. File Structure (Implementation Target)

```
02_mini-sdv-platform project/
├── docker-compose.yml                    ← orchestrates all 3 services
├── README.md                             ← architecture + quickstart + SDV context
├── config/
│   └── vss/
│       ├── vss_mini.json                 ← (existing) flat human-readable reference
│       └── vss_mini_covesa.json          ← (new) COVESA hierarchical format for Databroker
├── services/
│   ├── ecu-simulator/
│   │   ├── Dockerfile                    ← python:3.11-slim
│   │   ├── main.py                       ← (existing, complete)
│   │   └── requirements.txt             ← (existing) kuksa-client==0.4.3
│   └── dashboard/
│       ├── Dockerfile                    ← python:3.11-slim + streamlit
│       ├── main.py                       ← (new) Streamlit dashboard app
│       └── requirements.txt             ← (existing) kuksa-client + streamlit
└── docs/
    └── milestone-1/
        ├── PRD.md                        ← (this document set)
        ├── FRD.md
        └── TRD.md
```

### 11.1 Implementation Priority Order

| Order | File | Validates |
|---|---|---|
| 1 | `config/vss/vss_mini_covesa.json` | Databroker can load VSS catalog |
| 2 | `docker-compose.yml` | All 3 services start and reach healthy state |
| 3 | `services/ecu-simulator/Dockerfile` | ECU simulator builds and connects to Databroker |
| 4 | `services/dashboard/Dockerfile` | Dashboard container builds |
| 5 | `services/dashboard/main.py` | End-to-end signal flow visible in browser |
| 6 | `README.md` | Learner can onboard from zero |
