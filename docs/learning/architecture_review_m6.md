# Mini SDV Platform — Architecture Review & Study Guide
## Milestone 6: OTA Update Simulation (CHECK→DOWNLOAD→VERIFY→APPLY→REPORT)

> **Date:** 2026-05-25

---

## Table of Contents

1. [What M6 Adds and Why](#1-what-m6-adds-and-why)
2. [OTA in the Automotive Industry](#2-ota-in-the-automotive-industry)
   - 2-1. Why OTA Matters for SDV
   - 2-2. UN ECE R156 — The Legal Mandate
   - 2-3. Production OTA Frameworks
3. [The CHECK→DOWNLOAD→VERIFY→APPLY→REPORT Lifecycle](#3-the-checkdownloadverifyapplyreport-lifecycle)
4. [Package Integrity: SHA-256 Hashing](#4-package-integrity-sha-256-hashing)
5. [OTA Server Deep Dive](#5-ota-server-deep-dive)
   - 5-1. Version Manifest Design
   - 5-2. Flask Endpoints
   - 5-3. The POST /release Dev Endpoint
6. [OTA Manager Deep Dive](#6-ota-manager-deep-dive)
   - 6-1. Version State Persistence
   - 6-2. Polling Loop
   - 6-3. Download and Staging
   - 6-4. Package Extraction and Apply
7. [ECU Simulator: Config File Watch](#7-ecu-simulator-config-file-watch)
   - 7-1. os.path.getmtime Polling
   - 7-2. Live Parameter Reload
8. [The Cross-Device Link Bug and Fix](#8-the-cross-device-link-bug-and-fix)
9. [Dashboard OTA Panel](#9-dashboard-ota-panel)
10. [Full M6 Architecture Walkthrough](#10-full-m6-architecture-walkthrough)
11. [UPTANE: What M6 Simplifies vs Production](#11-uptane-what-m6-simplifies-vs-production)
12. [Docker Compose Changes in M6](#12-docker-compose-changes-in-m6)
13. [Known Constraints and Trade-offs](#13-known-constraints-and-trade-offs)
14. [Real-World OTA System Comparison](#14-real-world-ota-system-comparison)
15. [Review Quiz](#15-review-quiz)

---

## 1. What M6 Adds and Why

### The gap M6 closes

After M5, the platform has a complete vehicle→cloud signal pipeline and an AI monitoring layer. But all communication flows in one direction: vehicle → cloud. The cloud can observe the vehicle; it cannot update it.

```
M1–M5 (vehicle → cloud only):

  ECU → CAN → Databroker → Dashboard
                         → MQTT → Cloud
                         → ROS2 → AD Stack
                         → AI Monitor → Alerts
```

M6 adds the **cloud → vehicle** direction:

```
M6 (closes the loop):

  OTA Server (cloud)
       │ HTTP manifest + packages
       ▼
  OTA Manager (vehicle)
       │ writes ecu_config.json
       ▼
  ECU Simulator (reloads config live)
       │ new signal ranges
       ▼
  Databroker → Dashboard (observable change)
```

**What M6 teaches:**

| Concept | Description |
|---|---|
| OTA lifecycle | CHECK→DOWNLOAD→VERIFY→APPLY→REPORT — the universal OTA update pattern |
| Version manifest | How a server communicates available software versions to a device |
| Package integrity | SHA-256 hash verification before applying any update |
| Config file watch | How a running process detects and applies config changes without restart |
| Cross-device file copy | Why `os.replace()` fails across filesystem boundaries and how to fix it |
| UPTANE concepts | The automotive OTA security standard that M6 simplifies |

---

## 2. OTA in the Automotive Industry

### 2-1. Why OTA Matters for SDV

Traditional vehicles required a dealership visit for any software change. A technician connected a J2534 cable, ran proprietary software, and flashed individual ECUs one by one — a process taking hours.

SDV platforms flip this model:

| Property | Traditional | SDV + OTA |
|---|---|---|
| Bug fix delivery | Recall + dealer visit (weeks) | OTA push (hours) |
| Feature addition | Not possible post-sale | OTA (Tesla Autopilot, BMW heated seats) |
| Security patch | Recall campaign | Emergency OTA patch |
| Rollout control | All-or-nothing | Phased: 1% → 10% → 100% of fleet |
| Cost | ~€200–500 per vehicle per visit | Near-zero marginal cost |

The OTA capability is so fundamental to SDV economics that it's now regulated — not optional.

### 2-2. UN ECE R156 — The Legal Mandate

**UN ECE Regulation 156** (Software Update and Software Update Management System) mandates:
- All new vehicle types sold in the EU, Japan, South Korea from **July 2022** must have a certified SUMS (Software Update Management System)
- Vehicles must cryptographically verify software authenticity before installation
- Complete update audit logs must be retained
- Vehicles must be able to roll back to a previous known-good state

M6's `manifest → sha256 → apply → report` pattern maps directly to the R156 requirements, at educational scale (no cryptographic signing, which R156 mandates in production).

### 2-3. Production OTA Frameworks

| Framework | Used by | Key feature |
|---|---|---|
| **UPTANE** | Linux Foundation standard; adopted by OEMs | Director + Image repo split; ECU-level signing |
| **Eclipse hawkBit** | Bosch, Siemens, many Tier-1s | Java server; supports UPTANE extension |
| **Mender** | Commercial + open-source | Binary delta updates; artifact signing |
| **OTA Connect (HERE)** | Jaguar Land Rover, Continental | SaaS; integrated with Eclipse Kuksa |
| **Tesla OTA** | Tesla | Proprietary; simultaneous multi-ECU orchestration |

All of these implement the same core pattern M6 demonstrates: **manifest → download → verify → apply → report**.

---

## 3. The CHECK→DOWNLOAD→VERIFY→APPLY→REPORT Lifecycle

This five-phase cycle is the universal OTA update pattern across all frameworks:

```
┌──────────────────────────────────────────────────────────────┐
│                    OTA Manager Loop                          │
│                                                              │
│  ┌─────────┐                                                 │
│  │  CHECK  │  GET /manifest → compare versions              │
│  └────┬────┘                                                 │
│       │ newer version available?                             │
│       │ No → sleep(POLL_INTERVAL) → CHECK again             │
│       │ Yes ↓                                                │
│  ┌──────────┐                                                │
│  │ DOWNLOAD │  GET /packages/{ver}.tar.gz → staging dir     │
│  └────┬─────┘                                                │
│       │                                                      │
│  ┌──────────┐                                                │
│  │  VERIFY  │  SHA-256(downloaded file) == manifest.sha256? │
│  └────┬─────┘  No → delete file, REPORT error, retry       │
│       │ Yes ↓                                                │
│  ┌─────────┐                                                 │
│  │  APPLY  │  extract tar.gz → write ecu_config.json        │
│  └────┬────┘                                                 │
│       │                                                      │
│  ┌──────────┐                                                │
│  │  REPORT  │  save new version, publish MQTT complete      │
│  └──────────┘                                                │
└──────────────────────────────────────────────────────────────┘
```

### Phase details

| Phase | M6 action | MQTT payload key |
|---|---|---|
| CHECK | `GET /manifest` → compare `latest_version` vs `installed_version` | `"phase": "check"` |
| DOWNLOAD | `urllib.request.urlretrieve()` to `/tmp/ota-staging/{ver}.tar.gz` | `"phase": "downloading"` |
| VERIFY | `hashlib.sha256()` over file bytes | `"phase": "verifying"` |
| APPLY | `tarfile.extract()` → `shutil.copy2()` to `/shared/ecu_config.json` | `"phase": "installing"` |
| REPORT | `save_installed_version()` + `mqtt.publish(complete)` | `"phase": "complete"` |

### Rollback (VERIFY failure)

```
VERIFY fails (hash mismatch)
  → os.remove(corrupted_package)
  → mqtt.publish({phase: "error", reason: "hash_mismatch", rollback: true})
  → installed_version unchanged
  → loop continues (retry on next poll cycle)
```

In production, rollback also means reverting the ECU to the previous firmware version. M6 simulates this by simply not writing the config — the ECU simulator keeps running with its current parameters.

---

## 4. Package Integrity: SHA-256 Hashing

### Why verify?

Without verification, a network attacker (or a corrupted download) could deliver a malformed package that crashes the ECU, bricks the vehicle, or (in a real attack) installs malicious firmware.

Hash verification ensures **the bytes received match the bytes the server intended to send**.

### How SHA-256 works in M6

```python
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
```

**Why read in 65536-byte chunks?**

Loading the entire file into memory at once (`f.read()`) fails for large packages (e.g., a 500 MB firmware image would require 500 MB of RAM). Chunked reading (`65536` = 64 KB) keeps memory usage constant regardless of file size.

**The `iter(lambda, sentinel)` pattern:**

```python
iter(lambda: f.read(65536), b"")
```

This is equivalent to:
```python
while True:
    chunk = f.read(65536)
    if chunk == b"":    # EOF
        break
    h.update(chunk)
```

`iter()` with a sentinel value calls the callable repeatedly until it returns the sentinel. `b""` (empty bytes) is what `f.read()` returns at EOF.

### Hash in the manifest

```json
{
  "version": "1.1.0",
  "sha256": "21474495df885bb58704d85823ac7c4aa0c0efe1fb15413d6a9402f621d2e0a8"
}
```

The SHA-256 is pre-computed at package build time and stored in the manifest. The OTA manager computes the hash of the downloaded file and compares the two strings. If they differ, the package is rejected.

**What SHA-256 does NOT protect against:**

If the OTA server itself is compromised, an attacker can serve both a malicious package AND a matching hash. This is why production OTA (UPTANE, R156) requires **asymmetric code signing** (RSA or ECDSA): the private key never leaves the OEM's signing infrastructure; the vehicle only has the public key. A compromised server cannot forge a valid signature without the private key.

M6 uses SHA-256 only (no signing) — sufficient for simulation, insufficient for production.

---

## 5. OTA Server Deep Dive

### 5-1. Version Manifest Design

```json
{
  "latest_version": "1.0.0",
  "packages": [
    {
      "version": "1.0.0",
      "url": "/packages/1.0.0.tar.gz",
      "sha256": "3fb7f...",
      "changelog": "Baseline ECU configuration."
    },
    {
      "version": "1.1.0",
      "url": "/packages/1.1.0.tar.gz",
      "sha256": "21474...",
      "changelog": "Performance update: extended speed range..."
    }
  ]
}
```

**`latest_version` as a pointer:** The manifest separates the concept of "what packages exist" (the `packages` array) from "what version should devices be running" (the `latest_version` pointer). This allows:
- **Staged rollout:** Set `latest_version` to `1.1.0` for 10% of vehicles, keep `1.0.0` for the rest
- **Emergency rollback:** Set `latest_version` back to `1.0.0` — all devices on `1.1.0` will see "up to date", but new installs use `1.0.0` (or trigger a downgrade if the OTA manager supports it)

### 5-2. Flask Endpoints

```python
@app.get("/manifest")          # Device polls this every POLL_INTERVAL_SEC
@app.get("/packages/<file>")   # Device downloads package after CHECK
@app.post("/release/<ver>")    # Engineer promotes a version (dev/CI endpoint)
@app.get("/health")            # Liveness probe
```

Flask's `send_from_directory()` serves the tar.gz files with correct `Content-Type` and supports range requests (partial download resume) transparently.

### 5-3. The POST /release Dev Endpoint

```python
@app.post("/release/<version>")
def release_version(version):
    manifest = load_manifest()
    manifest["latest_version"] = version
    save_manifest(manifest)
    return jsonify({"previous": old, "latest_version": version})
```

In production, this endpoint would be part of a CI/CD pipeline (Jenkins, GitHub Actions) triggered after automated testing passes:

```
Build ECU firmware → Run HIL tests → Sign package → POST /release/{ver} → Fleet rollout begins
```

In M6 it's a simple HTTP endpoint for manual testing. **No authentication** — acceptable for simulation; production requires OAuth2 or mTLS.

---

## 6. OTA Manager Deep Dive

### 6-1. Version State Persistence

```python
STATE_FILE = "/tmp/ota_state.json"

def get_installed_version() -> str:
    if os.path.exists(STATE_FILE):
        return json.loads(open(STATE_FILE).read())["version"]
    return "1.0.0"   # factory default

def save_installed_version(version: str):
    with open(STATE_FILE, "w") as f:
        json.dump({"version": version}, f)
```

The installed version is persisted to a JSON file in `/tmp`. This survives container restarts (as long as the container's `/tmp` is not cleared by Docker). In production, this would be stored in secure, tamper-evident persistent storage (e.g., a TPM-protected partition or an AUTOSAR NVM block).

**Factory default:** If the state file doesn't exist (first boot, or reset), `get_installed_version()` returns `"1.0.0"`. This represents the firmware version shipped from the factory.

### 6-2. Polling Loop

```python
while True:
    installed = get_installed_version()
    publish_status(mqtt, "check", installed_version=installed)
    manifest = fetch_manifest()

    latest = manifest.get("latest_version", "")
    if not latest or latest <= installed:
        log.info(f"[CHECK] Up to date. installed={installed} latest={latest}")
        time.sleep(POLL_INTERVAL)
        continue

    # → proceed to DOWNLOAD
```

**Version comparison:** `latest <= installed` uses Python string comparison. This works correctly for well-formed semantic versions with the same number of components (`"1.0.0"` < `"1.1.0"` < `"2.0.0"`). It breaks for versions like `"1.10.0"` vs `"1.9.0"` (string `"1.9.0"` > `"1.10.0"` because `"9"` > `"1"`). Production systems use `packaging.version.Version` for correct semver comparison.

**Why not use event-driven webhooks?**

An alternative design has the server push a notification to the vehicle when a new version is available. This reduces latency (seconds vs up to `POLL_INTERVAL` seconds). However, it requires the vehicle to have a stable inbound network address — vehicles behind NAT or on cellular networks don't have this. Polling is universal; webhooks are an optimization for server-reachable devices.

### 6-3. Download and Staging

```python
STAGING_DIR = "/tmp/ota-staging"
pkg_path = os.path.join(STAGING_DIR, f"{latest}.tar.gz")
urllib.request.urlretrieve(pkg_url, pkg_path)
```

`urllib.request.urlretrieve()` downloads the entire file to disk before returning. For large packages, this means:
- Enough disk space for the full package in the staging area
- No download resumption if interrupted

Production OTA clients (Mender, hawkBit client) use streaming downloads with resume capability — if a 500 MB download is interrupted at 490 MB, they resume from where they left off using HTTP `Range` headers.

**Why a staging directory?**

The downloaded package is placed in a separate staging area, not directly applied. This allows:
1. VERIFY to read the staged file before any ECU modification
2. Cleanup of failed downloads without touching the running ECU config
3. Space management (multiple staged versions can coexist)

### 6-4. Package Extraction and Apply

```python
with tarfile.open(pkg_path) as tar:
    members = [m for m in tar.getmembers()
               if os.path.basename(m.name) == "ecu_config.json"]
    tar.extract(members[0], path=extract_dir)

shutil.copy2(extracted, ECU_CONFIG_PATH)
```

**Security: path traversal protection**

The member filter `os.path.basename(m.name) == "ecu_config.json"` prevents path traversal attacks. A malicious tar.gz could contain a member named `../../etc/passwd` — without filtering, `tar.extractall()` would overwrite the file at that path. By only extracting members whose basename is `ecu_config.json`, arbitrary path traversal is prevented.

**Why `shutil.copy2()` instead of `os.replace()`?**

See Section 8 for the full explanation. Short version: `os.replace()` is an atomic rename — it only works within the same filesystem. The staging dir (`/tmp/ota-staging/`) and the config dest (`/shared/`) are on different filesystems (container's tmpfs vs bind-mounted WSL2 path). `shutil.copy2()` copies bytes across filesystem boundaries.

---

## 7. ECU Simulator: Config File Watch

### 7-1. os.path.getmtime Polling

```python
config_mtime = os.path.getmtime(ECU_CONFIG_PATH) if os.path.exists(ECU_CONFIG_PATH) else 0.0

while True:
    # Check for config update every cycle (1s)
    if os.path.exists(ECU_CONFIG_PATH):
        mtime = os.path.getmtime(ECU_CONFIG_PATH)
        if mtime != config_mtime:
            config_mtime = mtime
            new_cfg = load_config()
            vehicle.reload_config(new_cfg)

    # ... publish CAN frames ...
    time.sleep(UPDATE_INTERVAL)
```

`os.path.getmtime()` returns the file's modification timestamp as a Unix float (seconds since epoch, e.g., `1748203548.7`). Comparing the current mtime to the stored mtime detects changes without reading the file content on every cycle.

**Why polling instead of inotify/watchdog?**

`inotify` (Linux kernel file change notification) is more efficient — zero CPU between changes vs one `os.stat()` call per second. However:
- `inotify` requires the `watchdog` Python package (extra dependency)
- Bind-mounted paths from Docker volumes don't always reliably trigger `inotify` events
- At 1Hz, one `os.stat()` per cycle is negligible overhead

Polling `getmtime` at 1Hz is the pragmatic choice here.

### 7-2. Live Parameter Reload

```python
class VehicleState:
    def reload_config(self, config: dict) -> None:
        self.cfg = config
        log.info(f"[OTA] VehicleState config reloaded: version={config.get('version')}")

    def speed(self) -> float:
        cfg = self.cfg   # always reads from current config
        mid = (cfg["speed_min"] + cfg["speed_max"]) / 2.0
        amp = (cfg["speed_max"] - cfg["speed_min"]) / 2.0
        return mid + amp * math.sin(self._t * 0.04) + noise
```

The `VehicleState` methods always read from `self.cfg` (the current config dict). When `reload_config()` replaces the dict, the next `speed()` call immediately uses the new parameters — no restart, no gap in CAN frame output.

**Observable effect after OTA 1.0.0 → 1.1.0:**

| Parameter | v1.0.0 | v1.1.0 |
|---|---|---|
| Speed range | 10–120 km/h | 20–150 km/h |
| SoC drain rate | 0.05%/cycle | 0.03%/cycle |
| Cabin temp range | 19.5–24.5 °C | 18.0–26.0 °C |

The Dashboard's speed chart visibly shifts to a higher maximum within seconds of the OTA completing.

---

## 8. The Cross-Device Link Bug and Fix

### The bug

When the first implementation used `os.replace()`:

```python
os.replace(extracted, ECU_CONFIG_PATH)
# extracted    = /tmp/ota-staging/extracted/ecu_config.json  (container /tmp)
# ECU_CONFIG_PATH = /shared/ecu_config.json                  (bind mount)
```

Error:
```
[Errno 18] Invalid cross-device link: '/tmp/ota-staging/extracted/ecu_config.json' -> '/shared/ecu_config.json'
```

### Why this error occurs

`os.replace()` is a thin wrapper around the C `rename()` syscall. `rename()` is **atomic** — the kernel moves the file by updating the directory entry, not by copying bytes. This atomic property only works when source and destination are on the **same filesystem** (same block device or tmpfs mount).

In M6:
- `/tmp/ota-staging/` = container's own tmpfs (ephemeral, per-container)
- `/shared/` = bind mount from WSL2's `/tmp/sdv-ota/` (ext4 filesystem)

These are two different filesystems → `rename()` fails with `EXDEV` (errno 18 = "Invalid cross-device link").

```
Container filesystem layout:
  / (overlay filesystem)
  ├── tmp/            ← container's own tmpfs
  │   └── ota-staging/
  │       └── extracted/ecu_config.json   ← SOURCE
  └── shared/         ← bind mount (different device!)
      └── ecu_config.json                 ← DEST
```

### The fix

```python
shutil.copy2(extracted, ECU_CONFIG_PATH)
```

`shutil.copy2()` copies file **bytes** (via `read()` + `write()` loops) rather than renaming the directory entry. Byte copying works across any two paths regardless of filesystem boundary.

`copy2` vs `copy`:
- `shutil.copy()` — copies content + permissions
- `shutil.copy2()` — copies content + permissions + metadata (timestamps) ← used in M6

**Trade-off vs `os.replace()`:**

| Property | `os.replace()` | `shutil.copy2()` |
|---|---|---|
| Atomic | ✅ Yes (kernel guarantees) | ❌ No (reader may see partial write) |
| Cross-device | ❌ No | ✅ Yes |
| Performance | O(1) — just a dir entry update | O(file size) |

For the ECU config file (~300 bytes), the non-atomic write is acceptable — the mtime check in ecu-simulator uses `os.path.getmtime()`, which reads the timestamp after the write completes. A partially written JSON would fail `json.load()`, leaving the previous config intact.

---

## 9. Dashboard OTA Panel

### MQTT topic reuse

The M5 dashboard already subscribes to an MQTT topic for AI alerts. M6 reuses the same paho background thread by subscribing to an additional topic:

```python
client.subscribe(AI_ALERT_TOPIC)    # M5: sdv/vehicle-001/alerts/ai
client.subscribe(OTA_STATUS_TOPIC)  # M6: sdv/vehicle-001/ota/status
```

The `on_message` callback routes by topic:

```python
def on_message(_client, _userdata, msg):
    data = json.loads(msg.payload.decode())
    if msg.topic == AI_ALERT_TOPIC:
        st.session_state.ai_alert = data
    elif msg.topic == OTA_STATUS_TOPIC:
        st.session_state.ota_status = data
```

This is the **publish-subscribe pattern in action**: one MQTT connection, multiple topic subscriptions, routing by topic string. The same pattern scales to dozens of topics without additional threads or connections.

### Phase colour mapping

```python
phase_config = {
    "check":       ("✓ Up to date",  "success"),   # green
    "downloading": ("⬇ Downloading", "warning"),   # yellow
    "verifying":   ("🔍 Verifying",  "warning"),   # yellow
    "installing":  ("⚙ Installing",  "warning"),   # orange
    "complete":    ("✅ Complete",    "success"),   # green
    "error":       ("✗ Error",       "error"),     # red
}
```

The colour mapping gives an immediate visual status without reading the text — a UI convention borrowed from CI/CD pipeline dashboards (GitHub Actions, Jenkins).

---

## 10. Full M6 Architecture Walkthrough

Tracing a complete OTA update from engineer trigger to ECU reload:

```
Step 1 — Engineer triggers release (via curl or CI/CD)
  POST http://localhost:8080/release/1.1.0
  ota-server: manifest["latest_version"] = "1.1.0"
  ota-server: writes updated manifest.json to disk

Step 2 — OTA Manager: CHECK phase (≤30s later)
  GET http://localhost:8080/manifest
  Response: {latest_version: "1.1.0", packages: [...]}
  Comparison: "1.1.0" > "1.0.0" → update available
  MQTT publish: {phase: "check", installed_version: "1.0.0"}
  MQTT publish: {phase: "downloading", to_version: "1.1.0", changelog: "..."}

Step 3 — OTA Manager: DOWNLOAD phase
  GET http://localhost:8080/packages/1.1.0.tar.gz
  urllib.request.urlretrieve() → /tmp/ota-staging/1.1.0.tar.gz
  ~300 bytes (tiny config package)

Step 4 — OTA Manager: VERIFY phase
  MQTT publish: {phase: "verifying", version: "1.1.0"}
  sha256_file("/tmp/ota-staging/1.1.0.tar.gz")
    = "21474495df885bb58704d85823ac7c4aa0c0efe1fb15413d6a9402f621d2e0a8"
  manifest sha256
    = "21474495df885bb58704d85823ac7c4aa0c0efe1fb15413d6a9402f621d2e0a8"
  Match → proceed
  log: [VERIFY] Hash OK: 21474495…

Step 5 — OTA Manager: APPLY phase
  MQTT publish: {phase: "installing", version: "1.1.0", previous_version: "1.0.0"}
  tarfile.open("1.1.0.tar.gz") → extract ecu_config.json to /tmp/ota-staging/extracted/
  shutil.copy2("/tmp/ota-staging/extracted/ecu_config.json", "/shared/ecu_config.json")
  log: Config written → /shared/ecu_config.json

Step 6 — ECU Simulator: config file watch fires
  os.path.getmtime("/tmp/sdv-ota/ecu_config.json") changed
  load_config() → reads new JSON: {version: "1.1.0", speed_max: 150.0, ...}
  vehicle.reload_config(new_cfg)
  log: [OTA] Config reloaded: version=1.1.0
  Next CAN frame: TX CAN 0x100 → Speed = 147.xx km/h (now within 20–150 range)

Step 7 — CAN Gateway: picks up new speed range transparently
  Reads CAN frame, decodes float32 → gRPC SetCurrentValues to Databroker
  (no change needed — CAN frame format unchanged, only values differ)

Step 8 — OTA Manager: REPORT phase
  save_installed_version("1.1.0") → writes /tmp/ota_state.json
  os.remove("/tmp/ota-staging/1.1.0.tar.gz")
  MQTT publish: {phase: "complete", version: "1.1.0", previous_version: "1.0.0"}
  log: [COMPLETE] OTA 1.0.0 → 1.1.0 successful.

Step 9 — Dashboard: OTA panel updates
  paho on_message callback: st.session_state.ota_status = {phase: "complete", ...}
  Next Streamlit rerun: render_ota_status() shows "✅ Complete — version: 1.1.0"
  Signal charts: speed range visibly extends to ~150 km/h
```

**End-to-end latency budget:**

| Phase | Duration |
|---|---|
| Poll cycle wait | 0–30 s |
| DOWNLOAD (localhost) | < 10 ms |
| VERIFY (SHA-256, ~300B) | < 1 ms |
| APPLY (file copy) | < 5 ms |
| ECU config reload | < 1 s (next cycle) |
| **Total worst case** | **~31 s** |

---

## 11. UPTANE: What M6 Simplifies vs Production

UPTANE is the industry-standard automotive OTA security framework. M6 demonstrates its core flow but simplifies several security layers:

### UPTANE full architecture (production)

```
OEM Backend:
  ┌─────────────────┐    ┌─────────────────────┐
  │ Director Repo   │    │    Image Repo        │
  │ (per-vehicle    │    │ (all packages,       │
  │  targeting)     │    │  immutable)          │
  └────────┬────────┘    └──────────┬──────────┘
           │ signed targets.json    │ signed packages
           └────────────┬───────────┘
                        │
              Vehicle OTA Client
              ├── Primary ECU (gateway)
              │   ├── validates Director targets
              │   ├── validates Image repo metadata
              │   └── distributes to Secondary ECUs
              └── Secondary ECU (powertrain, HVAC, ...)
                  └── validates with its own ECU key
```

### M6 simplifications

| UPTANE requirement | M6 implementation | Production |
|---|---|---|
| Asymmetric signing | SHA-256 hash only | RSA-2048 or ECDSA-P256 signatures |
| Director + Image repo split | Single Flask server | Two separate repos (different trust roots) |
| Per-ECU key management | Not implemented | Each ECU has unique key pair in TPM/HSM |
| Rollback attack prevention | Version string comparison only | Monotonic version counters, no downgrade |
| Partial verification (Secondary ECUs) | Not implemented | Primary verifies for Secondary ECUs |
| Timeserver / anti-freeze | Not implemented | Signed timestamp prevents replay attacks |

**Key insight:** M6 teaches the *shape* of UPTANE (manifest → download → verify → apply → report) without the cryptographic complexity. Understanding the shape is the prerequisite for understanding the security additions.

---

## 12. Docker Compose Changes in M6

### Two new services

```yaml
ota-server:
  volumes:
    - ./config/ota/packages:/packages:ro   # read-only: packages never change
    - ./config/ota:/manifest               # read-write: manifest.json is updated by POST /release
  network_mode: host

ota-manager:
  environment:
    ECU_CONFIG_PATH: /shared/ecu_config.json
  volumes:
    - /tmp/sdv-ota:/shared   # bind mount: same path visible to WSL2 ecu-simulator
  network_mode: host
```

### The bind mount design

```
Docker container (ota-manager):
  /shared/ecu_config.json    ← writes here

WSL2 host filesystem:
  /tmp/sdv-ota/ecu_config.json   ← same bytes, seen by ecu-simulator

Mapping: /tmp/sdv-ota (WSL2) ↔ /shared (container)
```

This is a **host bind mount** (not a named volume). The path `/tmp/sdv-ota` in WSL2 and `/shared` in the container refer to the same inode. Any write by ota-manager appears instantly in WSL2 — which is why the ecu-simulator's `getmtime()` poll detects the change within 1 second.

**Why not a Docker named volume?**

Named volumes are managed by Docker and stored at `/var/lib/docker/volumes/{name}/_data/` inside WSL2. The ecu-simulator (a WSL2 process, not a Docker container) would need to know this internal Docker path to read the config. A host bind mount to `/tmp/sdv-ota` is explicit and predictable.

### Read-only vs read-write volume mounts

```yaml
- ./config/ota/packages:/packages:ro   # :ro = read-only
- ./config/ota:/manifest               # default = read-write
```

The packages directory is mounted `:ro` because package files are immutable — they should never be modified by the server process. The manifest directory is read-write because `POST /release` updates `manifest.json`.

---

## 13. Known Constraints and Trade-offs

| ID | Constraint | Impact | Mitigation |
|---|---|---|---|
| CON-60 | ecu-simulator runs in WSL2, not Docker | Config path must be a WSL2 filesystem path | Bind mount `/tmp/sdv-ota` bridges Docker and WSL2 |
| CON-61 | `shutil.copy2()` is not atomic | ecu-simulator could read a partial config | Config is ~300 bytes — copy completes in microseconds; partial read is theoretical |
| CON-62 | Version comparison uses string `<=` | Breaks for `1.9.0` vs `1.10.0` | Use `packaging.version.Version` for production |
| CON-63 | No code signing | SHA-256 only — a compromised server can forge matching hash | Production requires asymmetric signing (RSA/ECDSA) |
| CON-64 | `POST /release` has no authentication | Anyone with network access can trigger an update | Production requires OAuth2/mTLS on management endpoints |
| CON-65 | State file in `/tmp` — lost on container restart | Installed version resets to `1.0.0` | Production stores version in persistent, tamper-evident storage |
| CON-66 | No download resume | Large package interrupted = full re-download | Production uses HTTP Range headers for resume |

---

## 14. Real-World OTA System Comparison

| Component | M6 | Mender | Eclipse hawkBit | UPTANE |
|---|---|---|---|---|
| Server | Flask (1 file) | Mender Server (Go + DB) | Java Spring Boot | Specification (any backend) |
| Client | Python polling | Mender client (Go daemon) | hawkBit DDI client | UPTANE reference client |
| Package format | tar.gz + JSON | `.mender` artifact (delta-capable) | Binary blob | Any (metadata separate) |
| Integrity | SHA-256 | SHA-256 + artifact signing | SHA-256 | RSA/ECDSA signatures |
| Rollback | Skip apply | Boot partition swap | App-level callback | Director targets rollback |
| Multi-ECU | No | No (single device) | Yes (DMF protocol) | Yes (primary + secondaries) |
| Phased rollout | No | Yes (groups) | Yes (rollout groups) | Partial (Director per vehicle) |
| Regulatory | — | UNECE R156 compatible | UNECE R156 compatible | UNECE R156 compliant |

---

## 15. Review Quiz

**Q1.** Why does the manifest separate `latest_version` (a pointer) from the `packages` array (all available packages)? What does this enable?

> **A:** It decouples "what software exists" from "what version devices should run." This enables: (1) staged rollout — set `latest_version=1.1.0` for 10% of the fleet while others stay on `1.0.0`; (2) emergency rollback — set `latest_version` back to `1.0.0` without deleting the `1.1.0` package; (3) A/B testing of different versions across fleet segments.

---

**Q2.** The OTA manager uses `latest <= installed` (string comparison) for version gating. Give a specific case where this produces the wrong result, and how to fix it.

> **A:** `"1.9.0" > "1.10.0"` because Python compares strings character-by-character: `"9"` > `"1"`. A device on `1.9.0` would not install `1.10.0` because the comparison says it's already "newer." Fix: use `packaging.version.Version("1.9.0") < packaging.version.Version("1.10.0")` which parses the components as integers.

---

**Q3.** The SHA-256 hash is chunked into 65536-byte reads. What would happen if the entire file were read into memory at once, and why is chunking better?

> **A:** `f.read()` loads the entire file into RAM. For a 500 MB firmware image, this requires 500 MB of RAM — likely exceeding the OTA manager's memory limit, causing an OOM kill. Chunked reading (`f.read(65536)`) keeps memory usage constant at 64 KB regardless of file size. The `iter(lambda: f.read(65536), b"")` pattern stops when EOF returns an empty bytes object.

---

**Q4.** Why does SHA-256 hash verification alone NOT protect against a compromised OTA server?

> **A:** If the server is compromised, the attacker controls both the package file AND the manifest hash field. They can serve a malicious package and set the hash to match the malicious file — the client computes the hash, it matches the manifest, and the malicious update is applied. True protection requires **asymmetric code signing**: the OEM signs packages with a private key that never leaves their infrastructure; the vehicle verifies with the public key. A compromised server cannot forge a valid signature without the private key.

---

**Q5.** Why did `os.replace()` fail with `[Errno 18] Invalid cross-device link`, and what does `os.replace()` actually do at the kernel level?

> **A:** `os.replace()` calls the C `rename()` syscall, which is an atomic directory-entry update (no byte copying). The kernel can only do this when source and destination are on the same filesystem. In M6, `/tmp/ota-staging/` is the container's own tmpfs and `/shared/` is a bind-mounted WSL2 path — two different filesystems. `rename()` across filesystems returns `EXDEV` (errno 18). Fix: `shutil.copy2()` which copies bytes and works across any two paths.

---

**Q6.** The ECU Simulator uses `os.path.getmtime()` polling to detect config changes instead of `inotify`. What are the trade-offs?

> **A:** `getmtime` polling: CPU cost = one `os.stat()` syscall per second (negligible); works reliably with bind mounts. `inotify`: zero CPU between events, but bind-mounted paths from Docker volumes don't always trigger `inotify` events reliably (kernel may not propagate the event across the mount boundary). Polling at 1Hz is the pragmatic choice here. `watchdog` (which wraps `inotify`) adds a package dependency and has known issues with bind mounts.

---

**Q7.** The path traversal protection in `apply_package()` filters tar members by `os.path.basename(m.name) == "ecu_config.json"`. What attack does this prevent?

> **A:** A malicious tar.gz could contain a member named `../../etc/cron.d/backdoor` or `../../root/.ssh/authorized_keys`. Without filtering, `tar.extractall()` would write to those paths relative to the extraction directory — potentially overwriting system files. By only extracting members whose basename (last component) equals `ecu_config.json`, path traversal is prevented regardless of the full path inside the archive.

---

**Q8.** The OTA state file is stored in `/tmp/ota_state.json`. What happens to the installed version if the Docker container is restarted, and what would production systems use instead?

> **A:** The container's `/tmp` is ephemeral — it's reset on container restart. After a restart, `get_installed_version()` returns `"1.0.0"` (the hardcoded default), even if `1.1.0` was already installed. This causes the OTA manager to "install" `1.1.0` again (wasteful but harmless in M6). Production systems store the version in persistent, tamper-evident storage: a dedicated flash partition, an AUTOSAR NVM block, or a TPM-sealed key-value store.

---

**Q9.** The manifest directory is mounted read-write but the packages directory is mounted read-only (`config/ota/packages:/packages:ro`). Why?

> **A:** Package files are immutable artifacts — once built and published, they should never change (changing a package would invalidate its SHA-256 hash, breaking all devices that already verified it). The `:ro` mount enforces this at the OS level: even if there's a bug in ota-server, it cannot overwrite a package file. The manifest, however, must be writable because `POST /release/{version}` updates `latest_version` in `manifest.json`.

---

**Q10.** The `VehicleState.reload_config()` method replaces `self.cfg` atomically. Why does signal output continue without interruption during a config reload?

> **A:** Python's Global Interpreter Lock (GIL) ensures that `self.cfg = config` (a simple object reference assignment) is atomic at the Python level — no other thread can see a partially-updated reference. The `speed()` method reads `cfg = self.cfg` once at the start; if a reload happens between two `speed()` calls, the second call gets the new config. There's no gap in CAN frame output because the main loop always completes the current `send_signal()` calls before checking `getmtime()`. The reload happens at cycle boundary, not mid-frame.

---

**Q11.** What is the difference between `shutil.copy()` and `shutil.copy2()`? Which does M6 use and why?

> **A:** `shutil.copy()` copies file content + permission bits. `shutil.copy2()` additionally copies metadata: modification time, access time, and other stat fields. M6 uses `copy2()` so the destination file's mtime matches the source's mtime. However, in practice the ecu-simulator detects the change by the destination file's mtime changing (from non-existent to any value, or from the previous version's mtime to a new one) — the exact mtime value doesn't matter for the detection logic. Either `copy` or `copy2` would work here.

---

**Q12.** Describe the complete cloud→vehicle loop that M6 completes. What was missing before M6, and what is the full bidirectional flow now?

> **A:** Before M6, all communication flowed vehicle→cloud only: ECU signals traveled CAN→Gateway→Databroker→Dashboard/MQTT/ROS2/AI-Monitor. The cloud could observe but not update the vehicle. M6 adds the reverse: OTA Server (cloud) → OTA Manager (vehicle) → ECU config file → ECU Simulator parameter reload → new signal ranges in CAN frames → Databroker → Dashboard. The full bidirectional loop: ECU generates signals → cloud receives and monitors them (MQTT, AI) → cloud pushes software updates → ECU behavior changes → new signals flow back to cloud. This closed loop is the operational model of every modern SDV platform.
