#!/usr/bin/env bash
# Build the RTX A6000 (sm86) TensorRT engines inside the pinned DeepStream 9.2 container:
#   bash scripts/deepstream.sh bash /workspace/scripts/a6000/build-engines.sh
# Then pin them: python scripts/a6000/configure.py generate
# Existing engines are never overwritten (delete one deliberately to rebuild it). Engines are specific
# to the GPU architecture and TensorRT version; a successful build is not a detection result, so
# rerun scripts/a6000/validate_profiles.py afterwards.
set -Eeuo pipefail
ROOT=/workspace
LOGS="$ROOT/reports/a6000/build-logs"
mkdir -p "$LOGS"
if ! nvidia-smi --query-gpu=name --format=csv,noheader | grep -q 'RTX A6000'; then
  echo 'An RTX A6000 was not detected; refusing this architecture-specific build.' >&2
  exit 2
fi
nvidia-smi > "$LOGS/nvidia-smi.log"
trtexec --version > "$LOGS/trtexec-version.log" 2>&1 || true

build() {
  local id="$1" onnx="$2" engine="$3"
  shift 3
  if [[ -e "$ROOT/$engine" ]]; then
    echo "Keeping existing engine: $engine"
    return 0
  fi
  echo "Building $id -> $engine ..."
  trtexec --onnx="$ROOT/$onnx" --saveEngine="$ROOT/$engine" --skipInference "$@" > "$LOGS/$id-build.log" 2>&1
  test -s "$ROOT/$engine"
  sha256sum "$ROOT/$engine"
}

build trafficcamnet-smoke models/trafficcamnet-smoke/resnet18_trafficcamnet_pruned.onnx \
  models/trafficcamnet-smoke/resnet18_trafficcamnet_pruned_fp16_sm86.engine --fp16
# Selected detector: FP16 (2.4 ms vs 5.1 ms FP32 per frame; boxes match FP32, reports/a6000/optimization-receipt.json).
build rt-detr-person-candidate models/rt-detr-person-candidate/rt_detr_r18vd_raw.onnx \
  models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine --fp16
# Dynamic-batch RT-DETR (batch 1-8, tuned for 4) for pipelines shared by several cameras. Its ONNX is the
# dynamic-batch re-export of the same checkpoint (scripts/rt-detr-candidate/export_onnx_dynamic.py).
build rt-detr-person-candidate-batched models/rt-detr-person-candidate/rt_detr_r18vd_raw_dynbatch.onnx \
  models/rt-detr-person-candidate/rt_detr_r18vd_raw_dynbatch_b1-8_fp16_sm86.engine --fp16 \
  --minShapes=pixel_values:1x3x640x640 --optShapes=pixel_values:4x3x640x640 --maxShapes=pixel_values:8x3x640x640
build rf-detr-person-candidate models/rf-detr-person-candidate/rf_detr_small_1080p_rgb01_resize_norm_in_graph.onnx \
  models/rf-detr-person-candidate/rf_detr_small_1080p_rgb01_fp32_sm86.engine --stronglyTyped --noTF32
echo 'A6000 engines ready. Next: python scripts/a6000/configure.py generate, then scripts/a6000/validate_profiles.py.'
