# Functional Requirements Document (FRD)
## Milestone 6: OTA Update Simulation
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | FRD |
| Milestone | 6 — OTA Update Simulation |
| Status | Draft |
| Hypothesis Layers | Domain (L3) · Interaction (L4) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | [PRD.md](PRD.md) · Milestone 5 FRD |
| Next Layer | [TRD.md](TRD.md) |

---

## 1. System Context

```
【M6 Full Architecture】

  ota-server :8080  (Docker, host network)
  ├── GET  /manifest          → version manifest JSON
  ├── GET  /packages/{ver}    → ECU config package (tar.gz)
  └── POST /release/{ver}     → trigger new release (dev tool)
         │
         │ HTTP poll every 30s
         ▼
  ota-manager  (Docker, host network)
  ├── CHECK   → compare versions
  ├── DOWNLOAD → GET package
  ├── VERIFY  → SHA-256 hash check
  ├── APPLY   → write config + notify ecu-simulator
  └── REPORT  → MQTT publish sdv/vehicle-001/ota/status
         │                        │
         │ config file watch      │ MQTT
         ▼                        ▼
  ecu-simulator (WSL2)       Mosquitto :1883
  (reloads config on change)      │
                                   ▼
                             Dashboard OTA panel
```

**Architectural invariants carried from M1–M5:**
- Databroker remains the single source of truth for signal state (DR-00)
- All existing services (dashboard, mqtt-bridge, ros2-bridge, ai-monitor) unchanged (DR-10)
- OTA update changes ECU behavior, not Databroker schema (DR-60)

---

## 2. User Stories

| ID | As a… | I want to… | So that… | Acceptance Criterion |
|---|---|---|---|---|
| US-60 | SDV engineer | trigger an OTA update and watch it progress phase by phase | I understand the cloud→vehicle software delivery pipeline | MQTT shows CHECK→DOWNLOAD→VERIFY→APPLY→REPORT phases |
| US-61 | SDV engineer | see the Dashboard OTA panel update in real time | I have a single view of update progress and installed version | Panel shows phase, version, and status badge |
| US-62 | SDV engineer | observe that signal behavior changes after OTA completes | I confirm the update actually affected the running ECU | Dashboard speed range shifts from 10–120 to 20–150 km/h |
| US-63 | SDV engineer | see a rollback when a corrupted package is detected | I understand the safety mechanism of hash verification | MQTT shows `error: hash_mismatch, rollback: true` |

---

## 3. Domain Rules (L3)

### DR-60: OTA updates ECU config only — not Databroker schema
The OTA package contains ECU simulation parameters (speed range, SoC drain rate, cabin temp range).
It MUST NOT modify VSS paths, Databroker config, or any service other than ecu-simulator.

### DR-61: Version format
Version strings follow semantic versioning: `MAJOR.MINOR.PATCH` (e.g., `1.0.0`, `1.1.0`).
The ota-manager MUST only install a version strictly greater than the currently installed version.
Downgrade is out of scope for M6.

### DR-62: Package integrity check (mandatory)
Before applying any package, ota-manager MUST verify the SHA-256 hash of the downloaded file
against the hash in the manifest. A mismatch MUST trigger rollback (DR-65).

### DR-63: Config reload without container restart
The ecu-simulator MUST reload its configuration without restarting the container.
Implementation: file watch on the config path — when the file changes, re-read parameters.

### DR-64: MQTT status publish (all phases)
ota-manager MUST publish a status message to `sdv/{VEHICLE_ID}/ota/status` at the
start and end of each phase (CHECK, DOWNLOAD, VERIFY, APPLY, REPORT).

### DR-65: Rollback on verification failure
If SHA-256 verification fails, ota-manager MUST:
1. Delete the corrupted downloaded file
2. Publish MQTT error with `rollback: true`
3. Remain at the currently installed version
4. Continue polling on the next cycle

### DR-66: ota-server is stateless across restarts
Package files and manifests are served from mounted config files.
No database required. Server state = files on disk.

---

## 4. Functional Requirements (L4)

### FR-60: ota-server — package registry

| ID | Requirement |
|---|---|
| FR-60-1 | `GET /manifest` returns JSON: `{latest_version, packages: [{version, url, sha256, changelog}]}` |
| FR-60-2 | `GET /packages/{version}.tar.gz` streams the package file |
| FR-60-3 | `POST /release/{version}` updates the manifest to set `latest_version` (dev/test endpoint) |
| FR-60-4 | Returns HTTP 404 if requested version does not exist |
| FR-60-5 | Logs each request: method, path, client IP |

### FR-61: ota-manager — update agent

| ID | Requirement |
|---|---|
| FR-61-1 | On startup, reads `installed_version` from a local state file (default: `1.0.0` if missing) |
| FR-61-2 | Every `POLL_INTERVAL_SEC` seconds, calls `GET /manifest` on ota-server |
| FR-61-3 | If `latest_version > installed_version`: proceeds to DOWNLOAD phase |
| FR-61-4 | Downloads package to a staging directory (`/tmp/ota-staging/`) |
| FR-61-5 | Computes SHA-256 of downloaded file; compares with manifest hash |
| FR-61-6 | On hash match: extracts package, writes new ECU config to shared config path |
| FR-61-7 | On hash mismatch: deletes file, publishes rollback status, aborts install |
| FR-61-8 | After successful apply: updates local state file with new `installed_version` |
| FR-61-9 | Publishes MQTT status JSON at each phase transition (DR-64) |
| FR-61-10 | On ota-server connection failure: logs warning, retries on next poll cycle |

### FR-62: ecu-simulator — config file watch

| ID | Requirement |
|---|---|
| FR-62-1 | On startup, reads ECU parameters from config file path (env: `ECU_CONFIG_PATH`) |
| FR-62-2 | Watches config file for changes using `watchdog` or `os.path.getmtime` polling |
| FR-62-3 | On config file change: reloads parameters and logs `[OTA] Config reloaded: version=1.1.0` |
| FR-62-4 | Signal simulation continues uninterrupted during reload (no pause or reset) |

### FR-63: Dashboard — OTA status panel

| ID | Requirement |
|---|---|
| FR-63-1 | Subscribes to `sdv/{VEHICLE_ID}/ota/status` via MQTT (reuses existing paho background thread) |
| FR-63-2 | Displays: installed version, available version, current phase, last update timestamp |
| FR-63-3 | Phase badge colours: CHECK=blue, DOWNLOAD=yellow, VERIFY=yellow, APPLY=orange, COMPLETE=green, ERROR=red |
| FR-63-4 | Shows changelog text from the manifest when an update is available |

### FR-64: ECU config package format

| Field | Description |
|---|---|
| `version` | Semantic version string |
| `speed_min` | Minimum simulated speed (km/h) |
| `speed_max` | Maximum simulated speed (km/h) |
| `soc_start` | Initial battery SoC (%) |
| `soc_drain_rate` | SoC drain per cycle (%) |
| `cabin_temp_min` | Minimum cabin temperature (°C) |
| `cabin_temp_max` | Maximum cabin temperature (°C) |
| `changelog` | Human-readable description of changes |

Package format: `.tar.gz` containing `ecu_config.json` + `version.txt`.

---

## 5. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-60 | Full OTA cycle (CHECK → COMPLETE) completes within 30 seconds on localhost |
| NFR-61 | ota-server handles concurrent requests without corruption (Flask single-threaded is sufficient) |
| NFR-62 | SHA-256 verification adds < 100ms overhead for packages < 1MB |
| NFR-63 | ecu-simulator config reload does not produce a gap in CAN frame output (< 1 frame missed) |
| NFR-64 | No new dependencies added to ros2-bridge, ros2-subscriber, mqtt-bridge, can-gateway |

---

## 6. Out of Scope

- UPTANE director/image repo split (concept only, not implemented)
- Asymmetric code signing (SHA-256 hash only)
- Delta / differential updates
- Multi-ECU orchestrated update
- Update scheduling / maintenance window
- Bandwidth throttling
