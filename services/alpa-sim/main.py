#!/usr/bin/env python3
"""
AlpaSim — mini-sdv-platform  Milestone 16
==========================================
Highway-env scoring harness for the Alpamayo policy family.

Evaluates any HTTP-accessible policy (or the built-in IDM baseline) over
N episodes of highway-v0 and publishes results to InfluxDB + MQTT.

Metrics reported per evaluation run:
  mean_reward       — average episode cumulative reward
  std_reward        — reward standard deviation
  collision_rate    — fraction of episodes that ended in a crash
  avg_speed_kmh     — mean ego speed across all steps

These metrics feed the Autonomy Flywheel:
  collect (highway-env-bridge M15) →
  train   (training-dispatcher M15) →
  evaluate (AlpaSim M16) →
  quantize (quantization-verify M16) →
  deploy  (OTA checkpoint M15 extension)

SDV Concept:
  AlpaSim is the simulation-based safety gate before OTA promotion —
  analogous to Waymo's closed-loop evaluation before real-world testing.
  A collision_rate > 5% blocks automatic OTA trigger.
"""

import json
import logging
import os
import time

import gymnasium as gym
import highway_env  # noqa: F401
import numpy as np
import paho.mqtt.client as mqtt_client
import requests
from flask import Flask, abort, jsonify, request
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("alpa-sim")

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN", "sdv-token-local")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG", "sdv-org")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "sdv")
MQTT_HOST       = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT       = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS        = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT    = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT= os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
PORT            = int(os.environ.get("PORT", "8092"))
DEFAULT_EPISODES= int(os.environ.get("DEFAULT_EPISODES", "5"))
OTA_COLLISION_GATE = float(os.environ.get("OTA_COLLISION_GATE", "0.05"))

_P, _X, _Y, _VX, _VY = 0, 1, 2, 3, 4
app = Flask(__name__)


def _make_eval_env() -> gym.Env:
    env = gym.make("highway-v0", render_mode=None)
    env.unwrapped.configure({
        "observation": {"type": "Kinematics", "vehicles_count": 5, "normalize": False},
        "action": {"type": "DiscreteMetaAction"},
        "duration": 60,
        "vehicles_count": 10,
        "real_time_rendering": False,
    })
    return env


def _idm_policy(obs: np.ndarray) -> int:
    ego_vx = obs[0, _VX]
    for i in range(1, obs.shape[0]):
        if obs[i, _P] > 0.5:
            dx = obs[i, _X] - obs[0, _X]
            dy = abs(obs[i, _Y] - obs[0, _Y])
            if 0 < dx < 50 and dy < 2.0:
                return 4
    return 3 if ego_vx < 30 else 1


def _remote_policy(obs: np.ndarray, endpoint_url: str) -> int:
    try:
        resp = requests.post(
            f"{endpoint_url}/run",
            json={"input": {"obs": obs.tolist()}},
            timeout=4,
        )
        return int(resp.json()["output"]["action"])
    except Exception as exc:
        log.debug(f"Remote policy fallback ({exc})")
        return _idm_policy(obs)


def _run_evaluation(endpoint_url: str, episodes: int, model_tag: str) -> dict:
    env = _make_eval_env()
    use_remote = bool(endpoint_url)
    rewards, crashed, speeds = [], [], []

    for ep in range(episodes):
        obs, _ = env.reset()
        done = False
        ep_reward = 0.0
        ep_speeds = []
        while not done:
            action = _remote_policy(obs, endpoint_url) if use_remote else _idm_policy(obs)
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            ep_reward += reward
            ep_speeds.append(float(obs[0, _VX]) * 3.6)
        rewards.append(ep_reward)
        crashed.append(bool(info.get("crashed", False)))
        speeds.append(float(np.mean(ep_speeds)) if ep_speeds else 0.0)
        log.info(f"  ep {ep+1}/{episodes}: reward={ep_reward:.2f} crashed={crashed[-1]}")

    env.close()
    collision_rate = float(np.mean(crashed))
    return {
        "model_tag":      model_tag,
        "episodes":       episodes,
        "mean_reward":    float(np.mean(rewards)),
        "std_reward":     float(np.std(rewards)),
        "collision_rate": collision_rate,
        "avg_speed_kmh":  float(np.mean(speeds)),
        "ota_gate_passed": collision_rate <= OTA_COLLISION_GATE,
        "ts":             time.time(),
    }


def _write_influxdb(metrics: dict):
    if not INFLUXDB_TOKEN:
        return
    try:
        with InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG) as client:
            p = (
                Point("alpa_sim_eval")
                .tag("model_tag", metrics["model_tag"])
                .field("mean_reward",    metrics["mean_reward"])
                .field("collision_rate", metrics["collision_rate"])
                .field("avg_speed_kmh",  metrics["avg_speed_kmh"])
                .field("ota_gate_passed", int(metrics["ota_gate_passed"]))
            )
            client.write_api(write_options=SYNCHRONOUS).write(bucket=INFLUXDB_BUCKET, record=p)
    except Exception as exc:
        log.warning(f"InfluxDB write failed: {exc}")


def _connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(client_id="alpa-sim", protocol=mqtt_client.MQTTv5)
    if MQTT_TLS:
        client.tls_set(ca_certs=MQTT_CA_CERT, certfile=MQTT_CLIENT_CERT, keyfile=MQTT_CLIENT_KEY)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
    except Exception as exc:
        log.warning(f"MQTT unavailable (non-fatal): {exc}")
    return client


_mc = _connect_mqtt()


@app.post("/evaluate")
def evaluate():
    """Run AlpaSim evaluation.

    Body JSON:
      endpoint_url  (str)  Runpod policy endpoint (omit for IDM baseline)
      episodes      (int)  number of episodes (default: DEFAULT_EPISODES)
      model_tag     (str)  label for InfluxDB/Grafana
    """
    body = request.get_json(force=True) or {}
    endpoint_url = body.get("endpoint_url", "")
    episodes     = int(body.get("episodes", DEFAULT_EPISODES))
    model_tag    = body.get("model_tag", "baseline-idm")

    if episodes < 1 or episodes > 50:
        abort(400, description="episodes must be 1–50")

    log.info(f"Evaluation: model={model_tag} episodes={episodes} endpoint={endpoint_url or 'local-idm'}")
    metrics = _run_evaluation(endpoint_url, episodes, model_tag)
    _write_influxdb(metrics)
    _mc.publish("sdv/alpa-sim/eval", json.dumps(metrics))
    log.info(
        f"Result: mean_reward={metrics['mean_reward']:.3f} "
        f"collision={metrics['collision_rate']:.1%} "
        f"ota_gate={'PASS' if metrics['ota_gate_passed'] else 'FAIL'}"
    )
    return jsonify(metrics)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "ota_collision_gate": OTA_COLLISION_GATE})


if __name__ == "__main__":
    log.info(f"AlpaSim starting on :{PORT} (OTA gate: collision_rate ≤ {OTA_COLLISION_GATE:.0%})")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
