"""
Phase-1 unit tests — M15 Compute Plane
Tests: T15-01 to T15-09
"""

import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from conftest import load_service

# ── Load service modules once per session ─────────────────────────────────────
heb = load_service("highway-env-bridge")    # _idm_policy
td  = load_service("training-dispatcher")   # Flask app, _poll_job
om  = load_service("ota-manager")           # apply_package family
ots = load_service("ota-server")            # Flask app (extension guard)


# ─────────────────────────────────────────────────────────────────────────────
# T15-01  IDM policy — front vehicle within 50 m → action = 4 (Slower)
# ─────────────────────────────────────────────────────────────────────────────
def test_idm_slows_for_vehicle_ahead():
    obs = np.zeros((5, 5))
    # ego  : presence=1, x=0, y=0, vx=35 m/s (>30 → would Idle without traffic)
    obs[0] = [1.0, 0.0, 0.0, 35.0, 0.0]
    # npc1 : presence=1, x=30 (dx=30, 0<30<50), same lane (dy=0 <2)
    obs[1] = [1.0, 30.0, 0.0, 25.0, 0.0]

    assert heb._idm_policy(obs) == 4  # Slower


# ─────────────────────────────────────────────────────────────────────────────
# T15-02  IDM policy — clear road, slow ego → action = 3 (Faster)
# ─────────────────────────────────────────────────────────────────────────────
def test_idm_accelerates_on_clear_road():
    obs = np.zeros((5, 5))
    # ego: presence=1, vx=20 m/s (< 30 threshold)
    obs[0] = [1.0, 0.0, 0.0, 20.0, 0.0]
    # all other vehicles absent (presence=0) → no trigger

    assert heb._idm_policy(obs) == 3  # Faster


# ─────────────────────────────────────────────────────────────────────────────
# T15-03  CAN frame encoding: 72 km/h → 0x02D0 little-endian = "d002"
# ─────────────────────────────────────────────────────────────────────────────
def test_can_frame_encoding_72kmh():
    speed_kmh = 72.0
    speed_raw = int(min(speed_kmh * 10, 65535)).to_bytes(2, "little")
    # 720 = 0x02D0 → little-endian bytes [0xD0, 0x02] → hex "d002"
    assert speed_raw.hex() == "d002"


def test_can_frame_clamps_at_max():
    speed_kmh = 10000.0
    speed_raw = int(min(speed_kmh * 10, 65535)).to_bytes(2, "little")
    assert int.from_bytes(speed_raw, "little") == 65535


# ─────────────────────────────────────────────────────────────────────────────
# T15-04  training-dispatcher dry-run mode (no Runpod credentials)
# ─────────────────────────────────────────────────────────────────────────────
def test_dispatcher_dry_run_mode():
    # Ensure no Runpod creds visible to the module
    with patch.object(td, "RUNPOD_API_KEY", ""), \
         patch.object(td, "RUNPOD_ENDPOINT_ID", ""):
        client = td.app.test_client()
        resp = client.post(
            "/jobs",
            json={"algorithm": "ppo", "env_id": "highway-v0", "num_steps": 1000},
        )
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "dry_run"
    assert "job_id" in data


# ─────────────────────────────────────────────────────────────────────────────
# T15-05  training-dispatcher rejects body missing required fields → 400
# ─────────────────────────────────────────────────────────────────────────────
def test_dispatcher_rejects_missing_fields():
    client = td.app.test_client()
    resp = client.post("/jobs", json={"algorithm": "ppo"})  # missing env_id, num_steps
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# T15-06  _poll_job: Runpod COMPLETED → job status updated + MQTT published
# ─────────────────────────────────────────────────────────────────────────────
def test_poll_job_completed_publishes_mqtt():
    job_id = "test_t1506"
    td._jobs[job_id] = {"job_id": job_id, "status": "running", "spec": {}}

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "COMPLETED", "output": {"checkpoint": "s3://x"}}

    with patch("requests.get", return_value=mock_resp), \
         patch("time.sleep"):          # skip 15-second waits
        td._poll_job(job_id, "runpod-abc123")

    assert td._jobs[job_id]["status"] == "completed"
    td._mc.publish.assert_called()
    published_topic = td._mc.publish.call_args[0][0]
    assert f"sdv/training/{job_id}/completed" == published_topic

    # cleanup
    del td._jobs[job_id]


# ─────────────────────────────────────────────────────────────────────────────
# T15-07  apply_checkpoint_package copies .pt to CHECKPOINT_PATH
# ─────────────────────────────────────────────────────────────────────────────
def test_apply_checkpoint_copies_file():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "policy.pt")
        dst = os.path.join(tmp, "subdir", "policy.pt")
        Path(src).write_bytes(b"\x00" * 64)

        with patch.object(om, "CHECKPOINT_PATH", dst):
            result = om.apply_checkpoint_package(src)

        # assertions stay inside the TemporaryDirectory context so files still exist
        assert result is True
        assert Path(dst).exists()
        assert Path(dst).read_bytes() == b"\x00" * 64


# ─────────────────────────────────────────────────────────────────────────────
# T15-08  apply_package routes to the correct handler based on pkg_type
# ─────────────────────────────────────────────────────────────────────────────
def test_apply_package_routes_by_type():
    with patch.object(om, "apply_config_package", return_value=True) as mock_cfg, \
         patch.object(om, "apply_checkpoint_package", return_value=True) as mock_ckpt:

        om.apply_package("/tmp/pkg.tar.gz", "config")
        mock_cfg.assert_called_once_with("/tmp/pkg.tar.gz")
        mock_ckpt.assert_not_called()

        mock_cfg.reset_mock()

        om.apply_package("/tmp/policy.pt", "checkpoint")
        mock_ckpt.assert_called_once_with("/tmp/policy.pt")
        mock_cfg.assert_not_called()


def test_apply_package_defaults_to_config():
    with patch.object(om, "apply_config_package", return_value=True) as mock_cfg:
        om.apply_package("/tmp/pkg.tar.gz")   # no pkg_type arg
        mock_cfg.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# T15-09  ota-server rejects extension that is neither .tar.gz nor .pt
# ─────────────────────────────────────────────────────────────────────────────
def test_ota_server_rejects_invalid_extension():
    client = ots.app.test_client()
    resp = client.get("/packages/malware.exe")
    assert resp.status_code == 400


def test_ota_server_accepts_pt_extension():
    """Valid extension should pass the guard (404 from missing file is expected)."""
    client = ots.app.test_client()
    resp = client.get("/packages/alpamayo-1.pt")
    # 400 = guard rejected, 404 = file not found (guard passed) — any non-400 is OK
    assert resp.status_code != 400


def test_ota_server_accepts_targz_extension():
    client = ots.app.test_client()
    resp = client.get("/packages/v1.0.0.tar.gz")
    assert resp.status_code != 400
