"""
Phase-1 unit tests — M16 Autonomy Flywheel
Tests: T16-01 to T16-05
"""

import json
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import load_service

alpa = load_service("alpa-sim")


# ── Shared fake env factory ───────────────────────────────────────────────────

def _fake_env(crash: bool = False, vx: float = 22.2):
    """Returns a mock gymnasium Env with predictable step output."""
    obs = np.zeros((5, 5))
    obs[0, 3] = vx          # ego vx (column _VX=3)
    env = MagicMock()
    env.reset.return_value = (obs, {})
    env.step.return_value  = (obs, 1.0, True, False, {"crashed": crash})
    return env


# ─────────────────────────────────────────────────────────────────────────────
# T16-01  collision_rate = 0 (no crashes) → ota_gate_passed = True
# ─────────────────────────────────────────────────────────────────────────────
def test_ota_gate_passes_when_no_collision():
    with patch.object(alpa, "_make_eval_env", return_value=_fake_env(crash=False)):
        result = alpa._run_evaluation("", 3, "baseline-idm")

    assert result["collision_rate"] == pytest.approx(0.0)
    assert result["ota_gate_passed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# T16-02  collision_rate = 1.0 (every episode crashes) → ota_gate_passed = False
# ─────────────────────────────────────────────────────────────────────────────
def test_ota_gate_fails_when_all_episodes_crash():
    with patch.object(alpa, "_make_eval_env", return_value=_fake_env(crash=True)):
        result = alpa._run_evaluation("", 3, "crash-model")

    assert result["collision_rate"] == pytest.approx(1.0)
    assert result["ota_gate_passed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# T16-03  /evaluate response contains all required keys
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED_KEYS = {"mean_reward", "std_reward", "collision_rate", "avg_speed_kmh", "ota_gate_passed"}

def test_evaluate_response_has_required_keys():
    with patch.object(alpa, "_make_eval_env", return_value=_fake_env()), \
         patch.object(alpa, "_write_influxdb"), \
         patch.object(alpa, "_mc"):
        client = alpa.app.test_client()
        resp = client.post("/evaluate", json={"model_tag": "test", "episodes": 2})

    assert resp.status_code == 200
    data = resp.get_json()
    assert REQUIRED_KEYS.issubset(data.keys()), f"Missing keys: {REQUIRED_KEYS - data.keys()}"


# ─────────────────────────────────────────────────────────────────────────────
# T16-04  /evaluate calls _write_influxdb after scoring
# ─────────────────────────────────────────────────────────────────────────────
def test_evaluate_writes_to_influxdb():
    with patch.object(alpa, "_make_eval_env", return_value=_fake_env()), \
         patch.object(alpa, "_write_influxdb") as mock_influx, \
         patch.object(alpa, "_mc"):
        client = alpa.app.test_client()
        client.post("/evaluate", json={"model_tag": "influx-test", "episodes": 1})

    mock_influx.assert_called_once()
    written_metrics = mock_influx.call_args[0][0]
    assert written_metrics["model_tag"] == "influx-test"


# ─────────────────────────────────────────────────────────────────────────────
# T16-05  /evaluate publishes to MQTT topic sdv/alpa-sim/eval
# ─────────────────────────────────────────────────────────────────────────────
def test_evaluate_publishes_mqtt():
    mock_mc = MagicMock()
    with patch.object(alpa, "_make_eval_env", return_value=_fake_env()), \
         patch.object(alpa, "_write_influxdb"), \
         patch.object(alpa, "_mc", mock_mc):
        client = alpa.app.test_client()
        client.post("/evaluate", json={"model_tag": "mqtt-test", "episodes": 1})

    mock_mc.publish.assert_called_once()
    topic = mock_mc.publish.call_args[0][0]
    assert topic == "sdv/alpa-sim/eval"

    payload = json.loads(mock_mc.publish.call_args[0][1])
    assert payload["model_tag"] == "mqtt-test"
