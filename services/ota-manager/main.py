#!/usr/bin/env python3
"""
OTA Manager — mini-sdv-platform  Milestone 6
=============================================
Vehicle-side OTA update agent.

Implements the CHECK → DOWNLOAD → VERIFY → APPLY → REPORT lifecycle:
  CHECK    Poll OTA server manifest for a newer version.
  DOWNLOAD Fetch the package file from the OTA server.
  VERIFY   Compute SHA-256 and compare with manifest hash.
  APPLY    Extract config JSON to the shared ECU config path.
  REPORT   Publish MQTT status and persist installed version.

SDV Concept:
  In production vehicles, this role is played by a dedicated OTA client
  such as Mender, GENIVI SOTA, or an OEM's proprietary agent. The
  CHECK→DOWNLOAD→VERIFY→APPLY→REPORT pattern is the core of UPTANE's
  primary device client flow (without the ECU key/director repo split,
  which is omitted for clarity in M6).

  The shared config file written here is watched by ecu-simulator, which
  reloads its simulation parameters without restarting — mirroring how
  real ECU firmware updates take effect after a controlled reset or
  live patch application.
"""

import hashlib
import json
import logging
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import paho.mqtt.client as mqtt_client

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ota-manager")

# ── Configuration ─────────────────────────────────────────────────────────────
OTA_SERVER_URL  = os.environ.get("OTA_SERVER_URL", "http://localhost:8080")
MQTT_HOST       = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT       = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID      = os.environ.get("VEHICLE_ID", "vehicle-001")
POLL_INTERVAL   = int(os.environ.get("POLL_INTERVAL_SEC", "30"))
ECU_CONFIG_PATH = os.environ.get("ECU_CONFIG_PATH", "/shared/ecu_config.json")
STATE_FILE      = os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")
STAGING_DIR     = "/tmp/ota-staging"
STATUS_TOPIC    = f"sdv/{VEHICLE_ID}/ota/status"


# ── Version state ─────────────────────────────────────────────────────────────

def get_installed_version() -> str:
    if os.path.exists(STATE_FILE):
        try:
            return json.loads(open(STATE_FILE).read())["version"]
        except Exception:
            pass
    return "1.0.0"


def save_installed_version(version: str) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump({"version": version}, f)


# ── MQTT ──────────────────────────────────────────────────────────────────────

def connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(client_id="ota-manager")
    retry = 2.0
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT)
            client.loop_start()
            log.info(f"MQTT connected → {MQTT_HOST}:{MQTT_PORT}")
            return client
        except Exception as exc:
            log.warning(f"MQTT connect failed: {exc}. Retrying in {retry:.0f}s...")
            time.sleep(retry)
            retry = min(retry * 2, 30.0)


def publish_status(mqtt, phase: str, **kwargs) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vehicle_id": VEHICLE_ID,
        "phase": phase,
        **kwargs,
    }
    mqtt.publish(STATUS_TOPIC, json.dumps(payload), qos=0)
    log.info(f"[{phase.upper()}] " + " | ".join(f"{k}={v}" for k, v in kwargs.items()))


# ── Package integrity ─────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def fetch_manifest() -> dict | None:
    try:
        with urllib.request.urlopen(f"{OTA_SERVER_URL}/manifest", timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        log.warning(f"Manifest fetch failed: {exc}")
        return None


def download_package(url: str, dest: str) -> bool:
    full_url = f"{OTA_SERVER_URL}{url}"
    try:
        log.info(f"Downloading {full_url} → {dest}")
        urllib.request.urlretrieve(full_url, dest)
        return True
    except Exception as exc:
        log.error(f"Download failed: {exc}")
        return False


# ── OTA lifecycle ─────────────────────────────────────────────────────────────

def apply_package(pkg_path: str) -> bool:
    extract_dir = os.path.join(STAGING_DIR, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with tarfile.open(pkg_path) as tar:
            # Security: only extract ecu_config.json, no path traversal
            members = [m for m in tar.getmembers()
                       if os.path.basename(m.name) == "ecu_config.json"]
            if not members:
                log.error("Package contains no ecu_config.json")
                return False
            tar.extract(members[0], path=extract_dir)

        extracted = os.path.join(extract_dir, "ecu_config.json")
        if not os.path.exists(extracted):
            extracted = os.path.join(extract_dir, members[0].name)

        os.makedirs(os.path.dirname(ECU_CONFIG_PATH), exist_ok=True)
        # shutil.copy2 works across filesystem boundaries (bind mount → container /tmp)
        shutil.copy2(extracted, ECU_CONFIG_PATH)
        log.info(f"Config written → {ECU_CONFIG_PATH}")
        return True
    except Exception as exc:
        log.error(f"Apply failed: {exc}")
        return False


def run() -> None:
    log.info("OTA Manager starting...")
    log.info(f"  OTA Server:     {OTA_SERVER_URL}")
    log.info(f"  MQTT:           {MQTT_HOST}:{MQTT_PORT}  topic={STATUS_TOPIC}")
    log.info(f"  Poll interval:  {POLL_INTERVAL}s")
    log.info(f"  ECU config:     {ECU_CONFIG_PATH}")

    os.makedirs(STAGING_DIR, exist_ok=True)
    mqtt = connect_mqtt()

    while True:
        installed = get_installed_version()

        # ── CHECK ────────────────────────────────────────────────────────
        publish_status(mqtt, "check", installed_version=installed)
        manifest = fetch_manifest()

        if manifest is None:
            time.sleep(POLL_INTERVAL)
            continue

        latest = manifest.get("latest_version", "")
        pkg_info = next(
            (p for p in manifest.get("packages", []) if p["version"] == latest),
            None,
        )

        if not latest or latest <= installed or pkg_info is None:
            log.info(f"[CHECK] Up to date. installed={installed} latest={latest}")
            time.sleep(POLL_INTERVAL)
            continue

        log.info(f"[CHECK] Update available: {installed} → {latest}")
        publish_status(
            mqtt, "downloading",
            from_version=installed,
            to_version=latest,
            changelog=pkg_info.get("changelog", ""),
        )

        # ── DOWNLOAD ─────────────────────────────────────────────────────
        pkg_path = os.path.join(STAGING_DIR, f"{latest}.tar.gz")
        if not download_package(pkg_info["url"], pkg_path):
            publish_status(mqtt, "error", version=latest, reason="download_failed")
            time.sleep(POLL_INTERVAL)
            continue

        # ── VERIFY ───────────────────────────────────────────────────────
        publish_status(mqtt, "verifying", version=latest)
        actual_hash   = sha256_file(pkg_path)
        expected_hash = pkg_info.get("sha256", "")

        if actual_hash != expected_hash:
            os.remove(pkg_path)
            publish_status(
                mqtt, "error",
                version=latest,
                reason="hash_mismatch",
                rollback=True,
                expected=expected_hash[:16] + "…",
                actual=actual_hash[:16] + "…",
            )
            log.error(
                f"[VERIFY] Hash mismatch — rollback. "
                f"expected={expected_hash[:16]}… got={actual_hash[:16]}…"
            )
            time.sleep(POLL_INTERVAL)
            continue

        log.info(f"[VERIFY] Hash OK: {actual_hash[:16]}…")

        # ── APPLY ────────────────────────────────────────────────────────
        publish_status(mqtt, "installing", version=latest, previous_version=installed)

        if not apply_package(pkg_path):
            publish_status(mqtt, "error", version=latest, reason="apply_failed")
            time.sleep(POLL_INTERVAL)
            continue

        # ── REPORT ───────────────────────────────────────────────────────
        save_installed_version(latest)
        os.remove(pkg_path)
        publish_status(
            mqtt, "complete",
            version=latest,
            previous_version=installed,
            changelog=pkg_info.get("changelog", ""),
        )
        log.info(f"[COMPLETE] OTA {installed} → {latest} successful.")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
