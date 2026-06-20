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
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ota-manager")

# ── Configuration ─────────────────────────────────────────────────────────────
OTA_SERVER_URL   = os.environ.get("OTA_SERVER_URL", "http://localhost:8080")
MQTT_HOST        = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT        = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID       = os.environ.get("VEHICLE_ID", "vehicle-001")
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
OTEL_ENABLED     = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
OTEL_ENDPOINT    = os.environ.get("OTEL_ENDPOINT", "http://localhost:4317")
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL_SEC", "30"))
ECU_CONFIG_PATH  = os.environ.get("ECU_CONFIG_PATH", "/shared/ecu_config.json")
# M15: policy checkpoint destination (for type=checkpoint OTA packages)
CHECKPOINT_PATH  = os.environ.get("CHECKPOINT_PATH", "/shared/policy.pt")
STATE_FILE       = os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")
STAGING_DIR      = "/tmp/ota-staging"
STATUS_TOPIC     = f"sdv/{VEHICLE_ID}/ota/status"


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

def setup_tracing(service_name: str) -> trace.Tracer:
    if not OTEL_ENABLED:
        return trace.get_tracer(service_name)
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    log.info(f"OpenTelemetry tracing enabled → {OTEL_ENDPOINT}")
    return trace.get_tracer(service_name)


def apply_tls(client: mqtt_client.Client) -> None:
    if not MQTT_TLS:
        return
    client.tls_set(
        ca_certs=MQTT_CA_CERT,
        certfile=MQTT_CLIENT_CERT,
        keyfile=MQTT_CLIENT_KEY,
    )


def connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(client_id="sdv-ota")
    apply_tls(client)
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

def apply_config_package(pkg_path: str) -> bool:
    """Extract ecu_config.json from a .tar.gz and write to ECU_CONFIG_PATH."""
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
        shutil.copy2(extracted, ECU_CONFIG_PATH)
        log.info(f"Config written → {ECU_CONFIG_PATH}")
        return True
    except Exception as exc:
        log.error(f"Config apply failed: {exc}")
        return False


def apply_checkpoint_package(pkg_path: str) -> bool:
    """Copy a .pt policy checkpoint to CHECKPOINT_PATH (M15 extension)."""
    try:
        os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
        shutil.copy2(pkg_path, CHECKPOINT_PATH)
        log.info(f"Checkpoint written → {CHECKPOINT_PATH} ({os.path.getsize(CHECKPOINT_PATH)/1e6:.1f} MB)")
        return True
    except Exception as exc:
        log.error(f"Checkpoint apply failed: {exc}")
        return False


def apply_package(pkg_path: str, pkg_type: str = "config") -> bool:
    if pkg_type == "checkpoint":
        return apply_checkpoint_package(pkg_path)
    return apply_config_package(pkg_path)


def run() -> None:
    log.info("OTA Manager starting...")
    log.info(f"  OTA Server:     {OTA_SERVER_URL}")
    log.info(f"  MQTT:           {MQTT_HOST}:{MQTT_PORT}  topic={STATUS_TOPIC}")
    log.info(f"  Poll interval:  {POLL_INTERVAL}s")
    log.info(f"  ECU config:     {ECU_CONFIG_PATH}")

    tracer = setup_tracing("ota-manager")
    os.makedirs(STAGING_DIR, exist_ok=True)
    mqtt = connect_mqtt()

    while True:
        installed = get_installed_version()

        with tracer.start_as_current_span("ota.check.cycle") as root_span:
            root_span.set_attribute("vehicle.id",            VEHICLE_ID)
            root_span.set_attribute("ota.installed_version", installed)

            # ── CHECK ────────────────────────────────────────────────────────
            publish_status(mqtt, "check", installed_version=installed)
            with tracer.start_as_current_span("manifest.fetch") as span:
                manifest = fetch_manifest()
                span.set_attribute("manifest.ok", manifest is not None)

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

            pkg_type = pkg_info.get("type", "config")  # M15: "config" | "checkpoint"
            log.info(f"[CHECK] Update available: {installed} → {latest} (type={pkg_type})")
            root_span.set_attribute("ota.latest_version", latest)
            root_span.set_attribute("ota.package_type", pkg_type)
            publish_status(
                mqtt, "downloading",
                from_version=installed,
                to_version=latest,
                package_type=pkg_type,
                changelog=pkg_info.get("changelog", ""),
            )

            # ── DOWNLOAD ─────────────────────────────────────────────────────
            ext = ".pt" if pkg_type == "checkpoint" else ".tar.gz"
            pkg_path = os.path.join(STAGING_DIR, f"{latest}{ext}")
            with tracer.start_as_current_span("package.download") as span:
                span.set_attribute("ota.version", latest)
                span.set_attribute("ota.type", pkg_type)
                ok = download_package(pkg_info["url"], pkg_path)
                span.set_attribute("download.ok", ok)
            if not ok:
                publish_status(mqtt, "error", version=latest, reason="download_failed")
                time.sleep(POLL_INTERVAL)
                continue

            # ── VERIFY ───────────────────────────────────────────────────────
            publish_status(mqtt, "verifying", version=latest)
            with tracer.start_as_current_span("package.verify") as span:
                actual_hash   = sha256_file(pkg_path)
                expected_hash = pkg_info.get("sha256", "")
                hash_ok       = actual_hash == expected_hash
                span.set_attribute("ota.version",  latest)
                span.set_attribute("ota.hash_ok",  hash_ok)

            if not hash_ok:
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
            with tracer.start_as_current_span("package.apply") as span:
                span.set_attribute("ota.version", latest)
                span.set_attribute("ota.type", pkg_type)
                applied = apply_package(pkg_path, pkg_type)
                span.set_attribute("apply.ok", applied)

            if not applied:
                publish_status(mqtt, "error", version=latest, reason="apply_failed")
                time.sleep(POLL_INTERVAL)
                continue

            # ── REPORT ───────────────────────────────────────────────────────
            save_installed_version(latest)
            os.remove(pkg_path)
            with tracer.start_as_current_span("mqtt.publish") as span:
                span.set_attribute("mqtt.topic",  STATUS_TOPIC)
                span.set_attribute("ota.version", latest)
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
