#!/usr/bin/env bash
# Request a run-scoped API reset. This script never deletes artifacts directly.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
RUN_ID=""
API_URL="${CCTV_API_URL:-http://127.0.0.1:8000}"
TIMEOUT="${CCTV_API_TIMEOUT_SECONDS:-15}"

usage() {
  cat <<'USAGE'
Usage: bash scripts/reset-demo.sh --run-id ACTIVE_RUN_ID [--api-url LOOPBACK_URL]

The script first verifies that the facility snapshot's active run_id matches the
requested target, then calls POST /api/v1/demo/reset with that run_id. It refuses
non-loopback URLs and mismatched/idle targets. It does not remove source footage,
models, configs, calibration, or database files directly.
USAGE
}
while (($#)); do
  case "$1" in
    --run-id) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --run-id requires an explicit active run id" >&2; exit 64; }; RUN_ID="$2"; shift 2 ;;
    --api-url) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --api-url requires a loopback base URL" >&2; exit 64; }; API_URL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done
[[ -n "$RUN_ID" ]] || { echo "ERROR: --run-id is required; refusing an ambiguous reset" >&2; exit 64; }
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: CCTV_API_TIMEOUT_SECONDS must be a positive integer" >&2; exit 64; }
PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv-wsl/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv-wsl/bin/python";
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv/bin/python";
  else PYTHON_BIN="$(command -v python3 || command -v python || true)"; fi
fi
command -v -- "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: project Python not found: $PYTHON_BIN" >&2; exit 2; }

exec "$PYTHON_BIN" - "$API_URL" "$RUN_ID" "$TIMEOUT" <<'PY'
import json, os, sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

base, run_id, timeout_text = sys.argv[1:]
parsed = urlsplit(base)
if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("ERROR: reset is restricted to a plain-http loopback API URL")
if parsed.username or parsed.password or parsed.query or parsed.fragment:
    raise SystemExit("ERROR: API URL must not contain credentials, query, or fragment")
timeout = int(timeout_text)
headers = {"Accept": "application/json"}
if os.environ.get("CCTV_API_TOKEN"):
    headers["Authorization"] = "Bearer " + os.environ["CCTV_API_TOKEN"]

def request(url, method="GET", payload=None):
    body = None
    request_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=body, headers=request_headers, method=method), timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise SystemExit(f"ERROR: API returned HTTP {exc.code} for {method} {url}: {detail}")
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: local API request failed for {method} {url}: {exc}")

root = base.rstrip("/")
_, snapshot = request(root + "/api/v1/facility/snapshot")
active = snapshot.get("run_id") if isinstance(snapshot, dict) else None
if active != run_id:
    raise SystemExit(f"ERROR: reset target mismatch: requested {run_id!r}, active {active!r}; no reset was sent")
status, response = request(root + "/api/v1/demo/reset", method="POST", payload={"run_id": run_id})
if not isinstance(response, dict):
    raise SystemExit("ERROR: reset endpoint returned a non-object response")
print(f"API acknowledged run-scoped reset for {run_id!r} (HTTP {status}).")
print("This script deleted no files; source footage, models, configs, and calibration were not touched directly.")
PY
