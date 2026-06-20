#!/usr/bin/env python3
"""
Runpod Alpamayo-1 Inference Test — mini-sdv-platform  Milestone 16
====================================================================
Validates that the Alpamayo-1 policy model deployed on a Runpod
Serverless endpoint responds correctly to highway-env observations.

Usage:
  python scripts/runpod-alpamayo-inference.py \
    --endpoint-id <RUNPOD_ENDPOINT_ID> \
    --api-key <RUNPOD_API_KEY> \
    --episodes 3

What this script does:
  1. Generates highway-v0 observations locally (CPU, no GPU)
  2. Sends each observation to Runpod Serverless (RTX 4090, ≥20 GB VRAM)
  3. Receives predicted action from the remote policy
  4. Measures inference latency and checks action validity
  5. Reports a summary table

Cost estimate:
  RTX 4090 Serverless ~$0.00069/s. 3 episodes × ~60 steps × ~0.5s/call
  ≈ 90 calls × ~1 s wait ≈ $0.06. Well within $10/loop budget.

Runpod Serverless input/output contract:
  Input:  {"obs": [[float, ...]]}     — KinematicObservation (5×5 matrix)
  Output: {"action": int}            — DiscreteMetaAction index 0–4
"""

import argparse
import json
import statistics
import sys
import time

import gymnasium as gym
import highway_env  # noqa: F401
import numpy as np
import requests


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Test Alpamayo-1 on Runpod Serverless")
    p.add_argument("--endpoint-id",  required=True,  help="Runpod Serverless endpoint ID")
    p.add_argument("--api-key",      required=True,  help="Runpod API key")
    p.add_argument("--episodes",     type=int, default=3)
    p.add_argument("--max-steps",    type=int, default=60)
    p.add_argument("--timeout-sec",  type=float, default=30.0,
                   help="Max seconds to wait for a single Runpod response")
    return p.parse_args()


def make_env() -> gym.Env:
    env = gym.make("highway-v0", render_mode=None)
    env.configure({
        "observation": {"type": "Kinematics", "vehicles_count": 5, "normalize": False},
        "action": {"type": "DiscreteMetaAction"},
        "duration": 60,
        "real_time_rendering": False,
    })
    return env


def call_runpod(endpoint_id: str, api_key: str, obs: np.ndarray, timeout: float) -> tuple[int, float]:
    """Submit async Runpod job and poll until COMPLETED.

    Returns (action, elapsed_seconds).
    """
    base    = f"https://api.runpod.ai/v2/{endpoint_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # Submit job
    resp = requests.post(
        f"{base}/run",
        headers=headers,
        json={"input": {"obs": obs.tolist()}},
        timeout=10,
    )
    resp.raise_for_status()
    job_id = resp.json()["id"]
    t0 = time.time()

    # Poll for result
    deadline = t0 + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        sr = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=5)
        sr.raise_for_status()
        data   = sr.json()
        status = data.get("status", "")
        if status == "COMPLETED":
            action = int(data["output"]["action"])
            return action, time.time() - t0
        if status == "FAILED":
            raise RuntimeError(f"Runpod job {job_id} failed: {data.get('error')}")

    raise TimeoutError(f"Runpod job {job_id} did not complete within {timeout} s")


def main():
    args = parse_args()
    env  = make_env()

    print(f"\nAlpamayo-1 Inference Test")
    print(f"  Endpoint : {args.endpoint_id}")
    print(f"  Episodes : {args.episodes} × max {args.max_steps} steps")
    print()

    all_latencies: list[float] = []
    results = []

    for ep in range(args.episodes):
        obs, _ = env.reset()
        done = False
        step = 0
        ep_reward = 0.0
        ep_latencies: list[float] = []
        errors = 0

        while not done and step < args.max_steps:
            try:
                action, latency = call_runpod(args.endpoint_id, args.api_key, obs, args.timeout_sec)
                ep_latencies.append(latency)
                all_latencies.append(latency)
            except Exception as exc:
                print(f"  [ep {ep+1} step {step+1}] Error: {exc}", file=sys.stderr)
                action = 1  # Idle fallback
                errors += 1

            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            ep_reward += reward
            step += 1

        avg_lat = statistics.mean(ep_latencies) if ep_latencies else 0
        results.append({
            "episode":   ep + 1,
            "steps":     step,
            "reward":    ep_reward,
            "crashed":   info.get("crashed", False),
            "avg_lat_s": avg_lat,
            "errors":    errors,
        })
        print(
            f"  ep {ep+1}: steps={step} reward={ep_reward:.2f} "
            f"crashed={info.get('crashed', False)} "
            f"avg_latency={avg_lat:.2f}s errors={errors}"
        )

    env.close()

    print("\n─── Summary ───────────────────────────────────")
    print(f"  Total calls  : {len(all_latencies)}")
    if all_latencies:
        print(f"  Latency p50  : {statistics.median(all_latencies):.3f} s")
        print(f"  Latency p95  : {sorted(all_latencies)[int(len(all_latencies)*0.95)]:.3f} s")
        print(f"  Latency max  : {max(all_latencies):.3f} s")
    crash_rate = sum(1 for r in results if r["crashed"]) / len(results)
    print(f"  Collision    : {crash_rate:.0%} ({sum(1 for r in results if r['crashed'])}/{len(results)})")
    mean_reward = statistics.mean(r["reward"] for r in results)
    print(f"  Mean reward  : {mean_reward:.2f}")
    print()


if __name__ == "__main__":
    main()
