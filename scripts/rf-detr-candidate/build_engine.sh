#!/usr/bin/env bash
# Inside the pinned DeepStream container: FP32 strongly typed RF-DETR engine + real-input outputs.
set -Eeuo pipefail
M=/workspace/models/rf-detr-person-candidate
# Variant selection (defaults: 512 input variant from export run-002).
ONNX=$M/${RFDETR_ONNX:-rf_detr_small_rgb01_norm_in_graph.onnx}
ENGINE=$M/${RFDETR_ENGINE:-rf_detr_small_rgb01_fp32_sm120.engine}
EXPORT=/workspace/reports/model-evaluation/rf-detr/export/${RFDETR_EXPORT_RUN:-run-002}
INPUT_PREFIX=${RFDETR_INPUT_PREFIX:-rgb01}
OUT=/workspace/reports/model-evaluation/rf-detr/engine/$(date -u +%Y%m%dT%H%M%SZ)
[[ ! -e "$ENGINE" ]] || { echo "Refusing to overwrite $ENGINE" >&2; exit 2; }
mkdir -p "$OUT"
trtexec --version > "$OUT/trtexec-version.log" 2>&1 || true
nvidia-smi > "$OUT/nvidia-smi.log" 2>&1 || true
printf '%s\n' "trtexec --onnx=$ONNX --saveEngine=$ENGINE --stronglyTyped --noTF32 --skipInference" > "$OUT/build-command.txt"
set +e
trtexec --onnx="$ONNX" --saveEngine="$ENGINE" --stronglyTyped --noTF32 --skipInference > "$OUT/trtexec-build.log" 2>&1
RC=$?
set -e
echo "build_exit=$RC" | tee "$OUT/build-exit.txt"
[[ $RC -eq 0 && -s "$ENGINE" ]] || exit 3
sha256sum "$ONNX" "$ENGINE" > "$OUT/sha256.txt"
for i in 000141 000283; do
  trtexec --loadEngine="$ENGINE" --loadInputs=input:$EXPORT/${INPUT_PREFIX}_$i.f32le --exportOutput="$OUT/trt_outputs_$i.json" \
    --iterations=1 --warmUp=0 --duration=0 --noDataTransfers=false > "$OUT/trtexec-infer-$i.log" 2>&1 || { echo "infer $i failed"; exit 4; }
done
echo "$OUT"
