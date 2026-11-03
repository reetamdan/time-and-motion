#!/usr/bin/env bash
# Runtime only: serves an installed frontend or its existing local build; no npm install/build occurs.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
HOST="${FRONTEND_HOST:-127.0.0.1}"
PORT="${FRONTEND_PORT:-5173}"
MODE="${CCTV_FRONTEND_MODE:-dev}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: bash scripts/run-frontend.sh [-- additional frontend-server arguments]

Environment: FRONTEND_HOST=127.0.0.1, FRONTEND_PORT=5173,
CCTV_FRONTEND_MODE=dev (or preview). Preview requires frontend/dist/index.html.
Use scripts/setup/setup.sh to install/build at installation time. For the normal
production demo, FastAPI serves frontend/dist and this separate server is unnecessary.
USAGE
  exit 0
fi

[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || { echo "ERROR: FRONTEND_PORT must be an integer from 1 to 65535" >&2; exit 64; }
[[ "$MODE" == "dev" || "$MODE" == "preview" ]] || { echo "ERROR: CCTV_FRONTEND_MODE must be dev or preview" >&2; exit 64; }
command -v node >/dev/null 2>&1 || { echo "ERROR: node not found; runtime does not install it" >&2; exit 2; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm not found; runtime does not install it" >&2; exit 2; }
node --version >/dev/null 2>&1 || { echo "ERROR: node is present but not executable; fix Windows/WSL PATH mixing" >&2; exit 2; }
npm --version >/dev/null 2>&1 || { echo "ERROR: npm is present but not executable; fix Windows/WSL PATH mixing" >&2; exit 2; }
[[ -f "$ROOT/frontend/package.json" ]] || { echo "ERROR: frontend/package.json not found" >&2; exit 2; }
[[ -d "$ROOT/frontend/node_modules" ]] || { echo "ERROR: frontend dependencies missing; run scripts/setup/setup.sh at installation time" >&2; exit 2; }
if [[ "$MODE" == "preview" && ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "ERROR: frontend/dist/index.html missing; build during installation with scripts/setup/setup.sh" >&2
  exit 2
fi
case "$HOST" in
  127.0.0.1|localhost|::1) ;;
  *) echo "WARNING: non-loopback or wildcard FRONTEND_HOST='$HOST'; configure access controls explicitly" >&2 ;;
esac

export CCTV_OFFLINE="${CCTV_OFFLINE:-1}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
export no_proxy="${no_proxy:-$NO_PROXY}"
cd "$ROOT/frontend"
echo "Starting frontend $MODE server on http://$HOST:$PORT"
exec npm run "$MODE" -- --host "$HOST" --port "$PORT" --strictPort "$@"
