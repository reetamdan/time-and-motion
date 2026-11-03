#!/usr/bin/env bash
# Install Python-only Service Maker dependencies into the mounted project.
# Run inside the pinned container, for example:
#   bash scripts/deepstream.sh bash /workspace/scripts/setup/install-service-maker-deps.sh
set -Eeuo pipefail

PROJECT_ROOT="${SERVICE_MAKER_PROJECT_ROOT:-/workspace}"
TARGET="$PROJECT_ROOT/perception/deepstream/service_maker/.deps"
REQUIREMENTS="$PROJECT_ROOT/perception/deepstream/service_maker/requirements.txt"

python3 -m pip install \
  --disable-pip-version-check \
  --no-deps \
  --only-binary=:all: \
  --upgrade \
  --target "$TARGET" \
  --requirement "$REQUIREMENTS"
