#!/usr/bin/env bash
# Runtime only: starts the already-installed local API; never installs or downloads anything.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
HOST="${BACKEND_HOST:-127.0.0.1}"
PORT="${BACKEND_PORT:-8000}"
PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv-wsl/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv-wsl/bin/python";
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON_BIN="$ROOT/.venv/bin/python";
  else PYTHON_BIN="$(command -v python3 || command -v python || true)"; fi
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: bash scripts/run-backend.sh [-- additional uvicorn arguments]

Environment: BACKEND_HOST=127.0.0.1, BACKEND_PORT=8000, PYTHON=explicit interpreter.
By default, Bash resolves .venv-wsl/bin/python, then .venv/bin/python, then python3.
The ASGI module is intentionally fixed to backend.app.main:app. Additional arguments
are passed to uvicorn. This runtime command never invokes pip, npm, model downloads,
engine builds, or container pulls.
USAGE
  exit 0
fi

[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || { echo "ERROR: BACKEND_PORT must be an integer from 1 to 65535" >&2; exit 64; }
command -v -- "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: project Python not found: $PYTHON_BIN. Run scripts/setup/setup.sh at installation time." >&2; exit 2; }
[[ -f "$ROOT/backend/app/main.py" ]] || { echo "ERROR: backend/app/main.py not found; cannot start backend.app.main:app" >&2; exit 2; }
case "$HOST" in
  127.0.0.1|localhost|::1) ;;
  *) echo "WARNING: non-loopback or wildcard BACKEND_HOST='$HOST'. Configure authentication/firewall explicitly; RTSP credentials and evidence must not be exposed." >&2 ;;
esac

# Project-local WSL toolchain (ffmpeg/ffprobe for evidence) when installed by install-wsl-toolchain.sh.
if [[ -f "$ROOT/.install/wsl/env.sh" ]]; then
  # shellcheck source=/dev/null
  source "$ROOT/.install/wsl/env.sh"
fi

export CCTV_CONFIG_DIR="${CCTV_CONFIG_DIR:-$ROOT/configs}"
export CCTV_DATA_DIR="${CCTV_DATA_DIR:-$ROOT/data}"
export CCTV_MODEL_DIR="${CCTV_MODEL_DIR:-$ROOT/models}"
export CCTV_OFFLINE="${CCTV_OFFLINE:-1}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export HF_DATASETS_OFFLINE="${HF_DATASETS_OFFLINE:-1}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
export no_proxy="${no_proxy:-$NO_PROXY}"
export PIP_NO_INDEX=1
export PYTHONDONTWRITEBYTECODE=1

cd "$ROOT"
echo "Starting backend.app.main:app on http://$HOST:$PORT (local artifacts only)"
exec "$PYTHON_BIN" -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT" "$@"
