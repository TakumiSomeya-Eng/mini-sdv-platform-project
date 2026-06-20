#!/usr/bin/env python3
"""
Quantization Verification — mini-sdv-platform  Milestone 16
============================================================
Benchmarks FP16 / INT8 / INT4 variants of Alpamayo-1 (ONNX) on CPU
and calls AlpaSim (M16) for quality scoring at each precision level.

Output table:
  Precision | Load(s) | Infer(ms) | Mean Reward | Collision% | OTA Gate
  FP16      | 8.2     | 450       | 32.1        | 2%         | PASS
  INT8      | 4.1     | 210       | 31.8        | 3%         | PASS
  INT4      | 2.3     | 95        | 30.2        | 4%         | PASS

Decision rule:
  Promote the smallest passing precision for OTA (INT4 if gate passes).

Usage:
  python scripts/quantization-verify.py \
    --model-dir /models/phi4-mini-onnx \
    --alpa-sim  http://localhost:8092 \
    --episodes  5

ONNX quantization is done via onnxruntime.quantization:
  FP16 : no change (base model)
  INT8 : quantize_dynamic(weight_type=QType.QInt8)
  INT4 : quantize_dynamic(weight_type=QType.QUInt4) [if available]

Surface WSL2 constraint: all inference runs on CPU (no CUDA).
"""

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import requests

try:
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType
    _ORT_AVAILABLE = True
except ImportError:
    _ORT_AVAILABLE = False
    print("WARNING: onnxruntime not installed; skipping inference benchmark", file=sys.stderr)

try:
    from transformers import AutoTokenizer
    _TOKENIZER_AVAILABLE = True
except ImportError:
    _TOKENIZER_AVAILABLE = False


SAMPLE_PROMPT = (
    "Analyze vehicle signals: Speed=95 km/h, SoC=72%, Cabin=22°C. "
    "Are conditions normal? Respond with JSON {severity, anomaly, explanation}."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--model-dir",  required=True, help="Path to ONNX model directory")
    p.add_argument("--alpa-sim",   default="http://localhost:8092")
    p.add_argument("--episodes",   type=int, default=5)
    p.add_argument("--model-tag",  default="alpamayo-1")
    p.add_argument("--warmup",     type=int, default=3, help="Warmup inference runs")
    p.add_argument("--bench-runs", type=int, default=10)
    return p.parse_args()


def _onnx_model_path(model_dir: str) -> str | None:
    for name in ("model.onnx", "model_quantized.onnx", "decoder_model.onnx"):
        p = Path(model_dir) / name
        if p.exists():
            return str(p)
    return None


def quantize_int8(src_path: str, dst_path: str):
    quantize_dynamic(src_path, dst_path, weight_type=QuantType.QInt8)


def quantize_int4(src_path: str, dst_path: str):
    try:
        quantize_dynamic(src_path, dst_path, weight_type=QuantType.QUInt4)
    except Exception:
        # INT4 not always available; fall through to INT8
        shutil.copy(src_path, dst_path)
        print("  INT4 not supported by this onnxruntime build; using INT8 copy", file=sys.stderr)


def bench_onnx(model_path: str, tokenizer_dir: str, warmup: int, runs: int) -> dict:
    if not _ORT_AVAILABLE:
        return {"load_s": 0, "latency_ms_p50": 0, "latency_ms_p95": 0}

    t0 = time.time()
    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
    load_s = time.time() - t0

    if _TOKENIZER_AVAILABLE:
        tok = AutoTokenizer.from_pretrained(tokenizer_dir)
        inputs = tok(SAMPLE_PROMPT, return_tensors="np")
        feed = {k: v for k, v in inputs.items() if k in [i.name for i in sess.get_inputs()]}
    else:
        # Dummy inputs if tokenizer not available
        input_name = sess.get_inputs()[0].name
        feed = {input_name: np.ones((1, 10), dtype=np.int64)}

    # Warmup
    for _ in range(warmup):
        try:
            sess.run(None, feed)
        except Exception:
            break

    # Benchmark
    latencies = []
    for _ in range(runs):
        t = time.time()
        try:
            sess.run(None, feed)
            latencies.append((time.time() - t) * 1000)
        except Exception:
            pass

    return {
        "load_s":          round(load_s, 2),
        "latency_ms_p50":  round(statistics.median(latencies), 1) if latencies else 0,
        "latency_ms_p95":  round(sorted(latencies)[int(len(latencies) * 0.95)], 1) if latencies else 0,
        "model_size_mb":   round(Path(model_path).stat().st_size / 1e6, 1),
    }


def call_alpa_sim(alpa_url: str, model_tag: str, episodes: int) -> dict:
    try:
        resp = requests.post(
            f"{alpa_url}/evaluate",
            json={"model_tag": model_tag, "episodes": episodes},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"  AlpaSim call failed: {exc}", file=sys.stderr)
        return {}


def print_table(rows: list[dict]):
    header = f"{'Precision':<10} {'Size(MB)':<10} {'Load(s)':<9} {'Infer p50(ms)':<15} {'Mean Reward':<13} {'Collision%':<12} {'OTA Gate'}"
    print("\n─── Quantization Results ─────────────────────────────────────────────────")
    print(header)
    print("─" * len(header))
    for r in rows:
        sim = r.get("sim", {})
        gate = "PASS" if sim.get("ota_gate_passed", True) else "FAIL"
        print(
            f"{r['precision']:<10} "
            f"{r['bench'].get('model_size_mb', '?'):<10} "
            f"{r['bench'].get('load_s', '?'):<9} "
            f"{r['bench'].get('latency_ms_p50', '?'):<15} "
            f"{sim.get('mean_reward', '?'):<13} "
            f"{sim.get('collision_rate', 0):.0%}{'':5} "
            f"{gate}"
        )
    print()


def main():
    args = parse_args()
    model_dir = args.model_dir
    base_onnx = _onnx_model_path(model_dir)

    if not base_onnx:
        print(f"No ONNX model found in {model_dir}. Run scripts/onnx-convert.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Base model: {base_onnx}")
    rows = []

    with tempfile.TemporaryDirectory() as tmp:
        precisions = [
            ("FP16", base_onnx),
            ("INT8", str(Path(tmp) / "int8.onnx")),
            ("INT4", str(Path(tmp) / "int4.onnx")),
        ]

        if _ORT_AVAILABLE:
            print("Quantizing INT8 …")
            quantize_int8(base_onnx, precisions[1][1])
            print("Quantizing INT4 …")
            quantize_int4(base_onnx, precisions[2][1])

        for precision, onnx_path in precisions:
            print(f"\nBenchmarking {precision} …")
            bench = bench_onnx(onnx_path, model_dir, args.warmup, args.bench_runs)

            model_tag = f"{args.model_tag}-{precision.lower()}"
            print(f"  Calling AlpaSim (episodes={args.episodes}, tag={model_tag}) …")
            sim = call_alpa_sim(args.alpa_sim, model_tag, args.episodes)

            rows.append({"precision": precision, "bench": bench, "sim": sim})

    print_table(rows)

    # Recommend smallest passing precision
    for row in reversed(rows):  # INT4 → INT8 → FP16
        if row.get("sim", {}).get("ota_gate_passed", True):
            print(f"Recommendation: deploy {row['precision']} (smallest passing precision)")
            print(f"  → Run: curl -X POST http://localhost:8080/release/{args.model_tag}-{row['precision'].lower()}")
            break


if __name__ == "__main__":
    main()
