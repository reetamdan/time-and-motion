#!/usr/bin/env bash
# Build one local TensorRT engine and require independent real-output validation.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
ONNX=""
ENGINE=""
VALIDATOR=""

usage() {
  cat <<'USAGE'
Usage: bash scripts/setup/build-engines.sh --onnx FILE --engine FILE \
  --validator EXECUTABLE -- TRTEXEC_OPTIONS...

Example shape (options must match the selected model/runtime; this is not a preset):
  bash scripts/setup/build-engines.sh --onnx models/detector.onnx \
    --engine models/detector.engine --validator /absolute/local/validate-engine \
    -- --fp16 --stronglyTyped --minShapes=... --optShapes=... --maxShapes=...

The validator is a project/local executable. It receives CCTV_ONNX, CCTV_ENGINE,
and CCTV_VALIDATION_RESULT in its environment. It must run a known-positive local
clip and write JSON containing: status="pass", person_detections>0,
bounding_boxes>0, metadata_observed=true, and known_clip_sha256 (64 hex chars).
No engine is built unless a validator is supplied. A successful build alone is
not treated as proof of detections, NVDEC, tracking, analytics, or acceptance.
USAGE
}

while (($#)); do
  case "$1" in
    --onnx) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --onnx requires a file" >&2; exit 64; }; ONNX="$2"; shift 2 ;;
    --engine) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --engine requires a file" >&2; exit 64; }; ENGINE="$2"; shift 2 ;;
    --validator) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --validator requires an executable" >&2; exit 64; }; VALIDATOR="$2"; shift 2 ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument before --: $1" >&2; usage >&2; exit 64 ;;
  esac
done

[[ -f "$ONNX" ]] || { echo "ERROR: local ONNX file not found: $ONNX" >&2; exit 2; }
[[ -n "$ENGINE" ]] || { echo "ERROR: --engine is required" >&2; exit 64; }
[[ -n "$VALIDATOR" && -f "$VALIDATOR" && -x "$VALIDATOR" ]] || { echo "ERROR: --validator must be an existing executable; no build-only success is reported" >&2; exit 64; }
(($# > 0)) || { echo "ERROR: provide explicit trtexec precision/shape/type options after --" >&2; exit 64; }
command -v trtexec >/dev/null 2>&1 || { echo "ERROR: trtexec not found; install/release-match TensorRT first" >&2; exit 2; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "ERROR: nvidia-smi not found in the build runtime" >&2; exit 2; }
if ! nvidia-smi --query-gpu=name --format=csv,noheader | grep -Eqi 'RTX[[:space:]]+5070[[:space:]]+Ti'; then
  echo "ERROR: expected RTX 5070 Ti was not identified; refusing to label this engine as a laptop build" >&2
  exit 2
fi
command -v realpath >/dev/null 2>&1 || { echo "ERROR: realpath is required to confine engine output safely" >&2; exit 2; }
[[ "$ENGINE" = /* ]] || ENGINE="$ROOT/$ENGINE"
ENGINE="$(realpath -m -- "$ENGINE")"
MODELS_ROOT="$(realpath -m -- "$ROOT/models")"
case "$ENGINE" in
  "$MODELS_ROOT"/*) ;;
  *) echo "ERROR: engine output must resolve below $MODELS_ROOT/" >&2; exit 64 ;;
esac
[[ ! -e "$ENGINE" ]] || { echo "ERROR: refusing to overwrite existing engine: $ENGINE" >&2; exit 2; }
mkdir -p -- "$(dirname -- "$ENGINE")"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/reports/engines/$RUN_ID"
if ! mkdir -p -- "$OUT"; then echo "ERROR: cannot create $OUT" >&2; exit 1; fi
VALIDATION_RESULT="$OUT/validation.json"

{
  printf 'utc_started=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'onnx=%q\nengine=%q\nvalidator=%q\ntrtexec_args=' "$ONNX" "$ENGINE" "$VALIDATOR"
  printf ' %q' "$@"; printf '\n'
} >"$OUT/build-metadata.txt"
nvidia-smi >"$OUT/nvidia-smi.log" 2>&1 || { echo "ERROR: nvidia-smi failed; see $OUT/nvidia-smi.log" >&2; exit 2; }
trtexec --version >"$OUT/trtexec-version.log" 2>&1 || true

set +e
trtexec --onnx="$ONNX" --saveEngine="$ENGINE" "$@" >"$OUT/trtexec.log" 2>&1
BUILD_RC=$?
set -e
if ((BUILD_RC != 0)); then
  printf 'BUILD FAILED (exit %d). See %s\n' "$BUILD_RC" "$OUT/trtexec.log" >&2
  exit "$BUILD_RC"
fi
[[ -s "$ENGINE" ]] || { echo "ERROR: trtexec exited 0 but no nonempty engine was produced" >&2; exit 3; }

set +e
CCTV_ONNX="$ONNX" CCTV_ENGINE="$ENGINE" CCTV_VALIDATION_RESULT="$VALIDATION_RESULT" \
  "$VALIDATOR" >"$OUT/validation.log" 2>&1
VALIDATOR_RC=$?
set -e
if ((VALIDATOR_RC != 0)); then
  printf 'ENGINE VALIDATION FAILED (exit %d). Engine is not accepted. See %s\n' "$VALIDATOR_RC" "$OUT/validation.log" >&2
  exit "$VALIDATOR_RC"
fi

PYTHON_BIN="${PYTHON:-$(command -v python3 || command -v python || true)}"
if [[ -n "$PYTHON_BIN" && "$PYTHON_BIN" != */* ]]; then PYTHON_BIN="$(command -v -- "$PYTHON_BIN" || true)"; fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || { echo "ERROR: Python is required to inspect validator evidence" >&2; exit 2; }
"$PYTHON_BIN" - "$ENGINE" "$VALIDATION_RESULT" "$OUT" <<'PY'
import hashlib, json, re, sys
from pathlib import Path
engine, result_path, out = map(Path, sys.argv[1:])
try:
    result = json.loads(result_path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"ERROR: validator did not produce readable result JSON: {exc}")
checks = {
    "status": result.get("status") == "pass",
    "person_detections": isinstance(result.get("person_detections"), int) and not isinstance(result.get("person_detections"), bool) and result["person_detections"] > 0,
    "bounding_boxes": isinstance(result.get("bounding_boxes"), int) and not isinstance(result.get("bounding_boxes"), bool) and result["bounding_boxes"] > 0,
    "metadata_observed": result.get("metadata_observed") is True,
    "known_clip_sha256": isinstance(result.get("known_clip_sha256"), str) and re.fullmatch(r"[0-9a-fA-F]{64}", result["known_clip_sha256"]) is not None,
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("ERROR: real-output validation evidence failed/incomplete: " + ", ".join(failed))
digest = hashlib.sha256(engine.read_bytes()).hexdigest()
summary = {
    "schema_version": 1,
    "engine": str(engine),
    "engine_sha256": digest,
    "build_result": "passed",
    "real_detection_validation": result,
    "scope_note": "Engine build and known-positive detector output only; NVDEC, tracking, analytics, capacity, and acceptance remain unproven."
}
(out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(f"Verified engine evidence: {out / 'summary.json'}")
print("Detector output validated by the supplied runner; no broader acceptance is claimed.")
PY
