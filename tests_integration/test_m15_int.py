"""
Phase-2 integration tests — M15 Compute Plane
T15-10: highway-env real simulation
T15-11: training-dispatcher full HTTP → thread → completion lifecycle
"""

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import load_service


# ─────────────────────────────────────────────────────────────────────────────
# T15-10  Real highway-env reset → observation shape (5, 5)
# ─────────────────────────────────────────────────────────────────────────────
def test_highway_env_reset_obs_shape():
    import gymnasium as gym
    import highway_env  # noqa: F401 — registers highway-v0

    env = gym.make("highway-v0", render_mode=None)
    env.unwrapped.configure({
        "observation": {"type": "Kinematics", "vehicles_count": 5, "normalize": False},
        "action": {"type": "DiscreteMetaAction"},
        "real_time_rendering": False,
    })
    obs, _ = env.reset()

    assert obs.shape == (5, 5), f"Expected (5,5) got {obs.shape}"
    assert obs.dtype in (np.float32, np.float64)
    env.close()


def test_highway_env_step_returns_valid_reward():
    import gymnasium as gym
    import highway_env  # noqa: F401

    env = gym.make("highway-v0", render_mode=None)
    env.unwrapped.configure({
        "observation": {"type": "Kinematics", "vehicles_count": 5, "normalize": False},
        "action": {"type": "DiscreteMetaAction"},
        "real_time_rendering": False,
    })
    obs, _ = env.reset()
    obs, reward, terminated, truncated, info = env.step(1)  # Idle

    assert isinstance(reward, float)
    assert obs.shape == (5, 5)
    assert isinstance(terminated, bool)
    env.close()


# ─────────────────────────────────────────────────────────────────────────────
# T15-11  Training-dispatcher full lifecycle:
#         POST /jobs → background thread polls Runpod → job shows "completed"
# ─────────────────────────────────────────────────────────────────────────────
def test_dispatcher_full_lifecycle():
    # Load a fresh instance with 0-second poll interval and fake Runpod creds
    td = load_service(
        "training-dispatcher",
        env_overrides={
            "POLL_INTERVAL_SEC": "0",
            "RUNPOD_API_KEY":     "test-api-key",
            "RUNPOD_ENDPOINT_ID": "test-ep-id",
        },
    )

    # Mock Runpod HTTP: job submission → COMPLETED on first poll
    mock_submit = MagicMock()
    mock_submit.raise_for_status = MagicMock()
    mock_submit.json.return_value = {"id": "rp-lifecycle-123"}

    mock_status = MagicMock()
    mock_status.json.return_value = {"status": "COMPLETED", "output": {"acc": 0.92}}

    client = td.app.test_client()

    with patch("requests.post", return_value=mock_submit), \
         patch("requests.get",  return_value=mock_status):

        # Step 1: submit job
        resp = client.post(
            "/jobs",
            json={"algorithm": "ppo", "env_id": "highway-v0", "num_steps": 100},
        )
        assert resp.status_code == 202
        body = resp.get_json()
        assert body["status"] == "running"
        job_id = body["job_id"]

        # Step 2: wait for background polling thread (poll_interval=0 → finishes fast)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.05)
            r = client.get(f"/jobs/{job_id}")
            if r.get_json().get("status") == "completed":
                break

        # Step 3: verify final state
        r = client.get(f"/jobs/{job_id}")
        data = r.get_json()
        assert data["status"] == "completed", f"Expected completed, got: {data}"
        assert data["output"] == {"acc": 0.92}
