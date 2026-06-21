# mini-sdv-platform

[![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)](https://python.org)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-k3s-326CE5?logo=kubernetes&logoColor=white)](https://k3s.io)
[![highway-env](https://img.shields.io/badge/highway--env-1.11-00B4D8)](https://github.com/Farama-Foundation/HighwayEnv)
[![ONNX Runtime](https://img.shields.io/badge/ONNX_Runtime-1.18-005CED?logo=onnx&logoColor=white)](https://onnxruntime.ai)
[![Grafana](https://img.shields.io/badge/Grafana-10.4-F46800?logo=grafana&logoColor=white)](https://grafana.com)
[![Pyroscope](https://img.shields.io/badge/Pyroscope-2.0-FF6B35?logo=grafana&logoColor=white)](https://grafana.com/oss/pyroscope)
[![InfluxDB](https://img.shields.io/badge/InfluxDB-2.7-22ADF6?logo=influxdb&logoColor=white)](https://influxdata.com)
[![MQTT](https://img.shields.io/badge/MQTT-Mosquitto_2.0-660066)](https://mosquitto.org)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-Tempo-000000?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![ROS2](https://img.shields.io/badge/ROS2-Humble-22314E?logo=ros&logoColor=white)](https://ros.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> An educational simulation of a modern **Software Defined Vehicle (SDV)** platform built with open-source tools, running on Kubernetes (k3s) with full observability, edge AI deployment, and an **Autonomy Flywheel** for continuous RL policy improvement.

Built incrementally across **18 milestones** — from a bare signal pipeline to a secured, observable, AI-accelerated platform that trains driving policies on Runpod GPUs and deploys them via OTA to edge inference.

---

## What Is a Software Defined Vehicle?

A traditional vehicle has dozens of ECUs communicating peer-to-peer over CAN bus. Each ECU owns its data. Adding a new feature requires wiring into each relevant ECU individually.

A **Software Defined Vehicle** flips this model:

```
Traditional:  ECU-A ←──CAN──→ ECU-B ←──CAN──→ ECU-C
                        (tightly coupled, hard to update)

SDV:          ECU-A ─CAN─┐
              ECU-B ─CAN─┼──▶  CAN Gateway  ──▶  Central Middleware  ──▶  Any App
              ECU-C ─CAN─┘      (M4)              (Databroker / VAL)
                        (decoupled — apps subscribe to signals, not ECUs)
```

All vehicle data flows through a central **Vehicle Abstraction Layer (VAL)**. Applications subscribe to named signals without knowing which ECU produces them. This project adds a second layer: an **Autonomy Flywheel** that continuously improves the driving policy using simulation, cloud training, and OTA deployment.

---

## Architecture

![mini-sdv-platform — all 18 milestones](docs/Image_mbvtnumbvtnumbvt.png)

**Flywheel loop** (M15 → M16 → M17): HEB collects driving episodes → TD dispatches PPO job to Runpod GPU → ALPA evaluates the trained policy (collision_rate ≤ 5% = OTA gate) → OTAS delivers the checkpoint → AIME runs inference at the edge.

> Node label glossary: see [docs/ARCHITECTURE.md § Node abbreviation reference](docs/ARCHITECTURE.md#node-abbreviation-reference).

---

## Milestone Progress

| M | Title | Key Technology | SDV Concept |
|---|-------|---------------|-------------|
| **M1** ✅ | Kuksa Databroker + Dashboard | Kuksa 0.4.4, Streamlit, gRPC | Vehicle Abstraction Layer (VAL) |
| **M2** ✅ | MQTT Bridge + Mosquitto | paho-mqtt, MQTT 5.0 | V2C (Vehicle-to-Cloud) gateway |
| **M3** ✅ | ROS2 Bridge | ROS2 Humble, DDS, CycloneDDS | AD stack middleware integration |
| **M4** ✅ | CAN Bus Simulation | SocketCAN, vcan0, custom kernel 6.18 | ECU → Central Gateway → VAL |
| **M5** ✅ | AI Signal Monitor | Claude Haiku API, Observe-Reason-Act | LLM-based anomaly detection |
| **M6** ✅ | OTA Update Pipeline | Flask, SHA-256, UPTANE pattern | CHECK → DOWNLOAD → VERIFY → APPLY |
| **M7** ✅ | Time-Series + Grafana | InfluxDB 2.7, Flux, Grafana 10.4 | Fleet telemetry persistence |
| **M8** ✅ | Fleet Simulator | Multi-vehicle, parallel threads | Multi-vehicle cloud ingestion |
| **M9** ✅ | Grafana Alerting | Alert rules, Webhook receiver | Anomaly alert routing (PagerDuty pattern) |
| **M10** ✅ | TLS / mTLS | OpenSSL, SAN certs, mutual auth | Per-service client certificates |
| **M11** ✅ | MQTT ACL / RBAC | Mosquitto ACL, MQTT 5.0 | Topic-level authorization |
| **M12** ✅ | OpenTelemetry + Tracing | OTel SDK, OTLP/HTTP | Distributed tracing — 3 pillars of observability |
| **M13** ✅ | Grafana Tempo | Tempo, TraceQL, WAL | Unified metrics + traces in Grafana |
| **M14** ✅ | Kubernetes (k3s) | k3s, Deployment, ConfigMap, Secret | Declarative orchestration, rolling updates |
| **M15** ✅ | Compute Plane | highway-env 1.11, Runpod Serverless API | RL environment + cloud GPU dispatch |
| **M16** ✅ | Autonomy Flywheel | PPO, ONNX FP16/INT4, AlpaSim OTA gate | Simulation → Train → Evaluate → Deploy loop |
| **M17** ✅ | Edge AI Deployment | Phi-4-mini ONNX, LanceDB, all-MiniLM-L6-v2 | On-device inference + semantic scene retrieval |
| **M18** ✅ | Continuous Profiling | Pyroscope 2.0, pyroscope-io SDK, Grafana | CPU flame graphs linked to traces and metrics |

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Orchestration** | k3s (Kubernetes) | Pod lifecycle, rolling updates, Secret management |
| **Vehicle MW** | Eclipse Kuksa Databroker 0.4.4 | VAL — VSS signals over gRPC |
| **CAN Bus** | SocketCAN / vcan0 (Linux kernel 6.18) | Virtual CAN bus for ECU simulation |
| **Messaging** | Eclipse Mosquitto 2.0 | MQTT 5.0 broker with mTLS + topic ACL |
| **AI (cloud)** | Anthropic Claude Haiku | LLM anomaly detection (M5) |
| **AI (edge)** | Microsoft Phi-4-mini + ONNX Runtime 1.18 | On-device inference, MIT license |
| **RL Simulation** | highway-env 1.11 + Gymnasium | CPU driving simulation, policy evaluation |
| **GPU Training** | Runpod Serverless (A100 / RTX 4090) | PPO + LoRA fine-tuning, ≤$10/loop |
| **Vector Store** | LanceDB + all-MiniLM-L6-v2 | Semantic driving scene retrieval |
| **Time-series DB** | InfluxDB 2.7 + Flux | Vehicle telemetry persistence |
| **Visualization** | Grafana 10.4.3 | Metrics + Traces + Profiles unified dashboard |
| **Tracing** | Grafana Tempo + OTel SDK | Distributed traces (TraceQL) |
| **Profiling** | Grafana Pyroscope 2.0 | Continuous CPU flame graphs + trace linking |
| **OTA** | Flask + SHA-256 (UPTANE pattern) | Config `.tar.gz` + policy checkpoint `.pt` |
| **Security** | mTLS (self-signed CA) + MQTT ACL | Per-service client certs, topic-level authz |
| **ROS2** | Humble + CycloneDDS | Autonomous driving middleware integration |

---

## Services

### M1–M14 (Signal Pipeline)

| Service | Port | Description |
|---------|------|-------------|
| `databroker` | 55555 | Vehicle Abstraction Layer (Kuksa) |
| `mosquitto` | 8883 | MQTT broker (mTLS + ACL) |
| `mqtt-bridge` | — | Kuksa → MQTT (V2C gateway) + OTel |
| `ai-monitor` | — | Claude Haiku anomaly detection + OTel |
| `ota-server` | 8080 | OTA package registry (config + checkpoint) |
| `ota-manager` | — | Vehicle-side OTA agent + OTel |
| `influxdb` | 8086 | Time-series database |
| `influxdb-writer` | — | Kuksa → InfluxDB writer |
| `grafana` | 3000 | Dashboards + Alerting |
| `tempo` | 3200 / 4318 | Distributed trace backend |
| `webhook-receiver` | 9000 | Grafana alert webhook sink |

### M15–M18 (Autonomy Flywheel)

| Service | Port | Description |
|---------|------|-------------|
| `highway-env-bridge` | — | highway-v0 simulation → Kuksa gRPC + MQTT CAN frames |
| `training-dispatcher` | 8090 | Runpod job dispatch + MQTT result publish |
| `alpa-sim` | 8092 | Policy evaluation harness (OTA gate: collision ≤ 5%) |
| `ai-monitor-edge` | — | Phi-4-mini ONNX inference + Pyroscope profiling |
| `scene-search` | 8093 | LanceDB semantic scene retrieval (all-MiniLM-L6-v2) |
| `pyroscope` | 4040 | Continuous CPU profiling backend |

---

## Testing

Three-phase test strategy; all phases automated via pytest.

```
tests/                  ← Phase 1: Unit (28 tests, no external deps)
tests_integration/      ← Phase 2: Integration (real highway-env, LanceDB, SentenceTransformers)
                        ← Phase 3: E2E (live k3s services; auto-skip when not running)
```

```bash
# Phase 1 — unit tests (always runnable)
pytest -c pytest.ini -v

# Phase 2 + 3 — integration / E2E (requires packages: highway-env, lancedb, sentence-transformers)
$env:PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION = "python"
pytest -c pytest-integration.ini -v
```

| Phase | Tests | Requirement |
|-------|-------|-------------|
| 1 Unit | 28/28 PASS | Python only (stubbed deps) |
| 2 Integration | 15 PASS / 1 SKIP | highway-env, lancedb, sentence-transformers installed |
| 3 E2E | auto-skip → PASS when live | k3s services + MQTT broker + Pyroscope running |

---

## Prerequisites

- **Windows 11** with WSL2
- **WSL2 Ubuntu 24.04** with custom kernel 6.18 (SocketCAN support — see [M4 TRD](docs/milestone-4/TRD.md))
- **Python 3.10** venv with `highway-env`, `lancedb`, `sentence-transformers`, `paho-mqtt`, `flask`
- **Anthropic API key** — `ANTHROPIC_API_KEY` (for ai-monitor / M5)
- **Runpod account + endpoint** — optional; training-dispatcher runs in dry-run mode without it
- **Phi-4-mini ONNX model** — optional; ai-monitor-edge falls back to rule-based detection

> **Docker note**: This project uses Docker Engine directly inside WSL2 (not Docker Desktop). The custom kernel has `ip_tables.ko` and bridge networking unavailable. Use `{"iptables": false, "bridge": "none"}` in `/etc/docker/daemon.json`.

---

## Quick Start

All commands run in a **WSL2 Ubuntu terminal**.

### 1. Start Docker Engine & k3s

```bash
echo '{"iptables": false, "bridge": "none"}' | sudo tee /etc/docker/daemon.json
sudo service docker start
sudo systemctl start k3s
kubectl get nodes   # → Ready
```

### 2. Deploy the Platform

```bash
cd "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project"

# First time only — build images and configure secrets
bash k8s/scripts/setup-k3s.sh
bash k8s/scripts/build-push.sh
bash k8s/scripts/init-config.sh

# Deploy all 18 milestones
export ANTHROPIC_API_KEY="sk-ant-..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/deployments/
kubectl get pods -n sdv   # all pods → Running
```

### 3. Start CAN Signal Pipeline (WSL2)

```bash
bash scripts/setup-wsl2.sh                          # SocketCAN bootstrap

# Terminal A — CAN Gateway
~/sdv-venv/bin/python services/can-gateway/main.py

# Terminal B — ECU Simulator
ECU_CONFIG_PATH=/tmp/sdv-ota/ecu_config.json \
  ~/sdv-venv/bin/python services/ecu-simulator/main.py

# Terminal C — highway-env driving simulation (M15)
~/sdv-venv/bin/python services/highway-env-bridge/main.py
```

### 4. Open in Windows Browser

| URL | Service |
|-----|---------|
| `http://localhost:3000` | Grafana — Dashboards, Traces, Profiles |
| `http://localhost:8501` | Streamlit Vehicle Dashboard |
| `http://localhost:4040` | Pyroscope Flame Graphs |
| `http://localhost:8086` | InfluxDB (admin / sdv-password) |
| `http://localhost:8092/health` | alpa-sim |
| `http://localhost:8093/health` | scene-search |

---

## Key Commands

```bash
# All pods
kubectl get pods -n sdv

# Live MQTT telemetry (mTLS)
mosquitto_sub -h localhost -p 8883 \
  --cafile config/certs/ca.crt \
  --cert config/certs/dashboard.crt \
  --key config/certs/dashboard.key \
  -t "sdv/vehicle-001/#" -v

# Trigger OTA update (config package)
curl -X POST http://localhost:8080/release/1.1.0

# Run AlpaSim evaluation (IDM baseline, 5 episodes)
curl -X POST http://localhost:8092/evaluate \
  -H "Content-Type: application/json" \
  -d '{"episodes": 5, "model_tag": "idm-baseline"}'

# Semantic scene search
curl "http://localhost:8093/scenes/search?q=highway+collision&k=3"

# Submit training job to Runpod (dry-run when no API key)
curl -X POST http://localhost:8090/jobs \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "ppo", "env_id": "highway-v0", "num_steps": 50000}'

# Rolling restart with zero downtime
kubectl rollout restart deployment/ai-monitor-edge -n sdv
kubectl rollout status  deployment/ai-monitor-edge -n sdv
```

---

## Production Mapping

| This Project | Production SDV | Spec / Standard |
|---|---|---|
| vcan0 + ECU Simulator | Physical CAN bus + ECU (NXP S32, Renesas R-Car) | ISO 11898, SAE J1939 |
| CAN Gateway (Python) | Central Gateway ECU | AUTOSAR Classic BSW |
| Kuksa Databroker | Central Vehicle Computer — VAL | COVESA VSS, AUTOSAR AP |
| Mosquitto mTLS + ACL | AWS IoT Core / Azure IoT Hub | TLS 1.3, MQTT 5.0 |
| Claude Haiku (M5) | OEM cloud AI safety monitor | ISO 21448 (SOTIF) |
| Phi-4-mini ONNX (M17) | In-vehicle neural network (NPU/MCU) | ISO 21448, AUTOSAR ML |
| highway-env + PPO (M15/16) | Closed-loop simulation (CARLA, LGSVL) | ISO 21448 |
| Runpod GPU training (M15) | OEM ML training cluster (AWS SageMaker) | MLOps best practices |
| alpa-sim OTA gate (M16) | Simulation-based safety gate (Waymo sim eval) | ISO 26262 functional safety |
| LanceDB scene search (M17) | In-vehicle semantic memory / situational awareness | COVESA VSS extensions |
| Pyroscope profiling (M18) | Automotive SW performance profiler (Lauterbach) | AUTOSAR Adaptive diagnostics |
| OTA Manager (UPTANE pattern) | Mender / Eclipse hawkBit | UPTANE, UNECE WP.29 |
| InfluxDB + Grafana | AWS Timestream / Grafana Cloud | OpenMetrics |
| Grafana Tempo + OTel | Jaeger / Zipkin in production K8s | OpenTelemetry OTLP |
| k3s single node | EKS / GKE / AKS managed cluster | CNCF Kubernetes |
| Self-signed CA + mTLS | cert-manager + ACM / Let's Encrypt | X.509, RFC 5280 |

---

## Project Structure

```
mini-sdv-platform/
├── README.md
├── pytest.ini                      ← Phase 1 unit test config
├── pytest-integration.ini          ← Phase 2/3 integration test config
│
├── k8s/
│   ├── namespace.yaml
│   ├── deployments/                ← 18 Deployments + ConfigMaps
│   │   ├── databroker.yaml         ← M1
│   │   ├── mosquitto.yaml          ← M2
│   │   ├── ...                     ← M3–M14
│   │   ├── highway-env-bridge.yaml ← M15
│   │   ├── training-dispatcher.yaml
│   │   ├── alpa-sim.yaml           ← M16
│   │   ├── ai-monitor-edge.yaml    ← M17
│   │   ├── scene-search.yaml
│   │   └── pyroscope.yaml          ← M18
│   └── scripts/
│       ├── setup-k3s.sh
│       ├── build-push.sh
│       └── init-config.sh
│
├── services/
│   ├── ecu-simulator/              ← M4
│   ├── can-gateway/                ← M4
│   ├── dashboard/                  ← M1
│   ├── mqtt-bridge/                ← M2 M10 M12
│   ├── ros2-bridge/                ← M3
│   ├── ai-monitor/                 ← M5 M10 M12 (Claude Haiku)
│   ├── ota-server/                 ← M6
│   ├── ota-manager/                ← M6 M10 M12
│   ├── influxdb-writer/            ← M7
│   ├── fleet-simulator/            ← M8
│   ├── webhook-receiver/           ← M9
│   ├── highway-env-bridge/         ← M15
│   ├── training-dispatcher/        ← M15
│   ├── alpa-sim/                   ← M16
│   ├── ai-monitor-edge/            ← M17 (Phi-4-mini ONNX + Pyroscope)
│   └── scene-search/               ← M17 (LanceDB + all-MiniLM-L6-v2)
│
├── scripts/
│   ├── setup-wsl2.sh               ← M4: SocketCAN bootstrap
│   ├── generate-certs.sh           ← M10: mTLS CA + client certs
│   ├── runpod-alpamayo-inference.py ← M16
│   ├── lora-finetune-dispatch.py   ← M16
│   ├── quantization-verify.py      ← M16
│   └── onnx-convert.py             ← M17
│
├── tests/                          ← Phase 1: Unit tests (28 tests)
│   ├── conftest.py
│   ├── test_m15.py
│   ├── test_m16.py
│   ├── test_m17.py
│   └── test_m18.py
│
├── tests_integration/              ← Phase 2+3: Integration & E2E tests
│   ├── conftest.py
│   ├── test_m15_int.py             ← highway-env real sim, dispatcher lifecycle
│   ├── test_m16_int.py             ← IDM evaluation with real highway-env
│   ├── test_m17_int.py             ← LanceDB persistence, 384-dim embeddings
│   ├── test_m18_int.py             ← Pyroscope (skip if not running)
│   ├── test_m16_e2e.py             ← live alpa-sim HTTP (skip if not running)
│   ├── test_m17_e2e.py             ← live scene-search HTTP (skip if not running)
│   └── test_m18_e2e.py             ← live Pyroscope + MQTT alerts
│
├── config/
│   ├── certs/                      ← TLS certs (gitignored)
│   ├── mosquitto/                  ← mTLS + ACL config
│   ├── grafana/provisioning/       ← datasources, dashboards (InfluxDB/Tempo/Pyroscope)
│   ├── tempo/tempo.yaml
│   └── ota/                        ← manifest.json + packages/
│
└── docs/
    ├── milestone-{1..14}/          ← PRD / FRD / TRD per milestone
    └── interview/                  ← Word interview docs (M15–M18 + FR001)
```

---

## License

MIT — built for learning. Fork it, break it, extend it.
