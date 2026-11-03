#!/usr/bin/env bash
# Run a command in the locally installed, pinned DeepStream 9.2 image.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REFERENCE_FILE="$ROOT/reports/environment/deepstream92/image-reference.txt"
if [[ ! -f "$REFERENCE_FILE" ]]; then
  echo "Pinned DeepStream image reference is missing; run scripts/setup/pull-deepstream.sh first." >&2
  exit 1
fi
IMAGE="$(tr -d '\r\n' < "$REFERENCE_FILE")"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Pinned DeepStream image is not installed locally; no automatic pull was attempted." >&2
  exit 1
fi
# WSL exposes the NVDEC/NVENC user libraries only under /usr/lib/wsl/lib and they must be mounted
# into the container. On native Linux (e.g. the future A6000 host) the NVIDIA Container Toolkit
# injects them itself (NVIDIA_DRIVER_CAPABILITIES=all), so nothing is mounted.
CODEC_MOUNTS=()
if [[ -d /usr/lib/wsl/lib ]]; then
  for library in libnvcuvid.so.1 libnvidia-encode.so.1; do
    if [[ ! -f "/usr/lib/wsl/lib/$library" ]]; then
      echo "Required WSL codec library is missing: /usr/lib/wsl/lib/$library" >&2
      exit 1
    fi
    CODEC_MOUNTS+=(-v "/usr/lib/wsl/lib/$library:/usr/lib/x86_64-linux-gnu/$library:ro")
  done
fi
if [[ "$#" -eq 0 ]]; then
  echo "Usage: bash scripts/deepstream.sh COMMAND [ARGS...]" >&2
  exit 64
fi
NAME_ARGS=()
if [[ -n "${DEEPSTREAM_CONTAINER_NAME:-}" ]]; then
  NAME_ARGS=(--name "$DEEPSTREAM_CONTAINER_NAME")
fi

# --init: the image entrypoint is a bash script that does not exec its command, so as PID 1
# it ignores SIGTERM and every `docker stop` waited the full grace period before SIGKILL
# (measured 10.49 s -> 0.45 s; reports/validation/stop-latency/result.json).
# --log-driver none: the attached `docker run` still streams stdout/stderr to the caller (the backend
# reads frames from it), but the daemon no longer keeps a second json-file copy of every frame record
# under /var/lib/docker/containers (unbounded disk and guest page-cache growth, about 120 KB/s per
# feed; reports/benchmarks/memory-diagnosis-*). Nothing in the project uses `docker logs`.
exec docker run --rm --init --gpus all --log-driver none "${NAME_ARGS[@]}" \
  -e CUDA_CACHE_DISABLE=0 \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  "${CODEC_MOUNTS[@]}" \
  -v "$ROOT:/workspace" \
  -w /workspace \
  "$IMAGE" "$@"
