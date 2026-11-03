#!/usr/bin/env bash
# Candidate driver for the project's non-overwriting build + real-output validator.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
OUT="$ROOT/reports/model-evaluation/rt-detr/engine"
ENGINE="$ROOT/models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp32_sm120.engine"
mkdir -p "$OUT"
[[ ! -e "$ENGINE" ]] || { echo "Refusing to overwrite existing candidate engine: $ENGINE" >&2; exit 2; }
[[ ! -e "$OUT/build-command.json" ]] || { echo "Candidate build already attempted; inspect evidence" >&2; exit 2; }
set +e
bash "$ROOT/scripts/setup/build-engines.sh" \
  --onnx /workspace/models/rt-detr-person-candidate/rt_detr_r18vd_raw.onnx \
  --engine /workspace/models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp32_sm120.engine \
  --validator /workspace/scripts/rt-detr-candidate/validate_engine.sh \
  -- --stronglyTyped --noTF32 2>&1 | tee "$OUT/build-engines-console.log"
BUILD_RC=${PIPESTATUS[0]}
set -e
python3 "$ROOT/scripts/rt-detr-candidate/relocate_engine_report.py" --build-rc "$BUILD_RC"
exit "$BUILD_RC"
