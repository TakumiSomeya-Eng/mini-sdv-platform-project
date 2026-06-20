#!/usr/bin/env python3
"""
LoRA Fine-tune Dispatch — mini-sdv-platform  Milestone 16
==========================================================
Collects highway-env trajectory data locally (CPU) and dispatches
a LoRA fine-tuning job to the training-dispatcher service (k3s M15),
which forwards it to Runpod RTX 4090.

Pipeline:
  1. Roll out IDM policy in highway-v0 for N episodes → trajectory JSONL
  2. POST trajectory to training-dispatcher /jobs
  3. Poll until COMPLETED
  4. Print checkpoint download URL (or trigger OTA if --ota-promote)

LoRA config (Alpamayo-1):
  base model : microsoft/phi-4-mini (3.8B, MIT)
  rank       : 8  (VRAM-safe on RTX 4090 in FP16)
  target     : q_proj, v_proj
  steps      : configurable (default 10000, ~$0.50 on RTX 4090)

Usage:
  python scripts/lora-finetune-dispatch.py \
    --dispatcher http://localhost:8090 \
    --episodes 20 \
    --num-steps 10000 \
    --tag alpamayo-1-v0.1.0
"""

import argparse
import json
import sys
import time
from pathlib import Path

import gymnasium as gym
import highway_env  # noqa: F401
import numpy as np
import requests


_P, _X, _Y, _VX, _VY = 0, 1, 2, 3, 4


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dispatcher",    default="http://localhost:8090")
    p.add_argument("--episodes",      type=int,   default=20)
    p.add_argument("--max-steps",     type=int,   default=60)
    p.add_argument("--num-steps",     type=int,   default=10000, help="Training steps on Runpod")
    p.add_argument("--lora-rank",     type=int,   default=8)
    p.add_argument("--tag",           default="alpamayo-1-lora")
    p.add_argument("--out",           default="/tmp/trajectories.jsonl")
    p.add_argument("--ota-promote",   action="store_true",
                   help="POST to OTA server when checkpoint is ready")
    p.add_argument("--ota-url",       default="http://localhost:8080")
    return p.parse_args()


def idm_policy(obs: np.ndarray) -> int:
    ego_vx = obs[0, _VX]
    for i in range(1, obs.shape[0]):
        if obs[i, _P] > 0.5:
            dx = obs[i, _X] - obs[0, _X]
            dy = abs(obs[i, _Y] - obs[0, _Y])
            if 0 < dx < 50 and dy < 2.0:
                return 4
    return 3 if ego_vx < 30 else 1


def collect_trajectories(episodes: int, max_steps: int, out_path: str) -> int:
    env = gym.make("highway-v0", render_mode=None)
    env.configure({
        "observation": {"type": "Kinematics", "vehicles_count": 5, "normalize": False},
        "action": {"type": "DiscreteMetaAction"},
        "duration": max_steps,
        "real_time_rendering": False,
    })
    total_steps = 0
    print(f"Collecting {episodes} episodes → {out_path}")
    with open(out_path, "w") as f:
        for ep in range(episodes):
            obs, _ = env.reset()
            done = False
            step = 0
            while not done:
                action = idm_policy(obs)
                next_obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                f.write(json.dumps({
                    "ep": ep, "step": step,
                    "obs": obs.tolist(), "action": action,
                    "reward": float(reward), "done": done,
                    "next_obs": next_obs.tolist(),
                }) + "\n")
                obs = next_obs
                step += 1
                total_steps += 1
            print(f"  ep {ep+1}/{episodes}: {step} steps, crashed={info.get('crashed', False)}")
    env.close()
    print(f"Collected {total_steps} total steps\n")
    return total_steps


def dispatch_job(dispatcher: str, tag: str, num_steps: int, lora_rank: int, traj_path: str) -> str:
    traj_size = Path(traj_path).stat().st_size
    payload = {
        "algorithm":      "lora",
        "env_id":         "highway-v0",
        "num_steps":      num_steps,
        "checkpoint_tag": tag,
        "lora_rank":      lora_rank,
        "fp16":           True,
        "trajectory_path": traj_path,
        "trajectory_bytes": traj_size,
    }
    print(f"Dispatching to {dispatcher}/jobs …")
    resp = requests.post(f"{dispatcher}/jobs", json=payload, timeout=15)
    resp.raise_for_status()
    data   = resp.json()
    job_id = data["job_id"]
    print(f"  job_id={job_id} status={data['status']}")
    return job_id


def poll_job(dispatcher: str, job_id: str, poll_interval: int = 15) -> dict:
    print(f"\nPolling {dispatcher}/jobs/{job_id} …")
    while True:
        time.sleep(poll_interval)
        try:
            r = requests.get(f"{dispatcher}/jobs/{job_id}", timeout=10)
            r.raise_for_status()
            job = r.json()
            status = job.get("status", "?")
            print(f"  [{time.strftime('%H:%M:%S')}] status={status}")
            if status in ("completed", "dry_run", "failed", "timeout", "error"):
                return job
        except Exception as exc:
            print(f"  Poll error: {exc}", file=sys.stderr)


def promote_ota(ota_url: str, tag: str, checkpoint_url: str):
    print(f"\nPromoting {tag} to OTA server …")
    resp = requests.post(
        f"{ota_url}/release/{tag}",
        json={"checkpoint_url": checkpoint_url, "type": "checkpoint"},
        timeout=10,
    )
    if resp.ok:
        print(f"  OTA promoted: {resp.json()}")
    else:
        print(f"  OTA promote failed: {resp.status_code} {resp.text}", file=sys.stderr)


def main():
    args = parse_args()

    total_steps = collect_trajectories(args.episodes, args.max_steps, args.out)
    job_id      = dispatch_job(args.dispatcher, args.tag, args.num_steps, args.lora_rank, args.out)
    job         = poll_job(args.dispatcher, job_id)

    print(f"\n─── Job Result ───────────────────────────────")
    print(f"  status      : {job.get('status')}")
    if "output" in job:
        print(f"  output      : {job['output']}")
        if args.ota_promote and "checkpoint_url" in job.get("output", {}):
            promote_ota(args.ota_url, args.tag, job["output"]["checkpoint_url"])
    print()


if __name__ == "__main__":
    main()
