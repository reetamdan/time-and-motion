#!/usr/bin/env bash
# INSTALLATION-TIME dependency setup. Never called by runtime scripts.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
VENV="${CCTV_VENV:-$ROOT/.venv-wsl}"
SKIP_PYTHON=0
SKIP_FRONTEND=0
SKIP_BUILD=0
OFFLINE=0
DEV=0
ISOLATED_FRONTEND=""
WHEELHOUSE="${CCTV_WHEELHOUSE:-$ROOT/.install/wheels}"

usage() {
  cat <<'USAGE'
Usage: bash scripts/setup/setup.sh [options]

Installation-time options:
  --venv PATH        Project virtual environment (default: .venv-wsl)
  --skip-python      Install frontend dependencies only
  --skip-frontend    Install Python dependencies only
  --skip-build       Do not build frontend/dist after dependency installation
  --offline          Install only from local caches/artifacts; never use an index
  --wheelhouse PATH  Local Python wheel directory for --offline
  --dev              Also install backend/requirements-dev.txt (pytest, numpy)
  --isolated-frontend [DIR]
                     Build the frontend from a copy in DIR (default ~/.cache/aegis-ops/frontend-build)
                     and copy only dist/ back. Use this in WSL: a Linux `npm ci` in the shared
                     project would replace the Windows node_modules (platform-specific binaries).
  -h, --help         Show this help

The default online mode may download dependencies. Models, container images, and
TensorRT engines are separate explicit installation steps. This script is not a
runtime entry point and is never invoked by run-*.sh, replay, or benchmark scripts.
USAGE
}

while (($#)); do
  case "$1" in
    --venv) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --venv requires a path" >&2; exit 64; }; VENV="$2"; shift 2 ;;
    --skip-python) SKIP_PYTHON=1; shift ;;
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --offline) OFFLINE=1; shift ;;
    --wheelhouse) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --wheelhouse requires a path" >&2; exit 64; }; WHEELHOUSE="$2"; shift 2 ;;
    --dev) DEV=1; shift ;;
    --isolated-frontend)
      if [[ $# -ge 2 && -n "$2" && "$2" != --* ]]; then ISOLATED_FRONTEND="$2"; shift 2
      else ISOLATED_FRONTEND="$HOME/.cache/aegis-ops/frontend-build"; shift; fi ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

if ((SKIP_PYTHON && SKIP_FRONTEND)); then
  echo "ERROR: both dependency groups are disabled; nothing to do" >&2
  exit 64
fi

PYTHON_BOOTSTRAP="${PYTHON:-${CCTV_WSL_PYTHON:-}}"
if [[ -z "$PYTHON_BOOTSTRAP" ]]; then
  PYTHON_BOOTSTRAP="$(command -v python3 || command -v python || true)"
elif ! [[ "$PYTHON_BOOTSTRAP" == */* ]]; then
  PYTHON_BOOTSTRAP="$(command -v -- "$PYTHON_BOOTSTRAP" || true)"
fi
REQUIREMENTS="${BACKEND_REQUIREMENTS:-}"
if [[ -z "$REQUIREMENTS" ]]; then
  for candidate in "$ROOT/backend/requirements.lock" "$ROOT/backend/requirements.txt"; do
    if [[ -f "$candidate" ]]; then REQUIREMENTS="$candidate"; break; fi
  done
fi

if (( ! SKIP_PYTHON )); then
  [[ -n "$PYTHON_BOOTSTRAP" && -x "$PYTHON_BOOTSTRAP" ]] || { echo "ERROR: Python 3 was not found. Install Python first; no runtime fallback is attempted." >&2; exit 2; }
  "$PYTHON_BOOTSTRAP" --version >/dev/null 2>&1 || { echo "ERROR: Python is present but not executable in this runtime; check Windows/WSL PATH mixing" >&2; exit 2; }
  [[ -n "$REQUIREMENTS" && -f "$REQUIREMENTS" ]] || { echo "ERROR: backend dependency manifest not found. Expected backend/requirements.lock or backend/requirements.txt, or set BACKEND_REQUIREMENTS." >&2; exit 2; }
  if ((OFFLINE)); then
    [[ -d "$WHEELHOUSE" ]] || { echo "ERROR: offline wheelhouse not found: $WHEELHOUSE" >&2; exit 2; }
  fi
fi
if (( ! SKIP_FRONTEND )); then
  command -v node >/dev/null 2>&1 || { echo "ERROR: node is not on PATH" >&2; exit 2; }
  command -v npm >/dev/null 2>&1 || { echo "ERROR: npm is not on PATH" >&2; exit 2; }
  node --version >/dev/null 2>&1 || { echo "ERROR: node was found but cannot execute; fix Windows/WSL PATH mixing before installation" >&2; exit 2; }
  npm --version >/dev/null 2>&1 || { echo "ERROR: npm was found but cannot execute; fix Windows/WSL PATH mixing before installation" >&2; exit 2; }
  [[ -f "$ROOT/frontend/package.json" ]] || { echo "ERROR: frontend/package.json not found" >&2; exit 2; }
  if ((OFFLINE)) && [[ ! -f "$ROOT/frontend/package-lock.json" ]]; then
    echo "ERROR: offline frontend setup requires frontend/package-lock.json and a populated npm cache" >&2
    exit 2
  fi
fi

export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONDONTWRITEBYTECODE=1
export npm_config_update_notifier=false
export npm_config_fund=false
export npm_config_audit=false

if (( ! SKIP_PYTHON )); then
  echo "Creating/reusing project virtual environment: $VENV"
  "$PYTHON_BOOTSTRAP" -m venv "$VENV"
  VENV_PYTHON="$VENV/bin/python"
  [[ -x "$VENV_PYTHON" ]] || { echo "ERROR: expected Linux venv interpreter not found: $VENV_PYTHON" >&2; exit 2; }
  if ((OFFLINE)); then
    "$VENV_PYTHON" -m pip install --no-index --find-links "$WHEELHOUSE" -r "$REQUIREMENTS"
  else
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS"
  fi
  if ((DEV)); then
    "$VENV_PYTHON" -m pip install -r "$ROOT/backend/requirements-dev.txt"
  fi
  "$VENV_PYTHON" -c 'import fastapi, uvicorn; print("FastAPI and uvicorn imports passed")'
fi

if (( ! SKIP_FRONTEND )); then
  FRONTEND_DIR="$ROOT/frontend"
  if [[ -n "$ISOLATED_FRONTEND" ]]; then
    mkdir -p "$ISOLATED_FRONTEND"
    # Sources only; node_modules/dist of the shared tree are never touched.
    tar -C "$ROOT/frontend" --exclude=./node_modules --exclude=./dist -cf - . | tar -C "$ISOLATED_FRONTEND" -xf -
    FRONTEND_DIR="$ISOLATED_FRONTEND"
    echo "Building the frontend in isolated directory: $FRONTEND_DIR"
  fi
  cd "$FRONTEND_DIR"
  if [[ -f package-lock.json ]]; then
    if ((OFFLINE)); then npm ci --offline; else npm ci; fi
  else
    (( ! OFFLINE )) || { echo "ERROR: npm install cannot be used in offline mode without a lockfile" >&2; exit 2; }
    echo "WARNING: package-lock.json is absent; npm install will resolve and create one. Commit and review the lockfile for reproducibility." >&2
    npm install
  fi
  if (( ! SKIP_BUILD )); then
    npm run build
    if [[ -n "$ISOLATED_FRONTEND" ]]; then
      rm -rf "$ROOT/frontend/dist.new" && cp -r "$FRONTEND_DIR/dist" "$ROOT/frontend/dist.new"
      rm -rf "$ROOT/frontend/dist" && mv "$ROOT/frontend/dist.new" "$ROOT/frontend/dist"
      echo "Copied the isolated build to $ROOT/frontend/dist"
    fi
  fi
fi

cat <<EOF
Installation-time setup completed.
  Python environment: $VENV
  Backend manifest: ${REQUIREMENTS:-skipped}
  Frontend: $([[ $SKIP_FRONTEND -eq 0 ]] && printf 'installed' || printf 'skipped')
  Frontend build: $([[ $SKIP_FRONTEND -eq 0 && $SKIP_BUILD -eq 0 ]] && printf 'built' || printf 'skipped')

No model, footage, container image, or engine readiness is implied. Install those
artifacts explicitly and run scripts/offline-test/run.sh before an offline demo.
EOF
