# M13 Architecture Review & Study Guide
## Unified Observability: Grafana Tempo as Distributed Tracing Backend

---

## 1. What We Built (3-Line Summary)

M13 replaced Jaeger (M12) with Grafana Tempo as the distributed tracing backend. Tempo receives OTLP/HTTP traces from `mqtt-bridge`, `ai-monitor`, and `ota-manager` on port 4318 — the same endpoint, requiring zero service-side changes. Traces are now queryable directly inside Grafana Explore using TraceQL, completing the unified observability stack (metrics + traces) in a single UI.

---

## 2. Why Replace Jaeger with Tempo?

```
M12 (Jaeger):
  Services → OTLP/HTTP → Jaeger UI (port 16686)   ← separate UI
  Grafana → InfluxDB dashboards                    ← separate UI
  Result: two UIs, no cross-linking

M13 (Tempo):
  Services → OTLP/HTTP → Tempo (port 4318)
  Grafana → InfluxDB dashboards  ┐
  Grafana → Tempo traces         ┘ ← single UI, cross-linked
  Result: one UI, trace-to-metrics links possible
```

**Key insight**: Tempo is not a better Jaeger — it is a Grafana-native backend that makes traces a first-class citizen alongside metrics and logs inside the existing Grafana stack.

---

## 3. Grafana Tempo Architecture

### 3.1 Component Overview

```
Services                    Tempo container
mqtt-bridge  ──OTLP/HTTP──▶  Distributor (port 4318)
ai-monitor   ──OTLP/HTTP──▶      │
ota-manager  ──OTLP/HTTP──▶      ▼
                             Ingester → WAL (Write-Ahead Log)
                                 │
                             Compactor → Local storage (/tmp/tempo/)
                                 │
                             Query Frontend (port 3200)
                                 │
                             Grafana Explore ◀── Browser
```

### 3.2 WAL (Write-Ahead Log) Pattern

```
Span arrives at Distributor
    ↓
Written to WAL immediately (durable, fast)
    ↓
Ingester accumulates spans in memory
    ↓
Block flushed to backend storage every ~35s
    ↓
Queryable via Query Frontend
```

Tempo log evidence:
```
level=info msg="queueing wal block for completion" block=... size=155377
level=info msg="completing WAL block"
level=info msg="opening newly completed block"
level=info msg="completed block"
```

This write path ensures **no span loss** even if the backend (local disk, S3, GCS) is temporarily unavailable — the WAL acts as a durability buffer.

---

## 4. Configuration

### 4.1 Tempo Config (`config/tempo/tempo.yaml`)

```yaml
server:
  http_listen_port: 3200          # Query API + Grafana datasource endpoint

distributor:
  receivers:
    otlp:
      protocols:
        http:
          endpoint: 0.0.0.0:4318  # Same port as Jaeger → zero service changes

storage:
  trace:
    backend: local                # Development: local filesystem
    local:
      path: /tmp/tempo/traces     # Completed blocks
    wal:
      path: /tmp/tempo/wal        # Write-Ahead Log
```

**Port 4318 continuity**: By binding Tempo to the same OTLP/HTTP port that Jaeger used, all three instrumented services continued sending traces without any code or config changes.

### 4.2 Grafana Datasource (`config/grafana/provisioning/datasources/tempo.yaml`)

```yaml
apiVersion: 1
datasources:
  - name: Tempo
    type: tempo
    uid: tempo-sdv
    url: http://localhost:3200
    access: proxy
    jsonData:
      tracesToMetrics:
        datasourceUid: influxdb-sdv    # Link traces → InfluxDB metrics
        tags:
          - key: vehicle.id
            value: vehicle_id
```

**Infrastructure as Code**: The datasource is provisioned automatically on Grafana startup — no manual UI configuration required. This follows the same pattern established in M7 for the InfluxDB datasource.

---

## 5. TraceQL — Tempo's Query Language

TraceQL is Tempo's native query language, similar to PromQL for metrics or LogQL for logs.

### 5.1 Basic Syntax

```
{ <selector> }
```

### 5.2 Queries Used in This Project

```
# All traces from a service
{resource.service.name="ai-monitor"}

# Traces with anomaly detected
{span.ai.anomaly=true}

# Slow traces (bottleneck detection)
{duration > 3s}

# OTA cycles that actually downloaded
{span.ota.phase="download"}

# Combine: slow ai-monitor cycles
{resource.service.name="ai-monitor" && duration > 5s}
```

### 5.3 TraceQL vs Jaeger Query UI

| | Jaeger UI | TraceQL |
|--|-----------|---------|
| Query style | Form-based dropdowns | Code (declarative) |
| Expressiveness | Service + operation + tags | Full boolean logic |
| Integration | Standalone UI | Grafana panel / alert |
| Span attribute filtering | Basic | `span.<key>=<value>` |
| Resource attribute filtering | None | `resource.<key>=<value>` |

---

## 6. Observed Results

### 6.1 Verified in Grafana Explore

```
Service: ai-monitor
Trace:   ai.monitor.cycle  (10.07s)
Spans:
  ├── ai.monitor.cycle   10.07s   ← root span
  └── databroker.poll    71.43ms  ← child span
```

### 6.2 Service Availability in Tempo

| Service | Status | Reason |
|---------|--------|--------|
| ai-monitor | ✅ Traces visible | Timer-based: polls every 10s regardless of signals |
| ota-manager | ✅ Traces visible | Timer-based: polls every 30s regardless of signals |
| mqtt-bridge | ⚠️ No traces | Event-based: only spans when signals flow from ECU simulator |

**Key lesson**: `mqtt-bridge` creates spans only when signals arrive from the Kuksa Databroker, which requires the ECU simulator (WSL2 process) and CAN gateway to be running. Timer-based services (ai-monitor, ota-manager) generate traces continuously and are therefore easier to verify in isolation.

---

## 7. Docker Engine Lesson: iptables and Custom Kernels

M13 surfaced an important infrastructure lesson: **Docker Desktop is incompatible with this PC's custom WSL2 kernel (6.18)**.

### Root Cause Chain

```
M4 required vcan0 (virtual CAN bus)
    ↓
Default Microsoft WSL2 kernel lacks SocketCAN modules
    ↓
Custom kernel 6.18 compiled with SocketCAN enabled
    ↓
ip_tables.ko and bridge networking omitted from the build
    ↓
Docker Desktop internally uses iptables → stuck at "Starting..."
Docker Engine can disable iptables → works with /etc/docker/daemon.json
```

### Fix: daemon.json

```json
{"iptables": false, "bridge": "none"}
```

```bash
echo '{"iptables": false, "bridge": "none"}' | sudo tee /etc/docker/daemon.json
sudo service docker start
```

This is sustainable because **all project services use `network_mode: host`** — Docker's bridge networking is never needed. The `--iptables=false` flag tells Docker not to manage NAT rules, which is correct when host networking bypasses NAT entirely.

---

## 8. Production Comparison

| Aspect | M13 (Development) | Production |
|--------|------------------|------------|
| Backend | Tempo local storage | Tempo + S3/GCS (object storage) |
| Retention | Until container restart | Configurable (days/weeks) |
| Sampling | 100% | Head/tail sampling (1–10%) |
| Query | TraceQL in Grafana Explore | Same, plus dashboards and alerts |
| Trace-to-metrics | Configured (influxdb-sdv link) | Production Prometheus/Mimir link |
| Multi-tenancy | Single tenant | `X-Scope-OrgID` header per fleet |

### Production Storage Backend

```yaml
# Replace local with S3 for production:
storage:
  trace:
    backend: s3
    s3:
      bucket: sdv-traces
      endpoint: s3.amazonaws.com
      region: ap-northeast-1
```

---

## 9. Observability Stack — Complete Picture After M13

```
Vehicle signals (VSS)
    │
    ├── InfluxDB ←── influxdb-writer     (time-series metrics)
    │       └── Grafana dashboards       (M7: metrics UI)
    │               └── Grafana Alerting (M9: anomaly alerts)
    │
    ├── Mosquitto ←── mqtt-bridge        (MQTT telemetry)
    │       └── ai-monitor → Mosquitto   (M5: AI anomaly alerts)
    │
    └── Tempo ←── mqtt-bridge            (distributed traces)
              ←── ai-monitor             (M12/M13: latency visibility)
              ←── ota-manager
                      └── Grafana Explore (TraceQL queries)
```

All three pillars of observability now converge in **Grafana**:
- **Metrics**: InfluxDB → Grafana dashboards
- **Traces**: Tempo → Grafana Explore
- **Logs**: `docker compose logs` (not yet unified — a candidate for a future milestone)

---

## 10. Next Milestone Candidates

| Candidate | Description | Technology |
|-----------|-------------|------------|
| A | Kubernetes migration — replace Docker Compose with K8s | Helm, cert-manager |
| B | SecOC simulation — HMAC-signed CAN frames | HMAC-SHA256, SocketCAN |
| C | OTA security hardening — UPTANE split repository | UPTANE spec |
| D | Log aggregation — unify Docker logs into Grafana Loki | Loki, Promtail |
| E | Service mesh — mTLS + observability via sidecar proxy | Envoy, Istio |
