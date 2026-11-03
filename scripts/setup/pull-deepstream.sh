#!/usr/bin/env bash
# Installation-time pull of the inspected DeepStream 9.2 amd64 manifest.
set -Eeuo pipefail

TAG='ghcr.io/nvidia/deepstream:9.2-triton-multiarch'
EXPECTED='sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85'
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EVIDENCE="$ROOT/reports/environment/deepstream92"
mkdir -p "$EVIDENCE"

docker buildx imagetools inspect --raw "$TAG" > "$EVIDENCE/tag-manifest.json"
DIGEST="$(python3 - "$EVIDENCE/tag-manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as handle:
    document = json.load(handle)
print(next(item['digest'] for item in document.get('manifests', [])
           if item.get('platform', {}).get('os') == 'linux'
           and item.get('platform', {}).get('architecture') == 'amd64'))
PY
)"
if [[ "$DIGEST" != "$EXPECTED" ]]; then
  echo "DeepStream tag moved: expected $EXPECTED but found $DIGEST. Reassess before pulling." >&2
  exit 2
fi
IMAGE="ghcr.io/nvidia/deepstream@$DIGEST"
printf '%s\n' "$IMAGE" > "$EVIDENCE/image-reference.txt"
docker pull "$IMAGE"
docker image inspect "$IMAGE" > "$EVIDENCE/image-inspect.json"
ARCH="$(docker image inspect "$IMAGE" --format '{{.Os}}/{{.Architecture}}')"
if [[ "$ARCH" != 'linux/amd64' ]]; then
  echo "Unexpected local image architecture: $ARCH" >&2
  exit 2
fi
printf 'Verified DeepStream image: %s (%s)\n' "$IMAGE" "$ARCH"
