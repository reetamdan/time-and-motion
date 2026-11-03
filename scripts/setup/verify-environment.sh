#!/usr/bin/env bash
# Read-only runtime verification. Missing commands and failed probes are evidence, not success.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="$PROJECT_ROOT/reports/environment/$RUN_ID-runtime"
CONTAINER_IMAGE=""
TIMEOUT_SECONDS="${CCTV_PROBE_TIMEOUT_SECONDS:-60}"
ORIGINAL_ARGS=("$@")

usage() {
  cat <<'USAGE'
Usage: bash scripts/setup/verify-environment.sh [options]

Options:
  --output-dir PATH       New evidence directory (default: reports/environment/<timestamp>-runtime)
  --container-image REF   Also inspect this existing image and run nvidia-smi with --pull=never
  -h, --help              Show this help

Run inside the exact WSL/native Linux environment intended for perception. The script
never installs packages, pulls images, loads models, or changes drivers. Exit 2 means
at least one required probe failed or was unavailable.
USAGE
}

while (($#)); do
  case "$1" in
    --output-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --output-dir requires a path" >&2; exit 64; }
      OUTPUT_DIR="$2"; shift 2 ;;
    --container-image)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --container-image requires a reference" >&2; exit 64; }
      CONTAINER_IMAGE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: CCTV_PROBE_TIMEOUT_SECONDS must be a positive integer" >&2; exit 64; }
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "ERROR: refusing to overwrite existing evidence directory: $OUTPUT_DIR" >&2
  exit 1
fi
if ! mkdir -p -- "$OUTPUT_DIR"; then
  echo "ERROR: cannot create evidence directory: $OUTPUT_DIR" >&2
  exit 1
fi

NAMES=(); REQUIRED=(); STATUSES=(); CODES=(); LOGS=(); COMMANDS=()

probe() {
  local name="$1" required="$2"; shift 2
  local log="$OUTPUT_DIR/$name.log" rc=0 status command_text
  printf -v command_text '%q ' "$@"
  if ! command -v -- "$1" >/dev/null 2>&1; then
    printf 'COMMAND NOT FOUND: %s\n' "$command_text" >"$log"
    rc=127; status="not_found"
  else
    if command -v timeout >/dev/null 2>&1; then
      timeout --signal=TERM "$TIMEOUT_SECONDS" "$@" >"$log" 2>&1
      rc=$?
    else
      "$@" >"$log" 2>&1
      rc=$?
    fi
    if ((rc == 0)); then status="passed";
    elif ((rc == 124 || rc == 143)); then status="timed_out";
    else status="failed"; fi
  fi
  NAMES+=("$name"); REQUIRED+=("$required"); STATUSES+=("$status"); CODES+=("$rc")
  LOGS+=("$name.log"); COMMANDS+=("$command_text")
  printf '[%s] %s (exit %s) -> %s\n' "${status^^}" "$name" "$rc" "$log"
}

printf '%s\n' \
  "utc_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "project_root=$PROJECT_ROOT" \
  "script=scripts/setup/verify-environment.sh" \
  "note=Inspection only; no installation, image pull, driver change, or benchmark is performed." \
  >"$OUTPUT_DIR/invocation.txt"
printf 'argv:' >>"$OUTPUT_DIR/invocation.txt"
printf ' %q' "$0" "${ORIGINAL_ARGS[@]}" >>"$OUTPUT_DIR/invocation.txt"
printf '\n' >>"$OUTPUT_DIR/invocation.txt"

probe os_release required cat /etc/os-release
probe kernel required uname -a
probe cpu optional bash -c 'command -v lscpu >/dev/null 2>&1 && lscpu || { echo "lscpu unavailable"; exit 127; };'
probe memory optional bash -c 'command -v free >/dev/null 2>&1 && free -h || { echo "free unavailable"; exit 127; };'
probe disk optional df -h "$PROJECT_ROOT"
probe nvidia_smi required nvidia-smi
probe nvidia_smi_query required nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,memory.used,temperature.gpu,utilization.gpu --format=csv
probe expected_gpu_identity required bash -o pipefail -c 'nvidia-smi --query-gpu=name --format=csv,noheader | grep -Ei "RTX[[:space:]]+5070[[:space:]]+Ti"'
probe nvcc optional nvcc --version
probe python3 optional python3 --version
probe node optional node --version
probe npm optional npm --version
probe ffmpeg optional ffmpeg -version
probe docker_version optional docker version
probe docker_info optional docker info

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$PROJECT_ROOT/.venv-wsl/bin/python" ]]; then PYTHON_BIN="$PROJECT_ROOT/.venv-wsl/bin/python";
  elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python";
  elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3";
  else PYTHON_BIN="python"; fi
fi
probe selected_python_version optional "$PYTHON_BIN" --version
probe python_package_versions optional "$PYTHON_BIN" -c 'import importlib.metadata as m, json; names=("torch","tensorrt","tensorrt-cu13","pycuda","numpy","PyYAML","fastapi","uvicorn"); print(json.dumps({n:(m.version(n) if any(d.metadata["Name"].lower()==n.lower() for d in m.distributions()) else "not installed") for n in names}, indent=2))'
probe cuda_torch_compute required "$PYTHON_BIN" -c 'import json, torch; assert torch.cuda.is_available(), "torch.cuda.is_available() is false"; torch.cuda.init(); x=torch.arange(1024,device="cuda",dtype=torch.float64); actual=(x*x).sum().item(); expected=sum(i*i for i in range(1024)); assert actual==expected, (actual,expected); print(json.dumps({"device":torch.cuda.get_device_name(0),"capability":torch.cuda.get_device_capability(0),"torch":torch.__version__,"torch_cuda":torch.version.cuda,"compute_check":"passed"}))'
probe tensorrt_python required "$PYTHON_BIN" -c 'import json, tensorrt; print(json.dumps({"tensorrt":getattr(tensorrt,"__version__","unknown")}))'
probe trtexec optional trtexec --version
probe deepstream_version required deepstream-app --version
probe gstreamer_version required gst-launch-1.0 --version
for plugin in nvstreammux nvinfer nvtracker nvdsanalytics nvvideoconvert; do
  probe "gstreamer_plugin_$plugin" required gst-inspect-1.0 "$plugin"
done
for plugin in nvv4l2decoder nvh264dec nvh265dec; do
  probe "gstreamer_plugin_$plugin" optional gst-inspect-1.0 "$plugin"
done

for config in cameras.yaml floors.yaml zones.yaml portals.yaml analytics.yaml models.yaml runtime.yaml; do
  probe "artifact_config_${config%.yaml}" optional test -f "$PROJECT_ROOT/configs/$config"
done
probe artifact_frontend_build optional test -f "$PROJECT_ROOT/frontend/dist/index.html"

if [[ -n "$CONTAINER_IMAGE" ]]; then
  probe container_image_inspect required docker image inspect "$CONTAINER_IMAGE"
  probe container_gpu_nvidia_smi required docker run --rm --pull=never --gpus all --entrypoint nvidia-smi "$CONTAINER_IMAGE"
fi

required_failures=0
optional_unavailable=0
for i in "${!NAMES[@]}"; do
  if [[ "${STATUSES[$i]}" != "passed" ]]; then
    if [[ "${REQUIRED[$i]}" == "required" ]]; then ((required_failures+=1)); else ((optional_unavailable+=1)); fi
  fi
done

{
  printf 'name\trequired\tstatus\texit_code\tlog\tcommand\n'
  for i in "${!NAMES[@]}"; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${NAMES[$i]}" "${REQUIRED[$i]}" "${STATUSES[$i]}" "${CODES[$i]}" "${LOGS[$i]}" "${COMMANDS[$i]}"
  done
} >"$OUTPUT_DIR/summary.tsv"

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"; value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"; value="${value//$'\r'/\\r}"; value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

{
  printf '{\n  "schema_version": 1,\n'
  printf '  "run_id": "%s",\n' "$(json_escape "$RUN_ID")"
  printf '  "utc_finished": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "scope": "selected-linux-runtime",\n'
  printf '  "phase_0_result": "BLOCKED",\n'
  printf '  "phase_0_note": "These probes do not replace NVDEC/TensorRT known-clip inference validation.",\n'
  printf '  "required_failure_count": %s,\n' "$required_failures"
  printf '  "optional_unavailable_count": %s,\n' "$optional_unavailable"
  printf '  "probes": [\n'
  for i in "${!NAMES[@]}"; do
    ((i > 0)) && printf ',\n'
    printf '    {"name":"%s","required":%s,"status":"%s","exit_code":%s,"log":"%s","command":"%s"}' \
      "$(json_escape "${NAMES[$i]}")" \
      "$([[ "${REQUIRED[$i]}" == "required" ]] && printf true || printf false)" \
      "$(json_escape "${STATUSES[$i]}")" "${CODES[$i]}" \
      "$(json_escape "${LOGS[$i]}")" "$(json_escape "${COMMANDS[$i]}")"
  done
  printf '\n  ]\n}\n'
} >"$OUTPUT_DIR/summary.json"

printf 'Evidence saved to: %s\n' "$OUTPUT_DIR"
if ((required_failures > 0)); then
  printf 'BLOCKED: %d required runtime probe(s) failed or were unavailable. Phase 0 is not passed.\n' "$required_failures" >&2
  exit 2
fi
printf 'Required probes passed. NVDEC and real TensorRT detection still require known-clip validation.\n'
exit 0
