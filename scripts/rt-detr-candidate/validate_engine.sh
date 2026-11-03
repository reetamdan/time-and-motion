#!/usr/bin/env bash
# Executable real-output validator for scripts/setup/build-engines.sh.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
: "${CCTV_ONNX:?required}" "${CCTV_ENGINE:?required}" "${CCTV_VALIDATION_RESULT:?required}"
EXPECTED_ONNX="$ROOT/models/rt-detr-person-candidate/rt_detr_r18vd_raw.onnx"
EXPECTED_ENGINE="$ROOT/models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp32_sm120.engine"
[[ "$CCTV_ONNX" == "$EXPECTED_ONNX" ]] || { echo "Unexpected ONNX path: $CCTV_ONNX" >&2; exit 64; }
[[ "$CCTV_ENGINE" == "$EXPECTED_ENGINE" ]] || { echo "Unexpected engine path: $CCTV_ENGINE" >&2; exit 64; }
[[ -s "$CCTV_ONNX" && -s "$CCTV_ENGINE" ]] || { echo "Missing/empty ONNX or engine" >&2; exit 2; }
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$ROOT/reports/model-evaluation/rt-detr/deepstream/run-$RUN_ID"
[[ ! -e "$RUN_DIR" ]] || { echo "Refusing to overwrite $RUN_DIR" >&2; exit 2; }
mkdir -p "$RUN_DIR"
EPOCH="rtdetr-$RUN_ID"
set +e
python3 /workspace/perception/deepstream/service_maker/extract_metadata.py \
  --source /workspace/demo/cam01.mp4 \
  --infer-config /workspace/perception/candidates/rt_detr/deepstream/cam01_rt_detr_infer.txt \
  --tracker-config /workspace/perception/candidates/rt_detr/deepstream/config_tracker_NvDCF_candidate.yml \
  --output "$RUN_DIR/frames.jsonl" \
  --run-summary "$RUN_DIR/run-summary.json" \
  --camera-id cam-01 --camera-epoch "$EPOCH" \
  >"$RUN_DIR/stdout.log" 2>"$RUN_DIR/run.log"
EXTRACTOR_RC=$?
set -e
set +e
python3 "$ROOT/scripts/rt-detr-candidate/validate_deepstream.py" \
  --run-dir "$RUN_DIR" --extractor-exit "$EXTRACTOR_RC" \
  --builder-result "$CCTV_VALIDATION_RESULT"
VALIDATOR_RC=$?
set -e
printf 'extractor_exit=%d validator_exit=%d run_dir=%s\n' "$EXTRACTOR_RC" "$VALIDATOR_RC" "$RUN_DIR"
exit "$VALIDATOR_RC"
