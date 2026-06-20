#!/usr/bin/env python3
"""
Training Dispatcher — mini-sdv-platform  Milestone 15
======================================================
k3s-deployed control-plane service that accepts RL training job requests
and dispatches them to Runpod Serverless GPU cloud.

Runpod Serverless API flow:
  POST https://api.runpod.ai/v2/{endpoint_id}/run     → {id: job_id}
  GET  https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}
       → {status: IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED, output: {...}}

Job lifecycle events are published to MQTT so AlpaSim (M16) and OTA
Manager (M15 extension) can react to training completion automatically.

SDV Concept:
  The "training plane" is decoupled from the "inference plane". This
  dispatcher is the bridge: it manages cloud GPU resources (Runpod RTX
  4090, ≥20 GB VRAM) without touching the vehicle stack. Cost guard:
  each job is limited by num_steps — a 100k-step LoRA job on RTX 4090
  runs ~8 min ≈ $0.80, well within the $10/loop budget.
"""

import json
import logging
import os
import time
import uuid
from threading import Thread

import paho.mqtt.client as mqtt_client
import requests
from flask import Flask, abort, jsonify, request

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("training-dispatcher")

RUNPOD_API_KEY     = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "")
MQTT_HOST          = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT          = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS           = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT       = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT   = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY    = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
PORT               = int(os.environ.get("PORT", "8090"))
POLL_INTERVAL_SEC  = int(os.environ.get("POLL_INTERVAL_SEC", "15"))
MAX_POLL_ATTEMPTS  = int(os.environ.get("MAX_POLL_ATTEMPTS", "80"))  # ~20 min max

RUNPOD_BASE    = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}"
RUNPOD_HEADERS = {"Authorization": f"Bearer {RUNPOD_API_KEY}", "Content-Type": "application/json"}

_jobs: dict[str, dict] = {}  # in-process job store (no persistence for educational scope)
app = Flask(__name__)


# ── MQTT ─────────────────────────────────────────────────────────────────────

def _connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(client_id="training-dispatcher", protocol=mqtt_client.MQTTv5)
    if MQTT_TLS:
        client.tls_set(ca_certs=MQTT_CA_CERT, certfile=MQTT_CLIENT_CERT, keyfile=MQTT_CLIENT_KEY)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
    except Exception as exc:
        log.warning(f"MQTT unavailable (non-fatal): {exc}")
    return client


_mc = _connect_mqtt()


def _publish(job_id: str, event: str, data: dict):
    _mc.publish(f"sdv/training/{job_id}/{event}", json.dumps({"job_id": job_id, **data}))


# ── Runpod polling ────────────────────────────────────────────────────────────

def _poll_job(job_id: str, runpod_job_id: str):
    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        time.sleep(POLL_INTERVAL_SEC)
        try:
            resp = requests.get(
                f"{RUNPOD_BASE}/status/{runpod_job_id}",
                headers=RUNPOD_HEADERS,
                timeout=10,
            )
            data = resp.json()
            status = data.get("status", "UNKNOWN")
            _jobs[job_id]["runpod_status"] = status
            log.info(f"Job {job_id}: poll {attempt}/{MAX_POLL_ATTEMPTS} → {status}")

            if status == "COMPLETED":
                _jobs[job_id].update({"status": "completed", "output": data.get("output", {})})
                _publish(job_id, "completed", {"output": data.get("output", {})})
                return
            if status == "FAILED":
                _jobs[job_id].update({"status": "failed", "error": data.get("error", "")})
                _publish(job_id, "failed", {"error": _jobs[job_id]["error"]})
                return
        except Exception as exc:
            log.warning(f"Job {job_id}: poll attempt {attempt} error: {exc}")

    _jobs[job_id]["status"] = "timeout"
    _publish(job_id, "timeout", {})
    log.warning(f"Job {job_id}: polling timed out after {MAX_POLL_ATTEMPTS} attempts")


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.post("/jobs")
def submit_job():
    """Submit an RL training job.

    Body JSON:
      algorithm       (str)  e.g. "ppo", "dqn"
      env_id          (str)  e.g. "highway-v0"
      num_steps       (int)  e.g. 100000
      checkpoint_tag  (str)  optional; tag for OTA checkpoint
      lora_rank       (int)  optional; LoRA rank (default 8)
      fp16            (bool) optional; use FP16 training (default true)
    """
    body = request.get_json(force=True) or {}
    missing = {"algorithm", "env_id", "num_steps"} - body.keys()
    if missing:
        abort(400, description=f"Missing required fields: {sorted(missing)}")

    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "spec": body,
        "submitted_at": time.time(),
    }

    if not (RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID):
        _jobs[job_id]["status"] = "dry_run"
        log.info(f"Job {job_id}: dry-run (no Runpod credentials configured)")
        _publish(job_id, "submitted", {"mode": "dry_run", "spec": body})
        return jsonify({"job_id": job_id, "status": "dry_run"}), 202

    payload = {"input": {
        "algorithm":      body["algorithm"],
        "env_id":         body["env_id"],
        "num_steps":      int(body["num_steps"]),
        "checkpoint_tag": body.get("checkpoint_tag", job_id),
        "lora_rank":      int(body.get("lora_rank", 8)),
        "fp16":           bool(body.get("fp16", True)),
    }}

    try:
        resp = requests.post(f"{RUNPOD_BASE}/run", headers=RUNPOD_HEADERS, json=payload, timeout=15)
        resp.raise_for_status()
        runpod_job_id = resp.json()["id"]
    except Exception as exc:
        _jobs[job_id].update({"status": "error", "error": str(exc)})
        log.error(f"Job {job_id}: Runpod submit failed: {exc}")
        return jsonify({"job_id": job_id, "status": "error", "error": str(exc)}), 500

    _jobs[job_id].update({"runpod_job_id": runpod_job_id, "status": "running"})
    _publish(job_id, "submitted", {"runpod_job_id": runpod_job_id, "spec": body})
    Thread(target=_poll_job, args=(job_id, runpod_job_id), daemon=True).start()
    log.info(f"Job {job_id}: dispatched to Runpod as {runpod_job_id}")
    return jsonify({"job_id": job_id, "runpod_job_id": runpod_job_id, "status": "running"}), 202


@app.get("/jobs/<job_id>")
def get_job(job_id: str):
    if job_id not in _jobs:
        abort(404, description=f"Job {job_id!r} not found")
    return jsonify(_jobs[job_id])


@app.get("/jobs")
def list_jobs():
    return jsonify(list(_jobs.values()))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "active_jobs": len(_jobs),
                    "runpod_configured": bool(RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID)})


if __name__ == "__main__":
    log.info(f"Training Dispatcher starting on :{PORT}")
    log.info(f"  Runpod endpoint: {RUNPOD_ENDPOINT_ID or '(not configured — dry-run mode)'}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
