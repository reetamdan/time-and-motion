#!/usr/bin/env bash
# Validate a pinned replay and invoke an explicit local runner; never synthesize replay results.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PROFILE=""
REFERENCE=""
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
STRICT=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/replay.sh --profile FILE --reference FILE [options] -- LOCAL_RUNNER [ARGS...]

Options:
  --run-id ID        Evidence directory name under reports/replay/
  --strict           Require frozen config/model/source hashes and revisions
  --dry-run          Validate local assets only; do not invoke a runner or create results
  -h, --help         Show this help

The local runner receives CCTV_REPLAY_PROFILE, CCTV_REPLAY_REFERENCE,
CCTV_REPLAY_PROFILE_SHA256, CCTV_REPLAY_REFERENCE_SHA256, CCTV_REPLAY_RUN_ID,
CCTV_REPLAY_OUTPUT, and CCTV_REPLAY_PREFLIGHT in its environment. It must perform
real replay/evaluation and write $CCTV_REPLAY_OUTPUT/result.json containing:
  schema_version, status ("pass" or "fail"), profile_sha256,
  reference_sha256, metrics (object), and evidence (object).
Hashes must match the supplied files. Runner stdout/stderr is preserved. No default
runner is guessed; omitting it is a blocker, not a passing replay.
USAGE
}

while (($#)); do
  case "$1" in
    --profile) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --profile requires a file" >&2; exit 64; }; PROFILE="$2"; shift 2 ;;
    --reference) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --reference requires a file" >&2; exit 64; }; REFERENCE="$2"; shift 2 ;;
    --run-id) [[ $# -ge 2 && "$2" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: invalid --run-id" >&2; exit 64; }; RUN_ID="$2"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument before --: $1" >&2; usage >&2; exit 64 ;;
  esac
done

[[ -n "$PROFILE" && -f "$PROFILE" ]] || { echo "ERROR: --profile must name an installed readable fixture/profile" >&2; exit 64; }
[[ -n "$REFERENCE" && -f "$REFERENCE" ]] || { echo "ERROR: --reference must name real reference labels/events; no reference is invented" >&2; exit 64; }
if (($# == 0 && ! DRY_RUN)); then
  echo "ERROR: no local replay runner supplied after --. Replay is BLOCKED; no result was created." >&2
  exit 64
fi
if ((DRY_RUN && $# > 0)); then
  echo "ERROR: --dry-run cannot be combined with a runner command" >&2
  exit 64
fi

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv-wsl/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv-wsl/bin/python";
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv/bin/python";
  else PYTHON_BIN="$(command -v python3 || command -v python || true)"; fi
fi
command -v -- "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: project Python not found: $PYTHON_BIN" >&2; exit 2; }
PROFILE="$(cd -- "$(dirname -- "$PROFILE")" && pwd -P)/$(basename -- "$PROFILE")"
REFERENCE="$(cd -- "$(dirname -- "$REFERENCE")" && pwd -P)/$(basename -- "$REFERENCE")"
OUT="$ROOT/reports/replay/$RUN_ID"
[[ ! -e "$OUT" ]] || { echo "ERROR: refusing to overwrite replay directory: $OUT" >&2; exit 1; }

PREFLIGHT_ARGS=(--root "$ROOT" --output-dir "$OUT/preflight" --replay-profile "$PROFILE" --reference "$REFERENCE")
((STRICT)) && PREFLIGHT_ARGS+=(--strict)
set +e
"$PYTHON_BIN" "$ROOT/scripts/offline-test/check_assets.py" "${PREFLIGHT_ARGS[@]}" >"$OUT-preflight.log" 2>&1
PREFLIGHT_RC=$?
set -e
if ((PREFLIGHT_RC != 0)); then
  printf 'Replay asset validation failed (exit %d). See %s-preflight.log and %s/preflight/report.md\n' "$PREFLIGHT_RC" "$OUT" "$OUT" >&2
  exit "$PREFLIGHT_RC"
fi
mkdir -p -- "$OUT"
mv -- "$OUT-preflight.log" "$OUT/preflight.log"

read -r PROFILE_SHA256 REFERENCE_SHA256 < <("$PYTHON_BIN" - "$PROFILE" "$REFERENCE" <<'PY'
import hashlib, sys
def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
print(digest(sys.argv[1]), digest(sys.argv[2]))
PY
)
printf 'utc_started=%s\nprofile=%q\nprofile_sha256=%s\nreference=%q\nreference_sha256=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PROFILE" "$PROFILE_SHA256" "$REFERENCE" "$REFERENCE_SHA256" >"$OUT/run-metadata.txt"
if ((DRY_RUN)); then
  printf 'Static replay assets validated. No runner was invoked and no replay result was produced.\n'
  exit 0
fi

export CCTV_REPLAY_PROFILE="$PROFILE"
export CCTV_REPLAY_REFERENCE="$REFERENCE"
export CCTV_REPLAY_PROFILE_SHA256="$PROFILE_SHA256"
export CCTV_REPLAY_REFERENCE_SHA256="$REFERENCE_SHA256"
export CCTV_REPLAY_RUN_ID="$RUN_ID"
export CCTV_REPLAY_OUTPUT="$OUT"
export CCTV_REPLAY_PREFLIGHT="$OUT/preflight/checklist.json"
export CCTV_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 PIP_NO_INDEX=1
printf 'runner:' >>"$OUT/run-metadata.txt"; printf ' %q' "$@" >>"$OUT/run-metadata.txt"; printf '\n' >>"$OUT/run-metadata.txt"

set +e
"$@" >"$OUT/runner.log" 2>&1
RUNNER_RC=$?
set -e
printf 'runner_exit_code=%d\nutc_finished=%s\n' "$RUNNER_RC" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/run-metadata.txt"
if ((RUNNER_RC != 0)); then
  printf 'Replay runner failed (exit %d). See %s/runner.log; no success is inferred.\n' "$RUNNER_RC" "$OUT" >&2
  exit "$RUNNER_RC"
fi

"$PYTHON_BIN" - "$OUT/result.json" "$PROFILE_SHA256" "$REFERENCE_SHA256" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    result = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"ERROR: runner exited 0 but result.json is missing/invalid: {exc}")
required = ("schema_version", "status", "profile_sha256", "reference_sha256", "metrics", "evidence")
missing = [key for key in required if key not in result]
if missing:
    raise SystemExit("ERROR: result.json missing fields: " + ", ".join(missing))
if result["status"] not in ("pass", "fail"):
    raise SystemExit('ERROR: result.json status must be "pass" or "fail"')
if result["profile_sha256"] != sys.argv[2] or result["reference_sha256"] != sys.argv[3]:
    raise SystemExit("ERROR: result provenance hashes do not match the validated inputs")
if not isinstance(result["metrics"], dict) or not isinstance(result["evidence"], dict):
    raise SystemExit("ERROR: result metrics and evidence must be objects")
print(f"Replay runner reported: {result['status']}; inspect {path} and linked raw evidence")
if result["status"] != "pass":
    raise SystemExit(4)
PY
