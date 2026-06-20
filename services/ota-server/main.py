#!/usr/bin/env python3
"""
OTA Server — mini-sdv-platform  Milestone 6
============================================
Simulates the cloud-side OTA package registry.

Endpoints:
  GET  /manifest              → version manifest JSON
  GET  /packages/{version}    → ECU config package (tar.gz)
  POST /release/{version}     → set latest_version in manifest (dev/test)

SDV Concept:
  In production, this role is played by Eclipse hawkBit, Mender, or an
  OEM's proprietary update server. The manifest + package-URL + hash
  pattern is the core of UPTANE's image repository.

  The POST /release endpoint simulates a deployment engineer releasing
  a new software version to the fleet — in production this would be
  a CI/CD pipeline artifact promotion step with cryptographic signing.
"""

import json
import logging
import os

from flask import Flask, abort, jsonify, send_from_directory

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ota-server")

PACKAGES_DIR  = os.environ.get("PACKAGES_DIR", "/packages")
MANIFEST_PATH = os.environ.get("MANIFEST_PATH", "/manifest/manifest.json")
PORT          = int(os.environ.get("PORT", "8080"))

app = Flask(__name__)


def load_manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def save_manifest(manifest: dict) -> None:
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


@app.get("/manifest")
def get_manifest():
    manifest = load_manifest()
    log.info(f"GET /manifest → latest_version={manifest['latest_version']}")
    return jsonify(manifest)


@app.get("/packages/<path:filename>")
def get_package(filename):
    log.info(f"GET /packages/{filename}")
    # Allow .tar.gz (config packages) and .pt (policy checkpoint packages, M15)
    if not (filename.endswith(".tar.gz") or filename.endswith(".pt")):
        abort(400, "Only .tar.gz and .pt packages are served")
    return send_from_directory(PACKAGES_DIR, filename)


@app.post("/release/<version>")
def release_version(version):
    """Dev endpoint: promote a version to latest_version in the manifest."""
    manifest = load_manifest()
    known = [p["version"] for p in manifest["packages"]]
    if version not in known:
        abort(404, f"Version {version} not found. Known: {known}")
    old = manifest["latest_version"]
    manifest["latest_version"] = version
    save_manifest(manifest)
    log.info(f"POST /release/{version} — promoted {old} → {version}")
    return jsonify({"previous": old, "latest_version": version})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    log.info(f"OTA Server starting on :{PORT}")
    log.info(f"  Packages dir: {PACKAGES_DIR}")
    log.info(f"  Manifest:     {MANIFEST_PATH}")
    manifest = load_manifest()
    log.info(f"  Latest version: {manifest['latest_version']}")
    app.run(host="0.0.0.0", port=PORT)
