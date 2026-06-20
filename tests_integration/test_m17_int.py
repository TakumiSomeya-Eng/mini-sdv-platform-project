"""
Phase-2 integration tests — M17 Edge AI Deployment
T17-07: scene POST → search returns it in top-k hits
T17-08: SentenceTransformer all-MiniLM-L6-v2 embedding is 384-dim
T17-09: LanceDB data persists across reconnections
T17-10: MQTT auto-index (skip if no broker at localhost:1883)
"""

import os
import time

import pytest

from conftest import load_service, mqtt_available


# ─────────────────────────────────────────────────────────────────────────────
# Shared scene-search fixture: initialises the service with a temp LanceDB
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def ss(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("lancedb_int"))
    svc = load_service("scene-search", env_overrides={"LANCEDB_PATH": db_path})
    svc._init()   # loads encoder, connects LanceDB, creates table
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# T17-07  POST /scenes then GET /scenes/search returns the indexed scene
# ─────────────────────────────────────────────────────────────────────────────
def test_scene_post_then_search_returns_hit(ss):
    client = ss.app.test_client()

    # Index a distinctive scene
    resp = client.post(
        "/scenes",
        json={"description": "rear-end collision at 35 km/h in dense highway traffic"},
    )
    assert resp.status_code == 201
    posted = resp.get_json()
    assert "scene_id" in posted

    # Search with a semantically similar query
    resp2 = client.get("/scenes/search?q=highway+collision&k=5")
    assert resp2.status_code == 200
    hits = resp2.get_json()["hits"]
    assert len(hits) >= 1, "Should return at least one result"

    # The indexed scene must appear in top-k
    descriptions = [h["description"] for h in hits]
    assert any("collision" in d for d in descriptions), (
        f"Indexed scene not found in top-k. Hits: {descriptions}"
    )


def test_search_without_index_returns_400(ss):
    client = ss.app.test_client()
    resp = client.get("/scenes/search")   # no q param
    assert resp.status_code == 400


def test_scenes_list_reflects_indexed_count(ss):
    client = ss.app.test_client()
    # Index one more scene
    client.post("/scenes", json={"description": "smooth motorway, no incidents"})

    resp = client.get("/scenes")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# T17-08  SentenceTransformer all-MiniLM-L6-v2 produces 384-dim vectors
# ─────────────────────────────────────────────────────────────────────────────
def test_embedding_dimension_is_384():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vec = model.encode("highway driving scene at 80 km/h")
    assert vec.shape == (384,), f"Expected (384,), got {vec.shape}"


def test_embedding_is_unit_normalized():
    import numpy as np
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vec = model.encode("test", normalize_embeddings=True)
    norm = float(np.linalg.norm(vec))
    assert abs(norm - 1.0) < 1e-5, f"Expected unit vector, norm={norm}"


# ─────────────────────────────────────────────────────────────────────────────
# T17-09  LanceDB data persists across two separate connections
# ─────────────────────────────────────────────────────────────────────────────
def test_lancedb_persistence_across_reconnections(tmp_path):
    import lancedb
    from lancedb.pydantic import LanceModel, Vector

    db_path = str(tmp_path / "persist_test.lance")

    class Row(LanceModel):
        vector: Vector(3)
        label: str

    # Session 1 — write
    db1 = lancedb.connect(db_path)
    t1 = db1.create_table("rows", schema=Row)
    t1.add([{"vector": [1.0, 0.0, 0.0], "label": "alpha"}])

    # Session 2 — reconnect and read
    db2 = lancedb.connect(db_path)
    assert "rows" in list(db2.table_names()), "Table not found after reconnect"
    t2 = db2.open_table("rows")
    rows = t2.to_pandas()
    assert len(rows) == 1
    assert rows.iloc[0]["label"] == "alpha"


def test_scene_service_data_survives_reinit(tmp_path):
    db_path = str(tmp_path / "reinit.lance")
    svc = load_service("scene-search", env_overrides={"LANCEDB_PATH": db_path})
    svc._init()

    # Write
    svc._table.add([{
        "vector":       svc._embed("test scene"),
        "scene_id":     "s001",
        "description":  "test scene",
        "episode":      1, "steps": 10, "total_reward": 5.0,
        "crashed":      False, "ts": time.time(),
    }])
    assert len(svc._table) == 1

    # Simulate pod restart by re-calling _init() (same DB_PATH)
    svc._init()
    assert len(svc._table) == 1, "Scene lost after re-init (persistence failure)"


# ─────────────────────────────────────────────────────────────────────────────
# T17-10  MQTT auto-index: publish to highway/metrics → scene auto-indexed
#         (skipped if no MQTT broker is running at localhost:1883)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not mqtt_available(), reason="No MQTT broker at localhost:1883")
def test_mqtt_auto_indexes_scene(tmp_path):
    import json, paho.mqtt.client as mqtt

    db_path = str(tmp_path / "mqtt_index.lance")
    svc = load_service("scene-search", env_overrides={"LANCEDB_PATH": db_path})
    svc._init()
    svc._connect_mqtt()   # subscribe to sdv/vehicle-001/highway/metrics

    time.sleep(0.2)       # let connection establish

    # Publish a synthetic highway/metrics message
    pub = mqtt.Client()
    pub.connect("localhost", 1883)
    payload = json.dumps({
        "episode": 42, "steps": 80, "total_reward": 12.5,
        "crashed": False, "ts": time.time(),
    })
    pub.publish(svc.METRICS_TOPIC, payload, qos=0)
    pub.disconnect()

    time.sleep(1.0)       # allow on_message callback to process

    assert len(svc._table) >= 1, "MQTT message did not auto-index a scene"
    latest = svc._table.to_pandas().iloc[-1]
    assert latest["episode"] == 42
