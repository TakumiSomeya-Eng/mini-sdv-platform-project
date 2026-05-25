# Technical Requirements Document (TRD)
## Milestone 6: OTA Update Simulation
### mini-sdv-platform

---

| Field | Value |
|---|---|
| Document Type | TRD |
| Milestone | 6 — OTA Update Simulation |
| Status | Draft |
| Hypothesis Layer | Implementation (L5) |
| Created | 2026-05-25 |
| Version | 1.0 |
| Depends On | [FRD.md](FRD.md) |

---

## 1. Implementation Hypothesis (L5)

> A Flask HTTP server (`ota-server`) hosting versioned JSON config packages and a Python polling agent (`ota-manager`) implementing the CHECK→DOWNLOAD→VERIFY→APPLY→REPORT cycle can simulate the full OTA pipeline with zero changes to the Databroker, MQTT bridge, ROS2 services, or AI monitor — requiring only an ecu-simulator config file watch and a new Dashboard OTA panel.

---

## 2. Technology Decisions

| Component | Choice | Rationale |
|---|---|---|
| OTA server | Flask (python:3.11-slim) | Minimal HTTP server; no framework overhead; serves static files + one dynamic endpoint |
| Package format | `.tar.gz` containing `ecu_config.json` | Standard archive; hashable; extractable with Python `tarfile` stdlib |
| Package integrity | `hashlib.sha256` (stdlib) | No external dependency; sufficient for simulation (production uses asymmetric signing) |
| Config watch | `os.path.getmtime()` polling | stdlib-only; avoids `watchdog` package dependency; 1s poll is fine for simulation |
| MQTT client | `paho-mqtt==1.6.1` | Consistent with mqtt-bridge and ai-monitor |
| Docker network | `network_mode: host` | Consistent with all M4+ services |
| Base image | `python:3.11-slim` | Consistent with all other services |

---

## 3. File Changes

### New Files

```
services/ota-server/
├── Dockerfile
├── main.py
└── requirements.txt          ← flask

services/ota-manager/
├── Dockerfile
├── main.py
└── requirements.txt          ← paho-mqtt==1.6.1  (requests is stdlib in 3.11)

config/ota/
├── packages/
│   ├── 1.0.0.tar.gz          ← baseline ECU config
│   └── 1.1.0.tar.gz          ← update ECU config
└── manifest.json             ← starts pointing to 1.0.0
```

### Modified Files

```
services/ecu-simulator/main.py          ← add config file watch + reload
services/ecu-simulator/requirements.txt ← no new deps (stdlib only)
services/dashboard/main.py              ← add OTA status panel
docker-compose.yml                      ← add ota-server, ota-manager services
README.md                               ← M6 architecture + quick start
```

---

## 4. config/ota/manifest.json

```json
{
  "latest_version": "1.0.0",
  "packages": [
    {
      "version": "1.0.0",
      "url": "/packages/1.0.0.tar.gz",
      "sha256": "<computed at build time>",
      "changelog": "Baseline ECU configuration. Speed: 10–120 km/h, SoC: 85%→55%, Cabin: 19.5–24.5°C."
    },
    {
      "version": "1.1.0",
      "url": "/packages/1.1.0.tar.gz",
      "sha256": "<computed at build time>",
      "changelog": "Performance update: extended speed range (20–150 km/h), optimized SoC drain, wider cabin temp range."
    }
  ]
}
```

---

## 5. ECU Config Package Format

Each package is a `.tar.gz` containing exactly one file: `ecu_config.json`.

**1.0.0/ecu_config.json (baseline — matches current hardcoded values):**
```json
{
  "version": "1.0.0",
  "speed_min": 10.0,
  "speed_max": 120.0,
  "soc_start": 85.0,
  "soc_drain_rate": 0.05,
  "cabin_temp_min": 19.5,
  "cabin_temp_max": 24.5,
  "changelog": "Baseline ECU configuration."
}
```

**1.1.0/ecu_config.json (update):**
```json
{
  "version": "1.1.0",
  "speed_min": 20.0,
  "speed_max": 150.0,
  "soc_start": 85.0,
  "soc_drain_rate": 0.03,
  "cabin_temp_min": 18.0,
  "cabin_temp_max": 26.0,
  "changelog": "Performance update: extended speed range, optimized drain, wider cabin temp range."
}
```

---

## 6. services/ota-server/main.py — Implementation Plan

```python
from flask import Flask, jsonify, send_from_directory, abort
import json, os

app = Flask(__name__)
PACKAGES_DIR = "/packages"
MANIFEST_PATH = "/manifest/manifest.json"

def load_manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)

@app.get("/manifest")
def get_manifest():
    return jsonify(load_manifest())

@app.get("/packages/<path:filename>")
def get_package(filename):
    return send_from_directory(PACKAGES_DIR, filename)

@app.post("/release/<version>")
def release_version(version):
    """Dev endpoint: set latest_version in manifest."""
    manifest = load_manifest()
    versions = [p["version"] for p in manifest["packages"]]
    if version not in versions:
        abort(404, f"Version {version} not in packages list")
    manifest["latest_version"] = version
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return jsonify({"latest_version": version})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

---

## 7. services/ota-manager/main.py — Implementation Plan

```python
import hashlib, json, os, tarfile, tempfile, time, urllib.request
import paho.mqtt.client as mqtt_client

OTA_SERVER      = os.environ.get("OTA_SERVER_URL", "http://localhost:8080")
MQTT_HOST       = os.environ.get("MQTT_HOST", "localhost")
VEHICLE_ID      = os.environ.get("VEHICLE_ID", "vehicle-001")
POLL_INTERVAL   = int(os.environ.get("POLL_INTERVAL_SEC", "30"))
ECU_CONFIG_PATH = os.environ.get("ECU_CONFIG_PATH", "/shared/ecu_config.json")
STATE_FILE      = "/tmp/ota_state.json"
ALERT_TOPIC     = f"sdv/{VEHICLE_ID}/ota/status"

def get_installed_version() -> str:
    if os.path.exists(STATE_FILE):
        return json.loads(open(STATE_FILE).read())["version"]
    return "1.0.0"

def save_installed_version(version: str):
    with open(STATE_FILE, "w") as f:
        json.dump({"version": version}, f)

def publish(mqtt, phase, **kwargs):
    payload = {"phase": phase, "vehicle_id": VEHICLE_ID,
               "timestamp": ..., **kwargs}
    mqtt.publish(ALERT_TOPIC, json.dumps(payload), qos=0)
    log.info(f"[{phase.upper()}] {kwargs}")

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def run():
    mqtt = connect_mqtt()
    while True:
        installed = get_installed_version()

        # CHECK
        publish(mqtt, "check", installed_version=installed)
        try:
            manifest = json.loads(
                urllib.request.urlopen(f"{OTA_SERVER}/manifest").read()
            )
        except Exception as exc:
            log.warning(f"Manifest fetch failed: {exc}")
            time.sleep(POLL_INTERVAL)
            continue

        latest = manifest["latest_version"]
        pkg_info = next((p for p in manifest["packages"]
                         if p["version"] == latest), None)

        if latest <= installed or pkg_info is None:
            log.info(f"[CHECK] Up to date. installed={installed}")
            time.sleep(POLL_INTERVAL)
            continue

        publish(mqtt, "downloading", from_version=installed,
                to_version=latest, changelog=pkg_info.get("changelog",""))

        # DOWNLOAD
        pkg_url = f"{OTA_SERVER}{pkg_info['url']}"
        tmp_path = f"/tmp/ota-staging/{latest}.tar.gz"
        os.makedirs("/tmp/ota-staging", exist_ok=True)
        urllib.request.urlretrieve(pkg_url, tmp_path)

        # VERIFY
        publish(mqtt, "verifying", version=latest)
        actual_hash = sha256_file(tmp_path)
        if actual_hash != pkg_info["sha256"]:
            os.remove(tmp_path)
            publish(mqtt, "error", version=latest,
                    reason="hash_mismatch", rollback=True)
            log.error(f"[VERIFY] Hash mismatch — rollback. "
                      f"expected={pkg_info['sha256'][:16]}… got={actual_hash[:16]}…")
            time.sleep(POLL_INTERVAL)
            continue

        # APPLY
        publish(mqtt, "installing", version=latest)
        with tarfile.open(tmp_path) as tar:
            tar.extractall("/tmp/ota-staging/extracted/")
        extracted_config = f"/tmp/ota-staging/extracted/ecu_config.json"
        os.replace(extracted_config, ECU_CONFIG_PATH)   # atomic write

        # REPORT
        save_installed_version(latest)
        publish(mqtt, "complete", version=latest, previous_version=installed)
        log.info(f"[COMPLETE] OTA {installed} → {latest} successful.")

        time.sleep(POLL_INTERVAL)
```

---

## 8. ecu-simulator/main.py Changes

### Config loading

```python
ECU_CONFIG_PATH = os.environ.get("ECU_CONFIG_PATH", "/shared/ecu_config.json")

def load_config() -> dict:
    if os.path.exists(ECU_CONFIG_PATH):
        with open(ECU_CONFIG_PATH) as f:
            return json.load(f)
    # Fallback: hardcoded baseline values
    return {
        "version": "1.0.0",
        "speed_min": 10.0, "speed_max": 120.0,
        "soc_start": 85.0, "soc_drain_rate": 0.05,
        "cabin_temp_min": 19.5, "cabin_temp_max": 24.5,
    }
```

### Config file watch (polling `getmtime`)

```python
config      = load_config()
config_mtime = os.path.getmtime(ECU_CONFIG_PATH) if os.path.exists(ECU_CONFIG_PATH) else 0

while True:
    # Check for config file update
    if os.path.exists(ECU_CONFIG_PATH):
        mtime = os.path.getmtime(ECU_CONFIG_PATH)
        if mtime != config_mtime:
            config = load_config()
            config_mtime = mtime
            log.info(f"[OTA] Config reloaded: version={config.get('version')}")

    # Use config values in simulation
    speed = simulate_speed(config["speed_min"], config["speed_max"], t)
    ...
```

---

## 9. Docker Compose Changes

### Shared volume for ECU config

```yaml
volumes:
  ota-config:    # shared between ota-manager (writer) and ecu-simulator (reader)
```

### New services

```yaml
ota-server:
  build:
    context: ./services/ota-server
  volumes:
    - ./config/ota/packages:/packages:ro
    - ./config/ota:/manifest
  network_mode: host
  restart: on-failure

ota-manager:
  build:
    context: ./services/ota-manager
  environment:
    OTA_SERVER_URL: http://localhost:8080
    MQTT_HOST: localhost
    VEHICLE_ID: vehicle-001
    POLL_INTERVAL_SEC: "30"
    ECU_CONFIG_PATH: /shared/ecu_config.json
  volumes:
    - ota-config:/shared
  depends_on:
    - ota-server
    - mosquitto
  network_mode: host
  restart: on-failure
```

### ecu-simulator volume mount

```yaml
# ecu-simulator runs in WSL2, not Docker — config path set via env:
# ECU_CONFIG_PATH=/path/to/shared/ecu_config.json
# For Docker-based ecu-simulator (if used):
ecu-simulator:
  volumes:
    - ota-config:/shared
  environment:
    ECU_CONFIG_PATH: /shared/ecu_config.json
```

> **Note:** Since ecu-simulator runs directly in WSL2 (not Docker), the shared path is a regular file path in WSL2 that ota-manager writes to. Both processes access the same filesystem path.

---

## 10. Triggering an Update (Quick Test)

```bash
# 1. Start all services
docker compose up -d

# 2. Check current manifest (should show latest_version: "1.0.0")
curl http://localhost:8080/manifest

# 3. Subscribe to OTA status
mosquitto_sub -h localhost -p 1883 -t "sdv/vehicle-001/ota/status" -v

# 4. Release version 1.1.0 (triggers update on next poll cycle)
curl -X POST http://localhost:8080/release/1.1.0

# 5. Watch OTA phases in MQTT (within POLL_INTERVAL_SEC seconds):
# → {phase: "check", installed_version: "1.0.0"}
# → {phase: "downloading", to_version: "1.1.0"}
# → {phase: "verifying", version: "1.1.0"}
# → {phase: "installing", version: "1.1.0"}
# → {phase: "complete", version: "1.1.0"}

# 6. Observe Dashboard signal charts — speed range changes to 20–150 km/h
```

---

## 11. Constraints

| ID | Constraint |
|---|---|
| CON-60 | ecu-simulator runs in WSL2 — shared config via filesystem path, not Docker volume |
| CON-61 | `os.replace()` is used for atomic config write — prevents ecu-simulator reading partial file |
| CON-62 | ota-server `POST /release/{version}` is a dev endpoint only — no auth (acceptable for simulation) |
| CON-63 | `urllib.request` (stdlib) used for HTTP — avoids `requests` dependency in ota-manager |
| CON-64 | Version comparison uses string comparison — valid only for well-formed semver with same major |
