# M11 Architecture Review & Study Guide
## MQTT Role-Based Access Control (ACL)

---

## 1. What We Built (3-Line Summary)

M11 extended the M10 TLS/mTLS layer with **Role-Based Access Control** at the MQTT topic level. Each service received a dedicated client certificate with a unique CN, and a Mosquitto ACL file maps each CN to the minimal set of topics it may publish or subscribe to. Unauthorized publish attempts are now explicitly rejected, enforcing the Principle of Least Privilege across the entire V2C pipeline.

---

## 2. Why ACL Matters in an SDV Platform

```
Problem after M10:
  mTLS verifies WHO is connecting, but NOT what they can do.
  ┌─────────────────────────────────────────────────────┐
  │ fleet-simulator (authenticated) could publish to:   │
  │   sdv/vehicle-001/ota/status  ← OTA manager topic  │
  │   sdv/vehicle-001/alerts/ai   ← AI monitor topic   │
  │ A compromised simulator could inject false data.    │
  └─────────────────────────────────────────────────────┘

Solution — Principle of Least Privilege:
  Each service can only access the topics it actually needs.
  Lateral movement between services is blocked at the broker.
```

---

## 3. Certificate-to-Permission Mapping

### 3.1 Service Certificates (M11)

| Service | CN (= MQTT username) | Cert files |
|---------|---------------------|------------|
| mqtt-bridge | `sdv-bridge` | bridge.crt / bridge.key |
| fleet-simulator | `sdv-fleet` | fleet.crt / fleet.key |
| ai-monitor | `sdv-ai` | ai.crt / ai.key |
| ota-manager (all 3) | `sdv-ota` | ota.crt / ota.key |
| dashboard | `sdv-dashboard` | dashboard.crt / dashboard.key |

**M10 → M11 change**: The shared `client.crt` (CN=sdv-client) is replaced by five service-specific certificates. The CN becomes the MQTT username via `use_identity_as_username true`.

### 3.2 ACL Permission Matrix

| Username | Publish | Subscribe |
|----------|---------|-----------|
| `sdv-bridge` | `sdv/vehicle-001/#` | — |
| `sdv-fleet` | `sdv/vehicle-002/#`, `sdv/vehicle-003/#` | — |
| `sdv-ai` | `sdv/vehicle-001/alerts/#` | — |
| `sdv-ota` | `sdv/+/ota/#` | — |
| `sdv-dashboard` | — | `sdv/#` |

---

## 4. Mosquitto ACL File Format

```
# Per-user rules: "user <username>" followed by "topic <access> <pattern>"
# Access types: read (subscribe), write (publish), readwrite (both)
# Pattern wildcards: + (single level), # (multi level)

user sdv-bridge
topic write sdv/vehicle-001/#      # publish only — no subscribe

user sdv-dashboard
topic read sdv/#                   # subscribe only — no publish

# Default behavior when acl_file is set:
#   Any topic NOT listed → DENIED
#   Any user NOT listed → DENIED for all topics
```

### Key Design Decision: Separate Read and Write

In a production V2C platform, separating publishers from subscribers means:
- A compromised subscriber (e.g., dashboard) cannot inject false telemetry
- A compromised publisher (e.g., bridge) cannot read alert topics or OTA commands
- The blast radius of any breach is bounded to one service's topic scope

---

## 5. How Mosquitto Resolves ACL

```
PUBLISH sdv/vehicle-001/fake  from client with username=sdv-dashboard

Mosquitto ACL evaluation:
  1. Find rules for user "sdv-dashboard"
  2. Check each rule: topic write sdv/#? → NOT present
  3. Result: DENIED

MQTT 3.1.1 behavior: silent drop (client gets no error feedback)
MQTT 5.0 + QoS 1 behavior: PUBACK reason code 0x87 → client prints error

Verification command used:
  mosquitto_pub --protocol-version mqttv5 -q 1 \
    --cert dashboard.crt ...
  → Warning: Publish 1 failed: Not authorized.  ✅
```

---

## 6. MQTT 3.1.1 vs MQTT 5.0 for ACL Testing

| Feature | MQTT 3.1.1 | MQTT 5.0 |
|---------|-----------|----------|
| PUBACK for QoS 0 | No PUBACK | No PUBACK |
| PUBACK for QoS 1 | Reason code: none (always success) | Reason code: 0x87 = Not Authorized |
| ACL denial visibility | Silent drop on broker side | Error returned to client |
| Logging | `log_type notice` required on broker | Visible from client + broker |

**Lesson**: QoS 0 is fire-and-forget at the protocol level. For security testing, always use QoS 1 with MQTT 5.0 to confirm ACL enforcement from the client side.

---

## 7. Client ID vs Username

```
Before M11:
  client_id: "sdv-bridge-vehicle-001"   (arbitrary, not used for ACL)
  username:  "sdv-client"               (shared across all services → no separation)

After M11:
  client_id: "sdv-bridge"              (aligned with CN for observability)
  username:  "sdv-bridge"              (from certificate CN → drives ACL)
```

Mosquitto logs now clearly show which service is which:
```
New client connected ... as sdv-bridge (u'sdv-bridge')
New client connected ... as sdv-ai    (u'sdv-ai')
New client connected ... as sdv-fleet (u'sdv-fleet')
```

---

## 8. Troubleshooting Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Warning: File acl owner is not mosquitto` | ACL file created by non-mosquitto user | Warning only; fix with `chown mosquitto` inside container if needed |
| Publish silently dropped (no client error) | MQTT 3.1.1 QoS 0 — no PUBACK | Test with `--protocol-version mqttv5 -q 1` |
| `Not authorized` on legitimate publish | CN/username mismatch with ACL | Check cert CN with `openssl x509 -in cert.crt -noout -subject` |
| All services rejected after ACL added | ACL file missing or path wrong | Verify `acl_file` path and docker volume mount |

---

## 9. Production SDV Comparison

| Aspect | M11 (Development) | Production SDV |
|--------|------------------|----------------|
| ACL storage | Static file | Dynamic policy engine (OPA, AWS IoT policies) |
| Granularity | Topic wildcard | Topic + QoS + payload schema |
| Certificate scope | Per service type | Per vehicle instance (VIN-based CN) |
| Policy update | Restart required | Hot-reload or push-based |
| Audit log | `log_type notice` | SIEM integration (Splunk, CloudWatch) |

---

## 10. Next Milestone Candidates

| Candidate | Description | Technology |
|-----------|-------------|------------|
| A | Kubernetes migration — replace Docker Compose with K8s + cert-manager | Helm, cert-manager, K8s |
| B | SecOC simulation — Secure Onboard Communication for CAN frames | AUTOSAR SecOC, HMAC |
| C | Intrusion Detection — anomaly detection on MQTT publish patterns | eBPF / rule-based IDS |
| D | OTA security hardening — UPTANE director + image repository split | UPTANE spec |
| E | Observability stack — distributed tracing across all services | OpenTelemetry, Jaeger |
