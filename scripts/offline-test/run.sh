#!/usr/bin/env bash
# Honest static artifact preflight; network isolation and offline acceptance need operators/runtime evidence.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="$ROOT/reports/offline/$RUN_ID"
STRICT=0
ATTESTED=0
REQUIRE_ATTESTATION=0
ISOLATION_METHOD=""

usage() {
  cat <<'USAGE'
Usage: bash scripts/offline-test/run.sh [options]

Options:
  --run-id ID                       Evidence directory name (default: UTC timestamp)
  --output-dir PATH                 New evidence directory (overrides --run-id)
  --strict                          Frozen-demo checks: missing hashes/revisions become failures
  --operator-isolation-confirmed    Record an operator attestation, not independent verification
  --isolation-method TEXT           Describe firewall/network change and observation method
  --require-isolation-attestation   Fail when no attestation and method are supplied
  -h, --help                        Show this help

This command checks local config, footage, models, references, and the frontend
build. It does not disable networking, reboot, start the demo, measure a soak, or
claim offline acceptance. Exit 0 means static checks passed only; exit 2 means one
or more checks failed. Warnings and NOT VERIFIED items remain visible in reports.
USAGE
}

while (($#)); do
  case "$1" in
    --run-id)
      [[ $# -ge 2 && "$2" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: --run-id requires only letters, numbers, dot, underscore, dash" >&2; exit 64; }
      RUN_ID="$2"; OUTPUT_DIR="$ROOT/reports/offline/$RUN_ID"; shift 2 ;;
    --output-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --output-dir requires a path" >&2; exit 64; }
      OUTPUT_DIR="$2"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    --operator-isolation-confirmed) ATTESTED=1; shift ;;
    --isolation-method)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --isolation-method requires text" >&2; exit 64; }
      ISOLATION_METHOD="$2"; shift 2 ;;
    --require-isolation-attestation) REQUIRE_ATTESTATION=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv-wsl/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv-wsl/bin/python";
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv/bin/python";
  elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN="$(command -v python3)";
  elif command -v python >/dev/null 2>&1; then PYTHON_BIN="$(command -v python)";
  else echo "ERROR: Python 3 is required; no packages are installed by this check" >&2; exit 2; fi
fi

ARGS=(--root "$ROOT" --output-dir "$OUTPUT_DIR" --isolation-method "$ISOLATION_METHOD")
((STRICT)) && ARGS+=(--strict)
((ATTESTED)) && ARGS+=(--operator-isolation-confirmed)
((REQUIRE_ATTESTATION)) && ARGS+=(--require-isolation-attestation)

"$PYTHON_BIN" "$SCRIPT_DIR/check_assets.py" "${ARGS[@]}"
RC=$?
if ((RC != 0)); then
  printf 'Preflight did not pass (exit %d). Inspect the named report; no offline acceptance is claimed.\n' "$RC" >&2
fi
exit "$RC"
