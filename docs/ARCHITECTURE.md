# Architecture Reference — mini-sdv-platform

Concise design reference for engineers and interviewers. Covers system overview,
component topology, key design decisions, and production mapping.

---

## 1. System Overview

mini-sdv-platform is an educational simulation of a Software Defined Vehicle
stack built across 18 milestones. The M1–M14 signal pipeline mirrors a
production OEM architecture: virtual ECUs emit CAN frames, a central gateway
translates them into named VSS signals, and a Kuksa Databroker acts as the
Vehicle Abstraction Layer so any downstream consumer (dashboard, AI monitor,
time-series writer) subscribes to signals without ECU awareness. M15–M18 add an
Autonomy Flywheel: a closed loop in which a highway-env simulation generates
driving episodes, a cloud GPU (Runpod) trains a PPO policy, a simulation harness
(alpa-sim) gates the checkpoint on a 5% collision-rate threshold, and the
approved model is delivered to the edge inference service via OTA. The entire
stack runs on k3s (single-node) inside WSL2 on a consumer laptop, with
observability provided by InfluxDB, Grafana, Tempo (distributed tracing), and
Pyroscope (continuous profiling).

---

## 2. Component Map

![mini-sdv-platform architecture diagram — all 18 milestones](Image_mbvtnumbvtnumbvt.png)

**Autonomy Flywheel loop** (M15 → M16 → M17):  
HEB collects episodes → TD dispatches PPO job to Runpod → ALPA evaluates the
checkpoint (collision_rate ≤ 5% passes the OTA gate) → OTAS publishes the
checkpoint → AIME pulls the updated model.

### Node abbreviation reference

| Abbr. | Full service name | Milestone | Role |
|-------|-------------------|-----------|------|
| ECU | ecu-simulator | M4 | Generates synthetic CAN frames (Powertrain / BMS / HVAC) on vcan0 |
| CGWY | can-gateway | M4 | Reads vcan0 via python-can, translates to VSS, writes to Databroker over gRPC |
| HEB | highway-env-bridge | M15 | Runs highway-v0 Gymnasium sim; publishes VSS signals and MQTT episode metrics |
| DB | Kuksa Databroker | M1 | Vehicle Abstraction Layer — named VSS signal store (gRPC :55555) |
| DASH | dashboard | M1 | Streamlit live vehicle dashboard (:8501) |
| MB | mqtt-bridge | M2 | V2C gateway; subscribes to Databroker, publishes to Mosquitto; emits OTel traces |
| ROS | ros2-bridge | M3 | DDS / CycloneDDS bridge between Databroker and ROS 2 topics |
| AIM | ai-monitor | M5 | Cloud AI anomaly monitor (Claude Haiku, Observe-Reason-Act loop) |
| MQ | Mosquitto | M2/M10/M11 | MQTT broker with mTLS + ACL (:8883) — event bus for all inter-service messages |
| IW | influxdb-writer | M7 | Polls Databroker every second, writes VSS readings to InfluxDB |
| IDB | InfluxDB | M7 | Time-series database for vehicle telemetry and eval results (:8086) |
| GF | Grafana | M7/M13/M18 | Dashboards, alert rules, datasources for InfluxDB / Tempo / Pyroscope (:3000) |
| WH | webhook-receiver | M9 | HTTP sink for Grafana alert webhooks (:9000) |
| TP | Tempo | M13 | Distributed trace backend; receives OTLP spans (:4318), queried by Grafana (:3200) |
| AIME | ai-monitor-edge | M17 | On-device AI monitor (Phi-4-mini ONNX, rule-based fallback when model absent) |
| PYR | Pyroscope | M18 | Continuous CPU/memory profiling backend; receives pprof pushes (:4040) |
| FS | fleet-simulator | M8 | Simulates multiple vehicles in parallel; writes telemetry directly to InfluxDB |
| OTAS | ota-server | M6 | OTA package registry (Flask :8080, SHA-256 manifest, UPTANE pattern) |
| OTAM | ota-manager | M6 | OTA agent inside the vehicle; polls ota-server, applies updates, emits OTel traces |
| TD | training-dispatcher | M15 | Dispatches PPO training jobs to Runpod Serverless API (Flask :8090); dry-run mode when credentials absent |
| ALPA | alpa-sim | M16 | Evaluation harness; runs highway-v0 against a checkpoint, gates OTA on collision_rate ≤ 5% (:8092) |
| SS | scene-search | M17 | Semantic scene retrieval; encodes MQTT episode metrics with MiniLM → LanceDB vector store (:8093) |

---

## 3. Key Design Decisions

**hostNetwork on every k3s pod.**
The custom WSL2 kernel (built for SocketCAN) does not include the bridge
networking and ip_tables modules that Kubernetes normally requires for pod
networking. Using `hostNetwork: true` on every Deployment and disabling
kube-proxy and Flannel during k3s installation sidesteps the missing modules.
The trade-off is that all service ports bind directly to the WSL2 loopback,
which is acceptable for a single-node educational environment. A production
cluster would use a proper CNI (Cilium, Calico).

**`env.unwrapped.configure()` in alpa-sim, plain `configure()` in highway-env-bridge.**
highway-env registers `highway-v0` as a Gymnasium environment. After `gym.make()`,
the wrapper chain adds a `TimeLimit` wrapper that intercepts `configure()`.
In `alpa-sim`, the evaluation harness calls `env.unwrapped.configure(...)` to
reach the inner `HighwayEnv` directly and bypass the wrapper so configuration
changes (duration, vehicles_count) are applied reliably. In `highway-env-bridge`,
the code calls `env.configure(...)` on the unwrapped environment returned by
`gym.make()` before any episode starts, which is also safe. This distinction
matters when adding new wrappers — always call configure on `env.unwrapped` if
the environment has been wrapped after creation.

**Separate `tests/` and `tests_integration/` with two pytest configs.**
Unit tests in `tests/` stub every heavy dependency (gymnasium, kuksa_client,
paho, opentelemetry, optimum, pyroscope) via `sys.modules` in `conftest.py`.
This means Phase 1 (28 tests) runs in under 5 seconds with only stdlib + flask +
numpy installed — suitable for CI on a plain Python image. Phase 2/3 tests in
`tests_integration/` import real packages and hit live services. Keeping them in
a separate directory with a separate `pytest-integration.ini` allows CI to run
Phase 1 always and Phase 2/3 only in environments where the full dependency set
and live k3s cluster are available. Mixing them would either slow the fast suite
or require conditional skip logic scattered across test files.

**Dry-run mode in training-dispatcher when Runpod credentials are absent.**
If `RUNPOD_API_KEY` or `RUNPOD_ENDPOINT_ID` is not set (read from a k8s Secret
marked `optional: true`), the dispatcher returns status `dry_run` and publishes
a synthetic MQTT completion event. This keeps the rest of the Autonomy Flywheel
observable (alpa-sim, ota-server, scene-search remain active) without requiring
a paid Runpod account. It also lets portfolio reviewers run the full platform
demo for free.

**Rule-based fallback in ai-monitor-edge when MODEL_PATH is absent.**
Phi-4-mini ONNX weighs approximately 2 GB and requires a separate download and
conversion step (`scripts/onnx-convert.py`). Rather than crashing on startup
when `/opt/sdv/models/phi4-mini-onnx` is empty (mounted as a hostPath
`DirectoryOrCreate`), the service checks `os.path.isdir(MODEL_PATH)` and falls
back to a deterministic threshold-based analyzer. The fallback uses identical
input signal paths and produces identical MQTT output format, so Grafana alert
rules require no changes. The inference engine name (`rules` vs `phi4-mini-onnx`)
is included in every alert payload for observability.

**mTLS everywhere with a self-signed CA, not TLS-only.**
Mutual TLS gives each service a client certificate, making topic-level
authorization in Mosquitto ACL meaningful: you can lock `sdv/training/#` to the
dispatcher's certificate. TLS-only (server auth only) would not prevent a
compromised container from publishing to any topic. The self-signed CA approach
mirrors how production systems use cert-manager or AWS ACM internally.

**MQTT as the event bus between k8s services, not Kubernetes Services or gRPC.**
All M15–M18 inter-service events (training job completion, alpa-sim results,
scene ingestion from highway-env-bridge) flow over MQTT topics rather than
direct HTTP calls or Kubernetes Service discovery. This matches the V2C pattern
established in M2, keeps services loosely coupled, and allows external consumers
(a Windows MQTT client, a cloud subscriber) to observe the same events without
modifying k8s networking.

---

## 4. Data Flows

**Flow 1: CAN frame to Grafana dashboard**

1. `ecu-simulator` encodes a float32 speed value as a 4-byte CAN frame
   (arbitration ID `0x100`) and writes it to `vcan0`.
2. `can-gateway` reads the frame from `vcan0` via python-can, decodes the
   float32, and calls `VSSClient.set_current_values({"Vehicle.Speed": value})`
   on Databroker (gRPC, port 55555).
3. `influxdb-writer` polls Databroker every second, converts readings to
   InfluxDB `Point` objects, and writes them to the `sdv` bucket (HTTP, port 8086).
4. Grafana reads the `sdv` bucket via the InfluxDB Flux datasource and renders
   the speed time-series panel. Total end-to-end latency on a local machine:
   1–2 seconds.

**Flow 2: MQTT episode metrics to scene-search vector store**

1. `highway-env-bridge` completes one simulation episode and publishes a JSON
   metrics payload to `sdv/vehicle-001/highway/metrics` via MQTT (port 8883,
   mTLS).
2. `scene-search` has an active MQTT subscription on that topic. The `on_message`
   callback converts the payload to a natural-language description
   (`"episode reward 12.4 | 100 steps"`), encodes it with
   `all-MiniLM-L6-v2` (384-dimensional float32 vector), and inserts the record
   into LanceDB.
3. An operator or alert rule queries `GET /scenes/search?q=highway+collision&k=3`
   (HTTP, port 8093). `scene-search` encodes the query text with the same model
   and returns the k nearest neighbors by cosine similarity.

**Flow 3: Training completion to OTA checkpoint deployment**

1. A POST to `training-dispatcher /jobs` (port 8090) with `{"algorithm":"ppo",
   "env_id":"highway-v0","num_steps":100000}` dispatches a job to the Runpod
   Serverless API. A background thread polls the Runpod status endpoint every
   15 seconds.
2. When Runpod returns status `COMPLETED`, the dispatcher publishes
   `sdv/training/<job_id>/completed` to MQTT and stores the checkpoint path.
3. The checkpoint `.pt` file is uploaded to (or pulled from) `ota-server`
   (port 8080). `alpa-sim` evaluates the checkpoint via `POST /evaluate`
   (port 8092) running `highway-v0` for N episodes. If `collision_rate <= 5%`,
   `ota_gate_passed=true` is written to InfluxDB.
4. `ai-monitor-edge` polls the ota-server manifest, detects the new checkpoint
   version, downloads it, and reloads the ONNX model — no pod restart required.

---

## 5. Production Mapping Table

| This Project | Production SDV Stack | Standard / Reference |
|---|---|---|
| vcan0 + ecu-simulator | Physical CAN bus + ECU (NXP S32, Renesas R-Car) | ISO 11898, SAE J1939 |
| can-gateway (Python) | Central Gateway ECU (AUTOSAR Classic BSW) | AUTOSAR CP |
| Kuksa Databroker | Vehicle Abstraction Layer on Central Vehicle Computer | COVESA VSS, AUTOSAR AP |
| Mosquitto mTLS + ACL | AWS IoT Core / Azure IoT Hub | TLS 1.3, MQTT 5.0 |
| Claude Haiku anomaly monitor (M5) | OEM cloud AI safety monitor | ISO 21448 (SOTIF) |
| Phi-4-mini ONNX edge inference (M17) | In-vehicle neural network on NPU or MCU | ISO 21448, AUTOSAR ML |
| highway-env + PPO simulation (M15/M16) | Closed-loop sim platform (CARLA, LGSVL, ASAM OpenDRIVE) | ISO 21448 |
| Runpod GPU training (M15) | OEM ML training cluster (AWS SageMaker, internal HPC) | MLOps best practices |
| alpa-sim OTA gate — collision_rate <= 5% (M16) | Simulation-based safety gate before real-world testing | ISO 26262 functional safety |
| LanceDB + MiniLM scene retrieval (M17) | In-vehicle semantic memory / situational awareness | COVESA VSS extensions |
| Pyroscope continuous profiling (M18) | Automotive SW performance profiler (Lauterbach TRACE32) | AUTOSAR Adaptive diagnostics |
| OTA manager UPTANE pattern (M6) | Mender / Eclipse hawkBit production OTA | UPTANE, UNECE WP.29 R156 |
| InfluxDB + Grafana (M7/M9/M13) | AWS Timestream / Grafana Cloud fleet telemetry | OpenMetrics |
| Grafana Tempo + OTel SDK (M12/M13) | Jaeger / Zipkin in production Kubernetes | OpenTelemetry OTLP |
| k3s single-node (M14) | EKS / GKE / AKS managed multi-node cluster | CNCF Kubernetes |
| Self-signed CA + mTLS (M10) | cert-manager + ACM / Let's Encrypt | X.509, RFC 5280 |
