# M12 Architecture Review & Study Guide
## Distributed Tracing with OpenTelemetry + Jaeger

---

## 1. What We Built (3-Line Summary)

M12 added distributed tracing to three core services — `mqtt-bridge`, `ai-monitor`, and `ota-manager` — using the OpenTelemetry Python SDK. Each service emits structured spans (operation name, duration, attributes) via OTLP/HTTP to a Jaeger all-in-one backend. The Jaeger UI at `localhost:16686` now shows per-service trace timelines, revealing the latency of every signal forward, Claude API call, and OTA lifecycle phase.

---

## 2. Why Observability Matters in an SDV Platform

```
Before M12 — log-based observation only:
  2026-05-25T18:22:32 [INFO] mqtt-bridge: Published → sdv/vehicle-001/Vehicle/Speed = 87.3 km/h
  2026-05-25T18:22:42 [INFO] ai-monitor: [REASON] anomaly=False severity=info

  Questions we could NOT answer:
  - How long does signal forwarding take end-to-end?
  - Is the Claude API call the bottleneck in the AI monitor cycle?
  - Which OTA phase takes the most time during an update?

After M12 — distributed tracing:
  mqtt-bridge: signal.forward    → 382µs   (vehicle.id=vehicle-001, signal.path=Vehicle.Speed)
  ai-monitor: ai.monitor.cycle   → 3.2s
    ├─ databroker.poll            → 8ms
    ├─ claude.api.call            → 3.1s    (ai.model=claude-haiku, ai.anomaly=false)
    └─ (no anomaly, no publish)
  ota-manager: ota.check.cycle   → 12ms    (up to date, no download)
```

---

## 3. OpenTelemetry Concepts

### 3.1 The Three Pillars of Observability

| Pillar | Tool | What it answers |
|--------|------|-----------------|
| Logs | Python logging | "What happened?" |
| Metrics | Grafana + InfluxDB (M7) | "How much / how often?" |
| **Traces** | **OpenTelemetry + Jaeger (M12)** | **"How long did each step take?"** |

### 3.2 Key Terms

```
Trace  — a complete record of one end-to-end operation
         (e.g., one ai.monitor.cycle from poll to publish)

Span   — one unit of work within a trace
         (e.g., claude.api.call within ai.monitor.cycle)

Context propagation — linking spans across service boundaries
         (not needed here: MQTT has no header mechanism)

OTLP   — OpenTelemetry Protocol: the standard wire format
         for sending traces/metrics/logs to a backend
```

### 3.3 Span Hierarchy in This Project

```
ai-monitor: ai.monitor.cycle (root span, ~3s)
├── databroker.poll       (~8ms)   signal.count=3
├── claude.api.call       (~3.1s)  ai.model, ai.anomaly, ai.severity
└── mqtt.publish          (~5ms)   mqtt.topic, ai.severity  ← only when anomaly=true

mqtt-bridge: signal.forward (single flat span per signal, ~400µs)
    vehicle.id, signal.path, signal.value, mqtt.topic

ota-manager: ota.check.cycle (root span, varies)
├── manifest.fetch        (~10ms)  manifest.ok=true/false
├── package.download      (if update available, ~seconds)
├── package.verify        (if downloaded)  ota.hash_ok=true/false
├── package.apply         (if verified)    apply.ok=true/false
└── mqtt.publish          (if complete)    ota.version
```

---

## 4. Setup Pattern (Reusable Across Services)

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

OTEL_ENABLED  = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
OTEL_ENDPOINT = os.environ.get("OTEL_ENDPOINT", "http://localhost:4318/v1/traces")

def setup_tracing(service_name: str) -> trace.Tracer:
    if not OTEL_ENABLED:
        return trace.get_tracer(service_name)   # NoOp — zero overhead
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
```

### Why NoOp when disabled?

The default `TracerProvider` (before `set_tracer_provider()` is called) is a no-op implementation. `start_as_current_span()` becomes a zero-cost context manager — no allocations, no network calls. This means instrumentation code can stay in place without any runtime penalty when `OTEL_ENABLED=false`.

### 12-Factor App Pattern

```
Development:  OTEL_ENABLED=false  (no Jaeger needed, no overhead)
Production:   OTEL_ENABLED=true   OTEL_ENDPOINT=http://jaeger:4318/v1/traces
```

---

## 5. Span Instrumentation Pattern

```python
tracer = setup_tracing("ai-monitor")

while True:
    # Root span wraps the entire operation
    with tracer.start_as_current_span("ai.monitor.cycle") as root:
        root.set_attribute("vehicle.id", VEHICLE_ID)

        # Child spans wrap individual sub-operations
        with tracer.start_as_current_span("databroker.poll") as span:
            values = poll_databroker()
            span.set_attribute("signal.count",
                sum(1 for v in values.values() if v is not None))

        with tracer.start_as_current_span("claude.api.call") as span:
            span.set_attribute("ai.model", "claude-haiku-4-5-20251001")
            result = call_llm(ai_client, history)
            if result:
                span.set_attribute("ai.anomaly", result.get("anomaly", False))
                span.set_attribute("ai.severity", result.get("severity", "info"))
```

**Key rule**: spans are measured from `__enter__` to `__exit__` of the `with` block. The span duration equals the wall-clock time of the code inside — making latency measurement automatic and precise.

---

## 6. OTLP Transport: gRPC vs HTTP

| | gRPC (port 4317) | HTTP (port 4318) |
|-|-----------------|-----------------|
| Protocol | Protobuf over HTTP/2 | Protobuf over HTTP/1.1 |
| Python dep | `grpcio` | `requests` |
| Conflict risk | High (grpcio version clashes with kuksa-client) | Low |
| Endpoint format | `host:port` | `http://host:port/v1/traces` |

**M12 lesson**: `kuksa-client==0.4.3` requires `grpcio-tools` which pins `protobuf>=5.26.1`. The OTel gRPC exporter pinned to `protobuf<5.0` — irreconcilable conflict. Switching to the HTTP exporter avoids `grpcio` entirely and removes the conflict.

---

## 7. Jaeger Architecture (Development Setup)

```
Services                  Jaeger all-in-one container
mqtt-bridge  ──OTLP/HTTP──▶  Collector (port 4318)
ai-monitor   ──OTLP/HTTP──▶     │
ota-manager  ──OTLP/HTTP──▶     ▼
                           In-memory storage
                                │
                           Query service
                                │
                           UI (port 16686) ◀── Browser
```

**Production equivalent**: Jaeger all-in-one → separate Collector + Cassandra/Elasticsearch storage. Or swap Jaeger for Grafana Tempo (OTLP-compatible, integrates with the existing Grafana stack).

---

## 8. Observed Results

| Service | Operation | Typical Duration | Key Attributes |
|---------|-----------|-----------------|----------------|
| mqtt-bridge | `signal.forward` | 300–600µs | vehicle.id, signal.path, signal.value |
| ai-monitor | `ai.monitor.cycle` | ~3–4s | ai.anomaly, ai.severity |
| ai-monitor | `claude.api.call` | ~3s | ai.model (dominant bottleneck) |
| ai-monitor | `databroker.poll` | ~8ms | signal.count=3 |
| ota-manager | `ota.check.cycle` | ~10ms | installed_version, latest_version |

**Finding**: The Claude API call dominates the ai-monitor cycle (>95% of total duration). In production, this would justify async processing or a faster local model.

---

## 9. Production SDV Comparison

| Aspect | M12 (Development) | Production SDV |
|--------|------------------|----------------|
| Backend | Jaeger all-in-one (memory) | Jaeger + Cassandra / Grafana Tempo |
| Transport | OTLP/HTTP plain | OTLP/HTTP with mTLS |
| Sampling | 100% (all spans exported) | Head/tail sampling (1–10%) |
| Context propagation | Per-service (no cross-service) | W3C TraceContext across MQTT/HTTP |
| Storage | In-memory (lost on restart) | Persistent (days/weeks retention) |
| Integration | Standalone Jaeger | Grafana unified observability (logs + metrics + traces) |

---

## 10. Next Milestone Candidates

| Candidate | Description | Technology |
|-----------|-------------|------------|
| A | Kubernetes migration — replace Docker Compose with K8s + cert-manager | Helm, cert-manager |
| B | SecOC simulation — HMAC-signed CAN frames (AUTOSAR SecOC pattern) | HMAC-SHA256, SocketCAN |
| C | OTA security hardening — UPTANE director + image repository split | UPTANE spec |
| D | Grafana unified observability — link Jaeger traces to Grafana dashboards | Grafana Tempo, TraceQL |
| E | Service mesh — mTLS + observability via sidecar proxy | Envoy, Istio |
