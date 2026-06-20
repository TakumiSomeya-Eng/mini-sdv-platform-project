#!/usr/bin/env python3
"""
Scene Search — mini-sdv-platform  Milestone 17
===============================================
LanceDB (Apache 2.0) semantic vector store for driving scene retrieval.

Indexes scene descriptions from highway-env episodes (and highway-env-bridge
MQTT events) as embedding vectors. Enables natural-language queries like:
  "congested highway, vehicle cut in, speed below 40"
returning the k most similar past scenes with their signal snapshots.

Architecture:
  Ingest:  MQTT sdv/{vehicle_id}/highway/metrics → embed → store in LanceDB
  Query:   GET /scenes/search?q=<text>&k=5 → top-k similar scenes
  Add:     POST /scenes → explicit scene ingestion

Embedding model:
  sentence-transformers all-MiniLM-L6-v2 (Apache 2.0, 22 MB, CPU-fast)
  Runs on WSL2 CPU without GPU. Vectors are 384-dimensional float32.

SDV Concept:
  Scene retrieval enables "similar incident" analysis — when a new anomaly
  is detected, operators can query for past scenes with similar dynamics
  to understand recurrence patterns. This is the semantic layer above the
  raw time-series in InfluxDB.
"""

import json
import logging
import os
import time
from typing import Optional

import numpy as np
import paho.mqtt.client as mqtt_client
import lancedb
from lancedb.pydantic import LanceModel, Vector
from sentence_transformers import SentenceTransformer
from flask import Flask, abort, jsonify, request

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("scene-search")

MQTT_HOST        = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT        = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
VEHICLE_ID       = os.environ.get("VEHICLE_ID", "vehicle-001")
DB_PATH          = os.environ.get("LANCEDB_PATH", "/data/scenes.lance")
EMBED_MODEL      = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
PORT             = int(os.environ.get("PORT", "8093"))

METRICS_TOPIC = f"sdv/{VEHICLE_ID}/highway/metrics"

app = Flask(__name__)

# ── LanceDB schema ────────────────────────────────────────────────────────────

class SceneRecord(LanceModel):
    vector: Vector(384)   # all-MiniLM-L6-v2 output dimension
    scene_id: str
    description: str
    episode: Optional[int]
    steps: Optional[int]
    total_reward: Optional[float]
    crashed: Optional[bool]
    ts: float


# ── Globals (initialised at startup) ─────────────────────────────────────────

_db: lancedb.DBConnection = None
_table: lancedb.table.Table = None
_encoder: SentenceTransformer = None


def _init():
    global _db, _table, _encoder
    log.info(f"Loading sentence encoder: {EMBED_MODEL}")
    _encoder = SentenceTransformer(EMBED_MODEL)
    _db = lancedb.connect(DB_PATH)
    if "scenes" in _db.table_names():
        _table = _db.open_table("scenes")
        log.info(f"LanceDB opened ({len(_table)} scenes)")
    else:
        _table = _db.create_table("scenes", schema=SceneRecord)
        log.info("LanceDB table 'scenes' created")


def _embed(text: str) -> list[float]:
    return _encoder.encode(text, normalize_embeddings=True).tolist()


def _scene_text(data: dict) -> str:
    """Convert a scene data dict to a natural-language description."""
    parts = []
    if data.get("crashed"):
        parts.append("crash occurred")
    if (r := data.get("total_reward")) is not None:
        parts.append(f"episode reward {r:.1f}")
    if (s := data.get("steps")) is not None:
        parts.append(f"{s} steps")
    if (desc := data.get("description")):
        parts.append(desc)
    return " | ".join(parts) if parts else "highway driving scene"


# ── MQTT ingest ───────────────────────────────────────────────────────────────

def _on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        text = _scene_text(data)
        vec  = _embed(text)
        rec  = SceneRecord(
            vector=vec,
            scene_id=f"ep{data.get('episode', 0)}-{int(time.time()*1000)}",
            description=text,
            episode=data.get("episode"),
            steps=data.get("steps"),
            total_reward=data.get("total_reward"),
            crashed=data.get("crashed", False),
            ts=data.get("ts", time.time()),
        )
        _table.add([rec.dict()])
        log.info(f"Indexed scene: {text[:60]}")
    except Exception as exc:
        log.warning(f"MQTT ingest failed: {exc}")


def _connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(client_id="scene-search", protocol=mqtt_client.MQTTv5)
    if MQTT_TLS:
        client.tls_set(ca_certs=MQTT_CA_CERT, certfile=MQTT_CLIENT_CERT, keyfile=MQTT_CLIENT_KEY)
    client.on_message = _on_message
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.subscribe(METRICS_TOPIC, qos=0)
        client.loop_start()
        log.info(f"MQTT subscribed to {METRICS_TOPIC}")
    except Exception as exc:
        log.warning(f"MQTT unavailable (non-fatal): {exc}")
    return client


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.post("/scenes")
def add_scene():
    """Manually ingest a scene."""
    data = request.get_json(force=True) or {}
    description = data.get("description") or _scene_text(data)
    if not description:
        abort(400, "description required")

    vec  = _embed(description)
    rec  = SceneRecord(
        vector=vec,
        scene_id=data.get("scene_id", f"manual-{int(time.time()*1000)}"),
        description=description,
        episode=data.get("episode"),
        steps=data.get("steps"),
        total_reward=data.get("total_reward"),
        crashed=data.get("crashed", False),
        ts=data.get("ts", time.time()),
    )
    _table.add([rec.dict()])
    log.info(f"Manual scene indexed: {description[:60]}")
    return jsonify({"scene_id": rec.scene_id, "description": description}), 201


@app.get("/scenes/search")
def search_scenes():
    """Semantic search over indexed scenes.

    Query params:
      q   (str, required)  natural-language query
      k   (int, optional)  number of results (default 5, max 20)
    """
    q = request.args.get("q", "").strip()
    k = min(int(request.args.get("k", 5)), 20)
    if not q:
        abort(400, "q parameter required")

    vec     = _embed(q)
    results = _table.search(vec).limit(k).to_list()
    hits = [
        {
            "scene_id":     r["scene_id"],
            "description":  r["description"],
            "score":        float(1 - r.get("_distance", 0)),
            "crashed":      r.get("crashed"),
            "total_reward": r.get("total_reward"),
            "ts":           r.get("ts"),
        }
        for r in results
    ]
    return jsonify({"query": q, "k": k, "hits": hits})


@app.get("/scenes")
def list_scenes():
    """Return total scene count and last 10 scenes."""
    total = len(_table)
    recent = _table.to_pandas().tail(10).to_dict(orient="records") if total > 0 else []
    for r in recent:
        r.pop("vector", None)
    return jsonify({"total": total, "recent": recent})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "scenes": len(_table), "embed_model": EMBED_MODEL})


if __name__ == "__main__":
    _init()
    _connect_mqtt()
    log.info(f"Scene Search starting on :{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
