# Product Requirements Document (PRD)
## Milestone 6: OTA Update Simulation
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | PRD |
| Milestone | 6 — OTA Update Simulation |
| Status | Draft |
| Hypothesis Layers | Value (L1) · Behavior (L2) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | Milestone 5 (stable, deployed) |
| Next Layer | [FRD.md](FRD.md) |

---

## 1. Overview

Milestone 6 adds an **OTA (Over-The-Air) software update pipeline** that simulates the cloud → vehicle direction of the SDV communication loop. An OTA server hosts versioned ECU configuration packages; an OTA manager running in the vehicle context polls for updates, downloads and verifies them, applies the update, and reports installation status.

M1–M5 established the full vehicle → cloud signal pipeline. M6 closes the loop in the other direction: **cloud → vehicle** software delivery.

---

## 2. Problem Statement

### 2.1 The Gap M6 Closes

After M5, the platform demonstrates every major vehicle-to-cloud pattern:

| Direction | Implemented |
|---|---|
| ECU → CAN → Databroker | ✅ M4 |
| Databroker → Dashboard | ✅ M1 |
| Databroker → Cloud (MQTT) | ✅ M2 |
| Databroker → AD Stack (ROS2) | ✅ M3 |
| Databroker → AI Monitor | ✅ M5 |
| **Cloud → Vehicle (OTA)** | ❌ Missing |

In a real SDV, the OTA pipeline is as fundamental as telemetry. Every Tesla software update, every ADAS algorithm improvement, every security patch travels over OTA. Without M6, a learner has no model for how software gets **from the cloud back into the vehicle**.

### 2.2 Why OTA Matters in SDV Architecture

Traditional vehicles required a service centre visit for any software change. SDV platforms flip this: OTA enables:
- **Remote bug fixes** without vehicle recall
- **Feature additions** post-sale (Tesla FSD, BMW heated seats subscription)
- **Security patches** delivered within hours of CVE disclosure
- **A/B testing** of ECU algorithms across a fleet

The OTA pipeline is also where automotive cybersecurity is most critical — a compromised update server can push malicious firmware to millions of vehicles. UPTANE was designed specifically to address this threat.

---

## 3. Target Users

Same as M1–M5: SDV / Automotive Software Engineer learning vehicle platform architecture.

**New learning goal for M6:**
> Understand the cloud-to-vehicle software delivery pipeline: versioned manifests, package verification, staged installation, rollback, and status reporting — the patterns used in production OTA systems like UPTANE, Mender, and Eclipse hawkBit.

---

## 4. Value Hypothesis (L1)

**Hypothesis:**
> Simulating an OTA update pipeline — from a cloud server hosting versioned ECU packages to a vehicle-side manager that downloads, verifies, applies, and reports updates — provides concrete understanding of the cloud→vehicle direction of SDV architecture that telemetry-only platforms cannot demonstrate.

**Evidence:**
- UPTANE (the automotive OTA security standard) is mandatory in UN ECE WP.29 R156 regulations, which apply to all new vehicle types sold in the EU from 2022. Every SDV engineer must understand OTA.
- Eclipse hawkBit, Mender, and UPTANE are the dominant open-source OTA frameworks — all follow the same manifest → download → verify → apply → report pattern that M6 simulates.
- The M1–M5 platform already has MQTT (for status reporting) and a Databroker (for runtime signal changes) — M6 reuses both, adding only two new services.

**Acceptance Criteria:**
- AC-1: OTA server hosts a version manifest and at least two versioned ECU configuration packages
- AC-2: OTA manager polls the server, detects a newer version, and downloads the package
- AC-3: OTA manager verifies the package integrity (SHA-256 hash) before applying
- AC-4: After successful installation, the ECU simulator's behavior changes observably (e.g., different speed range)
- AC-5: OTA status is published to MQTT (`sdv/vehicle-001/ota/status`) at each phase
- AC-6: Dashboard displays OTA status panel (current version, available version, installation state)

---

## 5. Behavior Hypothesis (L2)

### 5.1 Architecture Addition

```
【M6 addition — cloud → vehicle direction】

  Cloud (OTA Server)
  ┌─────────────────┐
  │   ota-server    │  ← NEW: hosts manifest + packages
  │   (Flask HTTP)  │
  │   :8080         │
  └────────┬────────┘
           │ HTTP GET (poll every 30s)
           ▼
  Vehicle (OTA Manager)
  ┌─────────────────┐
  │   ota-manager   │  ← NEW: download → verify → apply
  └────────┬────────┘
           │ writes new config
           ▼
  ┌─────────────────┐     ┌─────────────────┐
  │  ecu-simulator  │     │    Mosquitto     │
  │  (reloads       │     │  ota/status pub  │
  │   config)       │     └─────────────────┘
  └─────────────────┘             │
                                  ▼
                           Dashboard OTA panel
```

### 5.2 OTA Update Flow (phases)

```
Phase 1 — CHECK
  ota-manager polls GET /manifest → receives {latest_version, sha256, url}
  Compares with current installed version
  If newer: proceed to DOWNLOAD

Phase 2 — DOWNLOAD
  GET /packages/{version}.tar.gz → save to local staging area
  Publish MQTT: {phase: "downloading", version: "1.1.0", progress: 0–100}

Phase 3 — VERIFY
  Compute SHA-256 of downloaded package
  Compare with manifest hash
  If mismatch: publish MQTT error, abort (simulate rollback)

Phase 4 — APPLY
  Extract package → write new ECU config file
  Signal ecu-simulator to reload config (SIGTERM or config file watch)
  Publish MQTT: {phase: "installing", version: "1.1.0"}

Phase 5 — REPORT
  Publish MQTT: {phase: "complete", version: "1.1.0", status: "success"}
  ota-manager now tracks installed_version = "1.1.0"
```

### 5.3 What the Update Changes (Observable Outcome)

The "ECU firmware update" changes the vehicle signal simulation parameters:

| Version | Speed Range | SoC Drain Rate | Cabin Temp Range |
|---|---|---|---|
| `1.0.0` (baseline) | 10–120 km/h | normal | 19.5–24.5 °C |
| `1.1.0` (update) | 20–150 km/h | optimized (slower) | 18.0–26.0 °C |

After OTA, the Dashboard and AI Monitor immediately reflect the new signal behavior — demonstrating that the software update affected the running system.

### 5.4 User-Observable Behavior

| Action | Observable Result |
|---|---|
| `docker compose up` | All services start; ota-manager polls server at version 1.0.0 |
| `curl localhost:8080/manifest` | Returns manifest JSON with latest version |
| `curl -X POST localhost:8080/release/1.1.0` | Triggers new version release on server |
| Dashboard OTA panel | Shows: `Installed: 1.0.0 → Available: 1.1.0 → Installing… → Complete` |
| `mosquitto_sub -t 'sdv/vehicle-001/ota/status'` | JSON status at each phase |
| Dashboard signal charts | Speed range visibly changes after update completes |

### 5.5 Rollback Simulation

If the SHA-256 hash of the downloaded package does not match the manifest, the OTA manager:
1. Discards the corrupted package
2. Publishes MQTT: `{phase: "error", reason: "hash_mismatch", rollback: true}`
3. Remains at the current installed version
4. Retries on the next poll cycle

---

## 6. Out of Scope for M6

- UPTANE full protocol (director repo + image repo, ECU keys) — concept taught, not implemented
- Differential / delta updates (full package only)
- Multi-ECU concurrent updates
- Code signing / asymmetric cryptography (SHA-256 hash only)
- Persistent update history / audit log
- Bandwidth throttling / background download
