#!/usr/bin/env bash
# Inside the pinned DeepStream container: RF-DETR variant 2 through the unmodified production extractor + NvDCF.
set -Eeuo pipefail
ROOT=/workspace
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$ROOT/reports/model-evaluation/rf-detr/deepstream/integration-$RUN_ID"
[[ ! -e "$RUN_DIR" ]] || { echo "Refusing to overwrite $RUN_DIR" >&2; exit 2; }
mkdir -p "$RUN_DIR"
set +e
python3 $ROOT/perception/deepstream/service_maker/extract_metadata.py \
  --source $ROOT/demo/cam01.mp4 \
  --infer-config $ROOT/perception/candidates/rf_detr/deepstream/cam01_rf_detr_1080p_infer.txt \
  --tracker-config $ROOT/perception/candidates/rt_detr/deepstream/config_tracker_NvDCF_candidate.yml \
  --output "$RUN_DIR/frames.jsonl" --run-summary "$RUN_DIR/run-summary.json" \
  --camera-id cam-01 --camera-epoch "rfdetr-$RUN_ID" >"$RUN_DIR/stdout.log" 2>"$RUN_DIR/run.log"
RC=$?
python3 $ROOT/scripts/rf-detr-candidate/validate_deepstream.py --run-dir "$RUN_DIR" --extractor-exit "$RC" --builder-result "$RUN_DIR/builder-result.json"
VRC=$?
set -e
echo "extractor_exit=$RC validator_exit=$VRC run_dir=$RUN_DIR"
exit $VRC
