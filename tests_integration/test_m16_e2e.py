"""
Phase-3 E2E tests — M16 Autonomy Flywheel (live k3s service)
T16-09: POST /evaluate to running alpa-sim → full metrics response
T16-10: Health + OTA gate field validation against running service

Skip when alpa-sim is not reachable at localhost:8092.
"""

import pytest
import requests

_BASE = "http://localhost:8092"
_TD_BASE = "http://localhost:8090"


def _reachable(base: str) -> bool:
    try:
        return requests.get(f"{base}/health", timeout=2).status_code == 200
    except Exception:
        return False


_ALPA_UP = _reachable(_BASE)
_TD_UP   = _reachable(_TD_BASE)


# ─────────────────────────────────────────────────────────────────────────────
# T16-09  POST /evaluate → full metrics JSON from live alpa-sim
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _ALPA_UP, reason=f"alpa-sim not running at {_BASE}")
def test_evaluate_returns_metrics_via_http():
    resp = requests.post(
        f"{_BASE}/evaluate",
        json={"episodes": 2, "model_tag": "phase3-e2e"},
        timeout=120,
    )
    assert resp.status_code == 200
    m = resp.json()

    required = {"model_tag", "episodes", "mean_reward", "std_reward",
                "collision_rate", "avg_speed_kmh", "ota_gate_passed", "ts"}
    assert required <= m.keys(), f"Missing fields: {required - m.keys()}"
    assert m["model_tag"] == "phase3-e2e"
    assert m["episodes"] == 2
    assert isinstance(m["mean_reward"], float)
    assert 0.0 <= m["collision_rate"] <= 1.0
    assert isinstance(m["ota_gate_passed"], bool)


@pytest.mark.skipif(not _ALPA_UP, reason=f"alpa-sim not running at {_BASE}")
def test_health_includes_ota_gate():
    resp = requests.get(f"{_BASE}/health", timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "ota_collision_gate" in body
    assert body["ota_collision_gate"] == 0.05


# ─────────────────────────────────────────────────────────────────────────────
# T16-10  Training-dispatcher health check + dry-run job submission
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _TD_UP, reason=f"training-dispatcher not at {_TD_BASE}")
def test_training_dispatcher_health():
    resp = requests.get(f"{_TD_BASE}/health", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.skipif(not _TD_UP, reason=f"training-dispatcher not at {_TD_BASE}")
def test_dispatcher_submit_job_returns_202():
    resp = requests.post(
        f"{_TD_BASE}/jobs",
        json={"algorithm": "ppo", "env_id": "highway-v0", "num_steps": 100},
        timeout=10,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] in ("running", "dry-run")
