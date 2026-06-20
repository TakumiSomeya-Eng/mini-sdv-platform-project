#!/usr/bin/env python3
"""
Highway-Env CAN Bridge — mini-sdv-platform  Milestone 15
=========================================================
Runs a highway-env simulation (CPU, no GPU) and bridges vehicle states
to Kuksa Databroker (gRPC) and MQTT as CAN-like frames.

highway-env provides:
  - Kinematic model (position, speed, heading, acceleration)
  - Multi-lane highway with surrounding vehicles
  - IDM-based default policy for ego and NPC vehicles

The IDM heuristic drives the ego vehicle locally; a remote Runpod policy
endpoint (RUNPOD_ENDPOINT) can replace it once Alpamayo-1 is trained (M16).

SDV Concept:
  In production, physical ECUs emit real CAN frames. Here, highway-env
  replaces the synthetic sinusoidal signals of M4 with physics-based
  driving trajectories — the input data for the Autonomy Flywheel (M16).
"""

import json
import logging
import os
import time

import gymnasium as gym
import highway_env  # noqa: F401  — registers highway-v0 / merge-v0 etc.
import numpy as np
import paho.mqtt.client as mqtt_client
import requests
from kuksa_client.grpc import VSSClient, Datapoint

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("highway-env-bridge")

DATABROKER_HOST  = os.environ.get("DATABROKER_HOST", "localhost")
DATABROKER_PORT  = int(os.environ.get("DATABROKER_PORT", "55555"))
MQTT_HOST        = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT        = int(os.environ.get("MQTT_PORT", "1883"))
VEHICLE_ID       = os.environ.get("VEHICLE_ID", "vehicle-001")
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")
ENV_ID           = os.environ.get("HIGHWAY_ENV_ID", "highway-v0")
STEP_INTERVAL    = float(os.environ.get("STEP_INTERVAL_SEC", "0.1"))
RUNPOD_ENDPOINT  = os.environ.get("RUNPOD_ENDPOINT", "")  # remote policy (optional)

CAN_TOPIC     = f"sdv/{VEHICLE_ID}/can/frames"
METRICS_TOPIC = f"sdv/{VEHICLE_ID}/highway/metrics"

VSS_SPEED = "Vehicle.Speed"
VSS_SOC   = "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current"
VSS_CABIN = "Vehicle.Cabin.HVAC.AmbientAirTemperature"

# KinematicObservation column indices (highway-env default)
_P, _X, _Y, _VX, _VY = 0, 1, 2, 3, 4


def _make_env(env_id: str) -> gym.Env:
    env = gym.make(env_id, render_mode=None)
    env.configure({
        "observation": {"type": "Kinematics", "vehicles_count": 5, "normalize": False},
        "action": {"type": "DiscreteMetaAction"},
        "duration": 100,
        "vehicles_count": 10,
        "lanes_count": 4,
        "real_time_rendering": False,
    })
    return env


def _idm_policy(obs: np.ndarray) -> int:
    """Heuristic: maintain ~108 km/h, slow for vehicles within 50 m ahead.

    Actions: 0=LaneLeft, 1=Idle, 2=LaneRight, 3=Faster, 4=Slower
    """
    ego_vx = obs[0, _VX]
    for i in range(1, obs.shape[0]):
        if obs[i, _P] > 0.5:
            dx = obs[i, _X] - obs[0, _X]
            dy = abs(obs[i, _Y] - obs[0, _Y])
            if 0 < dx < 50 and dy < 2.0:
                return 4  # Slower
    return 3 if ego_vx < 30 else 1  # Faster or Idle


def _remote_policy(obs: np.ndarray) -> int:
    """Call Runpod-hosted Alpamayo-1 policy via Serverless HTTP."""
    try:
        resp = requests.post(
            f"{RUNPOD_ENDPOINT}/run",
            json={"input": {"obs": obs.tolist()}},
            timeout=3,
        )
        return int(resp.json()["output"]["action"])
    except Exception as exc:
        log.debug(f"Remote policy fallback: {exc}")
        return _idm_policy(obs)


def _connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(
        client_id=f"highway-bridge-{VEHICLE_ID}", protocol=mqtt_client.MQTTv5
    )
    if MQTT_TLS:
        client.tls_set(ca_certs=MQTT_CA_CERT, certfile=MQTT_CLIENT_CERT, keyfile=MQTT_CLIENT_KEY)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def run():
    log.info(f"Starting highway-env bridge: env={ENV_ID} policy={'runpod' if RUNPOD_ENDPOINT else 'idm'}")
    env = _make_env(ENV_ID)
    mc  = _connect_mqtt()
    policy = _remote_policy if RUNPOD_ENDPOINT else _idm_policy

    with VSSClient(DATABROKER_HOST, DATABROKER_PORT, insecure=True) as vss:
        episode = 0
        while True:
            obs, _ = env.reset()
            done = False
            step = 0
            total_reward = 0.0

            log.info(f"Episode {episode} start")
            while not done:
                action = policy(obs)
                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                total_reward += reward
                step += 1

                speed_kmh = float(obs[0, _VX]) * 3.6

                try:
                    vss.set_current_values({
                        VSS_SPEED: Datapoint(speed_kmh),
                        VSS_SOC:   Datapoint(max(20.0, 85.0 - step * 0.01)),
                        VSS_CABIN: Datapoint(22.0 + 0.5 * float(np.sin(step * 0.1))),
                    })
                except Exception as exc:
                    log.warning(f"Databroker set_current_values: {exc}")

                # CAN frame: ID 0x100 = speed (uint16, little-endian, 0.1 km/h per bit)
                speed_raw = int(min(speed_kmh * 10, 65535)).to_bytes(2, "little")
                mc.publish(CAN_TOPIC, json.dumps({
                    "ts": time.time(), "step": step,
                    "can_id": "0x100", "data": speed_raw.hex(),
                }), qos=0)

                if step % 50 == 0:
                    log.info(f"  step={step} speed={speed_kmh:.1f} km/h reward_acc={total_reward:.2f}")

                time.sleep(STEP_INTERVAL)

            mc.publish(METRICS_TOPIC, json.dumps({
                "ts": time.time(), "episode": episode, "steps": step,
                "total_reward": float(total_reward),
                "crashed": bool(info.get("crashed", False)),
            }))
            log.info(f"Episode {episode} done: steps={step} reward={total_reward:.2f}")
            episode += 1


if __name__ == "__main__":
    run()
