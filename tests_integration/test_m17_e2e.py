"""
Phase-3 E2E tests — M17 Edge AI Deployment (live k3s service)
T17-11: Full scene retrieval pipeline via running scene-search HTTP service
T17-12: OTA-server manifest endpoint validation

Skip when scene-search is not reachable at localhost:8093.
"""

import time

import pytest
import requests

_BASE      = "http://localhost:8093"
_OTA_BASE  = "http://localhost:8080"


def _reachable(base: str) -> bool:
    try:
        return requests.get(f"{base}/health", timeout=2).status_code == 200
    except Exception:
        return False


_SS_UP  = _reachable(_BASE)
_OTA_UP = _reachable(_OTA_BASE)


# ─────────────────────────────────────────────────────────────────────────────
# T17-11  POST → search → result via live scene-search HTTP service
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _SS_UP, reason=f"scene-search not running at {_BASE}")
def test_scene_search_health():
    resp = requests.get(f"{_BASE}/health", timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "scenes" in body
    assert "embed_model" in body


@pytest.mark.skipif(not _SS_UP, reason=f"scene-search not running at {_BASE}")
def test_live_scene_post_and_search():
    marker = f"e2e-{int(time.time())}"
    desc   = f"phase3 test collision at motorway junction {marker}"

    # Index a distinctive scene
    post_resp = requests.post(
        f"{_BASE}/scenes",
        json={"description": desc},
        timeout=30,
    )
    assert post_resp.status_code == 201
    scene_id = post_resp.json()["scene_id"]

    # Search for it (semantic similarity)
    search_resp = requests.get(
        f"{_BASE}/scenes/search",
        params={"q": f"motorway junction {marker}", "k": 5},
        timeout=30,
    )
    assert search_resp.status_code == 200
    hits = search_resp.json()["hits"]
    assert len(hits) >= 1

    ids_in_hits = [h["scene_id"] for h in hits]
    assert scene_id in ids_in_hits, (
        f"Indexed scene_id {scene_id!r} not found in top-k. Got: {ids_in_hits}"
    )


@pytest.mark.skipif(not _SS_UP, reason=f"scene-search not running at {_BASE}")
def test_live_scene_list_count_increases():
    before = requests.get(f"{_BASE}/scenes", timeout=10).json()["total"]

    requests.post(
        f"{_BASE}/scenes",
        json={"description": "extra scene for count test"},
        timeout=30,
    )

    after = requests.get(f"{_BASE}/scenes", timeout=10).json()["total"]
    assert after == before + 1


# ─────────────────────────────────────────────────────────────────────────────
# T17-12  OTA-server manifest served correctly (used by ota-manager)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _OTA_UP, reason=f"ota-server not running at {_OTA_BASE}")
def test_ota_server_manifest_endpoint():
    resp = requests.get(f"{_OTA_BASE}/manifest", timeout=5)
    assert resp.status_code == 200
    manifest = resp.json()
    assert "version" in manifest
    assert "packages" in manifest
    assert isinstance(manifest["packages"], list)


@pytest.mark.skipif(not _OTA_UP, reason=f"ota-server not running at {_OTA_BASE}")
def test_ota_server_health():
    resp = requests.get(f"{_OTA_BASE}/health", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
