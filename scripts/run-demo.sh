#!/usr/bin/env bash
# Run the local demo in the foreground. Ctrl+C stops only processes started by this script.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
MODE="production"
PREFLIGHT=1
STRICT=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-demo.sh [options]

Options:
  --dev              Start Vite in addition to the API (production default uses FastAPI + frontend/dist)
  --strict           Require frozen hashes/revisions during static preflight
  --skip-preflight   Explicit developer escape hatch; the run remains unvalidated
  --run-id ID        Run/evidence identifier (letters, numbers, dot, underscore, dash)
  -h, --help         Show this help

Loopback defaults: API 127.0.0.1:8000; development frontend 127.0.0.1:5173.
Logs and preflight evidence go to reports/demo/<run_id>/. No packages, models,
images, or engines are downloaded or built. Ctrl+C stops the managed processes.
USAGE
}

while (($#)); do
  case "$1" in
    --dev) MODE="dev"; shift ;;
    --strict) STRICT=1; shift ;;
    --skip-preflight) PREFLIGHT=0; shift ;;
    --run-id)
      [[ $# -ge 2 && "$2" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: invalid --run-id" >&2; exit 64; }
      RUN_ID="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

OUT="$ROOT/reports/demo/$RUN_ID"
[[ ! -e "$OUT" ]] || { echo "ERROR: refusing to overwrite run directory: $OUT" >&2; exit 1; }
mkdir -p -- "$OUT"
PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv-wsl/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv-wsl/bin/python";
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv/bin/python";
  else PYTHON_BIN="$(command -v python3 || command -v python || true)"; fi
fi
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
HEALTH_URL="${BACKEND_HEALTH_URL:-http://127.0.0.1:$BACKEND_PORT/health/ready}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:$FRONTEND_PORT/}"
TIMEOUT="${CCTV_START_TIMEOUT_SECONDS:-90}"
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: CCTV_START_TIMEOUT_SECONDS must be a positive integer" >&2; exit 64; }
command -v -- "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: project Python not found: $PYTHON_BIN; run installation first" >&2; exit 2; }

{
  printf 'utc_started=%s\nrun_id=%s\nmode=%s\npreflight=%s\nstrict=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN_ID" "$MODE" "$PREFLIGHT" "$STRICT"
  printf 'backend_health_url=%s\n' "$HEALTH_URL"
  [[ "$MODE" == "dev" ]] && printf 'frontend_url=%s\n' "$FRONTEND_URL"
  printf 'note=Process readiness is not a benchmark or acceptance result.\n'
} >"$OUT/run-metadata.txt"

if ((PREFLIGHT)); then
  PREFLIGHT_ARGS=(--output-dir "$OUT/preflight")
  ((STRICT)) && PREFLIGHT_ARGS+=(--strict)
  set +e
  PYTHON="$PYTHON_BIN" bash "$ROOT/scripts/offline-test/run.sh" "${PREFLIGHT_ARGS[@]}" >"$OUT/preflight.log" 2>&1
  PREFLIGHT_RC=$?
  set -e
  if ((PREFLIGHT_RC != 0)); then
    printf 'Demo preflight failed (exit %d). See %s and %s\n' "$PREFLIGHT_RC" "$OUT/preflight.log" "$OUT/preflight/report.md" >&2
    exit "$PREFLIGHT_RC"
  fi
else
  printf 'WARNING: static preflight explicitly skipped; this run is not artifact-validated.\n' | tee "$OUT/preflight-skipped.txt" >&2
fi

BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then kill "$FRONTEND_PID" 2>/dev/null || true; fi
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then kill "$BACKEND_PID" 2>/dev/null || true; fi
  [[ -n "$FRONTEND_PID" ]] && wait "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]] && wait "$BACKEND_PID" 2>/dev/null || true
  printf 'utc_stopped=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/run-metadata.txt"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_url() {
  local name="$1" url="$2" pid="$3" response="$4" elapsed
  for ((elapsed=0; elapsed<TIMEOUT; elapsed++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'ERROR: %s process exited before readiness; inspect its log.\n' "$name" >&2
      return 1
    fi
    if "$PYTHON_BIN" -c 'import sys, urllib.request; response=urllib.request.urlopen(sys.argv[1], timeout=2); body=response.read(); open(sys.argv[2], "wb").write(body)' "$url" "$response" >/dev/null 2>&1; then
      printf '%s became ready after %d second(s): %s\n' "$name" "$elapsed" "$url"
      return 0
    fi
    sleep 1
  done
  printf 'ERROR: timed out after %d seconds waiting for %s at %s\n' "$TIMEOUT" "$name" "$url" >&2
  return 1
}

export CCTV_RUN_ID="$RUN_ID"
export CCTV_OFFLINE="${CCTV_OFFLINE:-1}"
PYTHON="$PYTHON_BIN" CCTV_RUN_ID="$RUN_ID" bash "$ROOT/scripts/run-backend.sh" >"$OUT/backend.log" 2>&1 &
BACKEND_PID=$!
printf 'backend_pid=%s\n' "$BACKEND_PID" >>"$OUT/run-metadata.txt"
if ! wait_for_url "backend readiness" "$HEALTH_URL" "$BACKEND_PID" "$OUT/readiness-response.json"; then
  printf 'Backend did not become ready. See %s\n' "$OUT/backend.log" >&2
  exit 2
fi

DISPLAY_URL="http://127.0.0.1:$BACKEND_PORT/"
if [[ "$MODE" == "dev" ]]; then
  CCTV_FRONTEND_MODE=dev bash "$ROOT/scripts/run-frontend.sh" >"$OUT/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  printf 'frontend_pid=%s\n' "$FRONTEND_PID" >>"$OUT/run-metadata.txt"
  if ! wait_for_url "frontend" "$FRONTEND_URL" "$FRONTEND_PID" "$OUT/frontend-response.html"; then
    printf 'Frontend did not become ready. See %s\n' "$OUT/frontend.log" >&2
    exit 2
  fi
  DISPLAY_URL="$FRONTEND_URL"
fi

printf 'processes_ready_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/run-metadata.txt"
printf 'Demo processes are ready at %s\nLogs: %s\nPress Ctrl+C to stop.\n' "$DISPLAY_URL" "$OUT"
printf 'Readiness does not prove detections, analytics, four-feed capacity, or offline acceptance.\n'

while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    printf 'ERROR: backend stopped unexpectedly; inspect %s\n' "$OUT/backend.log" >&2
    exit 2
  fi
  if [[ -n "$FRONTEND_PID" ]] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    printf 'ERROR: frontend stopped unexpectedly; inspect %s\n' "$OUT/frontend.log" >&2
    exit 2
  fi
  sleep 2
done
