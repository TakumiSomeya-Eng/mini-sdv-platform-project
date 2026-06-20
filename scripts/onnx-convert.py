#!/usr/bin/env python3
"""
ONNX Conversion — mini-sdv-platform  Milestone 17
==================================================
Downloads Phi-4-mini (Microsoft, MIT) from HuggingFace and converts it
to ONNX format using Optimum, then applies INT4 quantization for edge
deployment on WSL2 CPU (no CUDA required).

Output directory structure (suitable for hostPath volume in k3s):
  /models/phi4-mini-onnx/
    model.onnx               ← FP16 ONNX (from optimum export)
    model_int4.onnx          ← INT4 quantized (onnxruntime.quantization)
    tokenizer.json
    tokenizer_config.json
    special_tokens_map.json
    config.json

Disk space required: ~4 GB FP16 + ~1 GB INT4.
RAM required during conversion: ~8 GB.
Conversion time on WSL2 CPU: ~15–30 min.

Usage:
  python scripts/onnx-convert.py \
    --model-id microsoft/Phi-4-mini-instruct \
    --out-dir /models/phi4-mini-onnx \
    [--int4]  [--fp16-only]

After this script: mount /models/phi4-mini-onnx into ai-monitor-edge
as a k3s hostPath volume (see k8s/deployments/ai-monitor-edge.yaml).
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert Phi-4-mini to ONNX for edge inference")
    p.add_argument("--model-id",    default="microsoft/Phi-4-mini-instruct")
    p.add_argument("--out-dir",     default="/models/phi4-mini-onnx")
    p.add_argument("--fp16-only",   action="store_true", help="Skip INT4 quantization")
    p.add_argument("--int4",        action="store_true", help="Also produce INT4 model (default)")
    p.add_argument("--trust-remote", action="store_true", help="Pass --trust-remote-code to optimum")
    return p.parse_args()


def run(cmd: list[str], desc: str):
    print(f"\n▸ {desc}")
    print(f"  $ {' '.join(cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, check=False)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"  ✗ Failed (exit {result.returncode}) after {elapsed:.0f}s", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"  ✓ Done in {elapsed:.0f}s")


def main():
    args = parse_args()
    out  = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Export to ONNX (FP16) using optimum-cli
    export_cmd = [
        sys.executable, "-m", "optimum.exporters.onnx",
        "--model", args.model_id,
        "--task",  "text-generation-with-past",
        "--dtype", "fp16",
        "--device", "cpu",
        str(out),
    ]
    if args.trust_remote:
        export_cmd.append("--trust-remote-code")
    run(export_cmd, f"Export {args.model_id} → ONNX FP16")

    # 2. INT4 quantization (default unless --fp16-only)
    if not args.fp16_only:
        fp16_path = out / "model.onnx"
        int4_path = out / "model_int4.onnx"

        if not fp16_path.exists():
            # optimum may generate decoder_model.onnx instead
            candidates = list(out.glob("*.onnx"))
            if candidates:
                fp16_path = candidates[0]
            else:
                print("No .onnx file found after export; skipping INT4", file=sys.stderr)
                return

        quant_script = f"""
import sys
from onnxruntime.quantization import quantize_dynamic, QuantType
print("Quantizing {fp16_path} → {int4_path} (INT4) …")
try:
    quantize_dynamic("{fp16_path}", "{int4_path}", weight_type=QuantType.QUInt4)
    print("INT4 done")
except Exception as e:
    print(f"INT4 failed ({{e}}); trying INT8 …", file=sys.stderr)
    from onnxruntime.quantization import QuantType as QT
    quantize_dynamic("{fp16_path}", "{int4_path}", weight_type=QT.QInt8)
    print("INT8 fallback done")
"""
        run([sys.executable, "-c", quant_script], "Quantize FP16 → INT4")

    # Summary
    print("\n─── Conversion complete ──────────────────────────────────")
    for f in sorted(out.glob("*.onnx")):
        size_mb = f.stat().st_size / 1e6
        print(f"  {f.name:<35}  {size_mb:7.1f} MB")
    print(f"\nMount {out} as /models/phi4-mini-onnx in ai-monitor-edge:")
    print(f"  kubectl patch deploy ai-monitor-edge -n sdv \\")
    print(f"    -p '{{\"spec\":{{\"template\":{{\"spec\":{{\"volumes\":[{{\"name\":\"model\",\"hostPath\":{{\"path\":\"{out}\"}}}}]}}}}}}}}' ")
    print()


if __name__ == "__main__":
    main()
