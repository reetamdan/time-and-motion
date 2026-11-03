#!/usr/bin/env bash
# Run inside the pinned DeepStream container: bash run_diagnostic.sh VARIANT OUT_DIR [--with-tracker]
# CONFIG_DIR / CONFIG_PREFIX / NETWORK_SIZE / TRACKER_CONFIG may be overridden for another candidate (defaults: RT-DETR, 640).
set -Eeuo pipefail
VARIANT="$1"; OUT="$2"; TRACK="${3:-}"
HERE=/workspace/perception/candidates/rt_detr/diagnostics
CONFIG_DIR="${CONFIG_DIR:-$HERE}"
CONFIG="$CONFIG_DIR/cam01_${CONFIG_PREFIX:-rt_detr}_infer_diag_${VARIANT}.txt"
if [[ -e "$OUT" ]]; then echo "Refusing to reuse existing evidence dir $OUT" >&2; exit 2; fi
mkdir -p "$OUT"
WORK="$(mktemp -d /tmp/rtdetr-diag.XXXXXX)"
cd "$WORK"
TRACK_ARGS=()
if [[ "$TRACK" == "--with-tracker" ]]; then
  TRACK_ARGS=(--tracker-config "${TRACKER_CONFIG:-/workspace/perception/candidates/rt_detr/deepstream/config_tracker_NvDCF_candidate.yml}")
fi
set +e
python3 "$HERE/extract_pretracker.py" --infer-config "$CONFIG" --out-dir "$OUT" "${TRACK_ARGS[@]}" > "$OUT/stdout.log" 2> "$OUT/run.log"
STATUS=$?
set -e
python3 "$HERE/slice_input_dump.py" --dump "$WORK/ip_tensor_dump.bin" --out-dir "$OUT" --frames 141,283 --network-size "${NETWORK_SIZE:-640}" || STATUS=$?
rm -rf "$WORK"
exit "$STATUS"
